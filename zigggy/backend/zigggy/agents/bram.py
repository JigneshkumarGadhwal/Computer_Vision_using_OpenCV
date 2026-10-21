"""BRAM - research & evidence agent. Gathers approved, timestamped evidence with provenance,
cross-checks sources, flags contradictions and unsupported assumptions. Optional bounded LLM
review of resolution criteria (flags only, never probabilities)."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from ..database.models import Evidence, Market
from ..domain import Venue, utcnow
from ..probability.base import EvidenceItem
from ..probability.crypto import parse_spec as crypto_spec
from ..probability.weather import parse_spec as weather_spec
from .base import Agent, CandidateSet, EvidenceBundle

log = logging.getLogger("zigggy.bram")

MAX_FUTURE_SKEW_S = 120


class Bram(Agent):
    name = "BRAM"
    role = "Research & evidence: gathers approved data with source provenance and timestamps, cross-checks sources, flags contradictions."

    async def research(self, cands: CandidateSet) -> EvidenceBundle:
        t0 = self.begin()
        err = None
        try:
            core = self.core
            assets, stations, forecast_keys = set(), {}, []
            for snap in cands.snapshots:
                m = snap.market
                if m.category == "crypto":
                    spec = crypto_spec(m)
                    if spec:
                        assets.add(spec.asset)
                if m.category == "weather" and not m.is_simulated:
                    ws = weather_spec(m)
                    if ws:
                        stations[f"{ws.station.lat:.4f},{ws.station.lon:.4f}"] = ws.station
                forecast_keys.append((m.key, m.venue_market_id, m.venue))
            items: list[EvidenceItem] = []
            sources: set[str] = set()
            for a in sorted(assets):
                if a == "SIMBTC":
                    if core.sim_source:
                        items += core.sim_source.spot() + core.sim_source.vol()
                    continue
                if core.crypto_source:
                    got = await core.crypto_source.spot(a) + await core.crypto_source.vol(a)
                    self.api_calls += 2
                    items += got
            for st in stations.values():
                if core.weather_source:
                    items += await core.weather_source.forecast(st)
                    self.api_calls += 1
            for key, mid, venue in forecast_keys:
                if venue is Venue.SIM and core.sim_source:
                    items += core.sim_source.forecast(key, mid)
                if core.forecast_file:
                    items += core.forecast_file.forecast(key)
            # validation: timestamps, provenance
            now = utcnow()
            valid: list[EvidenceItem] = []
            contradictions: list[str] = []
            for e in items:
                if (e.observed_at - now).total_seconds() > MAX_FUTURE_SKEW_S:
                    contradictions.append(f"rejected {e.source} {e.kind}: timestamp in the future")
                    continue
                if not e.source or not e.source_url:
                    contradictions.append(f"rejected {e.kind}: missing provenance")
                    continue
                valid.append(e)
            # cross-source consistency for spot prices
            spots: dict[str, list[EvidenceItem]] = defaultdict(list)
            for e in valid:
                if e.kind == "crypto_spot":
                    spots[e.subject].append(e)
            for asset, es in spots.items():
                if len(es) > 1:
                    px = [float(x.payload["price"]) for x in es]
                    dev = (max(px) - min(px)) / min(px)
                    if dev > 0.005:
                        contradictions.append(f"{asset} spot sources disagree by {dev:.2%}")
                        for x in es:
                            x.reliability = "low"
            await self._persist(valid)
            by_kind: dict[str, list[EvidenceItem]] = defaultdict(list)
            for e in valid:
                by_kind[e.kind].append(e)
                sources.add(e.source)
            await self._llm_review(cands)
            if valid:
                self.finding(f"{len(valid)} evidence items from {', '.join(sorted(sources))}"
                             + (f"; {len(contradictions)} contradiction(s)" if contradictions else ""))
                await self.core.events.emit("research_completed",
                                            f"{len(valid)} evidence items ({', '.join(sorted(sources))})"
                                            + (f"; contradictions: {contradictions[0]}" if contradictions else ""),
                                            agent=self.name, severity="warning" if contradictions else "info",
                                            data={"n": len(valid), "sources": sorted(sources), "contradictions": contradictions[:5]},
                                            persist=bool(contradictions) or self.decisions % 10 == 0)
            await self.record(cands.cycle_id, "evidence", None, n=len(valid), sources=sorted(sources), contradictions=contradictions[:10])
            return EvidenceBundle(dict(by_kind), contradictions, sorted(sources))
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)

    async def _persist(self, items: list[EvidenceItem]) -> None:
        if not items:
            return
        async with self.core.db.tx() as s:
            for e in items:
                if e.id is not None:
                    continue
                existing = (await s.execute(select(Evidence.id).where(Evidence.kind == e.kind, Evidence.subject == e.subject,
                                                                     Evidence.source == e.source, Evidence.observed_at == e.observed_at)
                                            .limit(1))).scalar_one_or_none()
                if existing:
                    e.id = existing
                    continue
                row = Evidence(market_key=e.subject if e.kind == "forecast" else None, kind=e.kind, subject=e.subject, source=e.source,
                               source_url=e.source_url, observed_at=e.observed_at, reliability=e.reliability, payload=e.payload,
                               summary=e.summary, is_simulated=e.is_simulated)
                s.add(row)
                await s.flush()
                e.id = row.id

    async def _llm_review(self, cands: CandidateSet) -> None:
        rev = self.core.llm
        if not rev or not rev.available:
            return
        for snap in cands.snapshots[:5]:
            m = snap.market
            if m.is_simulated or m.meta.get("llm_reviewed_at"):
                continue
            res = await rev.review(m.question, m.rules, m.resolution_source)
            if res is None:
                continue
            self.api_calls += 1
            flags = list(res.get("flags") or [])
            if res.get("ambiguous") and "ambiguous_wording" not in flags:
                flags.append("ambiguous_wording")
            async with self.core.db.tx() as s:
                r = await s.get(Market, m.key)
                if r:
                    r.meta = {**(r.meta or {}), "llm_reviewed_at": utcnow().isoformat(), "llm_flags": flags,
                              "llm_notes": str(res.get("notes", ""))[:500], "llm_model": rev.model}
            m.meta.update({"llm_reviewed_at": utcnow().isoformat(), "llm_flags": flags})
            if flags:
                self.finding(f"LLM rules review flagged {', '.join(flags)}", m.key)
                await self.core.events.emit("market_rejected", f"rules review flagged {', '.join(flags)}: {m.question[:80]}",
                                            agent=self.name, market_key=m.key, severity="warning", data={"flags": flags})

    async def prune(self, keep_days: int = 120) -> int:
        from sqlalchemy import delete

        cutoff = utcnow() - timedelta(days=keep_days)
        async with self.core.db.tx() as s:
            r = await s.execute(delete(Evidence).where(Evidence.fetched_at < cutoff))
        return r.rowcount or 0
