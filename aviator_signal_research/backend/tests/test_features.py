import numpy as np
import pandas as pd

from app import features as feat
from app.features import build_features, lookahead_audit, max_lookback, model_feature_names, target_hit
from app.synthetic import generate_independent


def _rounds(mults):
    return pd.DataFrame({"multiplier": mults,
                         "timestamp": pd.date_range("2026-01-01", periods=len(mults), freq="15s", tz="UTC")})


def test_feature_values_use_only_past_rounds():
    m = [1.0, 2.5, 1.2, 3.0, 10.0, 1.5, 2.0]
    F = build_features(_rounds(m))
    # Row t describes rounds < t.
    assert np.isnan(F["lag_logm_1"].iloc[0])
    assert F["lag_logm_1"].iloc[3] == np.log(1.2)
    assert F["lag_hit_2_1"].iloc[2] == 1.0 and F["lag_hit_2_1"].iloc[3] == 0.0
    # expanding Laplace frequency at row 4: rounds 0..3 -> hits(>=2) = 2 of 4 -> (2+1)/(4+2)
    assert F["efreq_2"].iloc[4] == (2 + 1) / (4 + 2)
    assert F["streak_below_2"].iloc[3] == 1  # round 2 (1.2) below 2
    assert F["streak_above_2"].iloc[5] == 2  # rounds 3,4 >= 2
    assert F["rounds_since_10x"].iloc[5] == 1 and F["rounds_since_10x"].iloc[6] == 2
    # rolling window features stay NaN until the window is full (never padded with invented values)
    assert F["rfreq_2_10"].isna().all()


def test_targets_and_tie_convention():
    df = _rounds([2.0, 1.99, 2.01])
    assert list(target_hit(df, 2.0)) == [1, 0, 1]
    assert list(target_hit(df, 2.0, tie_counts_as_hit=False)) == [0, 0, 1]


def test_lookahead_audit_passes_on_real_pipeline():
    df = generate_independent(1200, seed=1)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    res = lookahead_audit(df, seed=0)
    assert res["passed"] and res["checked_positions"] >= 20


def test_lookahead_audit_detects_planted_leak(monkeypatch):
    original = feat.build_features

    def leaky(rounds, thresholds=feat.FEATURE_THRESHOLDS, n_extra=0):
        F = original(rounds, thresholds, n_extra)
        F["lag_logm_1"] = np.log(rounds["multiplier"].astype(float).to_numpy())  # uses round t itself
        return F

    monkeypatch.setattr(feat, "build_features", leaky)
    res = feat.lookahead_audit(generate_independent(800, seed=2))
    assert not res["passed"]
    assert "lag_logm_1" in res["violations"][0]["features"]


def test_feature_registry_consistent():
    F = build_features(_rounds([1.5] * 30))
    names = model_feature_names()
    assert set(names) <= set(F.columns)
    assert max_lookback() == 500
    for d in feat.feature_definitions():
        assert d.description and d.lookback >= 0


def test_live_row_for_next_round():
    df = _rounds([1.5, 2.5, 3.5])
    F = build_features(df, n_extra=1)
    assert len(F) == 4 and F["lag_logm_1"].iloc[3] == np.log(3.5)
