"""FastAPI application. Interactive docs at /docs (OpenAPI at /openapi.json)."""
from __future__ import annotations

import csv
import io
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..analysis.eda import full_analysis
from ..analysis.fairness import audit_fairness
from ..backtest import DEFAULT_MARGINS, PayoutRules, run_backtest
from ..config import DEFAULT_PROTOCOL, PROJECT_ROOT, EvaluationProtocol, get_settings
from ..db import get_session, init_engine, session_scope
from ..features import AVAILABLE_AT, feature_definitions
from ..ingestion.parsers import ParseError
from ..ingestion.service import ImportError_, add_single_round, commit_import, load_rounds_frame, preview_import
from ..live import feed_status
from ..modeling.estimators import available_models, default_model_names
from ..modeling.evaluation import STATUS_INSUFFICIENT, create_run, run_evaluation
from ..models import (
    BacktestRun,
    DataSource,
    EvaluationRun,
    ImportJob,
    ModelVersion,
    Prediction,
    Round,
    SystemEvent,
    ValidationResult,
)
from ..reporting import render_report
from ..signals import DISCLAIMER, generate_signal, latest_run, resolve_live_outcomes
from ..synthetic import generate_independent, generate_regime, to_csv_bytes

@asynccontextmanager
async def lifespan(_app):
    init_engine()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Aviator Signal Research API",
    version="1.0.0",
    description=(
        "Research platform that tests whether crash-game round data contain out-of-sample predictive "
        "information. It does not place bets and ships no connector to any gambling site."
    ),
)
settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

_running: dict[int, str] = {}  # run_id -> progress message
_lock = threading.Lock()


def _source_or_404(session: Session, source_id: int) -> DataSource:
    src = session.get(DataSource, source_id)
    if src is None:
        raise HTTPException(404, f"source {source_id} not found")
    return src


def _source_dict(session: Session, s: DataSource) -> dict:
    n, first, last = session.execute(
        select(func.count(Round.id), func.min(Round.round_timestamp), func.max(Round.round_timestamp)).where(
            Round.source_id == s.id)
    ).one()
    verified = session.scalar(select(func.count()).select_from(Round).where(Round.source_id == s.id,
                                                                           Round.verified.is_(True)))
    return {
        "id": s.id, "name": s.name, "kind": s.kind, "is_synthetic": s.is_synthetic, "platform": s.platform,
        "game_version": s.game_version, "description": s.description, "created_at": s.created_at.isoformat(),
        "n_rounds": n, "n_verified": verified, "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
    }


# ------------------------------------------------------------------ health
@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "running_evaluations": dict(_running)}


@app.get("/api/config")
def config():
    s = get_settings()
    return {
        "protocol": DEFAULT_PROTOCOL.to_dict(),
        "reference_rtp": s.reference_rtp,
        "reference_note": "P(X>=m) = RTP/m is an UNVERIFIED reference hypothesis, configurable via AVR_REFERENCE_RTP.",
        "stale_after_seconds": s.stale_after_seconds,
        "max_valid_multiplier": s.max_valid_multiplier,
        "available_models": available_models(),
        "default_models": default_model_names(),
        "features": [{"name": d.name, "description": d.description, "lookback_rounds": d.lookback,
                      "model_input": d.model_input} for d in feature_definitions()],
        "feature_available_at": AVAILABLE_AT,
        "disclaimer": DISCLAIMER,
    }


# ----------------------------------------------------------------- sources
@app.get("/api/sources")
def list_sources(session: Session = Depends(get_session)):
    return [_source_dict(session, s) for s in session.scalars(select(DataSource).order_by(DataSource.id))]


@app.get("/api/sources/{source_id}")
def get_source(source_id: int, session: Session = Depends(get_session)):
    return _source_dict(session, _source_or_404(session, source_id))


@app.get("/api/sources/{source_id}/rounds")
def list_rounds(source_id: int, limit: int = Query(100, le=5000), offset: int = 0, newest_first: bool = True,
                session: Session = Depends(get_session)):
    _source_or_404(session, source_id)
    q = select(Round).where(Round.source_id == source_id)
    q = q.order_by(Round.sequence.desc() if newest_first else Round.sequence).offset(offset).limit(limit)
    return [
        {"sequence": r.sequence, "round_id": r.round_id, "multiplier": r.multiplier,
         "timestamp": r.round_timestamp.isoformat() if r.round_timestamp else None,
         "quality_status": r.quality_status, "quality_flags": r.quality_flags, "verified": r.verified,
         "session_id": r.session_id}
        for r in session.scalars(q)
    ]


@app.get("/api/sources/{source_id}/analysis")
def analysis(source_id: int, threshold: float = 2.0, session: Session = Depends(get_session)):
    src = _source_or_404(session, source_id)
    df = load_rounds_frame(session, source_id)
    out = full_analysis(df, get_settings().reference_rtp, threshold)
    out["is_synthetic"] = src.is_synthetic
    out["fairness"] = audit_fairness(df) if len(df) else None
    return out


@app.get("/api/sources/{source_id}/quality")
def quality(source_id: int, session: Session = Depends(get_session)):
    _source_or_404(session, source_id)
    jobs = session.scalars(select(ImportJob).where(ImportJob.source_id == source_id).order_by(ImportJob.id)).all()
    statuses = dict(session.execute(select(Round.quality_status, func.count()).where(
        Round.source_id == source_id).group_by(Round.quality_status)).all())
    return {
        "round_status_counts": statuses,
        "jobs": [{"id": j.id, "filename": j.filename, "status": j.status, "n_rows": j.n_rows,
                  "n_accepted": j.n_accepted, "n_rejected": j.n_rejected, "n_warnings": j.n_warnings,
                  "verdict": j.report.get("verdict"), "flag_counts": j.report.get("flag_counts"),
                  "dataset_warnings": j.report.get("dataset_warnings"), "file_sha256": j.file_sha256,
                  "created_at": j.created_at.isoformat()} for j in jobs],
    }


# ------------------------------------------------------------------ import
def _handle_import_errors(fn):
    try:
        return fn()
    except (ParseError, ImportError_, UnicodeDecodeError) as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...), source_name: str | None = Form(None),
                         default_timezone: str | None = Form(None), session: Session = Depends(get_session)):
    content = await file.read()
    return _handle_import_errors(lambda: preview_import(session, content, file.filename or "upload.csv",
                                                        source_name, default_timezone))


@app.post("/api/import/commit")
async def import_commit(
    file: UploadFile = File(...),
    source_name: str = Form(...),
    source_kind: str = Form("user_upload"),
    default_timezone: str | None = Form(None),
    platform: str | None = Form(None),
    game_version: str | None = Form(None),
    description: str | None = Form(None),
    session: Session = Depends(get_session),
):
    content = await file.read()
    out = _handle_import_errors(lambda: commit_import(
        session, content, file.filename or "upload.csv", source_name, source_kind,
        default_timezone=default_timezone, platform=platform, game_version=game_version, description=description))
    session.commit()
    return out


class DemoRequest(BaseModel):
    generator: str = Field("independent", pattern="^(independent|regime)$")
    n_rounds: int = Field(6000, ge=100, le=200_000)
    seed: int = 7
    source_name: str | None = None


@app.post("/api/demo/load")
def load_demo(req: DemoRequest, session: Session = Depends(get_session)):
    df = generate_independent(req.n_rounds, req.seed) if req.generator == "independent" else generate_regime(
        req.n_rounds, req.seed)
    name = req.source_name or f"SYNTHETIC demo ({req.generator}, seed {req.seed})"
    out = _handle_import_errors(lambda: commit_import(
        session, to_csv_bytes(df), f"synthetic_{req.generator}_{req.seed}.csv", name, "synthetic",
        description=f"Synthetic {req.generator} data generated by the app. NOT real Aviator results."))
    session.commit()
    return out


# -------------------------------------------------------------------- live
class RoundIn(BaseModel):
    multiplier: float | str
    round_id: str | None = None
    timestamp: datetime | None = None
    session_id: str | None = None
    verified: bool = False
    source_kind: str = Field("manual", pattern="^(manual|live_feed|authorized_api|public_observation)$")


@app.post("/api/live/{source_name}/rounds")
def add_round(source_name: str, body: RoundIn, session: Session = Depends(get_session)):
    try:
        r = add_single_round(session, source_name, body.source_kind, body.multiplier, body.round_id, body.timestamp,
                             body.session_id, body.verified)
    except ImportError_ as exc:
        raise HTTPException(422, str(exc))
    resolved = resolve_live_outcomes(session, r.source_id)
    session.commit()
    return {"source_id": r.source_id, "sequence": r.sequence, "round_id": r.round_id, "multiplier": r.multiplier,
            "resolved_predictions": resolved}


@app.get("/api/live/status")
def all_feed_status(session: Session = Depends(get_session)):
    return [feed_status(session, s) for s in session.scalars(select(DataSource).order_by(DataSource.id))]


@app.get("/api/live/status/{source_id}")
def one_feed_status(source_id: int, session: Session = Depends(get_session)):
    return feed_status(session, _source_or_404(session, source_id))


# ------------------------------------------------------------- evaluation
class EvaluationRequest(BaseModel):
    source_id: int
    thresholds: list[float] | None = None
    primary_threshold: float | None = None
    dev_fraction: float | None = Field(None, gt=0.3, lt=0.95)
    initial_train_size: int | None = Field(None, ge=300)
    block_size: int | None = Field(None, ge=20)
    min_test_rounds: int | None = Field(None, ge=50)
    models: list[str] | None = None
    include_regression: bool = True


def _bg_evaluate(run_id: int, source_id: int, protocol: EvaluationProtocol, models, include_regression):
    def progress(msg):
        with _lock:
            _running[run_id] = msg

    try:
        with session_scope() as s:
            run = s.get(EvaluationRun, run_id)
            run_evaluation(s, source_id, protocol, models, run=run, progress=progress,
                           include_regression=include_regression)
    finally:
        with _lock:
            _running.pop(run_id, None)


@app.post("/api/evaluations", status_code=202)
def start_evaluation(req: EvaluationRequest, background: BackgroundTasks, session: Session = Depends(get_session)):
    _source_or_404(session, req.source_id)
    overrides = {k: v for k, v in {
        "thresholds": tuple(req.thresholds) if req.thresholds else None,
        "primary_threshold": req.primary_threshold, "dev_fraction": req.dev_fraction,
        "initial_train_size": req.initial_train_size, "block_size": req.block_size,
        "min_test_rounds": req.min_test_rounds}.items() if v is not None}
    protocol = replace(DEFAULT_PROTOCOL, **overrides)
    if any(t <= 1.0 for t in protocol.thresholds):
        raise HTTPException(422, "thresholds must be > 1.0")
    if protocol.primary_threshold not in protocol.thresholds:
        protocol = replace(protocol, primary_threshold=protocol.thresholds[0])
    models = req.models or default_model_names()
    unknown = [m for m in models if m not in available_models()]
    if unknown:
        raise HTTPException(422, f"unknown models: {unknown}")
    if "expanding_frequency" not in models:
        models = ["expanding_frequency", *models]
    run = create_run(session, req.source_id, protocol)
    _running[run.id] = "queued"
    background.add_task(_bg_evaluate, run.id, req.source_id, protocol, models, req.include_regression)
    return {"run_id": run.id, "status": "running"}


def _run_brief(r: EvaluationRun) -> dict:
    return {"id": r.id, "source_id": r.source_id, "is_synthetic": r.is_synthetic, "status": r.status,
            "progress": _running.get(r.id), "n_rounds": r.n_rounds, "conclusion": r.conclusion,
            "overall_status": (r.summary or {}).get("overall_status"), "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None, "error": r.error}


@app.get("/api/evaluations")
def list_evaluations(source_id: int | None = None, session: Session = Depends(get_session)):
    q = select(EvaluationRun).order_by(EvaluationRun.id.desc())
    if source_id is not None:
        q = q.where(EvaluationRun.source_id == source_id)
    return [_run_brief(r) for r in session.scalars(q)]


@app.get("/api/evaluations/{run_id}")
def get_evaluation(run_id: int, session: Session = Depends(get_session)):
    r = session.get(EvaluationRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return {**_run_brief(r), "protocol": r.protocol, "summary": r.summary, "data_fingerprint": r.data_fingerprint,
            "code_version": r.code_version}


@app.get("/api/evaluations/{run_id}/report", response_class=PlainTextResponse)
def evaluation_report(run_id: int, session: Session = Depends(get_session)):
    r = session.get(EvaluationRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    bts = session.scalars(select(BacktestRun).where(BacktestRun.run_id == run_id)).all()
    return render_report(r, bts)


@app.get("/api/evaluations/{run_id}/results")
def evaluation_results(run_id: int, session: Session = Depends(get_session)):
    rows = session.scalars(select(ValidationResult).where(ValidationResult.run_id == run_id)).all()
    return [{"model_name": v.model_name, "target": v.target, "threshold": v.threshold, "phase": v.phase,
             "n_rounds": v.n_rounds, "period_start_seq": v.period_start_seq, "period_end_seq": v.period_end_seq,
             "metrics": v.metrics} for v in rows]


# ------------------------------------------------------------------ models
@app.get("/api/models")
def model_registry(source_id: int | None = None, run_id: int | None = None, selected_only: bool = False,
                   session: Session = Depends(get_session)):
    q = select(ModelVersion).order_by(ModelVersion.id.desc())
    if source_id is not None:
        q = q.where(ModelVersion.source_id == source_id)
    if run_id is not None:
        q = q.where(ModelVersion.run_id == run_id)
    if selected_only:
        q = q.where(ModelVersion.is_selected.is_(True))
    return [{"id": m.id, "run_id": m.run_id, "source_id": m.source_id, "name": m.name, "version": m.version,
             "family": m.family, "target": m.target, "threshold": m.threshold, "params": m.params,
             "features": m.features, "train_start_seq": m.train_start_seq, "train_end_seq": m.train_end_seq,
             "data_cutoff_round_id": m.data_cutoff_round_id, "artifact_path": m.artifact_path,
             "artifact_sha256": m.artifact_sha256, "calibration": m.calibration, "random_seed": m.random_seed,
             "is_selected": m.is_selected, "created_at": m.created_at.isoformat()}
            for m in session.scalars(q.limit(1000))]


# ----------------------------------------------------------------- signals
@app.get("/api/signals/{source_id}")
def signal(source_id: int, threshold: float = 2.0, persist: bool = True, session: Session = Depends(get_session)):
    _source_or_404(session, source_id)
    out = generate_signal(session, source_id, threshold, persist=persist)
    session.commit()
    return out


# ------------------------------------------------------------- predictions
def _pred_query(source_id, mode, run_id, phase, model_name, threshold):
    q = select(Prediction).order_by(Prediction.target_seq.desc(), Prediction.id.desc())
    for col, val in ((Prediction.source_id, source_id), (Prediction.mode, mode), (Prediction.run_id, run_id),
                     (Prediction.phase, phase), (Prediction.model_name, model_name),
                     (Prediction.threshold, threshold)):
        if val is not None:
            q = q.where(col == val)
    return q


def _pred_dict(p: Prediction) -> dict:
    o = p.outcome
    return {"id": p.id, "run_id": p.run_id, "source_id": p.source_id, "mode": p.mode, "phase": p.phase,
            "model_name": p.model_name, "model_version": p.model_version, "target_definition": p.target_definition,
            "threshold": p.threshold, "target_seq": p.target_seq, "probability": p.probability,
            "data_cutoff_seq": p.data_cutoff_seq, "data_cutoff_round_id": p.data_cutoff_round_id,
            "signal_status": p.signal_status, "content_hash": p.content_hash, "created_at": p.created_at.isoformat(),
            "outcome_multiplier": o.actual_multiplier if o else None, "outcome_hit": o.hit if o else None,
            "outcome_recorded_at": o.recorded_at.isoformat() if o else None}


@app.get("/api/predictions")
def list_predictions(source_id: int | None = None, mode: str | None = None, run_id: int | None = None,
                     phase: str | None = None, model_name: str | None = None, threshold: float | None = None,
                     limit: int = Query(200, le=5000), session: Session = Depends(get_session)):
    q = _pred_query(source_id, mode, run_id, phase, model_name, threshold).limit(limit)
    return [_pred_dict(p) for p in session.scalars(q)]


@app.get("/api/predictions/export.csv")
def export_predictions(source_id: int | None = None, mode: str | None = None, run_id: int | None = None,
                       phase: str | None = None, model_name: str | None = None, threshold: float | None = None,
                       session: Session = Depends(get_session)):
    rows = [_pred_dict(p) for p in session.scalars(_pred_query(source_id, mode, run_id, phase, model_name, threshold))]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=predictions.csv"})


# --------------------------------------------------------------- backtests
class BacktestRequest(BaseModel):
    run_id: int
    threshold: float = 2.0
    stake: float = Field(1.0, gt=0)
    margins: list[float] = list(DEFAULT_MARGINS)
    tie_counts_as_win: bool = True
    max_net_win: float | None = None
    fee_per_bet: float = 0.0
    decision_latency_s: float | None = None


def _bt_dict(b: BacktestRun, include_bets: bool = False) -> dict:
    res = dict(b.results)
    if not include_bets:
        res.pop("bets", None)
    return {"id": b.id, "run_id": b.run_id, "config": b.config, "results": res, "audit": b.audit,
            "created_at": b.created_at.isoformat()}


@app.post("/api/backtests")
def create_backtest(req: BacktestRequest, session: Session = Depends(get_session)):
    try:
        bt = run_backtest(session, req.run_id, req.threshold, req.stake, tuple(req.margins) or DEFAULT_MARGINS,
                          PayoutRules(req.tie_counts_as_win, req.max_net_win, req.fee_per_bet), req.decision_latency_s)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    return _bt_dict(bt)


@app.get("/api/backtests")
def list_backtests(run_id: int | None = None, session: Session = Depends(get_session)):
    q = select(BacktestRun).order_by(BacktestRun.id.desc())
    if run_id is not None:
        q = q.where(BacktestRun.run_id == run_id)
    return [_bt_dict(b) for b in session.scalars(q)]


@app.get("/api/backtests/{bt_id}")
def get_backtest(bt_id: int, include_bets: bool = False, session: Session = Depends(get_session)):
    b = session.get(BacktestRun, bt_id)
    if b is None:
        raise HTTPException(404, "backtest not found")
    return _bt_dict(b, include_bets)


@app.get("/api/backtests/{bt_id}/bets.csv")
def export_bets(bt_id: int, session: Session = Depends(get_session)):
    b = session.get(BacktestRun, bt_id)
    if b is None:
        raise HTTPException(404, "backtest not found")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["policy", "target_seq", "probability_used", "crash_multiplier", "net_pnl"])
    for policy, bets in b.results.get("bets", {}).items():
        for row in bets:
            w.writerow([policy, *row])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=backtest_{bt_id}_bets.csv"})


# ---------------------------------------------------------------- overview
@app.get("/api/overview/{source_id}")
def overview(source_id: int, threshold: float | None = None, session: Session = Depends(get_session)):
    src = _source_or_404(session, source_id)
    info = _source_dict(session, src)
    run = latest_run(session, source_id)
    th = threshold or (run.protocol.get("primary_threshold") if run else DEFAULT_PROTOCOL.primary_threshold)
    last = session.scalars(select(Round).where(Round.source_id == source_id).order_by(Round.sequence.desc()).limit(1)).first()
    out = {
        "source": info,
        "feed": feed_status(session, src),
        "selected_threshold": th,
        "most_recent_observation": {"round_id": last.round_id, "multiplier": last.multiplier,
                                    "timestamp": last.round_timestamp.isoformat() if last and last.round_timestamp else None}
        if last else None,
        "latest_run": _run_brief(run) if run else None,
        "model_status": STATUS_INSUFFICIENT,
        "demonstrated_improvement": False,
    }
    t = (run.summary or {}).get("thresholds", {}).get(f"{th:g}") if run else None
    if t:
        m = t["test"]["metrics"]
        out.update(
            model_status=t["status"],
            demonstrated_improvement=t["status"] == "RESEARCH SIGNAL",
            selected_model=t["selected_model"],
            baseline_performance=m["expanding_frequency"],
            model_performance=m[t["selected_model"]],
            comparison=t["test"]["comparison_vs_baseline"],
            calibration_reason=next((r for r in t["status_reasons"] if r.startswith("calibration")), None),
            n_test_rounds=t["test"]["n"],
            test_period=run.summary.get("test_period"),
        )
    return out


@app.get("/api/system/events")
def events(limit: int = Query(100, le=1000), session: Session = Depends(get_session)):
    return [{"id": e.id, "level": e.level, "category": e.category, "message": e.message, "details": e.details,
             "created_at": e.created_at.isoformat()}
            for e in session.scalars(select(SystemEvent).order_by(SystemEvent.id.desc()).limit(limit))]


@app.get("/api/system/integrity")
def integrity(session: Session = Depends(get_session)):
    """Ledger-wide checks that predictions preceded their outcomes."""
    bad_order = session.execute(text(
        """SELECT COUNT(*) FROM predictions p JOIN prediction_outcomes o ON o.prediction_id = p.id
           WHERE o.recorded_at < p.created_at""")).scalar()
    bad_cutoff = session.execute(text("SELECT COUNT(*) FROM predictions WHERE data_cutoff_seq >= target_seq")).scalar()
    n_pred = session.execute(text("SELECT COUNT(*) FROM predictions")).scalar()
    n_out = session.execute(text("SELECT COUNT(*) FROM prediction_outcomes")).scalar()
    return {"predictions": n_pred, "outcomes": n_out, "outcome_before_prediction": bad_order,
            "cutoff_not_before_target": bad_cutoff, "passed": bad_order == 0 and bad_cutoff == 0}


class ReferenceRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=10)
    render: str = Field("auto", pattern="^(auto|never|always)$")
    follow_doc_links: bool = True
    max_pages: int = Field(6, ge=1, le=20)


@app.post("/api/reference/collect")
def reference_collect(req: ReferenceRequest):
    """Collect official documentation pages (robots.txt obeyed, rate-limited, no anti-bot bypass)."""
    from ..collectors.policy import AccessPolicy
    from ..collectors.reference import collect_many

    bad = [u for u in req.urls if not u.startswith(("http://", "https://"))]
    if bad:
        raise HTTPException(422, f"invalid URLs: {bad}")
    out = collect_many(req.urls, AccessPolicy(), render=req.render, follow_doc_links=req.follow_doc_links,
                       max_pages=req.max_pages)
    with session_scope() as s:
        from ..models import log_event

        log_event(s, "warning" if out["errors"] else "info", "reference",
                  f"Reference collection: {len(out['documents'])} document(s), {len(out['errors'])} error(s)",
                  urls=req.urls, errors=out["errors"])
    return out


@app.get("/api/reference")
def reference_list():
    from ..collectors.reference import list_reference

    return list_reference()


@app.get("/api/docs/{name}", response_class=PlainTextResponse)
def project_doc(name: str):
    allowed = {"methodology": "docs/METHODOLOGY.md", "limitations": "docs/LIMITATIONS.md",
               "collection": "docs/COLLECTION.md",
               "api": "docs/API.md", "troubleshooting": "docs/TROUBLESHOOTING.md", "readme": "README.md"}
    if name not in allowed:
        raise HTTPException(404, "unknown document")
    path = PROJECT_ROOT / allowed[name]
    if not path.exists():
        raise HTTPException(404, "document missing")
    return path.read_text()
