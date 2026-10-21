"""Signal engine: turns the evaluated model into a transparent research signal
for the NEXT, not-yet-played round, and stores it immutably.

The signal is a probability estimate, never a promise. Its status is inherited
from the pre-registered evaluation of the model on unseen rounds, and is
downgraded to INSUFFICIENT EVIDENCE when the data or feed is unreliable.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import joblib
import numpy as np
from sqlalchemy import DateTime, bindparam, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .features import build_features
from .ingestion.service import load_rounds_frame
from .live import feed_status
from .modeling.estimators import PRIMARY_BASELINE, make_model
from .modeling.evaluation import STATUS_INSUFFICIENT, target_name
from .modeling.metrics import brier, wilson
from .modeling.walkforward import prediction_hash
from .models import DataSource, EvaluationRun, ModelVersion, Prediction

MODEL_STALE_AFTER_ROUNDS = 1000
DISCLAIMER = (
    "Probability estimate only — not a promise that the round will reach this multiplier. "
    "No predictive edge is implied unless status is RESEARCH SIGNAL, and even then losses remain likely."
)


def latest_run(session: Session, source_id: int, threshold: float | None = None) -> EvaluationRun | None:
    runs = session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.source_id == source_id, EvaluationRun.status == "completed")
        .order_by(EvaluationRun.id.desc())
    ).all()
    for r in runs:
        if threshold is None or f"{threshold:g}" in (r.summary or {}).get("thresholds", {}):
            return r
    return None


def resolve_live_outcomes(session: Session, source_id: int) -> int:
    """Attach outcomes to live predictions whose target round has now been observed.

    Only rounds ingested AFTER the prediction was created are accepted; a round
    that was already known when the prediction was made can never count."""
    res = session.execute(
        text(
            """INSERT INTO prediction_outcomes (prediction_id, actual_multiplier, hit, round_id, recorded_at)
               SELECT p.id, r.multiplier, CASE WHEN r.multiplier >= p.threshold THEN 1 ELSE 0 END, r.round_id, :now
               FROM predictions p
               JOIN rounds r ON r.source_id = p.source_id AND r.sequence = p.target_seq
               LEFT JOIN prediction_outcomes o ON o.prediction_id = p.id
               WHERE p.source_id = :src AND p.mode = 'live' AND o.id IS NULL
                 AND r.ingested_at >= p.created_at"""
        ).bindparams(bindparam("now", type_=DateTime(timezone=True))),
        {"now": datetime.now(timezone.utc), "src": source_id},
    )
    return res.rowcount or 0


def _recent_live_performance(session: Session, source_id: int, model_name: str, threshold: float, limit: int = 200):
    rows = session.execute(
        text(
            """SELECT p.model_name, p.target_seq, p.probability, o.hit FROM predictions p
               JOIN prediction_outcomes o ON o.prediction_id = p.id
               WHERE p.source_id = :src AND p.mode = 'live' AND p.threshold = :th
                 AND p.model_name IN (:m, :b)
               ORDER BY p.target_seq DESC LIMIT :lim"""
        ),
        {"src": source_id, "th": threshold, "m": model_name, "b": PRIMARY_BASELINE, "lim": 2 * limit},
    ).all()
    by_seq: dict[int, dict] = {}
    for name, seq, p, hit in rows:
        by_seq.setdefault(seq, {})[name] = (p, hit)
    paired = [(v[model_name], v[PRIMARY_BASELINE]) for v in by_seq.values() if model_name in v and PRIMARY_BASELINE in v]
    if not paired:
        return {"n_resolved": 0}
    pm = np.array([a[0] for a, _ in paired])
    pb = np.array([b[0] for _, b in paired])
    y = np.array([a[1] for a, _ in paired], dtype=float)
    return {
        "n_resolved": len(paired),
        "brier_model": brier(y, pm),
        "brier_baseline": brier(y, pb),
        "hit_rate": float(y.mean()),
        "note": "Live sample; too small for conclusions until several hundred rounds." if len(paired) < 300 else None,
    }


def _store(session, *, run, source_id, model_name, version, threshold, target_seq, prob, cutoff_seq, cutoff_rid,
           status) -> Prediction:
    existing = session.scalars(
        select(Prediction).where(
            Prediction.run_id == run.id, Prediction.source_id == source_id, Prediction.mode == "live",
            Prediction.model_name == model_name, Prediction.threshold == threshold, Prediction.target_seq == target_seq,
        )
    ).first()
    if existing is not None:
        return existing  # never overwrite an earlier prediction for the same round
    p = Prediction(
        run_id=run.id, source_id=source_id, mode="live", phase=None, model_name=model_name, model_version=version,
        target_definition=f"{target_name(threshold)} (crash multiplier >= {threshold:g})", threshold=threshold,
        target_seq=target_seq, probability=float(prob), data_cutoff_seq=cutoff_seq, data_cutoff_round_id=cutoff_rid,
        signal_status=status,
        content_hash=prediction_hash(model_name, version, threshold, target_seq, float(prob), cutoff_seq),
        created_at=datetime.now(timezone.utc),
    )
    session.add(p)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise
    return p


def generate_signal(session: Session, source_id: int, threshold: float, persist: bool = True) -> dict:
    src = session.get(DataSource, source_id)
    if src is None:
        raise ValueError("source not found")
    resolve_live_outcomes(session, source_id)
    feed = feed_status(session, src)
    df = load_rounds_frame(session, source_id)
    n = len(df)
    now = datetime.now(timezone.utc)
    out: dict = {
        "source_id": source_id,
        "source_name": src.name,
        "is_synthetic": src.is_synthetic,
        "data_mode": feed["data_mode"],
        "feed": feed,
        "threshold": threshold,
        "target": target_name(threshold),
        "breakeven_probability": 1.0 / threshold,
        "prediction_timestamp": now.isoformat(),
        "next_round_sequence": int(df["sequence"].iloc[-1]) + 1 if n else 0,
        "data_cutoff_round_id": str(df["round_id"].iloc[-1]) if n else None,
        "disclaimer": DISCLAIMER,
        "probability_reach": None,
        "probability_crash_before": None,
        "warnings": list(feed["warnings"]),
    }
    if n:
        k = int((df["multiplier"] >= threshold).sum())
        lo, hi = wilson(k, n)
        out["historical_frequency"] = {"share_reaching": k / n, "ci_low": lo, "ci_high": hi, "n": n,
                                       "note": "Descriptive statistic of stored rounds, not a prediction."}
    run = latest_run(session, source_id, threshold)
    if run is None:
        out.update(status=STATUS_INSUFFICIENT, status_reasons=["No completed evaluation run for this source/threshold."])
        return out
    tinfo = run.summary["thresholds"][f"{threshold:g}"]
    out["evaluation_run_id"] = run.id
    if not feed["signal_generation_enabled"]:
        out.update(status=STATUS_INSUFFICIENT, status_reasons=["Signal generation disabled: " + "; ".join(feed["warnings"])])
        return out
    mv = session.scalars(
        select(ModelVersion).where(ModelVersion.run_id == run.id, ModelVersion.threshold == threshold,
                                   ModelVersion.is_selected.is_(True))
    ).first()
    if mv is None or not mv.artifact_path:
        out.update(status=STATUS_INSUFFICIENT, status_reasons=["Selected model artifact missing."])
        return out
    try:
        blob = open(mv.artifact_path, "rb").read()
    except OSError:
        out.update(status=STATUS_INSUFFICIENT, status_reasons=[f"Model artifact not found at {mv.artifact_path}."])
        return out
    if hashlib.sha256(blob).hexdigest() != mv.artifact_sha256:
        out.update(status=STATUS_INSUFFICIENT, status_reasons=["Model artifact checksum mismatch (tampered or replaced)."])
        return out
    model = joblib.load(mv.artifact_path)
    F = build_features(df, (threshold,), n_extra=1)
    row = F.iloc[[n]]
    y_hist = (df["multiplier"].to_numpy(float) >= threshold).astype(int)
    p_model = float(model.predict(row, threshold, y_history=y_hist)[0])
    p_base = float(make_model(PRIMARY_BASELINE).predict(row, threshold)[0])
    rounds_since_cutoff = int(df["sequence"].iloc[-1]) - mv.train_end_seq
    status = tinfo["status"]
    reasons = list(tinfo["status_reasons"])
    if rounds_since_cutoff > MODEL_STALE_AFTER_ROUNDS:
        status = STATUS_INSUFFICIENT
        reasons.append(f"Model trained {rounds_since_cutoff} rounds ago (> {MODEL_STALE_AFTER_ROUNDS}); re-run evaluation.")
    test = tinfo["test"]
    cal_bucket = next((b for b in test["calibration_selected"] if b["p_min"] <= p_model <= b["p_max"]), None)
    cmp = test["comparison_vs_baseline"]
    out.update(
        status=status,
        status_reasons=reasons,
        model_name=mv.name,
        model_version=mv.version,
        model_registry_id=mv.id,
        training_data_cutoff_seq=mv.train_end_seq,
        training_data_cutoff_round_id=mv.data_cutoff_round_id,
        rounds_since_training_cutoff=rounds_since_cutoff,
        probability_reach=p_model,
        probability_crash_before=1 - p_model,
        baseline_probability_reach=p_base,
        expected_net_return_per_unit=p_model * threshold - 1,
        data_quality=run.summary.get("data_quality"),
        calibration_status=next((r for r in reasons if r.startswith("calibration")), None),
        out_of_sample={
            "n_test_rounds": test["n"],
            "brier_model": test["metrics"][mv.name]["brier"],
            "brier_baseline": test["metrics"][PRIMARY_BASELINE]["brier"],
            "brier_skill": cmp["brier_skill"],
            "brier_skill_ci": [cmp["brier_skill_ci_low"], cmp["brier_skill_ci_high"]],
            "p_holm": cmp.get("p_holm_across_thresholds"),
        },
        uncertainty={
            "calibration_bucket": cal_bucket,
            "note": "Observed hit rate among test-period predictions in the same probability bucket.",
        },
        recent_live_performance=_recent_live_performance(session, source_id, mv.name, threshold),
    )
    if src.is_synthetic:
        out["warnings"].append("SYNTHETIC DATA: this signal is for demonstrating the software only.")
    if persist:
        target_seq = out["next_round_sequence"]
        cutoff = target_seq - 1
        rid = out["data_cutoff_round_id"]
        pm = _store(session, run=run, source_id=source_id, model_name=mv.name, version=mv.version, threshold=threshold,
                    target_seq=target_seq, prob=p_model, cutoff_seq=cutoff, cutoff_rid=rid, status=status)
        _store(session, run=run, source_id=source_id, model_name=PRIMARY_BASELINE, version="1.0", threshold=threshold,
               target_seq=target_seq, prob=p_base, cutoff_seq=cutoff, cutoff_rid=rid, status=None)
        out["prediction_id"] = pm.id
        out["prediction_hash"] = pm.content_hash
        out["prediction_created_at"] = pm.created_at.isoformat()
        if abs(pm.probability - p_model) > 1e-12:
            out["warnings"].append("A prediction for this round was already stored earlier; showing the stored value.")
            out["probability_reach"] = pm.probability
            out["probability_crash_before"] = 1 - pm.probability
    return out
