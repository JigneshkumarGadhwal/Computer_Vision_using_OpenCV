"""Execution lifecycle, idempotency, partial fills, uncertain submissions, accounting, settlement."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from argona.database.models import Account, Fill, Market, Order, Position
from argona.domain import (Action, BookLevel, FeeModel, MarketStatus, NormalizedMarket, OrderBook, OrderStatus, Outcome,
                           TradingMode, Venue, VenueFill, VenueOrderAck, utcnow)
from argona.exchanges.base import SubmissionUncertain, TradingAdapter
from argona.risk.engine import RiskDecision


def market(key="T1", venue=Venue.KALSHI):
    return NormalizedMarket(venue, key, "Test market", "economics", MarketStatus.OPEN, utcnow() + timedelta(days=1), rules="r",
                            fee_model=FeeModel("quadratic", 0.07, round_up_cents=True))


async def seed(core, m, book):
    await core.markets.upsert(m.venue.value, [m], core.state.runtime, full_listing=False)
    await core.markets.record_books({m.key: book})


def decision(qty, limit, fee=0.0):
    return RiskDecision(approved=True, contracts=qty, limit_price=limit, vwap=limit, stake_usd=qty * limit, expected_fee=fee)


async def test_paper_full_and_partial_fill_and_accounting(core):
    m = market()
    await seed(core, m, OrderBook([BookLevel(0.48, 100)], [BookLevel(0.50, 30), BookLevel(0.52, 1000)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="opp1", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(50, 0.50),
                                    account="paper", mode=TradingMode.PAPER, ttl_s=300, risk_assessment_id=None, estimate_id=None)
    assert o.status == OrderStatus.PARTIALLY_FILLED.value and o.filled_qty == 30  # only 30 available at <= limit
    st = await core.portfolio.state("paper")
    fee = FeeModel("quadratic", 0.07, round_up_cents=True).order_fee(0.50, 30)
    assert st.cash == pytest.approx(10_000 - 30 * 0.50 - fee)
    assert st.positions[0].contracts == 30 and st.positions[0].cost_basis == pytest.approx(15 + fee)
    assert st.reserved == pytest.approx(o.reserved) and st.reserved > 0
    # same snapshot cannot be consumed twice
    await core.execution.monitor()
    async with core.db.session() as s:
        assert (await s.get(Order, o.id)).filled_qty == 30
    # a new book trading through the limit fills the remainder
    await core.markets.record_books({m.key: OrderBook([BookLevel(0.48, 100)], [BookLevel(0.49, 100)], utcnow(), "t")})
    await core.execution.monitor()
    async with core.db.session() as s:
        oo = await s.get(Order, o.id)
    assert oo.status == OrderStatus.FILLED.value and oo.filled_qty == 50 and oo.reserved == 0


async def test_idempotent_submission(core):
    m = market()
    await seed(core, m, OrderBook([BookLevel(0.48, 100)], [BookLevel(0.50, 100)], utcnow(), "t"))
    args = dict(opportunity_id="dup", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(5, 0.5), account="paper",
                mode=TradingMode.PAPER, ttl_s=60, risk_assessment_id=None, estimate_id=None)
    assert await core.execution.submit(**args) is not None
    assert await core.execution.submit(**args) is None
    async with core.db.session() as s:
        assert len((await s.execute(select(Order))).scalars().all()) == 1


async def test_sell_and_settlement_realized_pnl(core):
    m = market()
    await seed(core, m, OrderBook([BookLevel(0.60, 100)], [BookLevel(0.40, 100)], utcnow(), "t"))
    await core.execution.submit(opportunity_id="b", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(10, 0.40),
                                account="paper", mode=TradingMode.PAPER, ttl_s=60, risk_assessment_id=None, estimate_id=None)
    await core.markets.record_books({m.key: OrderBook([BookLevel(0.60, 100)], [BookLevel(0.62, 100)], utcnow(), "t")})
    await core.execution.submit(opportunity_id="s", market=m, outcome=Outcome.YES, action=Action.SELL, decision=decision(4, 0.60),
                                account="paper", mode=TradingMode.PAPER, ttl_s=60, risk_assessment_id=None, estimate_id=None)
    f = FeeModel("quadratic", 0.07, round_up_cents=True)
    buy_fee, sell_fee = f.order_fee(0.40, 10), f.order_fee(0.60, 4)
    st = await core.portfolio.state("paper")
    pos = st.positions[0]
    assert pos.contracts == 6
    avg = (4.0 + buy_fee) / 10
    assert pos.realized == pytest.approx(4 * 0.60 - sell_fee - avg * 4)
    # resolve YES -> 6 contracts pay $1
    m.status, m.result, m.settlement_value = MarketStatus.RESOLVED, Outcome.YES, 1.0
    await core.markets.upsert("kalshi", [m], core.state.runtime, full_listing=False)
    await core.settle(m.key)
    st2 = await core.portfolio.state("paper")
    assert not st2.positions
    assert st2.cash == pytest.approx(10_000 - 4.0 - buy_fee + 4 * 0.60 - sell_fee + 6.0)
    assert st2.realized_pnl == pytest.approx(st2.cash - 10_000)
    await core.settle(m.key)  # idempotent
    assert (await core.portfolio.state("paper")).cash == pytest.approx(st2.cash)
    types = [e["type"] for e in core.notifier.events]
    assert "market_resolved" in types and "pnl_realized" in types


async def test_no_settlement_pays_complement(core):
    m = market("T2")
    await seed(core, m, OrderBook([BookLevel(0.70, 100)], [BookLevel(0.72, 100)], utcnow(), "t"))
    await core.execution.submit(opportunity_id="n", market=m, outcome=Outcome.NO, action=Action.BUY, decision=decision(10, 0.30),
                                account="paper", mode=TradingMode.PAPER, ttl_s=60, risk_assessment_id=None, estimate_id=None)
    m.status, m.result, m.settlement_value = MarketStatus.RESOLVED, Outcome.NO, 0.0
    await core.markets.upsert("kalshi", [m], core.state.runtime, full_listing=False)
    await core.settle(m.key)
    perf = await core.portfolio.performance("paper")
    assert perf["trades"] == 1 and perf["wins"] == 1


async def test_insufficient_paper_cash_rejected(core):
    m = market("T3")
    await seed(core, m, OrderBook([BookLevel(0.48, 1e6)], [BookLevel(0.50, 1e6)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="big", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(100_000, 0.5),
                                    account="paper", mode=TradingMode.PAPER, ttl_s=60, risk_assessment_id=None, estimate_id=None)
    assert o.status == OrderStatus.REJECTED.value and "insufficient" in o.error


async def test_ttl_expiry(core, clock):
    m = market("T4")
    await seed(core, m, OrderBook([BookLevel(0.40, 10)], [BookLevel(0.60, 10)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="ttl", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(5, 0.45),
                                    account="paper", mode=TradingMode.PAPER, ttl_s=10, risk_assessment_id=None, estimate_id=None)
    assert o.status == OrderStatus.OPEN.value
    async with core.db.tx() as s:
        (await s.get(Order, o.id)).expires_at = utcnow() - timedelta(seconds=1)
    await core.execution.monitor()
    async with core.db.session() as s:
        assert (await s.get(Order, o.id)).status == OrderStatus.EXPIRED.value


class FakeVenue(TradingAdapter):
    venue = "kalshi"

    def __init__(self, mode="timeout_then_found"):
        self.mode, self.placed, self.orders, self.fills = mode, 0, {}, []

    async def verify(self):
        return {"balance": 500.0}

    async def place_limit_order(self, market, outcome, action, price, qty, client_order_id, expires_at):
        self.placed += 1
        if self.mode.startswith("timeout"):
            if self.mode == "timeout_then_found":
                self.orders[client_order_id] = VenueOrderAck("v1", OrderStatus.OPEN)
            raise SubmissionUncertain("timeout")
        if self.mode == "reject":
            return VenueOrderAck(None, OrderStatus.REJECTED, error="insufficient balance")
        self.orders[client_order_id] = VenueOrderAck("v2", OrderStatus.OPEN)
        return self.orders[client_order_id]

    async def cancel_order(self, market, venue_order_id):
        return VenueOrderAck(venue_order_id, OrderStatus.CANCELED)

    async def get_order(self, market, venue_order_id, client_order_id):
        return self.orders.get(client_order_id)

    async def list_open_orders(self):
        return [{"order_id": a.venue_order_id} for a in self.orders.values()]

    async def get_fills(self, since):
        return self.fills

    async def get_balance(self):
        return 500.0

    async def get_positions(self):
        return []


async def test_live_uncertain_submission_reconciled_never_resubmitted(core):
    fake = FakeVenue("timeout_then_found")
    core.execution.install_trading_adapter("kalshi", fake)
    m = market("L1")
    await seed(core, m, OrderBook([BookLevel(0.48, 100)], [BookLevel(0.50, 100)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="live1", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(10, 0.5),
                                    account="live:kalshi", mode=TradingMode.LIVE, ttl_s=300, risk_assessment_id=None, estimate_id=None)
    assert o.status == OrderStatus.UNKNOWN.value
    await core.portfolio.ensure_account("live:kalshi", "LIVE", 500)
    await core.execution.monitor()
    async with core.db.session() as s:
        oo = await s.get(Order, o.id)
    assert oo.status == OrderStatus.OPEN.value and oo.venue_order_id == "v1" and fake.placed == 1
    # venue fill applied once, idempotently
    fake.fills = [VenueFill("f1", "v1", None, "L1", Outcome.YES, Action.BUY, 10, 0.5, 0.18, utcnow())]
    await core.execution.monitor()
    await core.execution.monitor()
    async with core.db.session() as s:
        assert len((await s.execute(select(Fill).where(Fill.account == "live:kalshi"))).scalars().all()) == 1
        assert (await s.get(Order, o.id)).status == OrderStatus.FILLED.value


async def test_live_unknown_not_found_becomes_rejected_after_grace(core):
    fake = FakeVenue("timeout_lost")
    core.execution.install_trading_adapter("kalshi", fake)
    m = market("L2")
    await seed(core, m, OrderBook([BookLevel(0.48, 100)], [BookLevel(0.50, 100)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="live2", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(10, 0.5),
                                    account="live:kalshi", mode=TradingMode.LIVE, ttl_s=300, risk_assessment_id=None, estimate_id=None)
    async with core.db.tx() as s:
        (await s.get(Order, o.id)).submitted_at = utcnow() - timedelta(seconds=200)
    await core.execution.monitor()
    async with core.db.session() as s:
        assert (await s.get(Order, o.id)).status == OrderStatus.REJECTED.value
    assert fake.placed == 1


async def test_live_rejection_recorded(core):
    core.execution.install_trading_adapter("kalshi", FakeVenue("reject"))
    m = market("L3")
    await seed(core, m, OrderBook([BookLevel(0.48, 100)], [BookLevel(0.50, 100)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="live3", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(10, 0.5),
                                    account="live:kalshi", mode=TradingMode.LIVE, ttl_s=300, risk_assessment_id=None, estimate_id=None)
    assert o.status == OrderStatus.REJECTED.value and o.reserved == 0


async def test_paper_and_live_accounts_are_separate(core):
    await core.portfolio.ensure_account("live:kalshi", "LIVE", 500)
    p = await core.portfolio.state("paper")
    l = await core.portfolio.state("live:kalshi")
    assert p.cash == 10_000 and l.cash == 500


async def test_emergency_stop_cancels_and_blocks(core):
    m = market("E1")
    await seed(core, m, OrderBook([BookLevel(0.40, 10)], [BookLevel(0.60, 10)], utcnow(), "t"))
    o = await core.execution.submit(opportunity_id="e", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(5, 0.45),
                                    account="paper", mode=TradingMode.PAPER, ttl_s=300, risk_assessment_id=None, estimate_id=None)
    res = await core.emergency_stop("test")
    assert res["canceled_orders"] == 1
    assert core.state.can_open_new_positions[0] is False
    from argona.core import ControlError

    with pytest.raises(ControlError):
        await core.set_mode(TradingMode.PAPER)
    await core.emergency_reset()
    assert core.state.mode is TradingMode.STOPPED


async def test_performance_is_json_safe_with_only_winners(core):
    import json

    m = market("W1")
    await seed(core, m, OrderBook([BookLevel(0.70, 100)], [BookLevel(0.40, 100)], utcnow(), "t"))
    await core.execution.submit(opportunity_id="w", market=m, outcome=Outcome.YES, action=Action.BUY, decision=decision(10, 0.40),
                                account="paper", mode=TradingMode.PAPER, ttl_s=60, risk_assessment_id=None, estimate_id=None)
    m.status, m.result, m.settlement_value = MarketStatus.RESOLVED, Outcome.YES, 1.0
    await core.markets.upsert("kalshi", [m], core.state.runtime, full_listing=False)
    await core.settle(m.key)
    perf = await core.portfolio.performance("paper")
    assert perf["wins"] == 1 and perf["profit_factor"] is None
    json.dumps(perf, allow_nan=False)
