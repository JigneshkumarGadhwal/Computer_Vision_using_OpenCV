import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_PROTOCOL  # noqa: E402
from dataclasses import replace  # noqa: E402

# Small but statistically meaningful protocol for fast tests.
FAST_PROTOCOL = replace(
    DEFAULT_PROTOCOL,
    thresholds=(2.0, 3.0),
    primary_threshold=2.0,
    initial_train_size=800,
    block_size=300,
    min_test_rounds=300,
    bootstrap_samples=200,
)
FAST_MODELS = ["expanding_frequency", "rolling_frequency_500", "reference_constant", "markov_conditional",
               "hmm_regime", "logistic_l1"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AVR_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("AVR_RAW_DATA_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("AVR_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("AVR_REPORT_DIR", str(tmp_path / "reports"))
    from app.db import init_engine

    init_engine()
    return tmp_path


@pytest.fixture()
def session(env):
    from app.db import session_scope

    with session_scope() as s:
        yield s


def import_synthetic(session, generator="independent", n=2600, seed=3, name=None):
    from app.ingestion.service import commit_import
    from app.synthetic import generate_independent, generate_regime, to_csv_bytes

    df = generate_independent(n, seed=seed) if generator == "independent" else generate_regime(n, seed=seed)
    res = commit_import(session, to_csv_bytes(df), f"{generator}.csv", name or f"syn-{generator}-{seed}", "synthetic")
    session.commit()
    return res
