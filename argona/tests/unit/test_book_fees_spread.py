import math
from datetime import timedelta

import pytest

from argona.domain import Action, BookLevel, FeeModel, MarketStatus, NormalizedMarket, OrderBook, Outcome, Venue, utcnow
from argona.spread_engine.engine import cross_venue_arbitrage, quote, spread_stats, structural_arbitrage, walk


def book(bids, asks, age_s=0):
    return OrderBook([BookLevel(p, s) for p, s in bids], [BookLevel(p, s) for p, s in asks], utcnow() - timedelta(seconds=age_s), "t")


def mkt(key="m1", fee=FeeModel(), event=None, close_h=24):
    return NormalizedMarket(Venue.KALSHI, key, f"Q {key}", "economics", MarketStatus.OPEN, utcnow() + timedelta(hours=close_h),
                            rules="r", event_id=event, fee_model=fee)


def test_no_side_is_complement_and_sorted():
    b = book([(0.40, 10), (0.42, 5)], [(0.47, 7), (0.45, 3)])
    assert b.best_bid() == 0.42 and b.best_ask() == 0.45
    assert b.best_ask(Outcome.NO) == pytest.approx(0.58)  # 1 - best YES bid
    assert b.best_bid(Outcome.NO) == pytest.approx(0.55)  # 1 - best YES ask
    assert b.mid == pytest.approx(0.435) and b.spread == pytest.approx(0.03)


def test_invalid_levels_dropped():
    b = book([(0.0, 10), (0.3, 0), (0.31, 5)], [(1.0, 5), (0.5, 2)])
    assert [l.price for l in b.yes_bids] == [0.31] and [l.price for l in b.yes_asks] == [0.5]


def test_kalshi_quadratic_fee_rounds_up_to_cent():
    f = FeeModel("quadratic", 0.07, round_up_cents=True)
    assert f.per_contract(0.5) == pytest.approx(0.0175)
    assert f.order_fee(0.5, 100) == pytest.approx(1.75)
    assert f.order_fee(0.5, 1) == pytest.approx(0.02)  # 0.0175 -> rounded up
    assert f.order_fee(0.01, 1) == pytest.approx(0.01)


def test_polymarket_fee_schedule():
    f = FeeModel("polymarket", 0.02, exponent=1)
    assert f.per_contract(0.4) == pytest.approx(0.02 * 0.24)
    assert FeeModel().per_contract(0.4) == 0.0


def test_walk_vwap_and_limit():
    levels = [BookLevel(0.50, 10), BookLevel(0.52, 10), BookLevel(0.60, 100)]
    filled, vwap, worst = walk(levels, 15)
    assert filled == 15 and vwap == pytest.approx((10 * 0.5 + 5 * 0.52) / 15) and worst == 0.52
    filled, vwap, worst = walk(levels, 50, limit=0.52)
    assert filled == 20 and worst == 0.52


def test_quote_buy_no_uses_yes_bids_and_fees_per_level():
    fee = FeeModel("quadratic", 0.07)
    b = book([(0.40, 5), (0.39, 100)], [(0.45, 50)])
    q = quote(b, Outcome.NO, Action.BUY, 10, fee)
    assert q.best == pytest.approx(0.60) and q.fillable == 10
    assert q.vwap == pytest.approx((5 * 0.60 + 5 * 0.61) / 10)
    exp_fee = (fee.per_contract(0.60) * 5 + fee.per_contract(0.61) * 5) / 10
    assert q.fee_per_contract == pytest.approx(exp_fee)
    assert q.total_cost == pytest.approx(10 * q.vwap + exp_fee * 10)


def test_partial_depth_flagged():
    q = quote(book([(0.4, 1)], [(0.5, 3)]), Outcome.YES, Action.BUY, 10, FeeModel())
    assert q.fillable == 3 and not q.complete


def test_spread_stats_flags():
    assert "wide_spread" in spread_stats(book([(0.3, 10)], [(0.45, 10)])).flags
    assert "one_sided" in spread_stats(book([], [(0.45, 10)])).flags
    assert "tight_spread" in spread_stats(book([(0.44, 10)], [(0.45, 10)])).flags


def test_structural_arb_never_risk_free_and_needs_positive_net():
    a, b = mkt("a", event="E"), mkt("b", event="E")
    # asks sum 0.45 + 0.45 = 0.90 < 1 -> buy-all-YES candidate
    res = structural_arbitrage([(a, book([(0.4, 50)], [(0.45, 50)])), (b, book([(0.4, 50)], [(0.45, 50)]))], exhaustive=True)
    yes = [r for r in res if r.kind == "structural_buy_all_yes"]
    assert yes and yes[0].net_profit_per_set == pytest.approx(0.10) and not yes[0].is_risk_free
    # fees can kill it
    f = FeeModel("flat", 0.06)
    res = structural_arbitrage([(mkt("a", f, "E"), book([(0.4, 50)], [(0.45, 50)])), (mkt("b", f, "E"), book([(0.4, 50)], [(0.45, 50)]))], exhaustive=True)
    assert not [r for r in res if r.kind == "structural_buy_all_yes"]


def test_cross_venue_flags_unconfirmed():
    a = (mkt("a"), book([(0.40, 50)], [(0.42, 50)]))
    b = (mkt("b"), book([(0.50, 50)], [(0.52, 50)]))
    res = cross_venue_arbitrage(a, b, confirmed_equivalent=False)
    assert res and res[0].net_profit_per_set == pytest.approx(1 - 0.42 - 0.50)
    assert "resolution_equivalence_unconfirmed" in res[0].risk_flags and not res[0].is_risk_free
