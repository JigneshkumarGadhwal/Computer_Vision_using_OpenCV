"""Portfolio accounting.

Accounts: ``paper`` (fully simulated, separate cash) and ``live:<venue>`` (cash/positions are
reconciled from the venue, which is authoritative).

Conventions
  * Long-only binary contracts. Cost basis includes entry fees; capital at risk = cost basis.
  * Buy fill:  cash -= qty*price + fee;  position.cost += qty*price + fee
  * Sell fill: cash += qty*price - fee;  realized += proceeds - avg_cost*qty
  * Settlement: cash += contracts*payout; realized += payout - remaining cost
  * Mark-to-market uses the executable best bid of the held outcome (liquidation value), falling
    back to the mid, then to cost if the book is empty. Unrealized = marked value - cost basis.
  * Working buy orders reserve qty*limit + estimated fees; available cash = cash - reserved.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, select

from ..database.models import Account, AccountSnapshot, Fill, Market, Order, Position, Settlement
from ..database.session import Database
from ..domain import Action, OrderStatus, Outcome, utcnow

log = logging.getLogger("argona.portfolio")

MarkFn = Callable[[str, Outcome], float | None]  # (market_key, outcome) -> liquidation price


@dataclass
class PositionView:
    id: int
    market_key: str
    outcome: str
    contracts: float
    cost_basis: float
    avg_price: float
    mark: float | None
    value: float
    unrealized: float
    realized: float
    category: str
    event_id: str | None
    question: str
    venue: str
    close_time: str | None
    is_simulated: bool


@dataclass
class PortfolioState:
    account: str
    mode: str
    starting_capital: float
    cash: float
    reserved: float
    available: float
    positions_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    exposure: float
    peak_equity: float
    drawdown: float
    day_pnl: float
    open_orders: int
    positions: list[PositionView] = field(default_factory=list)
    exposure_by_market: dict[str, float] = field(default_factory=dict)
    exposure_by_event: dict[str, float] = field(default_factory=dict)
    exposure_by_category: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["positions"] = [p.__dict__ for p in self.positions]
        return d


class PortfolioService:
    def __init__(self, db: Database, mark: MarkFn) -> None:
        self.db = db
        self.mark = mark

    async def ensure_account(self, name: str, mode: str, starting_capital: float) -> None:
        async with self.db.tx() as s:
            if await s.get(Account, name):
                return
            today = utcnow().date().isoformat()
            s.add(Account(name=name, mode=mode, starting_capital=starting_capital, cash=starting_capital,
                          peak_equity=starting_capital, day_start_equity=starting_capital, day_start_date=today))

    # ------------------------------------------------------------------ fills
    async def apply_fill(self, s, order: Order, qty: float, price: float, fee: float, venue_fill_id: str,
                         ts: datetime | None = None, best_price: float | None = None) -> Fill | None:
        """Apply one fill inside the caller's transaction. Idempotent on (account, venue_fill_id)."""
        if qty <= 0:
            return None
        dup = (await s.execute(select(Fill).where(Fill.account == order.account, Fill.venue_fill_id == venue_fill_id))).scalar_one_or_none()
        if dup:
            return None
        ts = ts or utcnow()
        acct = await s.get(Account, order.account)
        pos = (await s.execute(select(Position).where(Position.account == order.account, Position.market_key == order.market_key,
                                                      Position.outcome == order.outcome, Position.status == "open"))).scalar_one_or_none()
        slippage = abs(price - best_price) * qty if best_price is not None else 0.0
        f = Fill(order_id=order.id, account=order.account, market_key=order.market_key, outcome=order.outcome, action=order.action,
                 qty=qty, price=price, fee=fee, slippage=slippage, ts=ts, venue_fill_id=venue_fill_id, is_simulated=order.is_simulated)
        s.add(f)
        if order.action == Action.BUY.value:
            if pos is None:
                pos = Position(account=order.account, market_key=order.market_key, outcome=order.outcome, contracts=0, cost_basis=0,
                               realized_pnl=0, fees_paid=0, status="open", opened_at=ts)
                s.add(pos)
            pos.contracts = round(pos.contracts + qty, 6)
            pos.cost_basis = round(pos.cost_basis + qty * price + fee, 6)
            pos.fees_paid = round(pos.fees_paid + fee, 6)
            if acct:
                acct.cash = round(acct.cash - qty * price - fee, 6)
            # release the matching share of the reservation
            if order.quantity > 0:
                order.reserved = max(0.0, round(order.reserved - order.reserved * qty / max(order.quantity - order.filled_qty, qty), 6))
        else:
            if pos is None or pos.contracts + 1e-9 < qty:
                raise ValueError(f"sell fill {qty} exceeds position for {order.market_key} {order.outcome}")
            avg = pos.cost_basis / pos.contracts if pos.contracts else 0.0
            proceeds = qty * price - fee
            pnl = proceeds - avg * qty
            pos.contracts = round(pos.contracts - qty, 6)
            pos.cost_basis = round(pos.cost_basis - avg * qty, 6)
            pos.realized_pnl = round(pos.realized_pnl + pnl, 6)
            pos.fees_paid = round(pos.fees_paid + fee, 6)
            if pos.contracts <= 1e-6:
                pos.contracts, pos.cost_basis, pos.status, pos.closed_at = 0.0, 0.0, "closed", ts
            if acct:
                acct.cash = round(acct.cash + proceeds, 6)
        pos.updated_at = ts
        new_filled = round(order.filled_qty + qty, 6)
        prev_notional = (order.avg_fill_price or 0.0) * order.filled_qty
        order.avg_fill_price = (prev_notional + price * qty) / new_filled
        order.filled_qty = new_filled
        order.fees = round(order.fees + fee, 6)
        order.status = (OrderStatus.FILLED if new_filled + 1e-9 >= order.quantity else OrderStatus.PARTIALLY_FILLED).value
        if order.status == OrderStatus.FILLED.value:
            order.reserved = 0.0
        order.updated_at = ts
        if acct:
            acct.updated_at = ts
        return f

    # ------------------------------------------------------------ settlement
    async def settle_market(self, s, market: Market) -> list[Settlement]:
        """Settle all open positions (all accounts) in a resolved market. Idempotent per position."""
        if market.settlement_value is None and market.result is None:
            return []
        yes_value = market.settlement_value if market.settlement_value is not None else (1.0 if market.result == "YES" else 0.0)
        out = []
        rows = (await s.execute(select(Position).where(Position.market_key == market.key, Position.status == "open"))).scalars().all()
        for pos in rows:
            if pos.contracts <= 0:
                pos.status = "closed"
                continue
            if pos.account.startswith("live:"):
                continue  # live settlement is credited by the venue and picked up by reconciliation
            per = yes_value if pos.outcome == Outcome.YES.value else 1.0 - yes_value
            payout = round(pos.contracts * per, 6)
            pnl = round(payout - pos.cost_basis, 6)
            st = Settlement(account=pos.account, position_id=pos.id, market_key=market.key, outcome=pos.outcome,
                            result=market.result or f"{yes_value:.2f}", contracts=pos.contracts, payout_per_contract=per,
                            payout=payout, cost_basis=pos.cost_basis, realized_pnl=pnl, ts=utcnow())
            s.add(st)
            acct = await s.get(Account, pos.account)
            if acct:
                acct.cash = round(acct.cash + payout, 6)
            pos.realized_pnl = round(pos.realized_pnl + pnl, 6)
            pos.status, pos.closed_at, pos.updated_at = "settled", utcnow(), utcnow()
            pos.cost_basis = 0.0
            out.append(st)
        return out

    # ----------------------------------------------------------------- state
    async def state(self, account: str) -> PortfolioState | None:
        async with self.db.session() as s:
            acct = await s.get(Account, account)
            if not acct:
                return None
            positions = (await s.execute(select(Position, Market).join(Market, Market.key == Position.market_key, isouter=True)
                                         .where(Position.account == account, Position.status == "open"))).all()
            orders = (await s.execute(select(Order).where(Order.account == account, Order.status.in_(
                [OrderStatus.PENDING_SUBMIT.value, OrderStatus.OPEN.value, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.UNKNOWN.value])))).scalars().all()
            realized_closed = (await s.execute(select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(Position.account == account))).scalar_one()
            fees = (await s.execute(select(func.coalesce(func.sum(Fill.fee), 0)).where(Fill.account == account))).scalar_one()
        views: list[PositionView] = []
        by_m: dict[str, float] = {}
        by_e: dict[str, float] = {}
        by_c: dict[str, float] = {}
        pv = unreal = 0.0
        for pos, mk in positions:
            o = Outcome(pos.outcome)
            mark = self.mark(pos.market_key, o)
            value = pos.contracts * mark if mark is not None else pos.cost_basis
            u = value - pos.cost_basis
            pv += value
            unreal += u
            cat = mk.category if mk else "other"
            ev = (mk.event_id if mk and mk.event_id else pos.market_key)
            by_m[pos.market_key] = by_m.get(pos.market_key, 0) + pos.cost_basis
            by_e[ev] = by_e.get(ev, 0) + pos.cost_basis
            by_c[cat] = by_c.get(cat, 0) + pos.cost_basis
            views.append(PositionView(pos.id, pos.market_key, pos.outcome, pos.contracts, round(pos.cost_basis, 6),
                                      round(pos.cost_basis / pos.contracts, 6) if pos.contracts else 0.0, mark, round(value, 6),
                                      round(u, 6), pos.realized_pnl, cat, mk.event_id if mk else None, mk.question if mk else pos.market_key,
                                      mk.venue if mk else "?", mk.close_time.isoformat() if mk and mk.close_time else None,
                                      bool(mk.is_simulated) if mk else False))
        reserved = sum(o.reserved for o in orders if o.action == Action.BUY.value)
        for o in orders:  # pending buys count toward exposure caps
            if o.action == Action.BUY.value:
                by_m[o.market_key] = by_m.get(o.market_key, 0) + o.reserved
        equity = acct.cash + pv
        peak = max(acct.peak_equity, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        exposure = sum(p.cost_basis for p in views) + reserved
        return PortfolioState(
            account=account, mode=acct.mode, starting_capital=acct.starting_capital, cash=round(acct.cash, 6),
            reserved=round(reserved, 6), available=round(acct.cash - reserved, 6), positions_value=round(pv, 6),
            equity=round(equity, 6), realized_pnl=round(float(realized_closed), 6), unrealized_pnl=round(unreal, 6),
            fees_paid=round(float(fees), 6), exposure=round(exposure, 6), peak_equity=round(peak, 6), drawdown=round(dd, 6),
            day_pnl=round(equity - acct.day_start_equity, 6), open_orders=len(orders), positions=views,
            exposure_by_market=by_m, exposure_by_event=by_e, exposure_by_category=by_c,
        )

    async def snapshot(self, account: str) -> PortfolioState | None:
        st = await self.state(account)
        if not st:
            return None
        async with self.db.tx() as s:
            acct = await s.get(Account, account)
            today = utcnow().date().isoformat()
            if acct.day_start_date != today:
                acct.day_start_date, acct.day_start_equity = today, st.equity
            acct.peak_equity = max(acct.peak_equity, st.equity)
            s.add(AccountSnapshot(account=account, cash=st.cash, reserved=st.reserved, positions_value=st.positions_value,
                                  equity=st.equity, realized_pnl=st.realized_pnl, unrealized_pnl=st.unrealized_pnl,
                                  fees_paid=st.fees_paid, exposure=st.exposure, peak_equity=max(acct.peak_equity, st.equity),
                                  drawdown=st.drawdown))
        return st

    async def equity_curve(self, account: str, since: datetime | None = None, max_points: int = 1500) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            q = select(AccountSnapshot).where(AccountSnapshot.account == account)
            if since:
                q = q.where(AccountSnapshot.ts >= since)
            rows = (await s.execute(q.order_by(AccountSnapshot.ts))).scalars().all()
        step = max(1, len(rows) // max_points)
        return [{"ts": r.ts.isoformat(), "equity": r.equity, "cash": r.cash, "realized": r.realized_pnl,
                 "unrealized": r.unrealized_pnl, "drawdown": r.drawdown, "exposure": r.exposure} for r in rows[::step]]

    async def performance(self, account: str, window_days: float | None = None) -> dict[str, Any]:
        """Trade statistics over closed round trips (settled or fully exited positions)."""
        since = utcnow() - timedelta(days=window_days) if window_days else None
        async with self.db.session() as s:
            q = select(Position).where(Position.account == account, Position.status.in_(["closed", "settled"]))
            if since:
                q = q.where(Position.closed_at >= since)
            closed = (await s.execute(q)).scalars().all()
            fq = select(Fill).where(Fill.account == account)
            if since:
                fq = fq.where(Fill.ts >= since)
            fills = (await s.execute(fq)).scalars().all()
            snaps_q = select(AccountSnapshot.equity).where(AccountSnapshot.account == account)
            if since:
                snaps_q = snaps_q.where(AccountSnapshot.ts >= since)
            eq = [r for (r,) in (await s.execute(snaps_q.order_by(AccountSnapshot.ts))).all()]
            # cost of each closed position = sum of its buy fills
            buys: dict[tuple[str, str], float] = {}
            for f in fills:
                if f.action == "BUY":
                    k = (f.market_key, f.outcome)
                    buys[k] = buys.get(k, 0) + f.qty * f.price + f.fee
        pnls = [p.realized_pnl for p in closed]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        rets = []
        for p in closed:
            c = buys.get((p.market_key, p.outcome))
            if c:
                rets.append(p.realized_pnl / c)
        mdd, peak = 0.0, None
        for e in eq:
            peak = e if peak is None or e > peak else peak
            if peak:
                mdd = max(mdd, (peak - e) / peak)
        return {
            "trades": len(pnls), "wins": len(wins), "losses": len(losses),
            "win_rate": (len(wins) / len(pnls)) if pnls else None,
            "gross_profit": round(sum(wins), 6), "gross_loss": round(sum(losses), 6),
            "net_realized": round(sum(pnls), 6),
            # undefined without losing trades (would be +inf, which is not valid JSON) -> null
            "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
            "avg_return_per_trade": (sum(rets) / len(rets)) if rets else None,
            "expectancy_usd": (sum(pnls) / len(pnls)) if pnls else None,
            "fees": round(sum(f.fee for f in fills), 6), "slippage": round(sum(f.slippage for f in fills), 6),
            "max_drawdown": round(mdd, 6), "fills": len(fills),
        }

    async def ledger(self, account: str, limit: int = 500) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            fills = (await s.execute(select(Fill, Market.question).join(Market, Market.key == Fill.market_key, isouter=True)
                                     .where(Fill.account == account).order_by(Fill.ts.desc()).limit(limit))).all()
            sets = (await s.execute(select(Settlement, Market.question).join(Market, Market.key == Settlement.market_key, isouter=True)
                                    .where(Settlement.account == account).order_by(Settlement.ts.desc()).limit(limit))).all()
        rows = [{"ts": f.ts.isoformat(), "type": "fill", "market_key": f.market_key, "question": q, "outcome": f.outcome,
                 "action": f.action, "qty": f.qty, "price": f.price, "fee": f.fee, "slippage": f.slippage,
                 "cash_delta": round(-(f.qty * f.price + f.fee) if f.action == "BUY" else f.qty * f.price - f.fee, 6),
                 "realized_pnl": None, "simulated": f.is_simulated, "order_id": f.order_id} for f, q in fills]
        rows += [{"ts": st.ts.isoformat(), "type": "settlement", "market_key": st.market_key, "question": q, "outcome": st.outcome,
                  "action": f"RESOLVED {st.result}", "qty": st.contracts, "price": st.payout_per_contract, "fee": 0.0, "slippage": 0.0,
                  "cash_delta": st.payout, "realized_pnl": st.realized_pnl, "simulated": st.account == "paper", "order_id": None}
                 for st, q in sets]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return rows[:limit]
