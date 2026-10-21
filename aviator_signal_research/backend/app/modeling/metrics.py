"""Probabilistic evaluation metrics and model-comparison statistics."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats

EPS = 1e-6


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def roc_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Mann-Whitney AUC; None when only one class present."""
    y = np.asarray(y)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    ranks = stats.rankdata(p)
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def calibration_table(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Equal-frequency reliability table (equal-width bins collapse when all
    predictions sit near the base rate, which is the typical case here)."""
    order = np.argsort(p, kind="stable")
    bins = np.array_split(order, min(n_bins, max(len(p), 1)))
    out = []
    for b in bins:
        if len(b) == 0:
            continue
        k = y[b].sum()
        n = len(b)
        lo, hi = wilson(k, n)
        out.append(
            {
                "p_min": float(p[b].min()),
                "p_max": float(p[b].max()),
                "mean_predicted": float(p[b].mean()),
                "observed_rate": float(k / n),
                "observed_ci_low": lo,
                "observed_ci_high": hi,
                "count": int(n),
            }
        )
    return out


def ece(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    tab = calibration_table(y, p, n_bins)
    n = len(y)
    return float(sum(r["count"] / n * abs(r["mean_predicted"] - r["observed_rate"]) for r in tab))


def hosmer_lemeshow(y: np.ndarray, p: np.ndarray, n_groups: int = 10) -> dict:
    """Out-of-sample Hosmer-Lemeshow test (df = number of groups, since the
    probabilities were not fitted on these data)."""
    order = np.argsort(p, kind="stable")
    stat = 0.0
    g = 0
    for b in np.array_split(order, min(n_groups, max(len(p), 1))):
        if len(b) == 0:
            continue
        o = y[b].sum()
        e = p[b].sum()
        v = (p[b] * (1 - p[b])).sum()
        if v > 0:
            stat += (o - e) ** 2 / v
            g += 1
    return {"statistic": float(stat), "df": g, "p_value": float(1 - stats.chi2.cdf(stat, g)) if g else None}


def calibration_slope_intercept(y: np.ndarray, p: np.ndarray) -> dict:
    """Logistic recalibration y ~ a + b*logit(p). Perfect calibration: a=0, b=1.

    Uses a few Newton steps (no sklearn regularisation bias)."""
    p = np.clip(p, EPS, 1 - EPS)
    x = np.log(p / (1 - p))
    if np.std(x) < 1e-9:
        # Constant predictions: slope undefined; report calibration-in-the-large.
        return {"intercept": float(np.log((y.mean() + EPS) / (1 - y.mean() + EPS)) - x[0]), "slope": None}
    X = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(50):
        eta = X @ beta
        mu = 1 / (1 + np.exp(-eta))
        w = mu * (1 - mu) + 1e-12
        g = X.T @ (y - mu)
        H = X.T @ (X * w[:, None])
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return {"intercept": float(beta[0]), "slope": float(beta[1])}


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    phat = k / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (float(max(0.0, centre - half)), float(min(1.0, centre + half)))


def confusion_at(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = p > threshold
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "decision_threshold": float(threshold),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "n_positive_calls": tp + fp,
    }


def classification_metrics(y: np.ndarray, p: np.ndarray, target_multiplier: float | None = None) -> dict:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    out = {
        "n": int(len(y)),
        "base_rate": float(y.mean()) if len(y) else None,
        "mean_predicted": float(p.mean()) if len(p) else None,
        "brier": brier(y, p),
        "log_loss": log_loss(y, p),
        "roc_auc": roc_auc(y, p),
        "ece": ece(y, p),
        "calibration": calibration_slope_intercept(y, p),
        "calibration_in_the_large": float(p.mean() - y.mean()) if len(y) else None,
        "hosmer_lemeshow": hosmer_lemeshow(y, p),
        "confusion_at_0.5": confusion_at(y, p, 0.5),
    }
    if target_multiplier:
        # Break-even probability for a cash-out at M: p*M - 1 > 0  <=>  p > 1/M
        out["confusion_at_breakeven"] = confusion_at(y, p, 1.0 / target_multiplier)
    return out


def _newey_west_var(d: np.ndarray, lags: int | None = None) -> float:
    n = len(d)
    if lags is None:
        lags = int(math.floor(4 * (n / 100.0) ** (2 / 9)))
    dc = d - d.mean()
    gamma0 = float(dc @ dc) / n
    v = gamma0
    for k in range(1, lags + 1):
        gk = float(dc[k:] @ dc[:-k]) / n
        v += 2 * (1 - k / (lags + 1)) * gk
    return max(v, 1e-18)


def diebold_mariano(loss_base: np.ndarray, loss_model: np.ndarray) -> dict:
    """One-sided DM test. H1: model has lower expected loss than baseline.

    Uses Newey-West HAC variance and the Harvey-Leybourne-Newbold small-sample
    correction with a t-distribution reference."""
    d = np.asarray(loss_base, float) - np.asarray(loss_model, float)
    n = len(d)
    if n < 10:
        return {"statistic": None, "p_value_one_sided": None, "mean_loss_diff": None}
    var = _newey_west_var(d)
    dm = d.mean() / math.sqrt(var / n)
    h = 1
    corr = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    stat = dm * corr
    p = float(1 - stats.t.cdf(stat, df=n - 1))
    return {"statistic": float(stat), "p_value_one_sided": p, "mean_loss_diff": float(d.mean())}


def block_bootstrap_skill(
    y: np.ndarray, p_model: np.ndarray, p_base: np.ndarray, n_boot: int = 1000, block: int = 50, seed: int = 0
) -> dict:
    """Moving-block bootstrap CI for the Brier skill score vs baseline."""
    y = np.asarray(y, float)
    n = len(y)
    if n < 2 * block:
        block = max(1, n // 4)
    rng = np.random.default_rng(seed)
    lm = (p_model - y) ** 2
    lb = (p_base - y) ** 2
    n_blocks = int(math.ceil(n / block))
    starts_max = n - block
    skills = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, starts_max + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        bb = lb[idx].mean()
        skills[i] = 1 - lm[idx].mean() / bb if bb > 0 else 0.0
    point = 1 - lm.mean() / lb.mean() if lb.mean() > 0 else 0.0
    return {
        "brier_skill": float(point),
        "ci_low": float(np.percentile(skills, 2.5)),
        "ci_high": float(np.percentile(skills, 97.5)),
        "bootstrap_p_le_zero": float((skills <= 0).mean()),
        "block": int(block),
        "n_boot": int(n_boot),
    }


def holm_adjust(pvalues: list[float | None]) -> list[float | None]:
    """Holm-Bonferroni step-down adjusted p-values (None passes through)."""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    adjusted: list[float | None] = [None] * len(pvalues)
    order = sorted(idx, key=lambda i: pvalues[i])
    running = 0.0
    for rank, i in enumerate(order):
        val = min(1.0, (m - rank) * pvalues[i])
        running = max(running, val)
        adjusted[i] = running
    return adjusted


def regression_metrics(y_log: np.ndarray, pred_log: np.ndarray) -> dict:
    err = pred_log - y_log
    return {
        "n": int(len(y_log)),
        "mae_log": float(np.mean(np.abs(err))),
        "rmse_log": float(np.sqrt(np.mean(err ** 2))),
        "median_abs_error_multiplier": float(np.median(np.abs(np.exp(pred_log) - np.exp(y_log)))),
    }


def pinball(y: np.ndarray, q_pred: np.ndarray, q: float) -> float:
    diff = y - q_pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))
