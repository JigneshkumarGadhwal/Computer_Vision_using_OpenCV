from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.ingestion.service import add_single_round, commit_import
from app.live import feed_status
from app.modeling import evaluation as ev
from app.models import DataSource, ModelVersion, Prediction, PredictionOutcome, Round
from app.signals import generate_signal
from app.synthetic import generate_independent, to_csv_bytes
from tests.conftest import FAST_MODELS, FAST_PROTOCOL


def _manual_source_with_history(session, n=2400):
    """A 'manual' (non-synthetic) source whose history is synthetic-generated values.
    Used only to exercise the live code path in tests."""
    df = generate_independent(n, seed=17)
    res = commit_import(session, to_csv_bytes(df), "h.csv", "manual-src", "manual")
    session.commit()
    return res["source_id"]


def test_no_signal_without_evaluation(session):
    sid = _manual_source_with_history(session, 300)
    s = generate_signal(session, sid, 2.0)
    assert s["status"] == ev.STATUS_INSUFFICIENT
    assert s["probability_reach"] is None  # never invented
    assert "historical_frequency" in s and session.scalar(select(Prediction.id)) is None


def test_signal_lifecycle_store_once_then_resolve(session):
    sid = _manual_source_with_history(session)
    run = ev.run_evaluation(session, sid, FAST_PROTOCOL, FAST_MODELS, include_regression=False)
    session.commit()
    s1 = generate_signal(session, sid, 2.0)
    session.commit()
    assert s1["status"] in (ev.STATUS_NO_EDGE, ev.STATUS_SIGNAL, ev.STATUS_INSUFFICIENT)
    assert 0 < s1["probability_reach"] < 1
    assert abs(s1["probability_reach"] + s1["probability_crash_before"] - 1) < 1e-12
    assert s1["next_round_sequence"] == 2400 and s1["evaluation_run_id"] == run.id
    for key in ("model_name", "model_version", "training_data_cutoff_round_id", "out_of_sample", "calibration_status",
                "prediction_hash", "disclaimer"):
        assert s1[key] is not None
    s2 = generate_signal(session, sid, 2.0)
    session.commit()
    assert s2["prediction_id"] == s1["prediction_id"]  # same round -> same immutable prediction
    # the round happens; outcome attached afterwards, prediction unchanged
    add_single_round(session, "manual-src", "manual", 3.5)
    from app.signals import resolve_live_outcomes

    assert resolve_live_outcomes(session, sid) == 2  # model + baseline
    session.commit()
    p = session.get(Prediction, s1["prediction_id"])
    assert p.probability == s1["probability_reach"] and p.outcome.hit is True
    assert p.created_at <= p.outcome.recorded_at
    s3 = generate_signal(session, sid, 2.0)
    assert s3["next_round_sequence"] == 2401 and s3["recent_live_performance"]["n_resolved"] == 1


def test_known_round_cannot_resolve_a_later_prediction(session):
    """A prediction created after its target round was ingested must never be scored."""
    sid = _manual_source_with_history(session)
    ev.run_evaluation(session, sid, FAST_PROTOCOL, FAST_MODELS, include_regression=False)
    session.commit()
    add_single_round(session, "manual-src", "manual", 1.5)  # seq 2400 already known
    session.commit()
    s = generate_signal(session, sid, 2.0)
    assert s["next_round_sequence"] == 2401
    live_outcomes = session.scalar(
        select(PredictionOutcome.id).join(Prediction).where(Prediction.mode == "live"))
    assert live_outcomes is None


def test_stale_live_feed_disables_signals(session, monkeypatch):
    monkeypatch.setenv("AVR_STALE_AFTER_SECONDS", "60")
    r = add_single_round(session, "feed", "live_feed", 2.0)
    session.commit()
    src = session.get(DataSource, r.source_id)
    assert feed_status(session, src)["stale"] is False
    session.execute(update(Round).values(ingested_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
    st = feed_status(session, src)
    assert st["stale"] and not st["signal_generation_enabled"]
    sig = generate_signal(session, src.id, 2.0)
    assert sig["status"] == ev.STATUS_INSUFFICIENT and sig["probability_reach"] is None


def test_delayed_feed_classified(session):
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    r = add_single_round(session, "feed2", "live_feed", 2.0, round_timestamp=old)
    st = feed_status(session, session.get(DataSource, r.source_id))
    assert st["data_mode"] == "delayed"


def test_tampered_model_artifact_blocks_signal(session):
    sid = _manual_source_with_history(session)
    run = ev.run_evaluation(session, sid, FAST_PROTOCOL, FAST_MODELS, include_regression=False)
    session.commit()
    mv = session.scalars(select(ModelVersion).where(ModelVersion.run_id == run.id, ModelVersion.is_selected.is_(True),
                                                    ModelVersion.threshold == 2.0)).one()
    with open(mv.artifact_path, "ab") as f:
        f.write(b"tamper")
    s = generate_signal(session, sid, 2.0)
    assert s["status"] == ev.STATUS_INSUFFICIENT and "checksum" in s["status_reasons"][0]
