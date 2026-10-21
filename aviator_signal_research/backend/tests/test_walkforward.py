import numpy as np
import pytest

from app.features import build_features, target_hit
from app.modeling.metrics import brier, diebold_mariano, holm_adjust
from app.modeling.walkforward import InMemoryLedger, LeakageError, OutcomeVault, make_folds, walk_forward
from app.synthetic import generate_independent, generate_regime


def test_folds_are_chronological_and_disjoint():
    folds = make_folds(start=1000, end=2050, block=250, purge=10, warmup=500)
    assert [f.pred_start for f in folds] == [1000, 1250, 1500, 1750, 2000]
    assert folds[-1].pred_end == 2050
    for f in folds:
        assert f.train_start == 500 and f.train_end == f.pred_start - 10  # purge gap respected
        assert f.train_end <= f.pred_start
    covered = np.concatenate([np.arange(f.pred_start, f.pred_end) for f in folds])
    assert len(covered) == len(set(covered)) == 1050  # every round predicted exactly once
    with pytest.raises(ValueError):
        make_folds(100, 200, 50, purge=0, warmup=100)


def test_vault_enforces_commit_before_reveal():
    vault = OutcomeVault(np.arange(10), revealed_until=5)
    with pytest.raises(LeakageError):
        vault.history(6)
    with pytest.raises(LeakageError):
        vault.reveal(5, 8, committed={5, 6})  # round 7 not committed
    with pytest.raises(LeakageError):
        vault.reveal(6, 8, committed={6, 7})  # gap: round 5 skipped
    assert list(vault.reveal(5, 8, {5, 6, 7})) == [5, 6, 7]
    assert vault.revealed_until == 8


def test_ledger_rejects_duplicate_predictions():
    from app.modeling.estimators import make_model

    led = InMemoryLedger()
    m = make_model("expanding_frequency")
    led.commit(model=m, threshold=2.0, phase="validation", seqs=[1, 2], probs=[0.5, 0.5], train_end=1)
    with pytest.raises(LeakageError):
        led.commit(model=m, threshold=2.0, phase="validation", seqs=[2], probs=[0.5], train_end=1)


def _run(df, model, th=2.0, start=900, end=2400, block=300):
    F = build_features(df)
    y = target_hit(df, th)
    return walk_forward(F, y, model, th, start, end, block, warmup=500, seed=1)


def test_no_false_skill_on_independent_data():
    """Independent rounds: after Holm correction no model may beat the baseline."""
    df = generate_independent(2400, seed=11)
    base = _run(df, "expanding_frequency")
    pvals = []
    for name in ["markov_conditional", "hmm_regime", "logistic_l1", "logistic_l2", "hist_gbm"]:
        r = _run(df, name)
        assert np.array_equal(r.seqs, base.seqs)
        pvals.append(diebold_mariano((base.probs - base.y) ** 2, (r.probs - r.y) ** 2)["p_value_one_sided"])
    assert min(holm_adjust(pvals)) > 0.05


@pytest.mark.parametrize("seed", [5, 6, 7, 8])
def test_detects_planted_dependence(seed):
    """Regime data has real serial dependence; with enough history the HMM must beat
    the baseline significantly. (With < ~1000 training rows it often cannot -- an
    honest sample-size limit, which is why the test uses 4000 rounds.)"""
    df = generate_regime(4000, seed=seed)
    base = _run(df, "expanding_frequency", start=1500, end=4000)
    hmm = _run(df, "hmm_regime", start=1500, end=4000)
    assert brier(hmm.y, hmm.probs) < brier(base.y, base.probs)
    assert diebold_mariano((base.probs - base.y) ** 2, (hmm.probs - hmm.y) ** 2)["p_value_one_sided"] < 0.01


def test_leaky_feature_would_produce_spectacular_fake_skill():
    """Sanity check of the threat model: a single leaked feature makes metrics look
    superb. This is why the look-ahead audit (not the metric) is the gatekeeper."""
    df = generate_independent(2400, seed=4)
    F = build_features(df)
    F["lag_hit_2_1"] = target_hit(df, 2.0).astype(float)  # leak: current round's outcome
    y = target_hit(df, 2.0)
    leak = walk_forward(F, y, "logistic_l1", 2.0, 900, 2400, 300, warmup=500)
    base = walk_forward(F, y, "expanding_frequency", 2.0, 900, 2400, 300, warmup=500)
    assert brier(leak.y, leak.probs) < 0.5 * brier(base.y, base.probs)


def test_walk_forward_is_reproducible():
    df = generate_regime(1800, seed=8)
    a = _run(df, "hist_gbm", start=900, end=1800)
    b = _run(df, "hist_gbm", start=900, end=1800)
    assert np.array_equal(a.probs, b.probs)
