"""Synthetic data generators with KNOWN properties.

These are used for the demo mode and for testing the evaluation framework.
Synthetic data is never real Aviator data; every generated round id carries a
``SYN`` prefix and sources created from it are flagged ``is_synthetic``.

Generators
----------
independent
    i.i.d. rounds following the reference model P(X >= m) = rtp / m.
    No model can beat the baseline here except by chance; the framework must
    report NO DEMONSTRATED EDGE.
regime
    A hidden two-state Markov chain switches between a "hot" and a "cold"
    payout level. Outcomes are serially dependent, so recent frequencies carry
    real information. The framework should detect this planted signal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

SYNTHETIC_LABEL = "SYNTHETIC_DEMO_NOT_REAL"


def _crash_from_uniform(u: np.ndarray, rtp: np.ndarray | float) -> np.ndarray:
    """Inverse-CDF sampling: P(X >= m) = rtp/m for m >= 1, 2-decimal floor."""
    x = np.floor(100.0 * np.asarray(rtp) / u) / 100.0
    return np.clip(x, 1.0, 1_000_000.0)


def _timestamps(mult: np.ndarray, start: datetime, rng: np.random.Generator) -> list[datetime]:
    # Betting phase ~8s, flight time grows with log(multiplier).
    durations = 8.0 + np.log(mult) / 0.07 + rng.uniform(0.0, 2.0, size=len(mult))
    t = start
    out = []
    for d in durations:
        out.append(t)
        t = t + timedelta(seconds=float(d))
    return out


def generate_independent(n: int, seed: int = 7, rtp: float = 0.97, start: datetime | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    u = rng.uniform(1e-12, 1.0, size=n)
    mult = _crash_from_uniform(u, rtp)
    return _frame(mult, rng, start, generator="independent")


def generate_regime(
    n: int,
    seed: int = 11,
    rtp_hot: float = 1.25,
    rtp_cold: float = 0.75,
    stay_prob: float = 0.98,
    start: datetime | None = None,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    state = np.empty(n, dtype=int)
    s = 0
    for i in range(n):
        if rng.uniform() > stay_prob:
            s = 1 - s
        state[i] = s
    rtp = np.where(state == 1, rtp_hot, rtp_cold)
    u = rng.uniform(1e-12, 1.0, size=n)
    mult = _crash_from_uniform(u, rtp)
    df = _frame(mult, rng, start, generator="regime")
    df["hidden_state_DO_NOT_USE_AS_FEATURE"] = state
    return df


def _frame(mult: np.ndarray, rng: np.random.Generator, start: datetime | None, generator: str) -> pd.DataFrame:
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts = _timestamps(mult, start, rng)
    return pd.DataFrame(
        {
            "round_id": [f"SYN-{i:07d}" for i in range(1, len(mult) + 1)],
            "timestamp": [t.isoformat(timespec="milliseconds") for t in ts],
            "multiplier": mult,
            "session_id": "synthetic-session-1",
            "data_label": SYNTHETIC_LABEL,
            "generator": generator,
        }
    )


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    cols = [c for c in df.columns if c != "hidden_state_DO_NOT_USE_AS_FEATURE"]
    return df[cols].to_csv(index=False).encode()
