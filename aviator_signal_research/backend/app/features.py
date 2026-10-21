"""Leakage-safe feature engineering.

Every feature for target round ``t`` is computed exclusively from rounds
``0 .. t-1``. This is enforced structurally (every series is shifted by one
round *before* any rolling/expanding aggregation) and verified empirically by
:func:`lookahead_audit`, which runs on every evaluation.

Timestamps: the timestamp of round ``t`` itself is never used. Depending on
the data source it may be the *end* of the round, which would encode the
flight duration and therefore the crash multiplier. Only the timestamp of the
last completed round (``t-1``) is used.

Availability in live operation: all features are available once round
``t-1`` has settled and been ingested, i.e. during the betting phase of
round ``t``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURE_VERSION = "1.0"
FEATURE_THRESHOLDS = (1.5, 2.0, 3.0, 5.0, 10.0)
ROLL_WINDOWS = (10, 50, 200)
BASELINE_WINDOW = 500
N_LAGS = 5
AVAILABLE_AT = "After round t-1 has settled and been ingested; before round t betting closes."


@dataclass(frozen=True)
class FeatureDef:
    name: str
    description: str
    lookback: int  # number of previous rounds required (0 = expanding)
    model_input: bool = True  # False -> used only by baselines / diagnostics


def _tkey(m: float) -> str:
    return f"{m:g}".replace(".", "p")


def feature_definitions(thresholds=FEATURE_THRESHOLDS) -> list[FeatureDef]:
    defs: list[FeatureDef] = []
    for k in range(1, N_LAGS + 1):
        defs.append(FeatureDef(f"lag_logm_{k}", f"log(multiplier) of round t-{k}", k))
    for m in thresholds:
        for k in (1, 2, 3):
            defs.append(FeatureDef(f"lag_hit_{_tkey(m)}_{k}", f"1 if round t-{k} reached {m:g}x", k))
        for w in ROLL_WINDOWS:
            defs.append(FeatureDef(f"rfreq_{_tkey(m)}_{w}", f"share of rounds t-{w}..t-1 reaching {m:g}x", w))
        defs.append(
            FeatureDef(
                f"rfreq_{_tkey(m)}_{BASELINE_WINDOW}",
                f"share of rounds t-{BASELINE_WINDOW}..t-1 reaching {m:g}x (rolling baseline)",
                BASELINE_WINDOW,
                model_input=False,
            )
        )
        defs.append(
            FeatureDef(
                f"efreq_{_tkey(m)}",
                f"Laplace-smoothed share of ALL previous rounds reaching {m:g}x (expanding baseline)",
                0,
                model_input=False,
            )
        )
    for w in ROLL_WINDOWS:
        defs.append(FeatureDef(f"rmean_logm_{w}", f"mean log(multiplier) over rounds t-{w}..t-1", w))
    defs.append(FeatureDef("rmedian_m_50", "median multiplier over rounds t-50..t-1", 50))
    defs.append(FeatureDef("rstd_logm_50", "std of log(multiplier) over rounds t-50..t-1", 50))
    defs.append(FeatureDef("streak_below_2", "consecutive rounds < 2x ending at t-1", 1))
    defs.append(FeatureDef("streak_above_2", "consecutive rounds >= 2x ending at t-1", 1))
    defs.append(FeatureDef("rounds_since_10x", "rounds since last >= 10x (capped at 500)", 1))
    defs.append(FeatureDef("hour_sin", "sin(hour-of-day of round t-1, UTC); 0 if no timestamps", 1))
    defs.append(FeatureDef("hour_cos", "cos(hour-of-day of round t-1, UTC); 0 if no timestamps", 1))
    return defs


def model_feature_names(thresholds=FEATURE_THRESHOLDS) -> list[str]:
    return [d.name for d in feature_definitions(thresholds) if d.model_input]


def max_lookback(thresholds=FEATURE_THRESHOLDS) -> int:
    return max(d.lookback for d in feature_definitions(thresholds))


def _streak(flag: np.ndarray) -> np.ndarray:
    """Length of the run of True values ending at each position (inclusive)."""
    out = np.zeros(len(flag), dtype=float)
    run = 0
    for i, f in enumerate(flag):
        run = run + 1 if f else 0
        out[i] = run
    return out


def build_features(rounds: pd.DataFrame, thresholds=FEATURE_THRESHOLDS, n_extra: int = 0) -> pd.DataFrame:
    """Build the feature frame.

    ``rounds`` must be sorted by sequence with a ``multiplier`` column and an
    optional ``timestamp`` column. Row ``t`` of the result holds features for
    predicting round ``t``. ``n_extra`` appends rows for not-yet-observed
    future rounds (used by the live signal engine to predict round ``n``).
    """
    thresholds = tuple(sorted(set(thresholds) | set(FEATURE_THRESHOLDS)))
    mult = rounds["multiplier"].astype(float).to_numpy()
    if n_extra:
        mult = np.concatenate([mult, np.full(n_extra, np.nan)])
    n = len(mult)
    m = pd.Series(mult)
    logm = np.log(m)
    # Shift FIRST: prev_* at row t describes round t-1.
    prev_logm = logm.shift(1)
    prev_m = m.shift(1)
    feats: dict[str, pd.Series | np.ndarray] = {}

    for k in range(1, N_LAGS + 1):
        feats[f"lag_logm_{k}"] = logm.shift(k)
    for th in thresholds:
        hit = (m >= th).astype(float).where(m.notna())
        prev_hit = hit.shift(1)
        key = _tkey(th)
        for k in (1, 2, 3):
            feats[f"lag_hit_{key}_{k}"] = hit.shift(k)
        for w in ROLL_WINDOWS + (BASELINE_WINDOW,):
            feats[f"rfreq_{key}_{w}"] = prev_hit.rolling(w, min_periods=w).mean()
        cnt = prev_hit.expanding(min_periods=1).count()
        s = prev_hit.expanding(min_periods=1).sum()
        ef = (s + 1.0) / (cnt + 2.0)
        ef.iloc[0] = 0.5
        feats[f"efreq_{key}"] = ef.fillna(0.5)
    for w in ROLL_WINDOWS:
        feats[f"rmean_logm_{w}"] = prev_logm.rolling(w, min_periods=w).mean()
    feats["rmedian_m_50"] = prev_m.rolling(50, min_periods=50).median()
    feats["rstd_logm_50"] = prev_logm.rolling(50, min_periods=50).std()

    below = (m < 2.0).to_numpy()
    above = (m >= 2.0).to_numpy()
    sb = pd.Series(_streak(below)).shift(1)
    sa = pd.Series(_streak(above)).shift(1)
    feats["streak_below_2"] = sb
    feats["streak_above_2"] = sa
    since = np.zeros(n)
    last = None
    for i in range(n):
        since[i] = min(i - last, 500) if last is not None else min(i, 500)
        if not np.isnan(mult[i]) and mult[i] >= 10.0:
            last = i
    feats["rounds_since_10x"] = pd.Series(since)  # since[i] uses rounds < i only
    if "timestamp" in rounds.columns and rounds["timestamp"].notna().all() and len(rounds):
        ts = pd.to_datetime(rounds["timestamp"], utc=True, format="ISO8601")
        hours = ts.dt.hour.to_numpy() + ts.dt.minute.to_numpy() / 60.0
        if n_extra:
            hours = np.concatenate([hours, np.full(n_extra, np.nan)])
        prev_h = pd.Series(hours).shift(1)
        if n_extra:
            prev_h = prev_h.ffill()
        feats["hour_sin"] = np.sin(2 * np.pi * prev_h / 24.0)
        feats["hour_cos"] = np.cos(2 * np.pi * prev_h / 24.0)
    else:
        feats["hour_sin"] = pd.Series(np.zeros(n))
        feats["hour_cos"] = pd.Series(np.zeros(n))

    out = pd.DataFrame({k: np.asarray(v, dtype=float) for k, v in feats.items()})
    out.index = pd.RangeIndex(n, name="seq_pos")
    return out


def target_hit(rounds: pd.DataFrame, threshold: float, tie_counts_as_hit: bool = True) -> np.ndarray:
    """Binary target: did the round reach ``threshold`` before crashing?

    Convention: an auto cash-out at M succeeds iff crash multiplier >= M
    (``tie_counts_as_hit``). Platform rules may differ; configure accordingly.
    """
    m = rounds["multiplier"].astype(float).to_numpy()
    return (m >= threshold if tie_counts_as_hit else m > threshold).astype(int)


def lookahead_audit(rounds: pd.DataFrame, positions: list[int] | None = None, seed: int = 0) -> dict:
    """Empirically verify that features at row t do not depend on round >= t.

    For each tested position t, features are recomputed on a truncated copy in
    which round t's multiplier is replaced with an extreme value and all later
    rounds are removed. Any difference at row t means look-ahead leakage.
    """
    n = len(rounds)
    if n < 3:
        return {"passed": True, "checked_positions": 0, "violations": []}
    rng = np.random.default_rng(seed)
    if positions is None:
        positions = sorted(set(rng.integers(1, n, size=min(25, n - 1)).tolist()) | {n - 1})
    full = build_features(rounds)
    violations = []
    for t in positions:
        trunc = rounds.iloc[: t + 1].copy()
        trunc.iloc[t, trunc.columns.get_loc("multiplier")] = 9999.0 if trunc["multiplier"].iloc[t] < 100 else 1.0
        if "timestamp" in trunc.columns and trunc["timestamp"].notna().all():
            trunc["timestamp"] = pd.to_datetime(trunc["timestamp"], utc=True, format="ISO8601")
            trunc.iloc[t, trunc.columns.get_loc("timestamp")] = trunc["timestamp"].iloc[t] + pd.Timedelta(hours=7)
        f2 = build_features(trunc)
        a = full.iloc[t].to_numpy()
        b = f2.iloc[t].to_numpy()
        same = np.isclose(a, b, equal_nan=True)
        if not same.all():
            violations.append({"position": int(t), "features": list(full.columns[~same])})
    return {"passed": not violations, "checked_positions": len(positions), "violations": violations}
