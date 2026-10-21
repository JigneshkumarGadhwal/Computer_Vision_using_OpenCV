import json
from pathlib import Path

import pytest

from zigggy.domain import MarketStatus, Outcome
from zigggy.exchanges.kalshi import normalize_market as kn, parse_orderbook
from zigggy.exchanges.polymarket import normalize_market as pn, parse_book
from zigggy.market_data.classify import classify

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_kalshi_event_markets_normalized_and_mve_skipped():
    ev = json.loads((FIX / "kalshi_events.json").read_text())["events"][0]
    ms = [kn(m, ev) for m in ev["markets"]]
    assert ms[2] is None  # multivariate combo excluded
    m = ms[0]
    assert m.key == "kalshi:KXHIGHNY-26OCT22-B64.5" and m.category == "weather" and m.status is MarketStatus.OPEN
    assert m.best_bid == pytest.approx(0.24) and m.best_ask == pytest.approx(0.27)
    assert m.volume == pytest.approx(15234) and m.tick_size == pytest.approx(0.01)
    assert m.fee_model.kind == "quadratic" and m.fee_model.round_up_cents
    assert m.strike == {"strike_type": "between", "floor_strike": 64, "cap_strike": 65}
    assert m.mutually_exclusive and "Central Park" in m.rules and "National Weather Service" in m.resolution_source


def test_kalshi_resolution_and_legacy_cents():
    m = kn({"ticker": "A", "event_ticker": "E", "status": "finalized", "result": "no", "yes_bid": 3, "yes_ask": 5,
            "close_time": "2020-01-01T00:00:00Z", "rules_primary": "r"})
    assert m.status is MarketStatus.RESOLVED and m.result is Outcome.NO and m.settlement_value == 0.0
    assert m.best_bid == pytest.approx(0.03)


def test_kalshi_orderbook_fp_and_legacy():
    b = parse_orderbook({"orderbook_fp": {"yes_dollars": [["0.2300", "100.00"], ["0.2400", "50.00"]], "no_dollars": [["0.7300", "80.00"]]}})
    assert b.best_bid() == pytest.approx(0.24) and b.best_ask() == pytest.approx(0.27)
    assert b.yes_asks[0].size == 80
    legacy = parse_orderbook({"orderbook": {"yes": [[24, 10]], "no": [[73, 5]]}})
    assert legacy.best_bid() == pytest.approx(0.24) and legacy.best_ask() == pytest.approx(0.27)


def test_polymarket_markets_normalized():
    rows = json.loads((FIX / "polymarket_markets.json").read_text())
    a, b, c, d = [pn(r) for r in rows]
    assert d is None  # non-binary
    assert a.key == "polymarket:501" and a.yes_token_id == "111" and a.no_token_id == "222"
    assert a.category == "crypto" and a.outcome_label == "120,000" and a.min_order_size == 5
    assert a.fee_model.kind == "none" and a.event_id == "9001"
    assert b.fee_model.kind == "polymarket" and b.fee_model.rate == pytest.approx(0.02)
    assert c.status is MarketStatus.RESOLVED and c.result is Outcome.YES


def test_polymarket_book_sorted():
    bk = parse_book({"bids": [{"price": "0.30", "size": "10"}, {"price": "0.33", "size": "5"}],
                     "asks": [{"price": "0.40", "size": "7"}, {"price": "0.35", "size": "2"}], "timestamp": "1761000000000"})
    assert bk.best_bid() == 0.33 and bk.best_ask() == 0.35 and bk.ts.year == 2025


@pytest.mark.parametrize("text,cat", [("Will Bitcoin hit $150k?", "crypto"), ("Highest temperature in NYC", "weather"),
                                      ("Fed rate cut in December?", "economics"), ("WTI crude above $90?", "commodities"),
                                      ("Who will win the presidential election?", "politics"), ("Random thing", "other")])
def test_classifier(text, cat):
    assert classify(text) == cat
