from datetime import timedelta

import pytest

from argona.config import RiskLimits
from argona.domain import BookLevel, FeeModel, MarketStatus, NormalizedMarket, OrderBook, Outcome, TradingMode, Venue, utcnow
from argona.portfolio.service import PortfolioState
from argona.probability.engine import CombinedEstimate
from argona.risk.engine import RiskContext, RiskEngine
from argona.spread_engine.opportunity import evaluate_entry

LIM = RiskLimits(min_net_edge=0.02, min_confidence=0.1, min_liquidity_usd=0, reentry_cooldown_s=600)


def mk(key="T", event="E", cat="economics"):
    return NormalizedMarket(Venue.KALSHI, key, "Q", cat, MarketStatus.OPEN, utcnow() + timedelta(days=1), rules="r", event_id=event,
                            fee_model=FeeModel("quadratic", 0.07, round_up_cents=True))


def bk(age=0):
    return OrderBook([BookLevel(0.48, 5000)], [BookLevel(0.50, 5000), BookLevel(0.51, 5000)], utcnow() - timedelta(seconds=age), "t")


def pf(equity=10_000.0, **kw):
    base = dict(account="paper", mode="PAPER", starting_capital=10_000, cash=equity, reserved=0, available=equity, positions_value=0,
                equity=equity, realized_pnl=0, unrealized_pnl=0, fees_paid=0, exposure=0, peak_equity=equity, drawdown=0, day_pnl=0,
                open_orders=0)
    base.update(kw)
    return PortfolioState(**base)


def ctx(**kw):
    base = dict(mode=TradingMode.PAPER, trading_allowed=True, trading_block_reason="ok", working_order_markets=set(), held={},
                venue_tradable=True)
    base.update(kw)
    return RiskContext(**base)


def cand(m=None, q=0.62, b=None):
    m = m or mk()
    e = CombinedEstimate(m.key, q, 0.02, 0.9, "high", False, "ok", 0.49)
    return next(c for c in evaluate_entry(m, b or bk(), e, LIM, utcnow()) if c.outcome is Outcome.YES)


def test_approves_and_sizes_within_caps():
    d = RiskEngine(LIM).assess(cand(), bk(), pf(), ctx())
    assert d.approved and d.contracts > 0
    assert d.stake_usd <= LIM.max_position_usd + 1e-6
    assert d.limit_price >= 0.50 and d.kelly_raw > d.kelly_applied > 0
    assert all(c["passed"] for c in d.checks)


@pytest.mark.parametrize("over,needle", [
    (dict(day_pnl=-301), "daily_loss_limit"),
    (dict(drawdown=0.2), "drawdown_limit"),
    (dict(open_orders=20), "max_open_orders"),
])
def test_hard_limits_veto(over, needle):
    d = RiskEngine(LIM).assess(cand(), bk(), pf(**over), ctx())
    assert not d.approved and any(needle in r for r in d.reasons)


def test_blocks_when_stopped_paused_or_emergency():
    d = RiskEngine(LIM).assess(cand(), bk(), pf(), ctx(trading_allowed=False, trading_block_reason="emergency stop active"))
    assert not d.approved and "trading_allowed" in d.reasons[0]


def test_exposure_caps_bind():
    m = mk()
    d = RiskEngine(LIM).assess(cand(m), bk(), pf(exposure_by_market={m.key: 499.0}), ctx())
    assert not d.approved or d.stake_usd <= 1.0 + 1e-6
    d2 = RiskEngine(LIM).assess(cand(m), bk(), pf(exposure=6000.0), ctx())
    assert not d2.approved and any("exposure_headroom" in r for r in d2.reasons)
    d3 = RiskEngine(LIM).assess(cand(m), bk(), pf(exposure_by_event={"E": 800.0}), ctx())
    assert not d3.approved


def test_duplicate_opposite_cooldown_venue():
    m = mk()
    assert not RiskEngine(LIM).assess(cand(m), bk(), pf(), ctx(working_order_markets={m.key})).approved
    assert not RiskEngine(LIM).assess(cand(m), bk(), pf(), ctx(held={(m.key, "NO"): 10})).approved
    assert not RiskEngine(LIM).assess(cand(m), bk(), pf(), ctx(recent_entries={m.key})).approved
    assert not RiskEngine(LIM).assess(cand(m), bk(), pf(), ctx(venue_tradable=False, venue_reason="x")).approved


def test_stale_book_vetoed():
    d = RiskEngine(LIM).assess(cand(), bk(age=LIM.max_staleness_s + 5), pf(), ctx())
    assert not d.approved


def test_insufficient_cash():
    d = RiskEngine(LIM).assess(cand(), bk(), pf(available=0.5), ctx())
    assert not d.approved


def test_no_simulated_markets_in_live():
    m = mk()
    m.is_simulated = True
    d = RiskEngine(LIM).assess(cand(m), bk(), pf(), ctx(mode=TradingMode.LIVE))
    assert not d.approved and any("no_simulated_markets_live" in r for r in d.reasons)


def test_size_shrinks_until_edge_survives_depth():
    thin = OrderBook([BookLevel(0.48, 100)], [BookLevel(0.50, 20), BookLevel(0.58, 5000)], utcnow(), "t")
    c = cand(q=0.60, b=thin)
    d = RiskEngine(LIM).assess(c, thin, pf(), ctx())
    if d.approved:
        assert d.limit_price <= 0.51 and d.contracts <= 20
