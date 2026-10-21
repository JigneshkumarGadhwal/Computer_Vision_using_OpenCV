"""ILSA - fair-value & quantitative agent. Estimates probabilities, computes net edge after fees and
execution costs, applies model-quality thresholds, produces auditable recommendations."""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import timedelta

from ..database.models import Opportunity, ProbabilityEstimate
from ..domain import Outcome, utcnow
from ..probability.base import ModelContext
from ..probability.engine import CombinedEstimate, cohere
from ..spread_engine.opportunity import Candidate, evaluate_entry, evaluate_exit, rank
from .base import Agent, CandidateSet, EvidenceBundle, Valuation

log = logging.getLogger("argona.ilsa")


class Ilsa(Agent):
    name = "ILSA"
    role = "Fair value & quant: probability estimates, net edge after fees/slippage, uncertainty, model-quality gates."

    def __init__(self, core) -> None:
        super().__init__(core)
        self._last_q: dict[str, float] = {}

    async def evaluate(self, cands: CandidateSet, ev: EvidenceBundle, held: dict[tuple[str, str], float]) -> Valuation:
        t0 = self.begin()
        err = None
        try:
            core = self.core
            now = utcnow()
            limits = core.state.runtime.risk
            snaps = {s.market.key: s for s in cands.snapshots}
            estimates: dict[str, CombinedEstimate] = {}
            for key, snap in snaps.items():
                linked = []
                for other, rel in core.links_by_market.get(key, []):
                    ob = core.markets.books.get(other)
                    om = core.markets.markets.get(other)
                    if ob is not None and om is not None:
                        linked.append((om, ob, rel))
                ctx = ModelContext(now=now, evidence=ev.by_kind, linked=linked, calibration=core.calibration,
                                   prior_sigma_floor=limits.market_prior_sigma)
                estimates[key] = core.prob_engine.estimate(snap.market, snap.book, ctx)
            # multi-outcome coherence for exclusive event groups observed in full
            groups: dict[str, list[str]] = defaultdict(list)
            for key, snap in snaps.items():
                if snap.market.mutually_exclusive and snap.market.event_id:
                    groups[snap.market.event_id].append(key)
            for event_id, keys in groups.items():
                total_open = sum(1 for m in core.markets.markets.values() if m.event_id == event_id)
                if len(keys) == total_open and len(keys) > 1:
                    cohere([estimates[k] for k in keys])
            est_ids = await self._persist_estimates(estimates, snaps)
            cand_list: list[Candidate] = []
            for key, snap in snaps.items():
                est = estimates[key]
                for o in (Outcome.YES, Outcome.NO):
                    h = held.get((key, o.value), 0.0)
                    if h > 0 and limits.exit_on_edge_reversal:
                        c = evaluate_exit(snap.market, snap.book, est, o, h, limits, now)
                        if c:
                            cand_list.append(c)
                if not est.abstained:
                    cand_list += evaluate_entry(snap.market, snap.book, est, limits, now)
            ranked = rank(cand_list)
            qualified = [c for c in ranked if c.qualified]
            core.markets.stats.estimated = sum(1 for e in estimates.values() if not e.abstained)
            core.markets.stats.qualified = len(qualified)
            for key, e in estimates.items():
                if e.abstained or e.q is None:
                    continue
                prev = self._last_q.get(key)
                if prev is None or abs(prev - e.q) >= 0.03:
                    self._last_q[key] = e.q
                    await core.events.emit("probability_updated",
                                           f"P(YES)={e.q:.3f} (mkt {e.market_mid:.3f}, conf {e.confidence:.2f}) via "
                                           f"{', '.join(c['model'] for c in e.components if c.get('independent'))}: {snaps[key].market.question[:70]}",
                                           agent=self.name, market_key=key, persist=False,
                                           data={"q": e.q, "mid": e.market_mid, "confidence": e.confidence})
            if qualified:
                self.finding(f"{len(qualified)} qualified; best {qualified[0].market.question[:60]} "
                             f"{qualified[0].outcome.value} net EV {qualified[0].net_ev:.3f}", qualified[0].market.key)
            await self.record(cands.cycle_id, "valuation", None, estimated=core.markets.stats.estimated,
                              abstained=sum(1 for e in estimates.values() if e.abstained), qualified=len(qualified),
                              top=[c.summary() for c in qualified[:5]])
            return Valuation(estimates, est_ids, ranked)
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)

    async def _persist_estimates(self, estimates: dict[str, CombinedEstimate], snaps) -> dict[str, int]:
        ids: dict[str, int] = {}
        async with self.core.db.tx() as s:
            for key, e in estimates.items():
                row = ProbabilityEstimate(market_key=key, model=e.model, model_version=e.version, q_yes=e.q, sigma=e.sigma,
                                          confidence=e.confidence, market_mid=e.market_mid, abstained=e.abstained, reason=e.reason,
                                          components=e.components + ([{"abstentions": e.abstentions}] if e.abstentions else []),
                                          evidence_ids=[x.id for x in e.evidence if x.id is not None],
                                          supporting=e.supporting, conflicting=e.conflicting, snapshot_id=snaps[key].snapshot_id)
                s.add(row)
                await s.flush()
                ids[key] = row.id
        return ids

    async def persist_opportunities(self, cycle_id: str, val: Valuation, mode: str, max_rejected: int = 40) -> dict[int, str]:
        """Persist qualified opportunities and the most interesting rejections. Returns id(candidate)->opportunity id."""
        out: dict[int, str] = {}
        rejected = 0
        now = utcnow()
        ttl = self.core.state.runtime.risk.max_staleness_s
        async with self.core.db.tx() as s:
            for c in val.candidates:
                if not c.qualified:
                    if c.gross_edge <= 0 or rejected >= max_rejected:
                        continue
                    rejected += 1
                oid = uuid.uuid4().hex[:20]
                out[id(c)] = oid
                est = val.estimates.get(c.market.key)
                s.add(Opportunity(
                    id=oid, cycle_id=cycle_id, ts=now, market_key=c.market.key, venue=c.market.venue.value, category=c.market.category,
                    kind=c.kind, outcome=c.outcome.value, action=c.action.value, fair_value=c.fair_value, best_price=c.quote.best,
                    entry_price=c.quote.vwap, exit_value=c.win_prob, gross_edge=c.gross_edge, fee_per_contract=c.quote.fee_per_contract,
                    slippage_per_contract=c.quote.slippage_per_contract + self.core.state.runtime.risk.slippage_buffer,
                    net_ev_per_contract=c.net_ev, est_return=c.est_return, confidence=c.confidence, evidence_quality=c.quality,
                    liquidity_usd=c.liquidity_usd, max_contracts=c.quote.fillable, score=c.score,
                    status="qualified" if c.qualified else "rejected", reasons=c.reasons, risk_flags=c.flags,
                    details={"ref_qty": c.ref_qty, "hours_to_resolution": c.hours_to_resolution,
                             "supporting": est.supporting if est else [], "conflicting": est.conflicting if est else []},
                    estimate_id=val.estimate_ids.get(c.market.key), expires_at=now + timedelta(seconds=ttl), mode=mode))
        for c in val.candidates:
            if c.qualified and id(c) in out:
                await self.core.events.emit("opportunity_qualified",
                                            f"{c.action.value} {c.outcome.value} @ {c.quote.best:.3f} fair {c.win_prob:.3f} net EV {c.net_ev:+.3f} "
                                            f"({c.est_return:+.1%}) conf {c.confidence:.2f}: {c.market.question[:70]}",
                                            agent=self.name, market_key=c.market.key, data={"opportunity_id": out[id(c)], **c.summary()})
        return out
