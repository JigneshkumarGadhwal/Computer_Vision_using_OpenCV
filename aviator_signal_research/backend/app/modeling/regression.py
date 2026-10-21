"""Secondary research task: estimating the exact (log) crash multiplier.

Low average error here does NOT imply profitable decisions: a cash-out
decision depends on the probability of exceeding a threshold, not on a point
estimate of a heavy-tailed variable.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..features import model_feature_names
from .metrics import diebold_mariano, pinball, regression_metrics
from .walkforward import make_folds

QUANTILES = (0.1, 0.5, 0.9)


def _fit_predict(name, X_tr, y_tr, X_te, seed):
    if name == "train_mean_log":
        return np.full(len(X_te), y_tr.mean())
    if name == "train_median_log":
        return np.full(len(X_te), np.median(y_tr))
    ok = ~np.isnan(X_tr).any(axis=1)
    X_tr, y_tr = X_tr[ok], y_tr[ok]
    X_te = np.where(np.isnan(X_te), 0.0, X_te)
    if name == "ridge":
        est = make_pipeline(StandardScaler(), Ridge(alpha=100.0))
    elif name == "hgb_regressor":
        est = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=150,
                                            min_samples_leaf=100, random_state=seed)
    else:
        raise ValueError(name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        est.fit(X_tr, y_tr)
        return est.predict(X_te)


def _quantiles(name, X_tr, y_tr, X_te, seed):
    out = {}
    for q in QUANTILES:
        if name == "empirical":
            out[q] = np.full(len(X_te), np.quantile(y_tr, q))
        else:
            ok = ~np.isnan(X_tr).any(axis=1)
            est = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_depth=3, learning_rate=0.05,
                                                max_iter=100, min_samples_leaf=100, random_state=seed)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                est.fit(X_tr[ok], y_tr[ok])
                out[q] = est.predict(np.where(np.isnan(X_te), 0.0, X_te))
    return out


def evaluate_regression(F: pd.DataFrame, mult: np.ndarray, start: int, end: int, test_end: int,
                        block: int, warmup: int, seed: int = 0) -> dict:
    y = np.log(np.asarray(mult, float))
    X = F[model_feature_names()].to_numpy()
    point_models = ("train_mean_log", "train_median_log", "ridge", "hgb_regressor")
    results: dict = {"phases": {}}
    for phase, (s, e) in {"validation": (start, end), "test": (end, test_end)}.items():
        if e - s <= 0:
            continue
        folds = make_folds(s, e, block, 0, warmup)
        preds = {m: [] for m in point_models}
        qpreds = {k: {q: [] for q in QUANTILES} for k in ("empirical", "hgb_quantile")}
        ys = []
        for f in folds:
            X_tr, y_tr = X[f.train_start:f.train_end], y[f.train_start:f.train_end]
            X_te = X[f.pred_start:f.pred_end]
            for m in point_models:
                preds[m].append(_fit_predict(m, X_tr, y_tr, X_te, seed))
            for k in qpreds:
                qq = _quantiles("empirical" if k == "empirical" else "hgb", X_tr, y_tr, X_te, seed)
                for q in QUANTILES:
                    qpreds[k][q].append(qq[q])
            ys.append(y[f.pred_start:f.pred_end])
        yy = np.concatenate(ys)
        phase_out = {"n": int(len(yy)), "start_seq": s, "end_seq": e - 1, "point": {}, "quantile": {}}
        base_err = np.abs(np.concatenate(preds["train_median_log"]) - yy)
        for m in point_models:
            p = np.concatenate(preds[m])
            met = regression_metrics(yy, p)
            met["dm_vs_train_median_abs_error"] = diebold_mariano(base_err, np.abs(p - yy))
            phase_out["point"][m] = met
        for k in qpreds:
            phase_out["quantile"][k] = {
                f"pinball_q{q}": pinball(yy, np.concatenate(qpreds[k][q]), q) for q in QUANTILES
            }
            phase_out["quantile"][k]["coverage_q0.1_q0.9"] = float(
                np.mean((yy >= np.concatenate(qpreds[k][0.1])) & (yy <= np.concatenate(qpreds[k][0.9])))
            )
        results["phases"][phase] = phase_out
    results["note"] = (
        "Target is log(crash multiplier). Baseline is the training-window median. "
        "A lower point error does not imply profitable betting decisions."
    )
    return results
