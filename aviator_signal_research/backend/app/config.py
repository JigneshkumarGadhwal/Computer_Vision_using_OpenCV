"""Application configuration.

All settings come from environment variables (prefix ``AVR_``) so that no
secrets or deployment-specific paths are hard-coded. Defaults are chosen for a
local SQLite prototype.

The *pre-registered evaluation protocol* (signal criteria, split fractions,
thresholds) also lives here. Changing these values after looking at final-test
results invalidates the "pre-registered" claim; the values used for each run
are stored alongside the run in the model registry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"AVR_{name}", default)


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_list(name: str, default: list[float]) -> list[float]:
    raw = os.environ.get(f"AVR_{name}")
    if not raw:
        return list(default)
    return [float(x) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class EvaluationProtocol:
    """Pre-registered evaluation protocol. Stored with every evaluation run."""

    thresholds: tuple[float, ...] = (1.5, 2.0, 3.0, 5.0)
    primary_threshold: float = 2.0
    # Chronological split: first `dev_fraction` of rounds for walk-forward
    # model development/selection; the rest is the untouched final test.
    dev_fraction: float = 0.7
    # Walk-forward inside the development period.
    initial_train_size: int = 1000
    block_size: int = 250  # rounds predicted per fold (retrain schedule)
    # Purge gap between train end and first predicted round. Features use at
    # most `max_lookback` previous rounds, so no gap is strictly needed for
    # leakage, but a gap guards against duplicated/overlapping sessions.
    purge_gap: int = 0
    # Signal criteria (fixed before testing).
    alpha: float = 0.05
    min_test_rounds: int = 500
    # Calibration gate. ECE is reported but NOT used as a gate: its noise floor
    # for a perfectly calibrated model at n=1000 is ~0.035, so a fixed ECE cut
    # would reject good models on small samples. Instead we require
    #   * Hosmer-Lemeshow (10 equal-frequency groups, df=10, out-of-sample)
    #     p >= calibration_alpha  (no evidence of miscalibration), and
    #   * |mean predicted - observed rate| <= max_calibration_gap.
    calibration_alpha: float = 0.05
    max_calibration_gap: float = 0.03
    min_brier_skill: float = 0.0  # BSS must be strictly greater than this
    bootstrap_samples: int = 1000
    bootstrap_block: int = 50
    random_seed: int = 20260101

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'aviator_research.db'}"
        )
    )
    raw_data_dir: Path = field(
        default_factory=lambda: Path(_env("RAW_DATA_DIR", str(PROJECT_ROOT / "data" / "raw")))
    )
    model_artifact_dir: Path = field(
        default_factory=lambda: Path(_env("MODEL_DIR", str(PROJECT_ROOT / "data" / "models")))
    )
    report_dir: Path = field(
        default_factory=lambda: Path(_env("REPORT_DIR", str(PROJECT_ROOT / "reports")))
    )
    # Reference hypothesis for the generating distribution:
    #   P(X >= m) = rtp / m   for m >= 1
    # This is a commonly described crash-game model, NOT verified for any
    # specific operator. Configure per platform once documented.
    reference_rtp: float = field(default_factory=lambda: _env_float("REFERENCE_RTP", 0.97))
    # A live feed is considered stale if no new round arrives in this window.
    stale_after_seconds: int = field(default_factory=lambda: _env_int("STALE_AFTER_SECONDS", 120))
    cors_origins: list[str] = field(
        default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    )
    max_valid_multiplier: float = field(default_factory=lambda: _env_float("MAX_VALID_MULTIPLIER", 1_000_000.0))
    suspicious_repeat_run: int = field(default_factory=lambda: _env_int("SUSPICIOUS_REPEAT_RUN", 5))


def get_settings() -> Settings:
    return Settings()


DEFAULT_PROTOCOL = EvaluationProtocol(
    thresholds=tuple(_env_list("THRESHOLDS", [1.5, 2.0, 3.0, 5.0])),
    primary_threshold=_env_float("PRIMARY_THRESHOLD", 2.0),
)
