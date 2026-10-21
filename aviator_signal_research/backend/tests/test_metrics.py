import numpy as np
from sklearn.metrics import brier_score_loss, log_loss as sk_log_loss, roc_auc_score
from statsmodels.stats.multitest import multipletests

from app.modeling import metrics as M


def test_scores_match_reference_implementations():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 500)
    p = np.clip(rng.uniform(size=500), 0.01, 0.99)
    assert np.isclose(M.brier(y, p), brier_score_loss(y, p))
    assert np.isclose(M.log_loss(y, p), sk_log_loss(y, p))
    assert np.isclose(M.roc_auc(y, p), roc_auc_score(y, p))
    assert M.roc_auc(np.ones(5), np.ones(5)) is None


def test_holm_matches_statsmodels():
    ps = [0.01, 0.04, 0.03, 0.2, None]
    ours = M.holm_adjust(ps)
    ref = multipletests([0.01, 0.04, 0.03, 0.2], method="holm")[1]
    assert np.allclose([x for x in ours if x is not None], ref) and ours[-1] is None


def test_diebold_mariano_behaviour():
    rng = np.random.default_rng(1)
    loss = rng.uniform(size=2000)
    same = M.diebold_mariano(loss, loss + rng.normal(0, 1e-3, 2000))
    assert 0.05 < same["p_value_one_sided"] < 0.95
    better = M.diebold_mariano(loss, loss - 0.05)
    assert better["p_value_one_sided"] < 1e-6


def test_calibration_tools():
    rng = np.random.default_rng(2)
    p = rng.uniform(0.3, 0.7, 5000)
    y = (rng.uniform(size=5000) < p).astype(float)
    assert M.hosmer_lemeshow(y, p)["p_value"] > 0.01
    bad = np.clip(p + 0.15, 0, 1)
    assert M.hosmer_lemeshow(y, bad)["p_value"] < 1e-6
    cal = M.calibration_slope_intercept(y, p)
    assert abs(cal["slope"] - 1) < 0.25 and abs(cal["intercept"]) < 0.2
    tab = M.calibration_table(y, p)
    assert sum(r["count"] for r in tab) == 5000


def test_wilson_and_bootstrap():
    lo, hi = M.wilson(50, 100)
    assert lo < 0.5 < hi
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 1000).astype(float)
    pb = np.full(1000, 0.5)
    r = M.block_bootstrap_skill(y, pb, pb, n_boot=200)
    assert r["brier_skill"] == 0 and r["ci_low"] <= 0 <= r["ci_high"]


def test_confusion_at_breakeven():
    y = np.array([1, 0, 1, 0])
    p = np.array([0.6, 0.6, 0.4, 0.4])
    c = M.confusion_at(y, p, 0.5)
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 1, 1, 1)
    assert M.classification_metrics(y, p, 2.0)["confusion_at_breakeven"]["decision_threshold"] == 0.5
