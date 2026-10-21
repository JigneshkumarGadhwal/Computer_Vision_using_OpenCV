"""Polymarket adapter.

Market data: Gamma API (``/markets``) for discovery/metadata and the CLOB (``/book``, ``/books``)
for order books - both public. Field names follow the official ``polymarket-client`` SDK models
(``outcomes``/``outcomePrices``/``clobTokenIds`` may be JSON-encoded strings, ``feeSchedule`` =
{rate, exponent, takerOnly}).

Trading: order signing (EIP-712) is delegated to the official SDK ``polymarket-client``
(optional extra ``argona[polymarket-live]``), loaded lazily only when live trading on Polymarket
is configured. Polymarket blocks order placement from several jurisdictions (including Germany
and the US for the international venue) - check https://polymarket.com/api/geoblock first.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

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
from .base import Capabilities, HttpClient, MarketDataAdapter, ProviderError, SubmissionUncertain, TradingAdapter, parse_ts, to_float

log = logging.getLogger("argona.polymarket")


def _seq(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return [x.strip() for x in v.split(",") if x.strip()]
    return list(v) if isinstance(v, (list, tuple)) else []


def normalize_market(m: dict[str, Any]) -> NormalizedMarket | None:
    outcomes = [str(o) for o in _seq(m.get("outcomes"))]
    if len(outcomes) != 2:
        return None  # ARGONA trades binary contracts; multi-outcome events are groups of binaries (negRisk)
    tokens = [str(t) for t in _seq(m.get("clobTokenIds"))]
    prices = [to_float(p) for p in _seq(m.get("outcomePrices"))]
    mid = m.get("id") or m.get("conditionId")
    if not mid:
        return None
    closed = bool(m.get("closed"))
    active = bool(m.get("active", True))
    uma = (m.get("umaResolutionStatus") or "").lower()
    result = None
    settle = None
    if closed and len(prices) == 2 and prices[0] is not None and prices[1] is not None:
        if prices[0] >= 0.999 and prices[1] <= 0.001:
            result, settle = Outcome.YES, 1.0
        elif prices[1] >= 0.999 and prices[0] <= 0.001:
            result, settle = Outcome.NO, 0.0
        elif uma == "resolved":
            settle = prices[0]  # 50/50 or other split resolution
    if result is not None or (closed and uma == "resolved"):
        status = MarketStatus.RESOLVED
    elif closed or not active or m.get("acceptingOrders") is False:
        status = MarketStatus.CLOSED
    else:
        status = MarketStatus.OPEN
    fee = FeeModel()
    sched = m.get("feeSchedule") or {}
    if m.get("feesEnabled") and isinstance(sched, dict) and to_float(sched.get("rate")):
        fee = FeeModel(kind="polymarket", rate=float(sched["rate"]), exponent=float(sched.get("exponent", 1) or 1))
    events = m.get("events") or []
    ev = events[0] if events and isinstance(events[0], dict) else {}
    tags = " ".join(t.get("label", "") for t in (ev.get("tags") or m.get("tags") or []) if isinstance(t, dict))
    question = m.get("question") or ev.get("title") or str(mid)
    category = classify(f"{question} {ev.get('title', '')} {tags}", m.get("category") or ev.get("category"))
    yes_first = outcomes[0].lower() in ("yes", "up", "over") or outcomes[1].lower() in ("no", "down", "under")
    if not yes_first:  # named outcomes (e.g. teams): treat outcome[0] as "YES" leg, label it
        question = f"{question} [{outcomes[0]} vs {outcomes[1]}]"
    return NormalizedMarket(
        venue=Venue.POLYMARKET,
        venue_market_id=str(mid),
        question=question,
        category=category,
        status=status,
        close_time=parse_ts(m.get("endDate") or m.get("endDateIso")),
        rules=m.get("description") or "",
        resolution_source=m.get("resolutionSource") or ev.get("resolutionSource") or "",
        event_id=str(ev.get("id")) if ev.get("id") else None,
        outcome_label=m.get("groupItemTitle") or None,
        result=result,
        settlement_value=settle,
        yes_token_id=tokens[0] if tokens else None,
        no_token_id=tokens[1] if len(tokens) > 1 else None,
        tick_size=to_float(m.get("orderPriceMinTickSize"), 0.01) or 0.01,
        min_order_size=to_float(m.get("orderMinSize"), 5.0) or 5.0,
        volume=to_float(m.get("volumeNum"), None) or to_float(m.get("volume"), 0.0) or 0.0,
        volume_24h=to_float(m.get("volume24hr"), 0.0) or 0.0,
        liquidity=to_float(m.get("liquidityNum"), None) or to_float(m.get("liquidity"), 0.0) or 0.0,
        best_bid=to_float(m.get("bestBid")),
        best_ask=to_float(m.get("bestAsk")),
        last_price=to_float(m.get("lastTradePrice")),
        fee_model=fee,
        mutually_exclusive=bool(m.get("negRisk")),
        meta={
            "slug": m.get("slug"), "condition_id": m.get("conditionId"), "neg_risk": bool(m.get("negRisk")),
            "outcomes": outcomes, "event_title": ev.get("title"), "enable_order_book": m.get("enableOrderBook"),
            "url": f"https://polymarket.com/event/{ev.get('slug') or m.get('slug') or ''}",
            "uma_resolution_status": uma or None,
        },
    )


def parse_book(d: dict[str, Any]) -> OrderBook:
    bids = [BookLevel(float(x["price"]), float(x["size"])) for x in d.get("bids") or []]
    asks = [BookLevel(float(x["price"]), float(x["size"])) for x in d.get("asks") or []]
    return OrderBook(yes_bids=bids, yes_asks=asks, ts=parse_ts(d.get("timestamp")) or utcnow(), source="polymarket:clob")


class PolymarketMarketData(MarketDataAdapter):
    venue = Venue.POLYMARKET.value
    capabilities = Capabilities(
        discover_markets=True, market_metadata=True, orderbook=True, batch_orderbook=True, streaming=False,
        place_orders=True, cancel_orders=True, order_status=True, fills=True, balances=True, positions=True,
        client_order_id=False, market_orders=True, demo_environment=False,
        notes=["Order placement is geoblocked in many jurisdictions (incl. DE, US for the international venue).",
               "Live trading needs the optional official SDK: pip install 'argona[polymarket-live]'.",
               "No client order id: ARGONA reconciles uncertain submissions by matching open orders/trades."],
    )

    def __init__(self, gamma_url: str, clob_url: str, *, max_markets: int = 1500, rps: float = 8.0, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self.gamma = HttpClient(gamma_url, rps=rps, timeout=timeout, transport=transport)
        self.clob = HttpClient(clob_url, rps=rps, timeout=timeout, transport=transport)
        self.http_clients += [self.gamma, self.clob]
        self.max_markets = max_markets
        self._batch_ok = True

    async def discover(self) -> list[NormalizedMarket]:
        out: list[NormalizedMarket] = []
        offset, page = 0, 500
        while len(out) < self.max_markets:
            params = {"active": "true", "closed": "false", "limit": page, "offset": offset,
                      "order": "volume24hr", "ascending": "false"}
            data = await self.gamma.request("GET", "/markets", params=params)
            rows = data if isinstance(data, list) else (data or {}).get("data") or []
            for m in rows:
                if m.get("enableOrderBook") is False:
                    continue
                nm = normalize_market(m)
                if nm:
                    out.append(nm)
            if len(rows) < page:
                break
            offset += page
        return out[: self.max_markets]

    async def fetch_books(self, markets: list[NormalizedMarket]) -> dict[str, OrderBook]:
        by_token = {m.yes_token_id: m for m in markets if m.yes_token_id}
        books: dict[str, OrderBook] = {}
        tokens = list(by_token)
        if self._batch_ok and len(tokens) > 1:
            try:
                for i in range(0, len(tokens), 50):
                    chunk = tokens[i:i + 50]
                    data = await self.clob.request("POST", "/books", json=[{"token_id": t} for t in chunk], idempotent=True)
                    for d in data or []:
                        t = d.get("asset_id") or d.get("token_id")
                        if t in by_token:
                            books[by_token[t].key] = parse_book(d)
                return books
            except ProviderError as e:
                log.info("polymarket batch /books failed (%s); falling back to /book", e)
                self._batch_ok = False
                books.clear()
        for t, m in by_token.items():
            try:
                d = await self.clob.request("GET", "/book", params={"token_id": t})
                books[m.key] = parse_book(d or {})
            except ProviderError as e:
                log.warning("polymarket book %s failed: %s", m.key, e)
        return books

    async def refresh_market(self, market: NormalizedMarket) -> NormalizedMarket | None:
        d = await self.gamma.request("GET", f"/markets/{market.venue_market_id}")
        if not d:
            return None
        nm = normalize_market(d)
        if nm:
            nm.category = market.category
        return nm


class PolymarketTrading(TradingAdapter):
    """Live order routing via the official polymarket-client SDK (lazy import)."""

    venue = Venue.POLYMARKET.value

    def __init__(self, private_key: str, wallet: str | None = None) -> None:
        self._pk = private_key
        self._wallet = wallet
        self._client: Any = None
        self.known_tokens: dict[str, tuple[str, Outcome]] = {}

    async def _c(self) -> Any:
        if self._client is None:
            try:
                from polymarket import AsyncSecureClient  # type: ignore
            except ImportError as e:  # pragma: no cover - optional dependency
                raise ProviderError("polymarket-client not installed: pip install 'argona[polymarket-live]'") from e
            self._client = await AsyncSecureClient.create(private_key=self._pk, wallet=self._wallet)
        return self._client

    async def verify(self) -> dict[str, Any]:
        c = await self._c()
        closed_only = await c.get_closed_only_mode()
        bal = await self.get_balance()
        return {"balance": bal, "closed_only": bool(closed_only)}

    async def get_balance(self) -> float:
        c = await self._c()
        ba = await c.get_balance_allowance(asset_type="COLLATERAL")
        return float(ba.balance) / 1e6  # USDC has 6 decimals

    async def place_limit_order(self, market: NormalizedMarket, outcome: Outcome, action: Action, price: float, qty: float,
                                client_order_id: str, expires_at: datetime | None) -> VenueOrderAck:
        c = await self._c()
        token = market.yes_token_id if outcome is Outcome.YES else market.no_token_id
        if not token:
            return VenueOrderAck(None, OrderStatus.REJECTED, error="missing token id")
        exp = None
        if expires_at:  # SDK requires >= 3 minutes in the future
            exp = int(max(expires_at, utcnow() + timedelta(minutes=4)).timestamp())
        try:
            resp = await c.place_limit_order(token_id=token, price=str(price), size=str(qty), side=action.value, expiration=exp)
        except (TimeoutError, OSError) as e:
            raise SubmissionUncertain(f"polymarket submit: {type(e).__name__}") from e
        if getattr(resp, "ok", False):
            st = str(getattr(resp, "status", "live")).lower()
            status = OrderStatus.FILLED if st == "matched" else OrderStatus.OPEN
            return VenueOrderAck(venue_order_id=str(resp.order_id), status=status,
                                 raw={"status": st, "trade_ids": list(getattr(resp, "trade_ids", ()))})
        return VenueOrderAck(None, OrderStatus.REJECTED, error=f"{getattr(resp, 'code', 'unknown')}: {getattr(resp, 'message', '')}")

    async def cancel_order(self, market: NormalizedMarket, venue_order_id: str) -> VenueOrderAck:
        c = await self._c()
        r = await c.cancel_order(order_id=venue_order_id)
        return VenueOrderAck(venue_order_id=venue_order_id, status=OrderStatus.CANCELED, raw={"resp": str(r)[:500]})

    async def get_order(self, market: NormalizedMarket, venue_order_id: str | None, client_order_id: str | None) -> VenueOrderAck | None:
        c = await self._c()
        if not venue_order_id:
            return None  # caller falls back to fingerprint matching via list_open_orders/get_fills
        try:
            o = await c.get_order(order_id=venue_order_id)
        except Exception as e:  # SDK raises typed errors; treat not-found as None
            if "not found" in str(e).lower() or "404" in str(e):
                return None
            raise ProviderError(str(e)) from e
        matched = float(o.size_matched)
        orig = float(o.original_size)
        st = o.status.lower()
        if matched >= orig > 0:
            status = OrderStatus.FILLED
        elif st in ("canceled", "cancelled", "canceled_market_resolved"):
            status = OrderStatus.CANCELED
        elif matched > 0:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.OPEN
        return VenueOrderAck(venue_order_id=o.id, status=status, filled_qty=matched, avg_fill_price=float(o.price))

    async def list_open_orders(self) -> list[dict[str, Any]]:
        c = await self._c()
        out = []
        async for o in c.list_open_orders().iter_items():
            out.append({"id": o.id, "asset_id": o.asset_id, "side": o.side, "price": float(o.price),
                        "original_size": float(o.original_size), "size_matched": float(o.size_matched),
                        "created_at": o.created_at.isoformat()})
        return out

    async def get_fills(self, since: datetime | None) -> list[VenueFill]:
        c = await self._c()
        out: list[VenueFill] = []
        async for t in c.list_account_trades(after=str(int(since.timestamp())) if since else None).iter_items():
            out.append(VenueFill(venue_fill_id=t.id, venue_order_id=t.taker_order_id, client_order_id=None,
                                 market_id=str(t.asset_id), outcome=Outcome.YES if str(t.outcome).lower() in ("yes", "up") else Outcome.NO,
                                 action=Action(t.side), qty=float(t.size), price=float(t.price),
                                 fee=float(t.size) * float(t.price) * float(t.fee_rate_bps) / 10_000, ts=utcnow()))
        return out

    async def get_positions(self) -> list[VenuePosition]:
        """Outcome tokens are ERC-1155 balances (6 decimals). Query the tokens ARGONA knows it traded;
        the execution service sets ``known_tokens`` before reconciliation."""
        c = await self._c()
        out = []
        for token, (market_id, outcome) in self.known_tokens.items():
            ba = await c.get_balance_allowance(asset_type="CONDITIONAL", token_id=token)
            qty = float(ba.balance) / 1e6
            if qty > 0:
                out.append(VenuePosition(market_id=market_id, outcome=outcome, contracts=qty))
        return out
