"""SQLAlchemy ORM tables.

Raw imported records (``raw_records``) are stored separately from cleaned,
validated rounds (``rounds``). Predictions and their outcomes live in separate
tables so that recording an outcome can never modify the original prediction.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


SOURCE_KINDS = (
    "historical_export",
    "user_upload",
    "authorized_api",
    "manual",
    "live_feed",
    "synthetic",
)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    platform: Mapped[str | None] = mapped_column(String(200), nullable=True)
    game_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rounds: Mapped[list["Round"]] = relationship(back_populates="source")


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    filename: Mapped[str] = mapped_column(String(500))
    raw_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40))  # committed | rejected
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_accepted: Mapped[int] = mapped_column(Integer, default=0)
    n_rejected: Mapped[int] = mapped_column(Integer, default=0)
    n_warnings: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RawRecord(Base):
    __tablename__ = "raw_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    validation_status: Mapped[str] = mapped_column(String(20))
    flags: Mapped[list] = mapped_column(JSON, default=list)


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("source_id", "round_id", name="uq_round_per_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True)
    round_id: Mapped[str] = mapped_column(String(200))
    # Chronological sequence position within the source (0-based, gap-free).
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    round_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    multiplier: Mapped[float] = mapped_column(Float)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(20), default="valid")  # valid | warning
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    server_seed_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    server_seed: Mapped[str | None] = mapped_column(String(256), nullable=True)
    client_seeds: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[DataSource] = relationship(back_populates="rounds")


class FeatureDefinition(Base):
    __tablename__ = "feature_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    lookback_rounds: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("name", "version", name="uq_feature_version"),)


class EvaluationRun(Base):
    """One full research experiment (walk-forward dev + final test)."""

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40))  # running | completed | failed
    protocol: Mapped[dict] = mapped_column(JSON)
    data_fingerprint: Mapped[str] = mapped_column(String(64))
    code_version: Mapped[str] = mapped_column(String(64))
    n_rounds: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelVersion(Base):
    """Model registry entry."""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(40))
    family: Mapped[str] = mapped_column(String(40))  # baseline | statistical | ml
    target: Mapped[str] = mapped_column(String(100))
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    features: Mapped[list] = mapped_column(JSON, default=list)
    train_start_seq: Mapped[int] = mapped_column(Integer)
    train_end_seq: Mapped[int] = mapped_column(Integer)
    data_cutoff_round_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration: Mapped[str | None] = mapped_column(String(40), nullable=True)
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    model_name: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(100))
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    phase: Mapped[str] = mapped_column(String(20))  # train | validation | test
    n_rounds: Mapped[int] = mapped_column(Integer)
    period_start_seq: Mapped[int] = mapped_column(Integer)
    period_end_seq: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON)


class Prediction(Base):
    """Immutable record of a prediction made *before* the outcome was known."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20))  # walk_forward | live
    phase: Mapped[str | None] = mapped_column(String(20), nullable=True)  # validation | test
    model_name: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(40))
    target_definition: Mapped[str] = mapped_column(String(200))
    threshold: Mapped[float] = mapped_column(Float)
    target_seq: Mapped[int] = mapped_column(Integer, index=True)
    probability: Mapped[float] = mapped_column(Float)
    data_cutoff_seq: Mapped[int] = mapped_column(Integer)
    data_cutoff_round_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signal_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint(
            "run_id", "source_id", "mode", "model_name", "threshold", "target_seq",
            name="uq_one_prediction_per_round",
        ),
    )

    outcome: Mapped["PredictionOutcome | None"] = relationship(back_populates="prediction", uselist=False)


class PredictionOutcome(Base):
    __tablename__ = "prediction_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), unique=True)
    actual_multiplier: Mapped[float] = mapped_column(Float)
    hit: Mapped[bool] = mapped_column(Boolean)
    round_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prediction: Mapped[Prediction] = relationship(back_populates="outcome")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    config: Mapped[dict] = mapped_column(JSON)
    results: Mapped[dict] = mapped_column(JSON)
    audit: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(20))  # info | warning | error
    category: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImmutableRowError(RuntimeError):
    pass


@event.listens_for(Prediction, "before_update")
def _block_prediction_update(_mapper, _conn, _target):
    raise ImmutableRowError("Predictions are immutable once stored.")


@event.listens_for(PredictionOutcome, "before_update")
def _block_outcome_update(_mapper, _conn, _target):
    raise ImmutableRowError("Prediction outcomes are immutable once stored.")


def log_event(session, level: str, category: str, message: str, **details) -> None:
    session.add(SystemEvent(level=level, category=category, message=message, details=details))
