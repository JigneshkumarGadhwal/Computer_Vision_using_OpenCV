"""End-to-end research experiment.

Pre-registered protocol (see config.EvaluationProtocol):

1. Chronological split: development period = first ``dev_fraction`` of rounds,
   final test = remainder. The final test is not used for any choice.
2. Development: walk-forward (expanding window, retrain each block) for every
   candidate model and threshold. Compare with the expanding-frequency
   baseline (Diebold-Mariano on Brier loss, Holm-adjusted across all
   model x threshold comparisons).
3. Selection: per threshold, the non-baseline model with the lowest
   development Brier score is the single candidate carried to the final test.
4. Final test: walk-forward continues over the test period for the
   candidate and the baselines. One DM test per threshold, Holm-adjusted
   across thresholds.
5. Signal status per threshold:
     INSUFFICIENT EVIDENCE  -> test rounds < min_test_rounds, failed leakage
                                audit or failed data-quality gate
     RESEARCH SIGNAL        -> Brier skill > min_brier_skill AND Holm-adjusted
                                p < alpha AND calibrated on the test period
                                (Hosmer-Lemeshow p >= calibration_alpha and
                                |calibration-in-the-large| <= max_calibration_gap)
     NO DEMONSTRATED EDGE   -> otherwise
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import DateTime, bindparam, insert, select, text
from sqlalchemy.orm import Session

from ..config import DEFAULT_PROTOCOL, EvaluationProtocol, get_settings
from ..features import (
    AVAILABLE_AT,
    FEATURE_VERSION,
    build_features,
    feature_definitions,
    lookahead_audit,
    max_lookback,
    target_hit,
)
from ..ingestion.service import load_rounds_frame
from ..models import (
    DataSource,
    EvaluationRun,
    FeatureDefinition,
    ModelVersion,
    Prediction,
    ValidationResult,
    log_event,
)
from .estimators import MIN_TRAIN_ROWS, PRIMARY_BASELINE, default_model_names, is_baseline, make_model
from .metrics import block_bootstrap_skill, brier, calibration_table, classification_metrics, diebold_mariano, holm_adjust
from .regression import evaluate_regression
from .walkforward import WalkForwardResult, prediction_hash, walk_forward

STATUS_INSUFFICIENT = "INSUFFICIENT EVIDENCE"
STATUS_NO_EDGE = "NO DEMONSTRATED EDGE"
STATUS_SIGNAL = "RESEARCH SIGNAL"
TEST_COMPARATORS = ("expanding_frequency", "rolling_frequency_500", "reference_constant")


def code_version() -> str:
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.read_bytes())
    try:
        git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except Exception:
        git = ""
    return f"{git or 'nogit'}+src{h.hexdigest()[:10]}"


def data_fingerprint(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for rid, m in zip(df["round_id"], df["multiplier"]):
        h.update(f"{rid}:{m:.6f};".encode())
    return h.hexdigest()


def target_name(threshold: float) -> str:
    return f"reaches_{threshold:g}x"


class DBLedger:
    """Commits predictions to the immutable ``predictions`` table, then records
    outcomes from the ``rounds`` table only for already-committed predictions."""

    def __init__(self, session: Session, run_id: int, source_id: int, seq_to_round: np.ndarray):
        self.session = session
        self.run_id = run_id
        self.source_id = source_id
        self.seq_to_round = seq_to_round  # position -> stored sequence number
        self.n_committed = 0

    def commit(self, *, model, threshold, phase, seqs, probs, train_end):
        now = datetime.now(timezone.utc)
        rows = []
        for s, p in zip(seqs, probs):
            target_seq = int(self.seq_to_round[int(s)])
            cutoff = target_seq - 1
            rows.append(
                {
                    "run_id": self.run_id,
                    "source_id": self.source_id,
                    "mode": "walk_forward",
                    "phase": phase,
                    "model_name": model.name,
                    "model_version": model.version,
                    "target_definition": f"{target_name(threshold)} (crash multiplier >= {threshold:g})",
                    "threshold": float(threshold),
                    "target_seq": target_seq,
                    "probability": float(p),
                    "data_cutoff_seq": cutoff,
                    "data_cutoff_round_id": None,
                    "signal_status": None,
                    "content_hash": prediction_hash(model.name, model.version, threshold, target_seq, float(p), cutoff),
                    "created_at": now,
                }
            )
        self.session.execute(insert(Prediction), rows)
        self.session.flush()
        self.n_committed += len(rows)
        return {int(s) for s in seqs}

    def record_outcomes(self, *, model, threshold, seqs, ys):
        lo = int(self.seq_to_round[int(seqs[0])])
        hi = int(self.seq_to_round[int(seqs[-1])])
        self.session.execute(
            text(
                """INSERT INTO prediction_outcomes (prediction_id, actual_multiplier, hit, round_id, recorded_at)
                   SELECT p.id, r.multiplier, CASE WHEN r.multiplier >= p.threshold THEN 1 ELSE 0 END,
                          r.round_id, :now
                   FROM predictions p JOIN rounds r
                     ON r.source_id = p.source_id AND r.sequence = p.target_seq
                   WHERE p.run_id = :run AND p.model_name = :model AND p.threshold = :th
                     AND p.mode = 'walk_forward' AND p.target_seq BETWEEN :lo AND :hi"""
            ).bindparams(bindparam("now", type_=DateTime(timezone=True))),
            {"now": datetime.now(timezone.utc), "run": self.run_id, "model": model.name, "th": float(threshold),
             "lo": lo, "hi": hi},
        )
        # Commit per block: keeps the ledger durable and avoids long SQLite write locks.
        self.session.commit()


def _comparison(res: WalkForwardResult, base: WalkForwardResult, protocol: EvaluationProtocol, seed: int) -> dict:
    assert np.array_equal(res.seqs, base.seqs)
    lm = (res.probs - res.y) ** 2
    lb = (base.probs - base.y) ** 2
    dm = diebold_mariano(lb, lm)
    boot = block_bootstrap_skill(res.y, res.probs, base.probs, n_boot=protocol.bootstrap_samples,
                                 block=protocol.bootstrap_block, seed=seed)
    return {
        "brier_model": brier(res.y, res.probs),
        "brier_baseline": brier(base.y, base.probs),
        "brier_skill": boot["brier_skill"],
        "brier_skill_ci_low": boot["ci_low"],
        "brier_skill_ci_high": boot["ci_high"],
        "dm_statistic": dm["statistic"],
        "p_value": dm["p_value_one_sided"],
    }


def _per_period(res: WalkForwardResult, base: WalkForwardResult, train_rate: float, n_periods: int = 4) -> list[dict]:
    out = []
    for idx in np.array_split(np.arange(len(res.y)), n_periods):
        if len(idx) < 10:
            continue
        y = res.y[idx]
        out.append(
            {
                "start_seq": int(res.seqs[idx[0]]),
                "end_seq": int(res.seqs[idx[-1]]),
                "n": int(len(idx)),
                "base_rate": float(y.mean()),
                "shift_vs_dev_rate": float(y.mean() - train_rate),
                "brier_model": brier(y, res.probs[idx]),
                "brier_baseline": brier(y, base.probs[idx]),
            }
        )
    return out


def _decide(test_cmp: dict, test_metrics: dict, p_holm: float | None, n_test: int, protocol: EvaluationProtocol,
            audit_ok: bool, quality_ok: bool) -> tuple[str, list[str]]:
    reasons = []
    if not audit_ok:
        reasons.append("look-ahead leakage audit failed")
    if not quality_ok:
        reasons.append("data-quality gate failed")
    if n_test < protocol.min_test_rounds:
        reasons.append(f"only {n_test} final-test rounds (< {protocol.min_test_rounds})")
    if reasons:
        return STATUS_INSUFFICIENT, reasons
    ok_skill = test_cmp["brier_skill"] > protocol.min_brier_skill
    ok_p = p_holm is not None and p_holm < protocol.alpha
    hl_p = test_metrics["hosmer_lemeshow"]["p_value"]
    gap = abs(test_metrics["calibration_in_the_large"])
    ok_cal = hl_p is not None and hl_p >= protocol.calibration_alpha and gap <= protocol.max_calibration_gap
    reasons.append(f"Brier skill {test_cmp['brier_skill']:+.5f} {'>' if ok_skill else '<='} {protocol.min_brier_skill}")
    reasons.append(f"Holm-adjusted DM p = {p_holm:.4f} {'<' if ok_p else '>='} {protocol.alpha}" if p_holm is not None
                   else "DM p-value unavailable")
    reasons.append(
        f"calibration {'OK' if ok_cal else 'FAILED'} (Hosmer-Lemeshow p = {hl_p:.4f}, "
        f"mean-prediction gap {gap:.4f}; ECE {test_metrics['ece']:.4f} reported only)"
    )
    return (STATUS_SIGNAL if (ok_skill and ok_p and ok_cal) else STATUS_NO_EDGE), reasons


def _register_features(session: Session) -> None:
    existing = {(f.name, f.version) for f in session.scalars(select(FeatureDefinition))}
    for d in feature_definitions():
        if (d.name, FEATURE_VERSION) not in existing:
            session.add(FeatureDefinition(name=d.name, version=FEATURE_VERSION, description=d.description,
                                          lookback_rounds=d.lookback, available_at=AVAILABLE_AT))


def _save_artifact(model, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_run(session: Session, source_id: int, protocol: EvaluationProtocol = DEFAULT_PROTOCOL) -> EvaluationRun:
    src = session.get(DataSource, source_id)
    if src is None:
        raise ValueError(f"source {source_id} not found")
    df = load_rounds_frame(session, source_id)
    run = EvaluationRun(source_id=source_id, is_synthetic=src.is_synthetic, status="running",
                        protocol=protocol.to_dict(), data_fingerprint=data_fingerprint(df) if len(df) else "",
                        code_version=code_version(), n_rounds=int(len(df)))
    session.add(run)
    session.commit()  # make the running job visible (and survive a later rollback)
    return run


def run_evaluation(
    session: Session,
    source_id: int,
    protocol: EvaluationProtocol = DEFAULT_PROTOCOL,
    model_names: list[str] | None = None,
    run: EvaluationRun | None = None,
    progress=None,
    include_regression: bool = True,
) -> EvaluationRun:
    settings = get_settings()
    run = run or create_run(session, source_id, protocol)
    try:
        _run(session, run, protocol, model_names or default_model_names(), settings, progress, include_regression)
    except Exception as exc:  # record failure honestly; never fake a result
        session.rollback()
        run = session.get(EvaluationRun, run.id)
        run.status = "failed"
        run.error = f"{exc}\n{traceback.format_exc()[-2000:]}"
        run.finished_at = datetime.now(timezone.utc)
        log_event(session, "error", "evaluation", f"Evaluation run {run.id} failed: {exc}", run_id=run.id)
        session.commit()
    return run


def _run(session, run, protocol, model_names, settings, progress, include_regression):
    def note(msg):
        if progress:
            progress(msg)

    src = session.get(DataSource, run.source_id)
    df = load_rounds_frame(session, run.source_id)
    n = len(df)
    warmup = max_lookback()
    n_dev = int(n * protocol.dev_fraction)
    n_test = n - n_dev
    summary: dict = {
        "source": {"id": src.id, "name": src.name, "kind": src.kind, "is_synthetic": src.is_synthetic},
        "n_rounds": n,
        "warmup_rounds": warmup,
        "dev_period": {"start_seq": int(df["sequence"].iloc[0]) if n else None,
                       "end_seq": int(df["sequence"].iloc[n_dev - 1]) if n_dev else None,
                       "n": n_dev},
        "test_period": {"start_seq": int(df["sequence"].iloc[n_dev]) if n_test > 0 else None,
                        "end_seq": int(df["sequence"].iloc[-1]) if n else None,
                        "n": n_test,
                        "start_time": df["timestamp"].iloc[n_dev].isoformat() if n_test > 0 and pd.notna(df["timestamp"].iloc[n_dev]) else None,
                        "end_time": df["timestamp"].iloc[-1].isoformat() if n and pd.notna(df["timestamp"].iloc[-1]) else None},
        "models_evaluated": model_names,
        "thresholds": {},
    }
    # ---- sufficiency gate -------------------------------------------------
    min_needed = protocol.initial_train_size + 2 * protocol.block_size + protocol.block_size
    if (n < min_needed or n_dev - protocol.initial_train_size < protocol.block_size
            or protocol.initial_train_size - warmup - protocol.purge_gap < MIN_TRAIN_ROWS):
        summary["overall_status"] = STATUS_INSUFFICIENT
        summary["reason"] = (
            f"{n} rounds available; this protocol needs at least {min_needed} "
            f"(initial train {protocol.initial_train_size} incl. {warmup} warm-up, block {protocol.block_size}) "
            "and initial_train_size - warm-up must leave >= 200 training rows."
        )
        _finish(session, run, summary, f"{STATUS_INSUFFICIENT}: {summary['reason']}")
        return

    # ---- data quality gate ------------------------------------------------
    warn_share = float((df["quality_status"] == "warning").mean())
    quality_ok = warn_share <= 0.05
    summary["data_quality"] = {"warning_share": warn_share, "gate_passed": quality_ok,
                               "verified_share": float(df["verified"].mean())}

    note("building features")
    F = build_features(df, protocol.thresholds)
    audit = lookahead_audit(df, seed=protocol.random_seed)
    summary["leakage_audit"] = audit
    _register_features(session)
    seq_map = df["sequence"].to_numpy()
    ledger = DBLedger(session, run.id, run.source_id, seq_map)
    mult = df["multiplier"].to_numpy(float)

    dev_tests: list[tuple[float, str, float | None]] = []
    per_threshold: dict = {}
    for th in protocol.thresholds:
        y = target_hit(df, th)
        dev_results: dict[str, WalkForwardResult] = {}
        for name in model_names:
            note(f"dev walk-forward {name} @ {th:g}x")
            dev_results[name] = walk_forward(
                F, y, name, th, protocol.initial_train_size, n_dev, protocol.block_size,
                protocol.purge_gap, warmup, phase="validation", ledger=ledger, seed=protocol.random_seed,
                rtp=settings.reference_rtp,
            )
        base = dev_results[PRIMARY_BASELINE]
        dev_rows = {}
        for name, res in dev_results.items():
            met = classification_metrics(res.y, res.probs, th)
            tr = (classification_metrics(res.train_y_last_fold, res.train_probs_last_fold, th)
                  if res.train_probs_last_fold is not None else None)
            cmp = _comparison(res, base, protocol, protocol.random_seed) if name != PRIMARY_BASELINE else None
            dev_rows[name] = {"family": res.description.get("family"), "validation": met, "train_last_fold": tr,
                              "vs_baseline": cmp, "n_folds": len(res.folds),
                              "period": {"start_seq": int(seq_map[res.seqs[0]]), "end_seq": int(seq_map[res.seqs[-1]])}}
            if cmp is not None and not is_baseline(name):
                dev_tests.append((th, name, cmp["p_value"]))
            for phase, m in (("train", tr), ("validation", met)):
                if m is None:
                    continue
                f = res.folds[-1] if phase == "train" else None
                session.add(ValidationResult(
                    run_id=run.id, model_name=name, target=target_name(th), threshold=th, phase=phase,
                    n_rounds=m["n"],
                    period_start_seq=int(seq_map[f.train_start]) if f else int(seq_map[res.seqs[0]]),
                    period_end_seq=int(seq_map[f.train_end - 1]) if f else int(seq_map[res.seqs[-1]]),
                    metrics=clean_json({**m, **({"vs_baseline": cmp} if (cmp and phase == "validation") else {})}),
                ))
            last = res.folds[-1]
            session.add(ModelVersion(
                run_id=run.id, source_id=run.source_id, name=name, version=res.description.get("version", "1.0"),
                family=res.description.get("family", "unknown"), target=target_name(th), threshold=th,
                params=json.loads(json.dumps(res.description.get("params", {}), default=str)),
                features=res.description.get("features", []), train_start_seq=int(seq_map[last.train_start]),
                train_end_seq=int(seq_map[last.train_end - 1]), calibration=res.description.get("calibration"),
                random_seed=protocol.random_seed,
            ))
        candidates = [nm for nm in model_names if not is_baseline(nm)]
        if not candidates:
            raise ValueError("at least one non-baseline model is required")
        selected = min(candidates, key=lambda nm: dev_rows[nm]["validation"]["brier"])
        per_threshold[th] = {"dev": dev_rows, "selected_model": selected, "dev_base": base, "y": y}

    # Holm adjustment across all development comparisons.
    adj = holm_adjust([p for _, _, p in dev_tests])
    for (th, name, _), a in zip(dev_tests, adj):
        per_threshold[th]["dev"][name]["vs_baseline"]["p_holm_dev_family"] = a
    summary["dev_family_size"] = len(dev_tests)

    # ---- final test ---------------------------------------------------------
    test_pvals = []
    for th in protocol.thresholds:
        info = per_threshold[th]
        y = info["y"]
        test_res = {}
        for name in dict.fromkeys([info["selected_model"], *TEST_COMPARATORS]):
            note(f"final test {name} @ {th:g}x")
            test_res[name] = walk_forward(
                F, y, name, th, n_dev, n, protocol.block_size, protocol.purge_gap, warmup, phase="test",
                ledger=ledger, seed=protocol.random_seed, rtp=settings.reference_rtp,
            )
        info["test_res"] = test_res
        sel = test_res[info["selected_model"]]
        base = test_res[PRIMARY_BASELINE]
        info["test_cmp"] = _comparison(sel, base, protocol, protocol.random_seed)
        test_pvals.append(info["test_cmp"]["p_value"])
    test_adj = holm_adjust(test_pvals)

    statuses = []
    for (th, info), p_holm in zip(per_threshold.items(), test_adj):
        test_res = info["test_res"]
        sel_name = info["selected_model"]
        sel = test_res[sel_name]
        base = test_res[PRIMARY_BASELINE]
        dev_rate = float(info["dev_base"].y.mean())
        test_metrics = {nm: classification_metrics(r.y, r.probs, th) for nm, r in test_res.items()}
        cmp = {**info["test_cmp"], "p_holm_across_thresholds": p_holm}
        status, reasons = _decide(cmp, test_metrics[sel_name], p_holm, len(sel.y), protocol, audit["passed"], quality_ok)
        statuses.append(status)
        for nm, r in test_res.items():
            session.add(ValidationResult(
                run_id=run.id, model_name=nm, target=target_name(th), threshold=th, phase="test",
                n_rounds=len(r.y), period_start_seq=int(seq_map[r.seqs[0]]), period_end_seq=int(seq_map[r.seqs[-1]]),
                metrics=clean_json({**test_metrics[nm], **({"vs_baseline": cmp} if nm == sel_name else {})}),
            ))
        # Final production model for the live signal engine: trained on all data.
        note(f"fitting final model {sel_name} @ {th:g}x")
        final = make_model(sel_name, seed=protocol.random_seed, rtp=settings.reference_rtp)
        final.fit(F.iloc[warmup:n], y[warmup:n], th)
        art = settings.model_artifact_dir / f"run{run.id}_{sel_name}_{th:g}.joblib"
        sha = _save_artifact(final, art)
        desc = final.describe()
        session.add(ModelVersion(
            run_id=run.id, source_id=run.source_id, name=sel_name, version=desc["version"], family=desc["family"],
            target=target_name(th), threshold=th, params=json.loads(json.dumps(desc["params"], default=str)),
            features=desc["features"], train_start_seq=int(seq_map[warmup]), train_end_seq=int(seq_map[n - 1]),
            data_cutoff_round_id=str(df["round_id"].iloc[-1]), artifact_path=str(art), artifact_sha256=sha,
            calibration=desc["calibration"], random_seed=protocol.random_seed, is_selected=True,
        ))
        summary["thresholds"][f"{th:g}"] = {
            "threshold": th,
            "target": target_name(th),
            "breakeven_probability": 1.0 / th,
            "selected_model": sel_name,
            "selection_rule": "lowest development-period Brier score among non-baseline models",
            "status": status,
            "status_reasons": reasons,
            "development": {nm: {k: v for k, v in row.items()} for nm, row in info["dev"].items()},
            "test": {
                "metrics": test_metrics,
                "comparison_vs_baseline": cmp,
                "calibration_selected": calibration_table(sel.y, sel.probs),
                "calibration_baseline": calibration_table(base.y, base.probs),
                "per_period": _per_period(sel, base, dev_rate),
                "dev_base_rate": dev_rate,
                "test_base_rate": float(sel.y.mean()),
                "n": int(len(sel.y)),
            },
            "final_model_artifact": str(art),
        }

    if include_regression:
        note("secondary: exact multiplier regression")
        summary["exact_multiplier"] = evaluate_regression(
            F, mult, protocol.initial_train_size, n_dev, n, protocol.block_size, warmup, protocol.random_seed)

    if any(s == STATUS_SIGNAL for s in statuses):
        overall = STATUS_SIGNAL
    elif all(s == STATUS_INSUFFICIENT for s in statuses):
        overall = STATUS_INSUFFICIENT
    else:
        overall = STATUS_NO_EDGE
    summary["overall_status"] = overall
    summary["predictions_committed"] = ledger.n_committed
    primary = summary["thresholds"].get(f"{protocol.primary_threshold:g}")
    conclusion = _conclusion(summary, overall, primary, src.is_synthetic)
    _finish(session, run, summary, conclusion)


def _conclusion(summary, overall, primary, synthetic) -> str:
    prefix = "[SYNTHETIC DATA — not real Aviator results] " if synthetic else ""
    signals = [k for k, v in summary["thresholds"].items() if v["status"] == STATUS_SIGNAL]
    if overall == STATUS_SIGNAL:
        txt = (f"Out-of-sample predictive improvement over the expanding-frequency baseline was demonstrated at "
               f"threshold(s) {', '.join(x + 'x' for x in signals)} under the pre-registered criteria.")
    elif overall == STATUS_NO_EDGE:
        txt = ("No model demonstrated a statistically reliable out-of-sample improvement over the "
               "expanding-frequency baseline at any evaluated threshold.")
    else:
        txt = "Evidence is insufficient to evaluate predictive skill."
    if primary:
        c = primary["test"]["comparison_vs_baseline"]
        txt += (f" Primary target {primary['target']}: selected model {primary['selected_model']}, "
                f"Brier skill {c['brier_skill']:+.5f} (95% CI {c['brier_skill_ci_low']:+.5f} to "
                f"{c['brier_skill_ci_high']:+.5f}) over {primary['test']['n']} unseen rounds.")
    return prefix + txt


def _finish(session, run, summary, conclusion):
    run.summary = clean_json(summary)
    run.conclusion = conclusion
    run.status = "completed"
    run.finished_at = datetime.now(timezone.utc)
    log_event(session, "info", "evaluation", f"Evaluation run {run.id} completed: {summary.get('overall_status')}",
              run_id=run.id)
    session.flush()
    try:
        from ..reporting import write_report

        write_report(session, run)
    except Exception as exc:  # report generation must not hide the result
        log_event(session, "warning", "report", f"report generation failed: {exc}", run_id=run.id)


def clean_json(obj):
    """JSON-safe copy: NaN/inf -> None, numpy -> python."""
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items() if not isinstance(v, WalkForwardResult)}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [clean_json(v) for v in obj.tolist()]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


def small_protocol(**overrides) -> EvaluationProtocol:
    """Convenience for tests / small datasets."""
    return replace(DEFAULT_PROTOCOL, **overrides)
