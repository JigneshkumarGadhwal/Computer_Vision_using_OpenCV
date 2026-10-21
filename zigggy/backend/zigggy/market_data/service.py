"""Market discovery persistence, eligibility screening, watchlist selection, book cache."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..config import RuntimeSettings
from ..database.models import Market, OrderBookSnapshot, PriceObservation
from ..database.session import Database
from ..domain import FeeModel, MarketStatus, NormalizedMarket, OrderBook, Outcome, Venue, as_utc, utcnow

log = logging.getLogger("zigggy.markets")

AMBIGUITY_PATTERNS = [
    r"\bat (?:its|their|the) (?:sole )?discretion\b", r"\bsubjective\b", r"\bmay be resolved\b.*\bdiscretion",
    r"\bresolution (?:source|criteria) (?:is|are) (?:to be determined|tbd)\b", r"\bin the spirit of\b",
]


def row_to_market(r: Market) -> NormalizedMarket:
    return NormalizedMarket(
        venue=Venue(r.venue), venue_market_id=r.venue_market_id, question=r.question, category=r.category,
        status=MarketStatus(r.status), close_time=r.close_time, rules=r.rules, resolution_source=r.resolution_source,
        event_id=r.event_id, outcome_label=r.outcome_label, result=Outcome(r.result) if r.result in ("YES", "NO") else None,
        settlement_value=r.settlement_value, yes_token_id=r.yes_token_id, no_token_id=r.no_token_id, tick_size=r.tick_size,
        min_order_size=r.min_order_size, volume=r.volume, volume_24h=r.volume_24h, liquidity=r.liquidity, best_bid=r.best_bid,
        best_ask=r.best_ask, last_price=r.last_price, fee_model=FeeModel.from_json(r.fee_model), strike=r.strike or {},
        mutually_exclusive=r.mutually_exclusive, is_simulated=r.is_simulated, meta=r.meta or {},
    )


@dataclass
class ScanStats:
    discovered: int = 0
    open: int = 0
    eligible: int = 0
    rejected: int = 0
    evaluated: int = 0
    estimated: int = 0
    qualified: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    last_discovery: str | None = None
    last_scan: str | None = None


class MarketService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.books: dict[str, OrderBook] = {}
        self.markets: dict[str, NormalizedMarket] = {}  # open markets cache
        self.stats = ScanStats()
        self._last_snapshot: dict[str, tuple[datetime, float | None, float | None]] = {}
        self.spread_history: dict[str, list[tuple[str, float | None, float | None]]] = {}

    async def load_cache(self) -> None:
        async with self.db.session() as s:
            rows = (await s.execute(select(Market).where(Market.status == MarketStatus.OPEN.value))).scalars().all()
        self.markets = {r.key: row_to_market(r) for r in rows}
        self.stats.open = len(self.markets)
        self.stats.eligible = sum(1 for r in rows if r.eligible)

    def eligibility(self, m: NormalizedMarket, runtime: RuntimeSettings, now: datetime) -> tuple[bool, list[str]]:
        flags: list[str] = []
        if m.category not in runtime.categories:
            flags.append("category_disabled")
        if m.status is not MarketStatus.OPEN:
            flags.append(f"status_{m.status.value}")
        if not m.close_time:
            flags.append("no_resolution_time")
        elif m.close_time <= now:
            flags.append("past_close")
        elif (m.close_time - now) > timedelta(days=runtime.risk.max_days_to_resolution):
            flags.append("long_dated")
        if not (m.rules or "").strip() and not m.is_simulated:
            flags.append("no_resolution_criteria")
        elif any(re.search(p, m.rules or "", re.I | re.S) for p in AMBIGUITY_PATTERNS):
            flags.append("ambiguous_resolution")
        if m.best_bid is None and m.best_ask is None and m.venue is not Venue.SIM:
            flags.append("no_quotes")
        if m.venue is Venue.POLYMARKET and not m.yes_token_id:
            flags.append("no_token_id")
        if m.meta.get("llm_flags"):
            flags += [f"llm:{f}" for f in m.meta["llm_flags"]]
        return not flags, flags

    async def upsert(self, venue: str, markets: list[NormalizedMarket], runtime: RuntimeSettings,
                     full_listing: bool = True) -> dict[str, list[str]]:
        """Persist a discovery batch. Returns keys that are new / status-changed. With ``full_listing``
        the batch is the venue's complete open listing, so absent markets are marked for refresh."""
        now = utcnow()
        new, changed = [], []
        incoming = {m.key: m for m in markets}
        async with self.db.tx() as s:
            existing = {r.key: r for r in (await s.execute(select(Market).where(Market.key.in_(list(incoming))))).scalars()} if incoming else {}
            for key, m in incoming.items():
                ok, flags = self.eligibility(m, runtime, now)
                r = existing.get(key)
                if r is None:
                    r = Market(key=key, venue=m.venue.value, venue_market_id=m.venue_market_id, first_seen=now)
                    s.add(r)
                    new.append(key)
                elif r.status != m.status.value:
                    changed.append(key)
                r.event_id, r.question, r.outcome_label, r.category = m.event_id, m.question, m.outcome_label, m.category
                r.rules, r.resolution_source, r.close_time, r.status = m.rules, m.resolution_source, m.close_time, m.status.value
                r.result = m.result.value if m.result else None
                r.settlement_value = m.settlement_value
                r.yes_token_id, r.no_token_id, r.tick_size, r.min_order_size = m.yes_token_id, m.no_token_id, m.tick_size, m.min_order_size
                r.volume, r.volume_24h, r.liquidity = m.volume, m.volume_24h, m.liquidity
                if key not in self.books:
                    r.best_bid, r.best_ask = m.best_bid, m.best_ask
                r.last_price = m.last_price
                r.fee_model, r.strike, r.meta = m.fee_model.to_json(), m.strike, {**(r.meta or {}), **m.meta}
                r.mutually_exclusive, r.is_simulated = m.mutually_exclusive, m.is_simulated
                r.flags, r.eligible, r.updated_at = flags, ok, now
                if m.status is MarketStatus.RESOLVED and r.resolved_at is None:
                    r.resolved_at = now
                if m.status is MarketStatus.OPEN:
                    self.markets[key] = m
                else:
                    self.markets.pop(key, None)
                    self.books.pop(key, None)
            # markets from this venue that disappeared from the open listing -> mark for refresh
            missing = [k for k, mm in self.markets.items() if mm.venue.value == venue and k not in incoming] if full_listing else []
            for k in missing:
                r = await s.get(Market, k)
                if r and r.status == MarketStatus.OPEN.value:
                    r.status = MarketStatus.CLOSED.value if (r.close_time and r.close_time <= now) else MarketStatus.UNKNOWN.value
                    r.eligible = False
                    changed.append(k)
                self.markets.pop(k, None)
        await self.recount(runtime)
        self.stats.last_discovery = now.isoformat()
        return {"new": new, "changed": changed}

    async def reevaluate(self, runtime: RuntimeSettings) -> None:
        """Recompute eligibility for open markets after a settings change."""
        now = utcnow()
        async with self.db.tx() as s:
            rows = (await s.execute(select(Market).where(Market.status == MarketStatus.OPEN.value))).scalars().all()
            for r in rows:
                ok, flags = self.eligibility(row_to_market(r), runtime, now)
                r.eligible, r.flags = ok, flags
        await self.recount(runtime)

    async def recount(self, runtime: RuntimeSettings) -> None:
        async with self.db.session() as s:
            rows = (await s.execute(select(Market.status, Market.eligible, Market.flags))).all()
        self.stats.discovered = len(rows)
        self.stats.open = sum(1 for st, _, _ in rows if st == "open")
        self.stats.eligible = sum(1 for st, el, _ in rows if st == "open" and el)
        self.stats.rejected = self.stats.open - self.stats.eligible
        reasons: dict[str, int] = {}
        for st, el, flags in rows:
            if st == "open" and not el:
                for f in flags or []:
                    reasons[f] = reasons.get(f, 0) + 1
        self.stats.rejection_reasons = reasons

    async def eligible_markets(self) -> list[NormalizedMarket]:
        async with self.db.session() as s:
            rows = (await s.execute(select(Market).where(Market.status == "open", Market.eligible.is_(True)))).scalars().all()
        return [row_to_market(r) for r in rows]

    async def record_books(self, books: dict[str, OrderBook]) -> dict[str, int]:
        """Cache books, persist price observations and (throttled) full snapshots. Returns snapshot ids."""
        now = utcnow()
        snap_ids: dict[str, int] = {}
        async with self.db.tx() as s:
            for key, b in books.items():
                self.books[key] = b
                mid, sp = b.mid, b.spread
                s.add(PriceObservation(market_key=key, ts=b.ts, best_bid=b.best_bid(), best_ask=b.best_ask(), mid=mid, spread=sp,
                                       bid_depth_usd=b.depth_usd(Outcome.YES, "bid"), ask_depth_usd=b.depth_usd(Outcome.YES, "ask"),
                                       source=b.source, is_simulated=b.is_simulated))
                hist = self.spread_history.setdefault(key, [])
                hist.append((as_utc(b.ts).isoformat(), mid, sp))
                del hist[:-200]
                last = self._last_snapshot.get(key)
                if last is None or (now - last[0]).total_seconds() > 300 or last[1] != mid or last[2] != sp:
                    snap = OrderBookSnapshot(market_key=key, ts=b.ts, book=b.to_json(), source=b.source, is_simulated=b.is_simulated)
                    s.add(snap)
                    await s.flush()
                    snap_ids[key] = snap.id
                    self._last_snapshot[key] = (now, mid, sp)
                r = await s.get(Market, key)
                if r:
                    r.best_bid, r.best_ask, r.last_book_at = b.best_bid(), b.best_ask(), b.ts
        return snap_ids

    def mark(self, key: str, outcome: Outcome) -> float | None:
        b = self.books.get(key)
        if b is not None:
            bid = b.best_bid(outcome)
            if bid is not None:
                return bid
            if b.mid is not None:
                return b.mid if outcome is Outcome.YES else 1 - b.mid
        m = self.markets.get(key)
        if m and m.best_bid is not None and m.best_ask is not None:
            return m.best_bid if outcome is Outcome.YES else 1 - m.best_ask
        return None

    async def get(self, key: str) -> NormalizedMarket | None:
        if key in self.markets:
            return self.markets[key]
        async with self.db.session() as s:
            r = await s.get(Market, key)
        return row_to_market(r) if r else None

    def freshness(self, key: str) -> float | None:
        b = self.books.get(key)
        return b.age_s() if b else None

    def snapshot_stats(self) -> dict[str, Any]:
        return self.stats.__dict__.copy()
