from datetime import timedelta

import pytest

from argona.config import RiskLimits
from argona.domain import BookLevel, FeeModel, MarketStatus, NormalizedMarket, OrderBook, Outcome, Venue, utcnow
from argona.probability.engine import CombinedEstimate
from argona.risk.sizing import kelly_binary, size_contracts
from argona.spread_engine.opportunity import evaluate_entry, evaluate_exit


def est(q, conf=0.8):
    return CombinedEstimate("k", q, 0.02, conf, "high", False, "ok", None)


def mk(fee=FeeModel(), hours=24):
    return NormalizedMarket(Venue.KALSHI, "T", "Q", "economics", MarketStatus.OPEN, utcnow() + timedelta(hours=hours), rules="r", fee_model=fee)


def bk(bids, asks):
    return OrderBook([BookLevel(*x) for x in bids], [BookLevel(*x) for x in asks], utcnow(), "t")


def test_kelly_textbook_yes_case():
    q, p = 0.6, 0.5
    assert kelly_binary(q, p) == pytest.approx((q - p) / (1 - p))
    assert kelly_binary(0.4, 0.5) == 0.0
    assert kelly_binary(0.9, 1.0) == 0.0


def test_kelly_no_contract_derived_from_payoff():
    # NO contract at 0.30 wins with prob 1-q = 0.8 -> f* = (0.8-0.3)/(1-0.3)
    assert kelly_binary(0.8, 0.30) == pytest.approx(0.5 / 0.7)


def test_size_contracts_fraction_confidence_and_cap():
    n, raw, app = size_contracts(equity=10_000, win_prob=0.6, cost_per_contract=0.5, kelly_fraction=0.25, confidence=0.8, cap_usd=1e9)
    assert raw == pytest.approx(0.2) and app == pytest.approx(0.2 * 0.25 * 0.8)
    assert n == pytest.approx(int(10_000 * app / 0.5))
    n2, *_ = size_contracts(equity=10_000, win_prob=0.6, cost_per_contract=0.5, kelly_fraction=0.25, confidence=0.8, cap_usd=50)
    assert n2 == 100
    n3, *_ = size_contracts(equity=10_000, win_prob=0.6, cost_per_contract=0.5, kelly_fraction=0.25, confidence=0.8, cap_usd=1, min_size=5)
    assert n3 == 0


def test_ev_yes_uses_executable_price_and_fees():
    lim = RiskLimits(min_net_edge=0.0, slippage_buffer=0.0, min_liquidity_usd=0, min_confidence=0)
    fee = FeeModel("quadratic", 0.07)
    cands = evaluate_entry(mk(fee), bk([(0.48, 1000)], [(0.50, 1000)]), est(0.60), lim, utcnow())
    yes = next(c for c in cands if c.outcome is Outcome.YES)
    f = fee.per_contract(0.50)
    assert yes.gross_edge == pytest.approx(0.10)
    assert yes.fee_adj_edge == pytest.approx(0.10 - f)
    assert yes.net_ev == pytest.approx(0.60 - 0.50 - f)
    assert yes.est_return == pytest.approx((0.60 - 0.50 - f) / (0.50 + f))
    no = next(c for c in cands if c.outcome is Outcome.NO)
    assert no.win_prob == pytest.approx(0.40) and no.quote.best == pytest.approx(0.52) and not no.qualified


def test_probability_above_price_is_not_enough_when_fees_eat_edge():
    lim = RiskLimits(min_net_edge=0.0, slippage_buffer=0.0, min_liquidity_usd=0, min_confidence=0)
    cands = evaluate_entry(mk(FeeModel("flat", 0.03)), bk([(0.48, 1000)], [(0.50, 1000)]), est(0.52), lim, utcnow())
    yes = next(c for c in cands if c.outcome is Outcome.YES)
    assert yes.gross_edge > 0 and not yes.qualified
    assert any("consumed by fees" in r for r in yes.reasons)


def test_rejects_stale_thin_wide_and_near_resolution():
    lim = RiskLimits()
    stale = OrderBook([BookLevel(0.40, 5)], [BookLevel(0.41, 5)], utcnow() - timedelta(seconds=lim.max_staleness_s + 10), "t")
    c = next(c for c in evaluate_entry(mk(), stale, est(0.9), lim, utcnow()) if c.outcome is Outcome.YES)
    assert not c.qualified and "stale_data" in c.flags and "thin_book" in c.flags
    wide = bk([(0.20, 5000)], [(0.40, 5000)])
    c = next(c for c in evaluate_entry(mk(), wide, est(0.9), lim, utcnow()) if c.outcome is Outcome.YES)
    assert "wide_spread" in c.flags
    c = next(c for c in evaluate_entry(mk(hours=0.1), bk([(0.40, 5000)], [(0.41, 5000)]), est(0.9), lim, utcnow()) if c.outcome is Outcome.YES)
    assert "too_close_to_resolution" in c.flags


def test_abstained_estimate_produces_no_candidates():
    e = CombinedEstimate("k", None, None, 0.0, "none", True, "market only", 0.5)
    assert evaluate_entry(mk(), bk([(0.4, 10)], [(0.41, 10)]), e, RiskLimits(), utcnow()) == []


def test_exit_when_bid_beats_hold_value():
    lim = RiskLimits(min_exit_edge=0.02, slippage_buffer=0.0, min_confidence=0)
    c = evaluate_exit(mk(), bk([(0.70, 100)], [(0.72, 100)]), est(0.60), Outcome.YES, 10, lim, utcnow())
    assert c.qualified and c.net_ev == pytest.approx(0.10)
    c2 = evaluate_exit(mk(), bk([(0.61, 100)], [(0.63, 100)]), est(0.60), Outcome.YES, 10, lim, utcnow())
    assert not c2.qualified
