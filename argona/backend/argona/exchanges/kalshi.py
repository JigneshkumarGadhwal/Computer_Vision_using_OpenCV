"""Kalshi Trade API v2 adapter.

Field names follow the official ``kalshi_python_sync`` SDK (OpenAPI 3.34): fixed-point dollar
strings (``yes_bid_dollars``...), fixed-point counts (``*_fp``), ``orderbook_fp`` with
``yes_dollars``/``no_dollars`` bid ladders, and v2 order routing at ``/portfolio/events/orders``.
Legacy integer-cent fields are accepted as a fallback.

Auth (trading only): headers KALSHI-ACCESS-KEY / -SIGNATURE / -TIMESTAMP where the signature
is RSA-PSS(SHA256, salt=digest length) over ``timestamp_ms + METHOD + path`` (path without query,
including the ``/trade-api/v2`` prefix). Market data endpoints are public.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..domain import (
    Action,
    BookLevel,
    FeeModel,
    MarketStatus,
    NormalizedMarket,
    OrderBook,
    OrderStatus,
    Outcome,
    Venue,
    VenueFill,
    VenueOrderAck,
    VenuePosition,
    utcnow,
)
from ..market_data.classify import classify
from .base import (
    AuthError,
    Capabilities,
    HttpClient,
    MarketDataAdapter,
    ProviderError,
    TradingAdapter,
    parse_ts,
    to_float,
)

log = logging.getLogger("argona.kalshi")

KALSHI_TAKER_RATE = 0.07  # General Trading Fees Table coefficient; multiplied by series/event fee_multiplier


def _price(m: dict[str, Any], key: str) -> float | None:
    v = to_float(m.get(f"{key}_dollars"))
    if v is None:
        cents = to_float(m.get(key))
        v = cents / 100.0 if cents is not None else None
    if v is not None and v > 1.0:  # defensive: cents slipped through
        v /= 100.0
    return v


def _count(m: dict[str, Any], key: str) -> float:
    v = to_float(m.get(f"{key}_fp"))
    if v is None:
        v = to_float(m.get(key), 0.0)
    return v or 0.0


def _status(raw: str | None, result: str | None) -> MarketStatus:
    s = (raw or "").lower()
    if s in ("active", "open"):
        return MarketStatus.OPEN
    if s in ("settled", "finalized", "determined") or (result and result.lower() in ("yes", "no")):
        return MarketStatus.RESOLVED
    if s in ("closed", "inactive", "paused"):
        return MarketStatus.CLOSED
    return MarketStatus.UNKNOWN


def _tick(m: dict[str, Any]) -> float:
    ranges = m.get("price_ranges") or []
    steps = [to_float(r.get("step")) for r in ranges if isinstance(r, dict)]
    steps = [s for s in steps if s]
    if steps:
        return min(steps)
    tick = to_float(m.get("tick_size"))
    if tick:
        return tick / 100.0 if tick >= 1 else tick
    return 0.01


def normalize_market(m: dict[str, Any], event: dict[str, Any] | None = None) -> NormalizedMarket | None:
    ticker = m.get("ticker")
    if not ticker:
        return None
    if m.get("mve_collection_ticker") or m.get("mve_selected_legs"):
        return None  # multivariate combos: payoff not a simple binary on one event
    if (m.get("market_type") or "binary") != "binary":
        return None
    event = event or {}
    result_raw = (m.get("result") or "").lower()
    status = _status(m.get("status"), result_raw)
    result = Outcome.YES if result_raw == "yes" else Outcome.NO if result_raw == "no" else None
    settle_val = to_float(m.get("settlement_value_dollars"))
    if settle_val is None and result is not None:
        settle_val = 1.0 if result is Outcome.YES else 0.0
    title = m.get("title") or event.get("title") or ticker
    sub = m.get("yes_sub_title") or m.get("subtitle") or ""
    question = f"{title} — {sub}" if sub and sub not in title else title
    fee_type = (event.get("fee_type_override") or "quadratic").lower()
    mult = to_float(event.get("fee_multiplier_override"), 1.0) or 1.0
    fee = FeeModel(kind="flat", rate=0.0) if fee_type == "flat" else FeeModel(kind="quadratic", rate=KALSHI_TAKER_RATE * mult, round_up_cents=True)
    if m.get("fee_waiver_expiration_time"):
        waiver = parse_ts(m.get("fee_waiver_expiration_time"))
        if waiver and waiver > utcnow():
            fee = FeeModel()
    rules = "\n".join(x for x in [m.get("rules_primary"), m.get("rules_secondary")] if x)
    sources = event.get("settlement_sources") or []
    src = ", ".join(s.get("name") or s.get("url") or "" for s in sources if isinstance(s, dict))
    strike = {k: m.get(k) for k in ("strike_type", "floor_strike", "cap_strike", "functional_strike") if m.get(k) is not None}
    category = classify(f"{question} {ticker} {event.get('series_ticker', '')}", event.get("category"))
    return NormalizedMarket(
        venue=Venue.KALSHI,
        venue_market_id=ticker,
        question=question,
        category=category,
        status=status,
        close_time=parse_ts(m.get("close_time")),
        rules=rules,
        resolution_source=src,
        event_id=m.get("event_ticker") or event.get("event_ticker"),
        outcome_label=sub or None,
        result=result,
        settlement_value=settle_val,
        tick_size=_tick(m),
        min_order_size=1.0,
        volume=_count(m, "volume"),
        volume_24h=_count(m, "volume_24h"),
        liquidity=to_float(m.get("liquidity_dollars"), 0.0) or (to_float(m.get("notional_value_dollars"), 0.0) or 0.0) * _count(m, "open_interest"),
        best_bid=_price(m, "yes_bid"),
        best_ask=_price(m, "yes_ask"),
        last_price=_price(m, "last_price"),
        fee_model=fee,
        strike=strike,
        mutually_exclusive=bool(event.get("mutually_exclusive", False)),
        meta={
            "series_ticker": event.get("series_ticker"),
            "event_title": event.get("title"),
            "expected_expiration_time": m.get("expected_expiration_time"),
            "occurrence_datetime": m.get("occurrence_datetime"),
            "can_close_early": m.get("can_close_early"),
            "url": f"https://kalshi.com/markets/{(event.get('series_ticker') or '').lower()}",
        },
    )


def parse_orderbook(payload: dict[str, Any], ts: datetime | None = None) -> OrderBook:
    """Kalshi returns bids only: YES bids and NO bids. A NO bid at p is a YES ask at 1-p."""
    ob = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes = ob.get("yes_dollars")
    no = ob.get("no_dollars")
    if yes is None and no is None:  # legacy cents ladder
        yes = [[(to_float(p, 0) or 0) / 100.0, q] for p, q in (ob.get("yes") or [])]
        no = [[(to_float(p, 0) or 0) / 100.0, q] for p, q in (ob.get("no") or [])]
    bids = [BookLevel(round(float(p), 6), float(q)) for p, q in (yes or [])]
    asks = [BookLevel(round(1.0 - float(p), 6), float(q)) for p, q in (no or [])]
    return OrderBook(yes_bids=bids, yes_asks=asks, ts=ts or utcnow(), source="kalshi:rest")


class KalshiMarketData(MarketDataAdapter):
    venue = Venue.KALSHI.value
    capabilities = Capabilities(
        discover_markets=True, market_metadata=True, orderbook=True, batch_orderbook=True, streaming=False,
        place_orders=True, cancel_orders=True, order_status=True, fills=True, balances=True, positions=True,
        client_order_id=True, market_orders=False, demo_environment=True,
        notes=["Streaming (WebSocket) requires API-key auth; ARGONA polls REST with incremental refresh.",
               "Trading requires a Kalshi account + API key; eligibility depends on your jurisdiction (KYC)."],
    )

    def __init__(self, base_url: str, *, max_markets: int = 3000, series: list[str] | None = None, rps: float = 8.0,
                 timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self.http = HttpClient(base_url, rps=rps, timeout=timeout, transport=transport)
        self.http_clients.append(self.http)
        self.max_markets = max_markets
        self.series = series or []
        self._batch_ok = True

    async def _events_page(self, cursor: str | None, series: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        if series:
            params["series_ticker"] = series
        return await self.http.request("GET", "/events", params=params) or {}

    async def discover(self) -> list[NormalizedMarket]:
        out: list[NormalizedMarket] = []
        for series in (self.series or [None]):
            cursor = None
            while len(out) < self.max_markets:
                page = await self._events_page(cursor, series)
                for ev in page.get("events") or []:
                    for m in ev.get("markets") or []:
                        nm = normalize_market(m, ev)
                        if nm:
                            out.append(nm)
                cursor = page.get("cursor")
                if not cursor or not page.get("events"):
                    break
        return out[: self.max_markets]

    async def fetch_books(self, markets: list[NormalizedMarket]) -> dict[str, OrderBook]:
        books: dict[str, OrderBook] = {}
        tickers = [m.venue_market_id for m in markets]
        if self._batch_ok and len(tickers) > 1:
            try:
                for i in range(0, len(tickers), 50):
                    chunk = tickers[i:i + 50]
                    data = await self.http.request("GET", "/markets/orderbooks", params=[("tickers", t) for t in chunk]) or {}
                    for ob in data.get("orderbooks") or []:
                        t = ob.get("ticker")
                        if t:
                            books[f"kalshi:{t}"] = parse_orderbook(ob)
                return books
            except (AuthError, ProviderError) as e:
                log.info("kalshi batch orderbooks unavailable (%s); falling back to per-market", e)
                self._batch_ok = False
                books.clear()
        for t in tickers:
            try:
                data = await self.http.request("GET", f"/markets/{t}/orderbook")
                books[f"kalshi:{t}"] = parse_orderbook(data or {})
            except ProviderError as e:
                log.warning("kalshi orderbook %s failed: %s", t, e)
        return books

    async def refresh_market(self, market: NormalizedMarket) -> NormalizedMarket | None:
        data = await self.http.request("GET", f"/markets/{market.venue_market_id}") or {}
        m = data.get("market")
        if not m:
            return None
        ev = {"event_ticker": market.event_id, "series_ticker": market.meta.get("series_ticker"),
              "title": market.meta.get("event_title"), "mutually_exclusive": market.mutually_exclusive}
        nm = normalize_market(m, ev)
        if nm:
            nm.category = market.category
            nm.resolution_source = nm.resolution_source or market.resolution_source
        return nm


class KalshiSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        from cryptography.hazmat.primitives import serialization

        self.key_id = key_id
        self.key = serialization.load_pem_private_key(private_key_pem, password=None)

    @classmethod
    def from_file(cls, key_id: str, path: Path) -> "KalshiSigner":
        return cls(key_id, Path(path).expanduser().read_bytes())

    def headers(self, method: str, url: str) -> dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        ts = str(int(time.time() * 1000))
        path = urlparse(url).path
        msg = (ts + method.upper() + path).encode()
        if isinstance(self.key, Ed25519PrivateKey):
            sig = self.key.sign(msg)
        else:
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(), "KALSHI-ACCESS-TIMESTAMP": ts}


def _order_status(o: dict[str, Any]) -> OrderStatus:
    s = (o.get("status") or "").lower()
    filled = _count(o, "fill_count")
    remaining = _count(o, "remaining_count")
    if s == "executed" or (remaining == 0 and filled > 0):
        return OrderStatus.FILLED
    if s == "canceled":
        return OrderStatus.CANCELED
    if s == "resting":
        return OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.OPEN
    if s == "pending":
        return OrderStatus.OPEN
    return OrderStatus.UNKNOWN


def _ack_from_order(o: dict[str, Any], outcome: Outcome | None = None) -> VenueOrderAck:
    filled = _count(o, "fill_count")
    cost = to_float(o.get("taker_fill_cost_dollars"), 0.0) + to_float(o.get("maker_fill_cost_dollars"), 0.0)
    fees = to_float(o.get("taker_fees_dollars"), 0.0) + to_float(o.get("maker_fees_dollars"), 0.0)
    avg = cost / filled if filled else None
    return VenueOrderAck(venue_order_id=o.get("order_id"), status=_order_status(o), filled_qty=filled,
                         avg_fill_price=avg, fees=fees, raw=o)


class KalshiTrading(TradingAdapter):
    venue = Venue.KALSHI.value

    def __init__(self, base_url: str, signer: KalshiSigner, *, rps: float = 5.0, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = HttpClient(base_url, rps=rps, timeout=timeout, transport=transport)
        self.signer = signer

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        return await self.http.request(method, path, header_factory=self.signer.headers, **kw)

    async def verify(self) -> dict[str, Any]:
        bal = await self.get_balance()
        return {"balance": bal}

    async def get_balance(self) -> float:
        d = await self._req("GET", "/portfolio/balance") or {}
        v = to_float(d.get("balance_dollars"))
        if v is None:
            v = (to_float(d.get("balance"), 0.0) or 0.0) / 100.0
        return v

    async def place_limit_order(self, market: NormalizedMarket, outcome: Outcome, action: Action, price: float, qty: float,
                                client_order_id: str, expires_at: datetime | None) -> VenueOrderAck:
        # v2 book-side semantics: BID = buy YES, ASK = sell YES. Buying NO at p == selling YES at 1-p.
        buy_yes = (outcome is Outcome.YES and action is Action.BUY) or (outcome is Outcome.NO and action is Action.SELL)
        yes_price = price if outcome is Outcome.YES else 1.0 - price
        body: dict[str, Any] = {
            "ticker": market.venue_market_id,
            "client_order_id": client_order_id,
            "side": "bid" if buy_yes else "ask",
            "count": f"{qty:.2f}",
            "price": f"{yes_price:.4f}",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        if expires_at:
            body["expiration_time"] = int(expires_at.timestamp())
        d = await self._req("POST", "/portfolio/events/orders", json=body, idempotent=False) or {}
        filled = to_float(d.get("fill_count"), 0.0) or 0.0
        remaining = to_float(d.get("remaining_count"), 0.0) or 0.0
        status = OrderStatus.FILLED if remaining == 0 and filled > 0 else OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.OPEN
        avg = to_float(d.get("average_fill_price"))
        if avg is not None and outcome is Outcome.NO:
            avg = 1.0 - avg
        fee = (to_float(d.get("average_fee_paid"), 0.0) or 0.0) * filled
        return VenueOrderAck(venue_order_id=d.get("order_id"), status=status, filled_qty=filled, avg_fill_price=avg, fees=fee, raw=d)

    async def cancel_order(self, market: NormalizedMarket, venue_order_id: str) -> VenueOrderAck:
        d = await self._req("DELETE", f"/portfolio/events/orders/{venue_order_id}", idempotent=True) or {}
        return VenueOrderAck(venue_order_id=venue_order_id, status=OrderStatus.CANCELED, raw=d)

    async def get_order(self, market: NormalizedMarket, venue_order_id: str | None, client_order_id: str | None) -> VenueOrderAck | None:
        if venue_order_id:
            try:
                d = await self._req("GET", f"/portfolio/orders/{venue_order_id}") or {}
                if d.get("order"):
                    return _ack_from_order(d["order"])
            except ProviderError as e:
                if "404" not in str(e):
                    raise
        if client_order_id:
            cursor = None
            for _ in range(10):
                params: dict[str, Any] = {"ticker": market.venue_market_id, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                d = await self._req("GET", "/portfolio/orders", params=params) or {}
                for o in d.get("orders") or []:
                    if o.get("client_order_id") == client_order_id:
                        return _ack_from_order(o)
                cursor = d.get("cursor")
                if not cursor:
                    break
        return None

    async def list_open_orders(self) -> list[dict[str, Any]]:
        d = await self._req("GET", "/portfolio/orders", params={"status": "resting", "limit": 200}) or {}
        return d.get("orders") or []

    async def get_fills(self, since: datetime | None) -> list[VenueFill]:
        params: dict[str, Any] = {"limit": 200}
        if since:
            params["min_ts"] = int(since.timestamp())
        out: list[VenueFill] = []
        cursor = None
        for _ in range(20):
            if cursor:
                params["cursor"] = cursor
            d = await self._req("GET", "/portfolio/fills", params=params) or {}
            for f in d.get("fills") or []:
                outcome = Outcome.YES if (f.get("side") or "yes").lower() == "yes" else Outcome.NO
                price = to_float(f.get("yes_price_dollars")) if outcome is Outcome.YES else to_float(f.get("no_price_dollars"))
                out.append(VenueFill(
                    venue_fill_id=str(f.get("fill_id") or f.get("trade_id")),
                    venue_order_id=f.get("order_id"), client_order_id=None,
                    market_id=f.get("ticker") or f.get("market_ticker"), outcome=outcome,
                    action=Action.BUY if (f.get("action") or "buy").lower() == "buy" else Action.SELL,
                    qty=_count(f, "count"), price=price or 0.0, fee=to_float(f.get("fee_cost"), 0.0) or 0.0,
                    ts=parse_ts(f.get("created_time") or f.get("ts")) or utcnow()))
            cursor = d.get("cursor")
            if not cursor:
                break
        return out

    async def get_positions(self) -> list[VenuePosition]:
        d = await self._req("GET", "/portfolio/positions", params={"limit": 1000, "count_filter": "position"}) or {}
        out = []
        for p in d.get("market_positions") or []:
            pos = to_float(p.get("position_fp"), None)
            if pos is None:
                pos = to_float(p.get("position"), 0.0)
            if not pos:
                continue
            out.append(VenuePosition(market_id=p["ticker"], outcome=Outcome.YES if pos > 0 else Outcome.NO, contracts=abs(pos),
                                     cost_basis=to_float(p.get("market_exposure_dollars"))))
        return out

    async def close(self) -> None:
        await self.http.close()


def kalshi_event_date(event_ticker: str | None) -> datetime | None:
    """Parse dates like KXHIGHNY-25OCT21 -> 2025-10-21."""
    if not event_ticker:
        return None
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%y%b%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

