"""Reproducible exploratory analysis and statistical diagnostics.

Every test here is descriptive. A statistically significant anomaly is NOT an
exploitable predictive signal; only the walk-forward evaluation can establish
out-of-sample predictive value.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox

from ..modeling.metrics import holm_adjust, wilson

EDA_THRESHOLDS = (1.2, 1.5, 2.0, 3.0, 5.0, 10.0)
GOF_EDGES = (1.0, 1.2, 1.5, 2.0, 3.0, 5.0, 10.0, math.inf)


def reference_survival(m: float, rtp: float) -> float:
    """Reference hypothesis P(X >= m) = rtp/m (and 1 at m = 1)."""
    return 1.0 if m <= 1.0 else min(1.0, rtp / m)


def _downsample(seq, values, max_points: int = 600):
    n = len(values)
    if n <= max_points:
        idx = np.arange(n)
    else:
        idx = np.linspace(0, n - 1, max_points).astype(int)
    return [{"seq": int(seq[i]), "value": (None if pd.isna(values[i]) else float(values[i]))} for i in idx]


def summary(m: np.ndarray) -> dict:
    q = np.percentile(m, [1, 5, 10, 25, 50, 75, 90, 95, 99])
    return {
        "n": int(len(m)),
        "min": float(m.min()),
        "max": float(m.max()),
        "median": float(np.median(m)),
        # The mean of a heavy-tailed multiplier is dominated by rare outliers;
        # report it together with the trimmed mean and log-mean.
        "mean": float(m.mean()),
        "trimmed_mean_1pct": float(stats.trim_mean(m, 0.01)),
        "mean_log": float(np.log(m).mean()),
        "quantiles": {k: float(v) for k, v in zip(["p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99"], q)},
        "share_exactly_1": float((m <= 1.0).mean()),
    }


def threshold_frequencies(m: np.ndarray, rtp: float) -> list[dict]:
    n = len(m)
    out = []
    for th in EDA_THRESHOLDS:
        k = int((m >= th).sum())
        lo, hi = wilson(k, n)
        ref = reference_survival(th, rtp)
        # Two-sided exact binomial test against the reference probability.
        p = stats.binomtest(k, n, ref).pvalue if n else None
        out.append(
            {
                "threshold": th,
                "share_reaching": k / n if n else None,
                "share_below": 1 - k / n if n else None,
                "ci_low": lo,
                "ci_high": hi,
                "reference_share_reaching": ref,
                "implied_rtp": th * k / n if n else None,
                "binomial_p_vs_reference": float(p) if p is not None else None,
            }
        )
    adj = holm_adjust([r["binomial_p_vs_reference"] for r in out])
    for r, a in zip(out, adj):
        r["binomial_p_holm"] = a
    return out


def goodness_of_fit(m: np.ndarray, rtp: float) -> dict:
    edges = GOF_EDGES
    obs = np.array([((m >= a) & (m < b)).sum() for a, b in zip(edges[:-1], edges[1:])], dtype=float)
    surv = [reference_survival(e, rtp) if np.isfinite(e) else 0.0 for e in edges]
    # P(1 <= X < 1.2) includes the instant-crash mass.
    probs = np.array([surv[i] - surv[i + 1] for i in range(len(edges) - 1)])
    exp = probs * len(m)
    chi2 = float(((obs - exp) ** 2 / np.where(exp > 0, exp, 1)).sum())
    dof = len(obs) - 1
    return {
        "bins": [
            {"from": a, "to": (None if not np.isfinite(b) else b), "observed": int(o), "expected": float(e)}
            for a, b, o, e in zip(edges[:-1], edges[1:], obs, exp)
        ],
        "chi2": chi2,
        "dof": dof,
        "p_value": float(1 - stats.chi2.cdf(chi2, dof)),
        "reference": f"P(X>=m) = {rtp}/m (UNVERIFIED reference hypothesis)",
    }


def runs_test(b: np.ndarray) -> dict:
    """Wald-Wolfowitz runs test for randomness of a binary sequence."""
    n1 = int(b.sum())
    n0 = len(b) - n1
    if n1 == 0 or n0 == 0:
        return {"runs": None, "z": None, "p_value": None}
    runs = 1 + int((b[1:] != b[:-1]).sum())
    mu = 2 * n1 * n0 / (n1 + n0) + 1
    var = (mu - 1) * (mu - 2) / (n1 + n0 - 1)
    z = (runs - mu) / math.sqrt(var)
    return {"runs": runs, "expected_runs": mu, "z": float(z), "p_value": float(2 * (1 - stats.norm.cdf(abs(z))))}


def streak_analysis(m: np.ndarray, threshold: float = 2.0, max_len: int = 15) -> dict:
    below = m < threshold
    lengths = []
    run = 0
    for v in below:
        if v:
            run += 1
        elif run:
            lengths.append(run)
            run = 0
    if run:
        lengths.append(run)
    lengths = np.array(lengths)
    q = below.mean()  # P(below)
    dist = []
    for L in range(1, max_len + 1):
        obs = int((lengths == L).sum()) if len(lengths) else 0
        # Under independence, run lengths are geometric: P(L) = q^(L-1) (1-q)
        exp = len(lengths) * (q ** (L - 1)) * (1 - q)
        dist.append({"length": L, "observed": obs, "expected_if_independent": float(exp)})
    # Conditional probability of reaching threshold after k consecutive misses.
    cond = []
    streak = np.zeros(len(m), dtype=int)
    for i in range(1, len(m)):
        streak[i] = streak[i - 1] + 1 if below[i - 1] else 0
    hit = ~below
    for k in range(0, 8):
        mask = streak[1:] == k if k < 7 else streak[1:] >= 7
        n = int(mask.sum())
        kk = int(hit[1:][mask].sum())
        lo, hi = wilson(kk, n)
        cond.append(
            {
                "after_misses": k if k < 7 else "7+",
                "n": n,
                "p_hit": kk / n if n else None,
                "ci_low": lo,
                "ci_high": hi,
            }
        )
    return {
        "threshold": threshold,
        "n_streaks": int(len(lengths)),
        "longest_below_streak": int(lengths.max()) if len(lengths) else 0,
        "length_distribution": dist,
        "conditional_after_misses": cond,
    }


def autocorrelation(m: np.ndarray, threshold: float = 2.0, lags: int = 20) -> dict:
    logm = np.log(m)
    hit = (m >= threshold).astype(float)
    out = {}
    for name, x in (("log_multiplier", logm), (f"hit_{threshold:g}", hit)):
        xc = x - x.mean()
        denom = float(xc @ xc)
        acf = [float(xc[k:] @ xc[:-k] / denom) if denom > 0 else 0.0 for k in range(1, lags + 1)]
        lb = acorr_ljungbox(x, lags=[5, 10, lags], return_df=True)
        out[name] = {
            "acf": acf,
            "conf_band_95": float(1.96 / math.sqrt(len(x))),
            "ljung_box": [
                {"lag": int(l), "stat": float(r.lb_stat), "p_value": float(r.lb_pvalue)} for l, r in lb.iterrows()
            ],
        }
    # Spearman rank autocorrelation (robust to heavy tails)
    rho, p = stats.spearmanr(m[:-1], m[1:])
    out["spearman_lag1"] = {"rho": float(rho), "p_value": float(p)}
    return out


def drift_analysis(df: pd.DataFrame, threshold: float = 2.0, n_blocks: int = 10) -> dict:
    m = df["multiplier"].to_numpy(float)
    hit = (m >= threshold).astype(int)
    blocks = np.array_split(np.arange(len(m)), n_blocks)
    rows = []
    for i, b in enumerate(blocks):
        if not len(b):
            continue
        k = int(hit[b].sum())
        lo, hi = wilson(k, len(b))
        rows.append(
            {
                "block": i,
                "start_seq": int(df["sequence"].iloc[b[0]]),
                "end_seq": int(df["sequence"].iloc[b[-1]]),
                "n": int(len(b)),
                "share_reaching": k / len(b),
                "ci_low": lo,
                "ci_high": hi,
                "median": float(np.median(m[b])),
            }
        )
    table = np.array([[r["share_reaching"] * r["n"], (1 - r["share_reaching"]) * r["n"]] for r in rows])
    chi2, p, _, _ = stats.chi2_contingency(table) if len(rows) > 1 else (None, None, None, None)
    half = len(m) // 2
    ks = stats.ks_2samp(m[:half], m[half:]) if half > 10 else None
    # CUSUM change-point statistic on the hit indicator.
    c = np.cumsum(hit - hit.mean())
    cp = int(np.argmax(np.abs(c)))
    # Approximate p-value via Kolmogorov distribution of the scaled max.
    sigma = hit.std() or 1.0
    stat = float(np.abs(c).max() / (sigma * math.sqrt(len(m))))
    cusum_p = float(stats.kstwobign.sf(stat))
    return {
        "threshold": threshold,
        "blocks": rows,
        "homogeneity_chi2": float(chi2) if chi2 is not None else None,
        "homogeneity_p": float(p) if p is not None else None,
        "ks_first_vs_second_half": {"stat": float(ks.statistic), "p_value": float(ks.pvalue)} if ks else None,
        "cusum": {"change_point_seq": int(df["sequence"].iloc[cp]), "statistic": stat, "p_value": cusum_p},
    }


def time_of_day(df: pd.DataFrame, threshold: float = 2.0) -> dict | None:
    if "timestamp" not in df.columns or df["timestamp"].isna().any() or not len(df):
        return None
    hours = pd.to_datetime(df["timestamp"], utc=True).dt.hour.to_numpy()
    hit = (df["multiplier"].to_numpy(float) >= threshold).astype(int)
    rows = []
    table = []
    for h in range(24):
        mask = hours == h
        n = int(mask.sum())
        if n == 0:
            continue
        k = int(hit[mask].sum())
        lo, hi = wilson(k, n)
        rows.append({"hour_utc": h, "n": n, "share_reaching": k / n, "ci_low": lo, "ci_high": hi})
        table.append([k, n - k])
    if len(table) > 1:
        chi2, p, _, _ = stats.chi2_contingency(np.array(table))
    else:
        chi2, p = None, None
    return {"threshold": threshold, "by_hour": rows, "chi2": None if chi2 is None else float(chi2),
            "p_value": None if p is None else float(p)}


def session_effects(df: pd.DataFrame, threshold: float = 2.0) -> dict | None:
    if "session_id" not in df.columns or df["session_id"].isna().all():
        return None
    s = df["session_id"].fillna("unknown")
    if s.nunique() < 2:
        return {"n_sessions": int(s.nunique()), "note": "single session: no between-session comparison"}
    hit = (df["multiplier"] >= threshold).astype(int)
    tab = pd.crosstab(s, hit)
    chi2, p, _, _ = stats.chi2_contingency(tab.to_numpy())
    return {"n_sessions": int(s.nunique()), "chi2": float(chi2), "p_value": float(p)}


def full_analysis(df: pd.DataFrame, rtp: float = 0.97, primary_threshold: float = 2.0) -> dict:
    if len(df) < 30:
        return {"error": "insufficient data", "n": int(len(df))}
    m = df["multiplier"].to_numpy(float)
    hit = (m >= primary_threshold).astype(int)
    roll = pd.Series(hit).rolling(200, min_periods=50).mean().to_numpy()
    seq = df["sequence"].to_numpy()
    hist_edges = np.concatenate([np.arange(1.0, 3.0, 0.1), [3, 4, 5, 7.5, 10, 20, 50, 100, np.inf]])
    counts = [int(((m >= a) & (m < b)).sum()) for a, b in zip(hist_edges[:-1], hist_edges[1:])]
    tests = {
        "runs_test": runs_test(hit),
    }
    ac = autocorrelation(m, primary_threshold)
    drift = drift_analysis(df, primary_threshold)
    tod = time_of_day(df, primary_threshold)
    # Family of dependence/drift tests, Holm-adjusted together.
    family = {
        "runs_test": tests["runs_test"]["p_value"],
        "ljung_box_log_lag10": ac["log_multiplier"]["ljung_box"][1]["p_value"],
        "ljung_box_hit_lag10": ac[f"hit_{primary_threshold:g}"]["ljung_box"][1]["p_value"],
        "spearman_lag1": ac["spearman_lag1"]["p_value"],
        "drift_homogeneity": drift["homogeneity_p"],
        "cusum": drift["cusum"]["p_value"],
        "time_of_day": tod["p_value"] if tod else None,
    }
    adj = holm_adjust(list(family.values()))
    return {
        "n": int(len(m)),
        "period_start": df["timestamp"].min().isoformat() if df["timestamp"].notna().any() else None,
        "period_end": df["timestamp"].max().isoformat() if df["timestamp"].notna().any() else None,
        "primary_threshold": primary_threshold,
        "summary": summary(m),
        "threshold_frequencies": threshold_frequencies(m, rtp),
        "goodness_of_fit": goodness_of_fit(m, rtp),
        "histogram": [
            {"from": float(a), "to": (None if not np.isfinite(b) else float(b)), "count": c}
            for a, b, c in zip(hist_edges[:-1], hist_edges[1:], counts)
        ],
        "series": _downsample(seq, m),
        "rolling_share_200": _downsample(seq, roll),
        "streaks": streak_analysis(m, primary_threshold),
        "autocorrelation": ac,
        "drift": drift,
        "time_of_day": tod,
        "session_effects": session_effects(df, primary_threshold),
        "dependence_tests": [
            {"test": k, "p_value": v, "p_holm": a, "significant_at_0.05": (a is not None and a < 0.05)}
            for (k, v), a in zip(family.items(), adj)
        ],
        "interpretation_note": (
            "Significant results indicate a departure from independence or stationarity in this sample. "
            "They do not by themselves imply an exploitable predictive signal."
        ),
    }
