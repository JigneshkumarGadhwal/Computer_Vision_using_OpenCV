import json
from dataclasses import replace

import pytest
from sqlalchemy import select, text

from app import features as feat
from app.modeling import evaluation as ev
from app.models import EvaluationRun, ImmutableRowError, ModelVersion, Prediction
from tests.conftest import FAST_MODELS, FAST_PROTOCOL, import_synthetic


def _evaluate(session, source_id, protocol=FAST_PROTOCOL, **kw):
    run = ev.run_evaluation(session, source_id, protocol, FAST_MODELS, **kw)
    session.commit()
    return session.get(EvaluationRun, run.id)


def test_independent_data_reports_no_edge(session):
    src = import_synthetic(session, "independent", n=2600, seed=21)
    run = _evaluate(session, src["source_id"], include_regression=False)
    assert run.status == "completed", run.error
    s = run.summary
    assert s["overall_status"] == ev.STATUS_NO_EDGE
    assert s["leakage_audit"]["passed"]
    for t in s["thresholds"].values():
        assert t["status"] == ev.STATUS_NO_EDGE
    assert "SYNTHETIC" in run.conclusion


def test_regime_data_reports_research_signal(session):
    src = import_synthetic(session, "regime", n=4500, seed=5)
    run = _evaluate(session, src["source_id"], replace(FAST_PROTOCOL, initial_train_size=1500, block_size=500))
    s = run.summary
    assert s["thresholds"]["2"]["status"] == ev.STATUS_SIGNAL, s["thresholds"]["2"]["status_reasons"]
    assert s["overall_status"] == ev.STATUS_SIGNAL
    # secondary regression task ran and reports both phases
    assert set(s["exact_multiplier"]["phases"]) == {"validation", "test"}


def test_selection_uses_development_period_only(session):
    src = import_synthetic(session, "regime", n=2600, seed=9)
    s = _evaluate(session, src["source_id"], include_regression=False).summary
    for t in s["thresholds"].values():
        dev = {k: v for k, v in t["development"].items() if v["family"] not in ("baseline",)}
        assert t["selected_model"] == min(dev, key=lambda k: dev[k]["validation"]["brier"])
        assert t["selected_model"] not in ("expanding_frequency", "rolling_frequency_500", "reference_constant")


def test_insufficient_data(session):
    src = import_synthetic(session, "independent", n=900, seed=1)
    run = _evaluate(session, src["source_id"])
    assert run.summary["overall_status"] == ev.STATUS_INSUFFICIENT
    assert "needs at least" in run.summary["reason"]


def test_leakage_bug_cannot_produce_a_signal(session, monkeypatch):
    """If a feature leaks the target, metrics look great but the audit fails and the
    framework refuses to report skill."""
    src = import_synthetic(session, "independent", n=2600, seed=21)
    original = feat.build_features

    def leaky(rounds, thresholds=feat.FEATURE_THRESHOLDS, n_extra=0):
        F = original(rounds, thresholds, n_extra)
        F["lag_hit_2_1"] = (rounds["multiplier"].astype(float).to_numpy() >= 2.0).astype(float)
        return F

    monkeypatch.setattr(feat, "build_features", leaky)
    monkeypatch.setattr(ev, "build_features", leaky)
    run = _evaluate(session, src["source_id"], include_regression=False)
    s = run.summary
    assert not s["leakage_audit"]["passed"]
    t = s["thresholds"]["2"]
    assert t["test"]["comparison_vs_baseline"]["brier_skill"] > 0.3  # the leak "works"...
    assert t["status"] == ev.STATUS_INSUFFICIENT  # ...but is never reported as a signal
    assert s["overall_status"] != ev.STATUS_SIGNAL


def test_evaluation_is_reproducible(session):
    src = import_synthetic(session, "regime", n=2400, seed=13)
    a = _evaluate(session, src["source_id"], include_regression=False)
    b = _evaluate(session, src["source_id"], include_regression=False)
    assert a.data_fingerprint == b.data_fingerprint
    strip = lambda s: json.dumps({k: v for k, v in s.items() if k != "thresholds"}, sort_keys=True)  # noqa: E731
    assert strip(a.summary) == strip(b.summary)
    for k in a.summary["thresholds"]:
        ta, tb = a.summary["thresholds"][k], b.summary["thresholds"][k]
        ta.pop("final_model_artifact"), tb.pop("final_model_artifact")
        assert json.dumps(ta, sort_keys=True) == json.dumps(tb, sort_keys=True)


def test_predictions_stored_before_outcomes_and_immutable(session):
    src = import_synthetic(session, "independent", n=2400, seed=2)
    run = _evaluate(session, src["source_id"], include_regression=False)
    n_bad = session.execute(text(
        "SELECT COUNT(*) FROM predictions p JOIN prediction_outcomes o ON o.prediction_id=p.id "
        "WHERE o.recorded_at < p.created_at OR p.data_cutoff_seq >= p.target_seq")).scalar()
    assert n_bad == 0
    n_pred = session.execute(text("SELECT COUNT(*) FROM predictions WHERE run_id=:r"), {"r": run.id}).scalar()
    n_out = session.execute(text("SELECT COUNT(*) FROM prediction_outcomes")).scalar()
    assert n_pred == n_out == run.summary["predictions_committed"] > 0
    # test-phase predictions cover every final-test round exactly once per model
    tp = run.summary["test_period"]
    sel = run.summary["thresholds"]["2"]["selected_model"]
    seqs = session.scalars(select(Prediction.target_seq).where(
        Prediction.run_id == run.id, Prediction.phase == "test", Prediction.model_name == sel,
        Prediction.threshold == 2.0)).all()
    assert sorted(seqs) == list(range(tp["start_seq"], tp["end_seq"] + 1))
    # ORM-level immutability
    p = session.scalars(select(Prediction).limit(1)).one()
    p.probability = 0.99
    with pytest.raises(ImmutableRowError):
        session.flush()
    session.rollback()
    # DB-level immutability (raw SQL)
    with pytest.raises(Exception, match="immutable"):
        session.execute(text("UPDATE predictions SET probability = 0.99 WHERE id = 1"))
    session.rollback()
    with pytest.raises(Exception, match="immutable"):
        session.execute(text("DELETE FROM predictions WHERE id = 1"))
    session.rollback()


def test_registry_records_reproducibility_info(session):
    src = import_synthetic(session, "independent", n=2400, seed=2)
    run = _evaluate(session, src["source_id"], include_regression=False)
    mvs = session.scalars(select(ModelVersion).where(ModelVersion.run_id == run.id)).all()
    assert len([m for m in mvs if not m.is_selected]) == len(FAST_MODELS) * len(FAST_PROTOCOL.thresholds)
    finals = [m for m in mvs if m.is_selected]
    assert len(finals) == len(FAST_PROTOCOL.thresholds)
    for m in finals:
        assert m.artifact_sha256 and m.random_seed == FAST_PROTOCOL.random_seed and m.train_end_seq == 2399
    assert run.protocol["initial_train_size"] == FAST_PROTOCOL.initial_train_size
    assert run.code_version


def test_report_written_from_stored_run(session, env):
    src = import_synthetic(session, "independent", n=2400, seed=2)
    run = _evaluate(session, src["source_id"], include_regression=False)
    text_ = (env / "reports" / f"evaluation_run_{run.id}.md").read_text()
    assert "SYNTHETIC DATA" in text_ and run.summary["overall_status"] in text_
    assert f"{run.summary['thresholds']['2']['test']['comparison_vs_baseline']['brier_skill']:+.5f}" in text_
