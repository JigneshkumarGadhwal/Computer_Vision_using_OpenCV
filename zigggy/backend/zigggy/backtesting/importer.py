"""Import real historical data for backtesting (resolved Polymarket markets + CLOB price history).

Polymarket publishes trade-price history but not historical order books, so imported snapshots
are *price-only*: the book is reconstructed as [p - half_spread, p + half_spread] with an
assumed depth. Both assumptions are stored on every snapshot (``source`` contains
"price-only") and reported in backtest notes. Only prices strictly before resolution are kept.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from ..config import RuntimeSettings
from ..database.models import Market, OrderBookSnapshot
from ..database.session import Database
from ..domain import BookLevel, MarketStatus, OrderBook
from ..exchanges.base import HttpClient, ProviderError, parse_ts
from ..exchanges.polymarket import normalize_market
from ..market_data.service import MarketService

log = logging.getLogger("zigggy.importer")


async def import_polymarket_history(db: Database, gamma_url: str, clob_url: str, *, n_markets: int = 50, half_spread: float = 0.01,
                                    depth: float = 200.0, fidelity_min: int = 60, transport=None) -> dict[str, Any]:
    gamma = HttpClient(gamma_url, rps=4, transport=transport)
    clob = HttpClient(clob_url, rps=4, transport=transport)
    ms = MarketService(db)
    stats = {"markets": 0, "snapshots": 0, "skipped": 0}
    try:
        rows = await gamma.request("GET", "/markets", params={"closed": "true", "limit": n_markets, "order": "volumeNum",
                                                             "ascending": "false"})
        rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
        markets = [m for m in (normalize_market(r) for r in rows) if m and m.status is MarketStatus.RESOLVED and m.result]
        rt = RuntimeSettings()
        await ms.upsert("polymarket", markets, rt, full_listing=False)
        for m in markets:
            raw = next((r for r in rows if str(r.get("id")) == m.venue_market_id), {})
            resolved_at = parse_ts(raw.get("closedTime")) or m.close_time
            if not m.yes_token_id or not resolved_at:
                stats["skipped"] += 1
                continue
            try:
                h = await clob.request("GET", "/prices-history", params={"market": m.yes_token_id, "interval": "max", "fidelity": fidelity_min})
            except ProviderError as e:
                log.warning("history %s failed: %s", m.key, e)
                stats["skipped"] += 1
                continue
            pts = (h or {}).get("history") or []
            async with db.tx() as s:
                r = await s.get(Market, m.key)
                if r:
                    r.resolved_at = resolved_at
                existing = {x for (x,) in (await s.execute(select(OrderBookSnapshot.ts).where(OrderBookSnapshot.market_key == m.key))).all()}
                for pt in pts:
                    ts = parse_ts(pt.get("t"))
                    p = float(pt.get("p", 0))
                    if not ts or ts >= resolved_at - timedelta(minutes=1) or not (0 < p < 1) or ts in existing:
                        continue  # never import post-resolution prices
                    bid, ask = max(0.001, p - half_spread), min(0.999, p + half_spread)
                    book = OrderBook([BookLevel(round(bid, 4), depth)], [BookLevel(round(ask, 4), depth)], ts,
                                     source=f"polymarket:prices-history(price-only,hs={half_spread},depth={depth})")
                    s.add(OrderBookSnapshot(market_key=m.key, ts=ts, book=book.to_json(), source=book.source, is_simulated=False))
                    stats["snapshots"] += 1
            stats["markets"] += 1
    finally:
        await gamma.close()
        await clob.close()
    return stats
