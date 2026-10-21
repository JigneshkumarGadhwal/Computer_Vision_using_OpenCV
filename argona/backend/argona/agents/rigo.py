"""RIGO - market discovery agent. Discovers markets, tracks state, refreshes books, flags stale data,
produces candidate snapshots. No order authority."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from ..database.models import Market, Order, Position
from ..domain import MarketStatus, NormalizedMarket, Venue, utcnow
from ..exchanges.base import ProviderError
from ..market_data.service import row_to_market
from ..probability.crypto import parse_spec as crypto_spec
from ..probability.weather import parse_spec as weather_spec
from .base import Agent, CandidateSet, MarketSnapshot

log = logging.getLogger("argona.rigo")


class Rigo(Agent):
    name = "RIGO"
    role = "Market discovery: finds and tracks markets, refreshes order books, flags stale or incomplete data."

    async def discover(self) -> dict[str, Any]:
        t0 = self.begin()
        summary: dict[str, Any] = {}
        err = None
        try:
            for venue, adapter in self.core.registry.data.items():
                try:
                    markets = await adapter.discover()
                    self.api_calls += 1
                except (ProviderError, OSError) as e:
                    flipped = self.core.registry.mark_sync(venue, False, error=str(e))
                    summary[venue] = {"error": str(e)[:200]}
                    if flipped or self.core.registry.status[venue].last_sync is None:
                        await self.core.events.emit("provider_disconnected", f"{venue}: discovery failed ({str(e)[:160]})",
                                                    severity="warning", agent=self.name, data={"venue": venue})
                    continue
                flipped = self.core.registry.mark_sync(venue, True, markets=len(markets))
                if flipped:
                    await self.core.events.emit("provider_recovered", f"{venue}: connected, {len(markets)} open markets listed",
                                                agent=self.name, data={"venue": venue})
                res = await self.core.markets.upsert(venue, markets, self.core.state.runtime)
                summary[venue] = {"listed": len(markets), "new": len(res["new"]), "changed": len(res["changed"])}
                if res["new"]:
                    names = [self.core.markets.markets[k].question[:70] for k in res["new"][:3] if k in self.core.markets.markets]
                    await self.core.events.emit("market_discovered",
                                                f"{venue}: {len(res['new'])} new market(s)" + (f" e.g. {names[0]}" if names else ""),
                                                agent=self.name, data={"venue": venue, "new": len(res["new"]), "examples": names})
                    self.finding(f"{len(res['new'])} new markets on {venue}")
                if res["changed"]:
                    await self.core.events.emit("market_updated", f"{venue}: {len(res['changed'])} market(s) changed status",
                                                agent=self.name, data={"venue": venue, "keys": res["changed"][:20]})
            st = self.core.markets.stats
            await self.record(None, "discovery", None, summary=summary, eligible=st.eligible, open=st.open)
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)
        return summary

    def _modelled(self, m: NormalizedMarket) -> int:
        """Priority: 3 = has an independent quantitative model available, 1 = generic."""
        if m.is_simulated:
            return 3
        if m.category == "crypto" and crypto_spec(m):
            return 3
        if m.category == "weather" and weather_spec(m):
            return 3
        if m.key in self.core.links_by_market or self.core.forecast_file_has(m.key):
            return 3
        if self.core.calibration.get(m.category):
            return 2
        return 1

    async def refresh(self, cycle_id: str) -> CandidateSet:
        t0 = self.begin()
        err = None
        try:
            async with self.core.db.session() as s:
                held = {k for (k,) in (await s.execute(select(Position.market_key).where(Position.status == "open"))).all()}
                working = {k for (k,) in (await s.execute(select(Order.market_key).where(Order.status.in_(
                    ["PENDING_SUBMIT", "OPEN", "PARTIALLY_FILLED", "UNKNOWN"])))).all()}
                must_rows = (await s.execute(select(Market).where(Market.key.in_(list(held | working))))).scalars().all() if (held or working) else []
            eligible = await self.core.markets.eligible_markets()
            pool = {m.key: m for m in eligible}
            for r in must_rows:
                if r.status == MarketStatus.OPEN.value:
                    pool.setdefault(r.key, row_to_market(r))
            linked_keys = {k for k in self.core.links_by_market}
            ranked = sorted(pool.values(), key=lambda m: (m.key in held or m.key in working, self._modelled(m),
                                                           m.key in linked_keys, m.volume_24h), reverse=True)
            budget = self.core.settings.max_books_per_cycle
            chosen = ranked[:budget]
            # pull in linked partners so cross-venue comparisons have both books
            extra = []
            for m in chosen:
                for other, _rel in self.core.links_by_market.get(m.key, []):
                    if other in pool and pool[other] not in chosen and pool[other] not in extra:
                        extra.append(pool[other])
            chosen += extra[:20]
            by_venue: dict[str, list[NormalizedMarket]] = defaultdict(list)
            for m in chosen:
                by_venue[m.venue.value].append(m)
            books = {}
            for venue, ms in by_venue.items():
                adapter = self.core.registry.data.get(venue)
                if not adapter:
                    continue
                try:
                    books.update(await adapter.fetch_books(ms))
                    self.api_calls += 1
                    self.core.registry.mark_sync(venue, True)
                except (ProviderError, OSError) as e:
                    if self.core.registry.mark_sync(venue, False, error=str(e)):
                        await self.core.events.emit("provider_disconnected", f"{venue}: order books unavailable ({str(e)[:150]})",
                                                    severity="warning", agent=self.name)
            snap_ids = await self.core.markets.record_books(books)
            now = utcnow()
            stale = [m.key for m in chosen if m.key not in books or books[m.key].age_s(now) > self.core.state.runtime.risk.max_staleness_s]
            snaps = [MarketSnapshot(m, books[m.key], snap_ids.get(m.key)) for m in chosen if m.key in books and m.key not in stale]
            self.core.markets.stats.evaluated = len(snaps)
            self.core.markets.stats.last_scan = now.isoformat()
            if stale:
                self.finding(f"{len(stale)} market(s) with stale/missing books this cycle")
            self.finding(f"refreshed {len(books)} books ({', '.join(f'{v}:{len(ms)}' for v, ms in by_venue.items())})")
            await self.record(cycle_id, "candidates", None, n=len(snaps), stale=len(stale), held=len(held))
            return CandidateSet(cycle_id, snaps, sorted(held), stale)
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)

    async def track_resolutions(self) -> list[str]:
        """Refresh markets we hold (or that passed close) and settle resolved ones."""
        t0 = self.begin()
        err = None
        resolved: list[str] = []
        try:
            now = utcnow()
            async with self.core.db.session() as s:
                held = {k for (k,) in (await s.execute(select(Position.market_key).where(Position.status == "open"))).all()}
                rows = (await s.execute(select(Market).where(Market.key.in_(list(held))))).scalars().all() if held else []
            for r in rows:
                if r.status == MarketStatus.OPEN.value and r.close_time and r.close_time > now and r.venue != Venue.SIM.value:
                    continue
                adapter = self.core.registry.data.get(r.venue)
                if not adapter:
                    continue
                try:
                    nm = await adapter.refresh_market(row_to_market(r))
                    self.api_calls += 1
                except ProviderError as e:
                    log.info("refresh %s failed: %s", r.key, e)
                    continue
                if not nm:
                    continue
                await self.core.markets.upsert(r.venue, [nm], self.core.state.runtime, full_listing=False)
                if nm.status is MarketStatus.RESOLVED:
                    resolved.append(r.key)
                    await self.core.settle(r.key)
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)
        return resolved
