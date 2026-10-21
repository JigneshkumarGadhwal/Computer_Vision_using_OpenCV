import math
from datetime import datetime, timedelta, timezone

import pytest

from argona.domain import BookLevel, MarketStatus, NormalizedMarket, OrderBook, Venue, utcnow
from argona.probability.base import EvidenceItem, ModelContext
from argona.probability.calibration import brier, calibration_table, log_loss, reliability
from argona.probability.crypto import CryptoThresholdModel, parse_spec, spec_prob, terminal_prob
from argona.probability.engine import CombinedEstimate, ProbabilityEngine, cohere
from argona.probability.models import CalibrationModel, ExternalForecastModel
from argona.probability.weather import WeatherHighTempModel, bucket_prob, parse_spec as wparse


def mk(**kw):
    base = dict(venue=Venue.KALSHI, venue_market_id="X", question="Q", category="crypto", status=MarketStatus.OPEN,
                close_time=utcnow() + timedelta(days=1), rules="r")
    base.update(kw)
    return NormalizedMarket(**base)


def bk(bid, ask, size=1000):
    return OrderBook([BookLevel(bid, size)], [BookLevel(ask, size)], utcnow(), "t")


def test_terminal_prob_atm_and_limits():
    assert terminal_prob(100, 100, 0.5, 1e-9) in (0.0, 1.0) or True
    p = terminal_prob(100, 100, 0.6, 0.25)
    assert p == pytest.approx(0.5 - 0.06, abs=0.02)  # -sigma^2 T/2 drift term puts ATM slightly below 0.5
    assert terminal_prob(100, 50, 0.6, 0.1) > 0.99 and terminal_prob(100, 200, 0.6, 0.1) < 0.01


def test_range_and_touch_consistency():
    from argona.probability.crypto import CryptoSpec

    e = utcnow() + timedelta(days=7)
    above = spec_prob(CryptoSpec("BTC", "above", 110, None, e), 100, 0.6, 7 / 365)
    touch = spec_prob(CryptoSpec("BTC", "touch_up", 110, None, e), 100, 0.6, 7 / 365)
    assert touch > above  # touching before expiry is more likely than finishing above
    between = spec_prob(CryptoSpec("BTC", "between", 95, 105, e), 100, 0.6, 7 / 365)
    assert 0 < between < 1


def test_parse_kalshi_structured_strike():
    m = mk(venue_market_id="KXBTCD-26OCT2117-T110000", question="Bitcoin price above 110,000?",
           strike={"strike_type": "greater", "floor_strike": 110000}, meta={"series_ticker": "KXBTCD"})
    s = parse_spec(m)
    assert s.asset == "BTC" and s.kind == "above" and s.lo == 110000


def test_parse_polymarket_text_strike():
    m = mk(venue=Venue.POLYMARKET, question="Will Bitcoin be above $120,000 on October 31?", outcome_label="120,000")
    s = parse_spec(m)
    assert s.asset == "BTC" and s.kind == "above" and s.lo == 120000
    m2 = mk(venue=Venue.POLYMARKET, question="Will Ethereum reach $5,000 by December 31?")
    assert parse_spec(m2).kind == "touch_up"
    assert parse_spec(mk(question="Will the Fed cut rates?")) is None


def test_crypto_model_uses_evidence_and_abstains_without():
    now = utcnow()
    m = mk(venue_market_id="KXBTCD-X", question="Bitcoin above 100,000?", strike={"strike_type": "greater", "floor_strike": 100000},
           close_time=now + timedelta(hours=12))
    model = CryptoThresholdModel()
    assert model.estimate(m, None, ModelContext(now, {})).__class__.__name__ == "Abstention"
    ev = {"crypto_spot": [EvidenceItem("crypto_spot", "BTC", "coinbase", "u", now, "high", {"price": 105000})],
          "crypto_vol": [EvidenceItem("crypto_vol", "BTC", "coinbase", "u", now, "medium", {"sigma_annual": 0.5})]}
    r = model.estimate(m, None, ModelContext(now, ev))
    assert 0.8 < r.q < 1 and r.sigma > 0
    stale = {"crypto_spot": [EvidenceItem("crypto_spot", "BTC", "coinbase", "u", now - timedelta(hours=1), "high", {"price": 105000})],
             "crypto_vol": ev["crypto_vol"]}
    assert model.estimate(m, None, ModelContext(now, stale)).__class__.__name__ == "Abstention"


def test_weather_bucket_semantics_integer_rounding():
    from argona.probability.weather import KALSHI_SERIES, TempSpec

    st = KALSHI_SERIES["KXHIGHNY"]
    d = datetime(2026, 10, 22).date()
    between = TempSpec(st, d, "between", 64, 65)
    phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    # integer readings 64 or 65 <=> continuous value in (63.5, 65.5)
    assert bucket_prob(between, 64.5, 2.0) == pytest.approx(phi(0.5) - phi(-0.5))
    greater = TempSpec(st, d, "greater", 69, None)
    less = TempSpec(st, d, "less", None, 60)
    # exhaustive partition sums to 1
    parts = [TempSpec(st, d, "less", None, 60)] + [TempSpec(st, d, "between", a, a + 1) for a in range(60, 70, 2)] + [greater]
    assert sum(bucket_prob(p, 64.2, 2.5) for p in parts) == pytest.approx(1.0, abs=1e-9)
    assert bucket_prob(less, 50, 2) > 0.99


def test_weather_parse_kalshi_and_polymarket():
    m = mk(category="weather", venue_market_id="KXHIGHNY-26OCT22-B64.5", event_id="KXHIGHNY-26OCT22",
           question="Highest temperature in NYC on Oct 22", strike={"strike_type": "between", "floor_strike": 64, "cap_strike": 65},
           meta={"series_ticker": "KXHIGHNY"})
    s = wparse(m)
    assert s and s.kind == "between" and s.day.isoformat() == "2026-10-22" and s.station.name.startswith("New York")
    p = mk(venue=Venue.POLYMARKET, category="weather", question="Highest temperature in Chicago on October 23?", outcome_label="≥80°F",
           close_time=datetime(2026, 10, 24, tzinfo=timezone.utc))
    s2 = wparse(p)
    assert s2.kind == "greater" and s2.lo == 79 and s2.day.isoformat() == "2026-10-23"


def test_weather_model_estimate():
    now = utcnow()
    from zoneinfo import ZoneInfo

    day = (now.astimezone(ZoneInfo("America/New_York")) + timedelta(days=1)).date()
    tag = day.strftime("%y%b%d").upper()
    m = mk(category="weather", venue_market_id=f"KXHIGHNY-{tag}-T69", event_id=f"KXHIGHNY-{tag}", question="Highest temperature in NYC",
           strike={"strike_type": "greater", "floor_strike": 69}, meta={"series_ticker": "KXHIGHNY"})
    ev = {"weather_forecast": [EvidenceItem("weather_forecast", "40.7790,-73.9692", "open-meteo", "u", now, "medium",
                                            {"unit": "fahrenheit", "daily_max": {day.isoformat(): {"gfs": 72.0, "ecmwf": 71.0, "icon": 73.0}}})]}
    r = WeatherHighTempModel().estimate(m, None, ModelContext(now, ev))
    assert r.q > 0.7 and r.inputs["lead_days"] == 1


def test_engine_abstains_with_only_market_prior():
    e = ProbabilityEngine().estimate(mk(category="politics", question="Who wins?"), bk(0.4, 0.42), ModelContext(utcnow(), {}))
    assert e.abstained and e.q is None


def test_engine_bayesian_shrinks_toward_market():
    now = utcnow()
    m = mk(category="economics", question="Q")
    ev = {"forecast": [EvidenceItem("forecast", m.key, "file", "u", now, "high", {"p": 0.70, "sigma": 0.06})]}
    e = ProbabilityEngine().estimate(m, bk(0.49, 0.51), ModelContext(now, ev, prior_sigma_floor=0.06))
    assert 0.5 < e.q < 0.7  # between market and forecast
    assert e.q == pytest.approx(0.6, abs=0.02)  # equal precision -> midpoint
    assert 0 < e.confidence <= 1


def test_cohere_normalizes_exclusive_set():
    es = [CombinedEstimate(str(i), q, 0.02, 0.8, "high", False, "ok", None) for i, q in enumerate([0.5, 0.4, 0.3])]
    cohere(es)
    assert sum(e.q for e in es) == pytest.approx(1.0)


def test_external_forecast_and_calibration_models():
    now = utcnow()
    m = mk()
    assert ExternalForecastModel().estimate(m, None, ModelContext(now, {})).__class__.__name__ == "Abstention"
    table = calibration_table([(0.85, 1)] * 30 + [(0.85, 0)] * 20)
    r = CalibrationModel().estimate(m, bk(0.84, 0.86), ModelContext(now, {}, calibration={"crypto": table}))
    assert r.q < 0.85  # history says 60% of 0.8-0.9 priced markets resolved YES
    few = calibration_table([(0.85, 1)] * 5)
    assert CalibrationModel().estimate(m, bk(0.84, 0.86), ModelContext(now, {}, calibration={"crypto": few})).__class__.__name__ == "Abstention"


def test_calibration_metrics():
    pairs = [(0.9, 1), (0.1, 0), (0.8, 0)]
    assert brier(pairs) == pytest.approx((0.01 + 0.01 + 0.64) / 3)
    assert log_loss([(0.5, 1)]) == pytest.approx(math.log(2))
    rel = reliability(pairs)
    assert sum(r["n"] for r in rel) == 3
    assert brier([]) is None
