"""Execution service - the ONLY component holding trading credentials.

Lifecycle per order:
  1. write-ahead: Order row persisted as PENDING_SUBMIT with a deterministic client_order_id
     (unique constraint => the same opportunity can never be submitted twice, even after a crash)
  2. submit to venue (live) or to the paper simulator (paper)
  3. ack -> OPEN/FILLED/REJECTED; timeout/5xx -> UNKNOWN (never retried blindly)
  4. monitor: fills (idempotent by venue fill id), partial fills, TTL expiry/cancel
  5. reconcile: UNKNOWN orders looked up by venue id / client id before anything else happens

Paper simulator: marketable limit orders fill against the *current real (or SIM) order book* up to
the limit price, consuming displayed depth only once per book snapshot; any remainder rests and
fills only if a later book trades through the limit (no optimistic queue-position fills). Fees
use the venue fee model. Paper orders never touch a venue.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..database.models import Account, Market, Order, Position
from ..database.session import Database
from ..domain import Action, NormalizedMarket, OrderStatus, Outcome, TradingMode, as_utc, utcnow
from ..events import EventBus
from ..exchanges.base import AuthError, ProviderError, SubmissionUncertain, TradingAdapter
from ..exchanges.registry import ProviderRegistry
from ..market_data.service import MarketService, row_to_market
from ..portfolio.service import PortfolioService
from ..risk.engine import RiskDecision

log = logging.getLogger("argona.execution")

WORKING = [OrderStatus.PENDING_SUBMIT.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.UNKNOWN.value]
UNKNOWN_GRACE_S = 90


def client_order_id_for(opportunity_id: str) -> str:
    return f"ARG-{opportunity_id}"


class ExecutionService:
    def __init__(self, db: Database, events: EventBus, portfolio: PortfolioService, markets: MarketService,
                 registry: ProviderRegistry) -> None:
        self.db = db
        self.events = events
        self.portfolio = portfolio
        self.markets = markets
        self.registry = registry
        self._trading: dict[str, TradingAdapter] = {}
        self.live_status: dict[str, dict[str, Any]] = {}
        self._consumed: dict[tuple[str, str, str], float] = {}  # (market, book_ts, price) -> qty consumed by paper fills
        self.stats = {"submitted": 0, "filled": 0, "rejected": 0, "canceled": 0, "unknown": 0, "errors": 0}

    # ------------------------------------------------------------ live setup
    def trading_adapter(self, venue: str) -> TradingAdapter:
        if venue not in self._trading:
            self._trading[venue] = self.registry.build_trading_adapter(venue)
        return self._trading[venue]

    def install_trading_adapter(self, venue: str, adapter: TradingAdapter) -> None:
        """Test hook / sandbox injection."""
        self._trading[venue] = adapter

    async def verify_live(self, venue: str) -> dict[str, Any]:
        try:
            info = await self.trading_adapter(venue).verify()
            st = {"verified": True, "at": utcnow().isoformat(), **{k: v for k, v in info.items() if k in ("balance", "closed_only")}}
            if info.get("closed_only"):
                st["verified"] = False
                st["error"] = "account is in closed-only mode (e.g. geoblocked): new orders are not allowed"
        except (AuthError, ProviderError, RuntimeError, OSError, ValueError) as e:
            st = {"verified": False, "at": utcnow().isoformat(), "error": str(e)[:300]}
        self.live_status[venue] = st
        ps = self.registry.status.get(venue)
        if ps:
            ps.trading_verified, ps.trading_error = st["verified"], st.get("error")
        return st

    # --------------------------------------------------------------- submit
    async def submit(self, *, opportunity_id: str, market: NormalizedMarket, outcome: Outcome, action: Action,
                     decision: RiskDecision, account: str, mode: TradingMode, ttl_s: int, risk_assessment_id: int | None,
                     estimate_id: int | None) -> Order | None:
        assert decision.approved and decision.limit_price is not None
        coid = client_order_id_for(opportunity_id)
        now = utcnow()
        is_sim = mode is not TradingMode.LIVE
        async with self.db.tx() as s:
            if (await s.execute(select(Order).where(Order.client_order_id == coid))).scalar_one_or_none():
                log.warning("duplicate submission prevented for %s", coid)
                return None
            o = Order(client_order_id=coid, account=account, mode=mode.value, venue=market.venue.value, market_key=market.key,
                      outcome=outcome.value, action=action.value, order_type="limit", limit_price=decision.limit_price,
                      quantity=decision.contracts, filled_qty=0, fees=0, status=OrderStatus.PENDING_SUBMIT.value,
                      opportunity_id=opportunity_id, risk_assessment_id=risk_assessment_id, estimate_id=estimate_id,
                      reserved=(decision.contracts * decision.limit_price + decision.expected_fee) if action is Action.BUY else 0.0,
                      expires_at=now + timedelta(seconds=ttl_s), is_simulated=is_sim,
                      raw={"vwap_at_decision": decision.vwap, "best_at_decision": self._best(market.key, outcome, action)})
            s.add(o)
            await s.flush()
            oid = o.id
        self.stats["submitted"] += 1
        tag = "PAPER" if is_sim else "LIVE"
        await self.events.emit("order_submitted", f"[{tag}] {action.value} {decision.contracts:.0f} {outcome.value} @ ≤{decision.limit_price:.3f} "
                               f"on {market.question[:90]}", agent="SPREAD", market_key=market.key,
                               data={"order_id": oid, "client_order_id": coid, "limit": decision.limit_price, "qty": decision.contracts,
                                     "simulated": is_sim})
        if is_sim:
            await self._paper_match(oid)
        else:
            await self._live_submit(oid, market)
        async with self.db.session() as s:
            return await s.get(Order, oid)

    def _best(self, key: str, outcome: Outcome, action: Action) -> float | None:
        b = self.markets.books.get(key)
        if not b:
            return None
        return b.best_ask(outcome) if action is Action.BUY else b.best_bid(outcome)

    # ---------------------------------------------------------------- paper
    async def _paper_match(self, order_id: int) -> None:
        async with self.db.tx() as s:
            o = await s.get(Order, order_id)
            if not o or o.status not in WORKING:
                return
            if o.status == OrderStatus.PENDING_SUBMIT.value:
                o.status, o.submitted_at, o.submit_attempts = OrderStatus.OPEN.value, utcnow(), 1
            book = self.markets.books.get(o.market_key)
            mrow = await s.get(Market, o.market_key)
            if book is None or mrow is None:
                return
            market = row_to_market(mrow)
            outcome, action = Outcome(o.outcome), Action(o.action)
            if action is Action.SELL:
                pos = (await s.execute(select(Position).where(Position.account == o.account, Position.market_key == o.market_key,
                                                              Position.outcome == o.outcome, Position.status == "open"))).scalar_one_or_none()
                if not pos or pos.contracts <= 0:
                    o.status, o.error = OrderStatus.REJECTED.value, "no position to sell"
                    return
            levels = book.asks(outcome) if action is Action.BUY else book.bids(outcome)
            remaining = o.quantity - o.filled_qty
            bts = as_utc(book.ts).isoformat()
            best = levels[0].price if levels else None
            fills: list[tuple[float, float]] = []
            for lvl in levels:
                if remaining <= 1e-9:
                    break
                crosses = lvl.price <= o.limit_price + 1e-9 if action is Action.BUY else lvl.price >= o.limit_price - 1e-9
                if not crosses:
                    break
                k = (o.market_key, bts, f"{outcome.value}:{action.value}:{lvl.price:.6f}")
                avail = lvl.size - self._consumed.get(k, 0.0)
                if avail <= 0:
                    continue
                take = min(avail, remaining)
                self._consumed[k] = self._consumed.get(k, 0.0) + take
                fills.append((lvl.price, take))
                remaining -= take
            acct = await s.get(Account, o.account)
            filled_now = 0.0
            rejected = False
            for price, qty in fills:
                fee = market.fee_model.order_fee(price, qty)
                if action is Action.BUY and acct and acct.cash < qty * price + fee - 1e-9:
                    o.status, o.error, o.reserved = OrderStatus.REJECTED.value, "insufficient paper cash", 0.0
                    rejected = True
                    break
                await self.portfolio.apply_fill(s, o, qty, price, fee, f"paper-{uuid.uuid4().hex[:16]}", utcnow(), best_price=best)
                filled_now += qty
            status = o.status
            fill_px = o.avg_fill_price
        if rejected:
            self.stats["rejected"] += 1
            await self.events.emit("order_rejected", f"[PAPER] {o.client_order_id} rejected: insufficient paper cash",
                                   severity="warning", agent="SPREAD", market_key=o.market_key, data={"order_id": order_id})
        if filled_now > 0:
            self.stats["filled"] += 1 if status == OrderStatus.FILLED.value else 0
            et = "order_filled" if status == OrderStatus.FILLED.value else "order_partially_filled"
            await self.events.emit(et, f"[PAPER] {o.action} {filled_now:.0f} {o.outcome} filled @ {fill_px:.3f} (simulated fill)",
                                   agent="SPREAD", market_key=o.market_key,
                                   data={"order_id": order_id, "filled": filled_now, "avg": fill_px, "status": status, "simulated": True})
        if len(self._consumed) > 20000:
            self._consumed.clear()

    # ----------------------------------------------------------------- live
    async def _live_submit(self, order_id: int, market: NormalizedMarket) -> None:
        async with self.db.session() as s:
            o = await s.get(Order, order_id)
        adapter = self.trading_adapter(market.venue.value)
        status, err, ack = OrderStatus.UNKNOWN, None, None
        try:
            ack = await adapter.place_limit_order(market, Outcome(o.outcome), Action(o.action), o.limit_price, o.quantity,
                                                  o.client_order_id, o.expires_at)
            status, err = ack.status, ack.error
        except SubmissionUncertain as e:
            status, err = OrderStatus.UNKNOWN, f"submission uncertain: {e}"
        except (AuthError, ProviderError) as e:
            status, err = OrderStatus.REJECTED, str(e)[:500]
        async with self.db.tx() as s:
            o = await s.get(Order, order_id)
            o.submitted_at, o.submit_attempts = utcnow(), o.submit_attempts + 1
            o.status, o.error = status.value, err
            if ack:
                o.venue_order_id = ack.venue_order_id
                o.raw = {**(o.raw or {}), "ack": ack.raw}
            if status in (OrderStatus.REJECTED,):
                o.reserved = 0.0
        if status is OrderStatus.UNKNOWN:
            self.stats["unknown"] += 1
            await self.events.emit("order_unknown", f"[LIVE] submission outcome uncertain for {o.client_order_id}; reconciling before any retry",
                                   severity="warning", agent="SPREAD", market_key=market.key, data={"order_id": order_id})
        elif status is OrderStatus.REJECTED:
            self.stats["rejected"] += 1
            await self.events.emit("order_rejected", f"[LIVE] order rejected: {err}", severity="warning", agent="SPREAD",
                                   market_key=market.key, data={"order_id": order_id})
        else:
            await self.events.emit("order_acknowledged", f"[LIVE] venue accepted {o.client_order_id} (id {ack.venue_order_id if ack else '?'})",
                                   agent="SPREAD", market_key=market.key, data={"order_id": order_id, "status": status.value})

    # -------------------------------------------------------------- monitor
    async def monitor(self) -> None:
        now = utcnow()
        async with self.db.session() as s:
            orders = (await s.execute(select(Order).where(Order.status.in_(WORKING)))).scalars().all()
        live_venues = set()
        for o in orders:
            if o.is_simulated:
                if o.expires_at and o.expires_at <= now:
                    await self._finish(o.id, OrderStatus.EXPIRED, "TTL elapsed")
                else:
                    await self._paper_match(o.id)
            else:
                live_venues.add(o.venue)
                await self._live_poll(o, now)
        for v in live_venues:
            await self.sync_live_fills(v)

    async def _live_poll(self, o: Order, now: datetime) -> None:
        async with self.db.session() as s:
            mrow = await s.get(Market, o.market_key)
        if not mrow:
            return
        market = row_to_market(mrow)
        try:
            adapter = self.trading_adapter(o.venue)
            ack = await adapter.get_order(market, o.venue_order_id, o.client_order_id)
        except (ProviderError, RuntimeError) as e:
            self.stats["errors"] += 1
            log.warning("order poll failed for %s: %s", o.client_order_id, e)
            return
        if ack is None:
            if o.status == OrderStatus.UNKNOWN.value and o.submitted_at and (now - o.submitted_at).total_seconds() > UNKNOWN_GRACE_S:
                await self._finish(o.id, OrderStatus.REJECTED, "not found at venue after grace period (never reached venue)")
            return
        async with self.db.tx() as s:
            oo = await s.get(Order, o.id)
            if ack.venue_order_id and not oo.venue_order_id:
                oo.venue_order_id = ack.venue_order_id
            if oo.status == OrderStatus.UNKNOWN.value:
                oo.status = OrderStatus.OPEN.value
                await self.events.emit("reconciliation", f"[LIVE] uncertain order {oo.client_order_id} found at venue ({ack.status.value})",
                                       agent="SPREAD", market_key=oo.market_key, persist=False)
            if ack.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED) and oo.status not in (OrderStatus.FILLED.value,):
                oo.status, oo.reserved = ack.status.value, 0.0
        if o.expires_at and o.expires_at <= now and ack.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
            await self.cancel(o.id, "TTL elapsed", final=OrderStatus.EXPIRED)

    async def sync_live_fills(self, venue: str) -> int:
        """Apply venue fills idempotently (venue fill id), matched to local orders by venue order id."""
        try:
            adapter = self.trading_adapter(venue)
        except RuntimeError:
            return 0
        account = f"live:{venue}"
        async with self.db.session() as s:
            acct = await s.get(Account, account)
            since = (acct.last_reconciled_at - timedelta(hours=1)) if acct and acct.last_reconciled_at else utcnow() - timedelta(days=2)
        try:
            fills = await adapter.get_fills(since)
        except ProviderError as e:
            log.warning("fill sync failed: %s", e)
            return 0
        applied = 0
        for f in fills:
            async with self.db.tx() as s:
                o = (await s.execute(select(Order).where(Order.venue_order_id == f.venue_order_id))).scalar_one_or_none()
                if not o:
                    continue
                r = await self.portfolio.apply_fill(s, o, f.qty, f.price, f.fee, f.venue_fill_id, f.ts,
                                                    best_price=(o.raw or {}).get("best_at_decision"))
                if r:
                    applied += 1
                    status = o.status
            if r:
                await self.events.emit("order_filled" if status == OrderStatus.FILLED.value else "order_partially_filled",
                                       f"[LIVE] fill {f.qty:.2f} @ {f.price:.3f} confirmed by {venue}", agent="SPREAD",
                                       market_key=o.market_key, data={"order_id": o.id, "fill_id": f.venue_fill_id, "simulated": False})
        return applied

    # --------------------------------------------------------------- cancel
    async def _finish(self, order_id: int, status: OrderStatus, reason: str) -> None:
        async with self.db.tx() as s:
            o = await s.get(Order, order_id)
            if not o or o.status not in WORKING:
                return
            o.status, o.error, o.reserved, o.updated_at = status.value, reason, 0.0, utcnow()
            key, coid, sim = o.market_key, o.client_order_id, o.is_simulated
        self.stats["canceled" if status is not OrderStatus.REJECTED else "rejected"] += 1
        et = {"EXPIRED": "order_expired", "CANCELED": "order_canceled", "REJECTED": "order_rejected"}[status.value]
        await self.events.emit(et, f"[{'PAPER' if sim else 'LIVE'}] {coid} {status.value.lower()}: {reason}", agent="SPREAD", market_key=key,
                               severity="info" if status is not OrderStatus.REJECTED else "warning", data={"order_id": order_id})

    async def cancel(self, order_id: int, reason: str = "operator", final: OrderStatus = OrderStatus.CANCELED) -> bool:
        async with self.db.session() as s:
            o = await s.get(Order, order_id)
            mrow = await s.get(Market, o.market_key) if o else None
        if not o or o.status not in WORKING:
            return False
        if not o.is_simulated and o.venue_order_id and mrow:
            try:
                await self.trading_adapter(o.venue).cancel_order(row_to_market(mrow), o.venue_order_id)
            except (ProviderError, RuntimeError) as e:
                await self.events.emit("order_rejected", f"[LIVE] cancel failed for {o.client_order_id}: {e}", severity="critical",
                                       agent="SPREAD", market_key=o.market_key)
                return False
        await self._finish(order_id, final, reason)
        return True

    async def cancel_all(self, reason: str) -> int:
        async with self.db.session() as s:
            ids = [i for (i,) in (await s.execute(select(Order.id).where(Order.status.in_(WORKING)))).all()]
        n = 0
        for i in ids:
            n += int(await self.cancel(i, reason))
        return n

    # ------------------------------------------------------------ reconcile
    async def reconcile_live(self, venue: str) -> dict[str, Any]:
        """Compare local live state with the venue (authoritative). Mismatches pause trading upstream."""
        report: dict[str, Any] = {"venue": venue, "ok": True, "issues": []}
        account = f"live:{venue}"
        try:
            adapter = self.trading_adapter(venue)
            if hasattr(adapter, "known_tokens"):
                async with self.db.session() as s:
                    rows = (await s.execute(select(Market).join(Position, Position.market_key == Market.key)
                                            .where(Position.account == account))).scalars().all()
                adapter.known_tokens = {**{r.yes_token_id: (r.venue_market_id, Outcome.YES) for r in rows if r.yes_token_id},
                                        **{r.no_token_id: (r.venue_market_id, Outcome.NO) for r in rows if r.no_token_id}}
            balance = await adapter.get_balance()
            venue_pos = await adapter.get_positions()
            open_orders = await adapter.list_open_orders()
        except (AuthError, ProviderError, RuntimeError) as e:
            report.update(ok=False, issues=[f"reconciliation failed: {e}"])
            return report
        await self.sync_live_fills(venue)
        async with self.db.tx() as s:
            acct = await s.get(Account, account)
            if acct is None:
                today = utcnow().date().isoformat()
                acct = Account(name=account, mode="LIVE", starting_capital=balance, cash=balance, peak_equity=balance,
                               day_start_equity=balance, day_start_date=today)
                s.add(acct)
            elif abs(acct.cash - balance) > 0.01:
                report["issues"].append(f"cash local {acct.cash:.2f} != venue {balance:.2f}; adopting venue balance")
                acct.cash = balance
            acct.last_reconciled_at = utcnow()
            local = (await s.execute(select(Position).where(Position.account == account, Position.status == "open"))).scalars().all()
            loc = {(p.market_key.split(":", 1)[1], p.outcome): p.contracts for p in local}
            ven = {(p.market_id, p.outcome.value): p.contracts for p in venue_pos}
            for k in set(loc) | set(ven):
                if abs(loc.get(k, 0) - ven.get(k, 0)) > 1e-6 and (venue != "polymarket" or k in loc):
                    report["issues"].append(f"position {k}: local {loc.get(k, 0)} vs venue {ven.get(k, 0)}")
            local_open = (await s.execute(select(Order).where(Order.account == account, Order.status.in_(WORKING)))).scalars().all()
            venue_ids = {str(o.get("order_id") or o.get("id")) for o in open_orders}
            unknown_at_venue = venue_ids - {o.venue_order_id for o in local_open if o.venue_order_id}
            if unknown_at_venue:
                report["issues"].append(f"{len(unknown_at_venue)} open venue orders not created by ARGONA")
        report["ok"] = not report["issues"]
        report["balance"] = balance
        return report

    async def recent_entry_markets(self, account: str, within_s: int) -> set[str]:
        if within_s <= 0:
            return set()
        since = utcnow() - timedelta(seconds=within_s)
        async with self.db.session() as s:
            rows = (await s.execute(select(Order.market_key).where(Order.account == account, Order.action == Action.BUY.value,
                                                                   Order.created_at >= since, Order.status != OrderStatus.REJECTED.value))).all()
        return {k for (k,) in rows}

    async def working_orders(self, account: str | None = None) -> list[Order]:
        async with self.db.session() as s:
            q = select(Order).where(Order.status.in_(WORKING))
            if account:
                q = q.where(Order.account == account)
            return list((await s.execute(q)).scalars().all())

    async def close(self) -> None:
        for a in self._trading.values():
            await a.close()

