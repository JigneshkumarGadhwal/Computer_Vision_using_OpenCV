"""Probability models for threshold targets, behind one small interface.

``fit(F, y, threshold)`` trains on a chronological training slice.
``predict(F, threshold, y_history)`` returns P(round reaches threshold) for
each row of F. ``y_history`` holds outcomes of all rounds *before* the first
row of F (already revealed in live operation); only sequential filters such
as the HMM use it. Model parameters are never updated inside ``predict``.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from ..features import _tkey, model_feature_names

try:  # optional dependency
    from lightgbm import LGBMClassifier

    HAVE_LIGHTGBM = True
except Exception:  # pragma: no cover
    HAVE_LIGHTGBM = False

EPS = 1e-4
MIN_TRAIN_ROWS = 200


class InsufficientTrainingData(ValueError):
    pass


def _clip(p):
    return np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)


def _logit(p):
    p = _clip(p)
    return np.log(p / (1 - p))


class PlattCalibrator:
    """Logistic recalibration fitted on a held-out *later* slice of training data."""

    def fit(self, p_raw, y):
        self.lr = LogisticRegression(C=1e4, max_iter=1000)
        if len(np.unique(y)) < 2:
            self.lr = None
            self.const = float(np.mean(y))
            return self
        self.lr.fit(_logit(p_raw).reshape(-1, 1), y)
        return self

    def transform(self, p_raw):
        if self.lr is None:
            return np.full(len(p_raw), self.const)
        return self.lr.predict_proba(_logit(p_raw).reshape(-1, 1))[:, 1]


@dataclass
class BaseModel:
    name: str = "base"
    family: str = "baseline"
    version: str = "1.0"
    params: dict = field(default_factory=dict)
    calibration: str | None = None
    features: list[str] = field(default_factory=list)

    def fit(self, F: pd.DataFrame, y: np.ndarray, threshold: float):
        return self

    def predict(self, F: pd.DataFrame, threshold: float, y_history: np.ndarray | None = None) -> np.ndarray:
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "name": self.name,
            "family": self.family,
            "version": self.version,
            "params": self.params,
            "calibration": self.calibration,
            "features": self.features,
        }


# ---------------------------------------------------------------- baselines
@dataclass
class ReferenceConstant(BaseModel):
    """P = rtp / M from the (unverified) reference distribution. Not fitted."""

    name: str = "reference_constant"
    rtp: float = 0.97

    def __post_init__(self):
        self.params = {"rtp": self.rtp}

    def predict(self, F, threshold, y_history=None):
        return np.full(len(F), min(1 - EPS, self.rtp / threshold))


@dataclass
class ExpandingFrequency(BaseModel):
    """THE primary baseline: Laplace-smoothed frequency over all previous rounds."""

    name: str = "expanding_frequency"

    def predict(self, F, threshold, y_history=None):
        col = f"efreq_{_tkey(threshold)}"
        self.features = [col]
        return _clip(F[col].to_numpy())


@dataclass
class RollingFrequency(BaseModel):
    """Laplace-smoothed frequency over the previous 500 rounds."""

    name: str = "rolling_frequency_500"
    window: int = 500

    def predict(self, F, threshold, y_history=None):
        key = _tkey(threshold)
        r = F[f"rfreq_{key}_{self.window}"].to_numpy()
        fallback = F[f"efreq_{key}"].to_numpy()
        p = np.where(np.isnan(r), fallback, (r * self.window + 1) / (self.window + 2))
        self.features = [f"rfreq_{key}_{self.window}"]
        return _clip(p)


@dataclass
class TrainingFrequency(BaseModel):
    """Frequency on the training window only (fixed until next retrain)."""

    name: str = "training_frequency"

    def fit(self, F, y, threshold):
        self.p = (y.sum() + 1) / (len(y) + 2)
        return self

    def predict(self, F, threshold, y_history=None):
        return np.full(len(F), self.p)


# ------------------------------------------------------- statistical models
@dataclass
class MarkovConditional(BaseModel):
    """P(hit_t | hit_{t-1}) estimated on the training window (conditional probability)."""

    name: str = "markov_conditional"
    family: str = "statistical"

    def fit(self, F, y, threshold):
        lag = F[f"lag_hit_{_tkey(threshold)}_1"].to_numpy()
        ok = ~np.isnan(lag)
        lag, yy = lag[ok], y[ok]
        self.p1 = (yy[lag == 1].sum() + 1) / ((lag == 1).sum() + 2)
        self.p0 = (yy[lag == 0].sum() + 1) / ((lag == 0).sum() + 2)
        self.params = {"p_given_prev_hit": float(self.p1), "p_given_prev_miss": float(self.p0)}
        self.features = [f"lag_hit_{_tkey(threshold)}_1"]
        return self

    def predict(self, F, threshold, y_history=None):
        lag = F[f"lag_hit_{_tkey(threshold)}_1"].to_numpy()
        return _clip(np.where(lag == 1, self.p1, self.p0))


def _hmm_forward(y, pi, A, e, alpha0=None):
    """Return predictive P(y_t=1 | y_<t) for each t (Bernoulli emissions)."""
    n = len(y)
    pred = np.empty(n)
    a = pi.copy() if alpha0 is None else alpha0.copy()
    for t in range(n):
        prior = a  # P(state_t | y_<t)
        pred[t] = prior @ e
        like = e if y[t] == 1 else (1 - e)
        post = prior * like
        s = post.sum()
        post = post / s if s > 0 else prior
        a = post @ A
    return pred, a


@dataclass
class BernoulliHMM(BaseModel):
    """Two-state hidden-regime model fitted with Baum-Welch on the training
    outcome sequence; predictions are causal forward-filter estimates."""

    name: str = "hmm_regime"
    family: str = "statistical"
    n_iter: int = 30
    max_train_rows: int = 3000
    seed: int = 0

    def fit(self, F, y, threshold):
        y = np.asarray(y, dtype=int)[-self.max_train_rows:]
        base = y.mean()
        pi = np.array([0.5, 0.5])
        A = np.array([[0.95, 0.05], [0.05, 0.95]])
        e = np.clip(np.array([base * 0.8, min(base * 1.2, 0.99)]), 0.01, 0.99)
        n = len(y)
        prev_ll = -np.inf
        for _ in range(self.n_iter):
            like = np.where(y[:, None] == 1, e[None, :], 1 - e[None, :])
            alpha = np.zeros((n, 2))
            c = np.zeros(n)
            alpha[0] = pi * like[0]
            c[0] = alpha[0].sum()
            alpha[0] /= c[0]
            for t in range(1, n):
                alpha[t] = (alpha[t - 1] @ A) * like[t]
                c[t] = alpha[t].sum()
                alpha[t] /= c[t]
            beta = np.ones((n, 2))
            for t in range(n - 2, -1, -1):
                beta[t] = (A @ (like[t + 1] * beta[t + 1])) / c[t + 1]
            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True)
            xi = (alpha[:-1, :, None] * A[None] * (like[1:] * beta[1:])[:, None, :]) / c[1:, None, None]
            pi = gamma[0]
            A = xi.sum(0) / gamma[:-1].sum(0)[:, None]
            A = A / A.sum(1, keepdims=True)
            e = np.clip((gamma * y[:, None]).sum(0) / gamma.sum(0), 0.005, 0.995)
            ll = float(np.log(c).sum())
            if ll - prev_ll < 1e-4:
                break
            prev_ll = ll
        self.pi, self.A, self.e = pi, A, e
        self.params = {
            "transition": A.round(4).tolist(),
            "emission_p_hit": e.round(4).tolist(),
            "log_likelihood": float(prev_ll),
            "train_rows": int(n),
        }
        self.features = ["outcome sequence (lagged)"]
        return self

    def predict(self, F, threshold, y_history=None):
        hist = np.asarray(y_history if y_history is not None else [], dtype=int)
        _, a = _hmm_forward(hist, self.pi, self.A, self.e) if len(hist) else (None, self.pi)
        # Within the block, outcome t-1 is revealed before predicting t.
        lag = F[f"lag_hit_{_tkey(threshold)}_1"].to_numpy()
        block_y = np.concatenate([lag[1:], [0]])  # y_t for t in block (last unused)
        preds = np.empty(len(F))
        alpha = a
        for t in range(len(F)):
            preds[t] = alpha @ self.e
            if t == len(F) - 1:
                break
            yt = block_y[t]
            if np.isnan(yt):
                alpha = alpha @ self.A
                continue
            like = self.e if yt == 1 else 1 - self.e
            post = alpha * like
            post /= post.sum()
            alpha = post @ self.A
        return _clip(preds)


# ------------------------------------------------------- feature-based models
@dataclass
class SklearnModel(BaseModel):
    """Feature-based classifier with optional Platt calibration on the last
    ``calib_fraction`` of the (chronological) training window."""

    estimator_factory: object = None
    calib_fraction: float = 0.2
    max_train_rows: int | None = None
    seed: int = 0

    def fit(self, F, y, threshold):
        self.features = model_feature_names()
        X = F[self.features].to_numpy()
        ok = ~np.isnan(X).any(axis=1)
        X, y = X[ok], np.asarray(y)[ok]
        if len(y) < MIN_TRAIN_ROWS or len(np.unique(y)) < 2:
            raise InsufficientTrainingData(f"{self.name}: only {len(y)} usable training rows")
        if self.calibration == "platt":
            cut = int(len(y) * (1 - self.calib_fraction))
            Xf, yf, Xc, yc = X[:cut], y[:cut], X[cut:], y[cut:]
        else:
            Xf, yf = X, y
        if self.max_train_rows and len(yf) > self.max_train_rows:
            Xf, yf = Xf[-self.max_train_rows:], yf[-self.max_train_rows:]  # most recent rows
        self.est = self.estimator_factory(self.seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.est.fit(Xf, yf)
        self._train_rows = int(len(yf))
        if self.calibration == "platt":
            self.cal = PlattCalibrator().fit(self._raw(Xc), yc)
        return self

    def _raw(self, X):
        if hasattr(self.est, "predict_proba"):
            return self.est.predict_proba(X)[:, 1]
        d = self.est.decision_function(X)
        return 1 / (1 + np.exp(-d))

    def predict(self, F, threshold, y_history=None):
        X = F[self.features].to_numpy()
        bad = np.isnan(X).any(axis=1)
        X = np.where(np.isnan(X), 0.0, X)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = self._raw(X)
            if self.calibration == "platt":
                p = self.cal.transform(p)
        if bad.any():  # warm-up rows: fall back to the expanding baseline
            p = np.where(bad, F[f"efreq_{_tkey(threshold)}"].to_numpy(), p)
        return _clip(p)


@dataclass
class MeanEnsemble(BaseModel):
    name: str = "ensemble_mean"
    family: str = "ensemble"
    members: list = field(default_factory=list)

    def fit(self, F, y, threshold):
        for m in self.members:
            m.fit(F, y, threshold)
        self.params = {"members": [m.name for m in self.members]}
        return self

    def predict(self, F, threshold, y_history=None):
        return _clip(np.mean([m.predict(F, threshold, y_history) for m in self.members], axis=0))


# ------------------------------------------------------------------ factory
def _lr_l2(seed):
    # Regularisation strength chosen by chronological inner CV on the training
    # window only (TimeSeriesSplit), scored by Brier score.
    return make_pipeline(
        StandardScaler(),
        LogisticRegressionCV(
            Cs=[1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1], cv=TimeSeriesSplit(n_splits=3),
            scoring="neg_brier_score", max_iter=2000,
        ),
    )


def _lr_l1(seed):
    return make_pipeline(
        StandardScaler(), LogisticRegression(C=0.02, l1_ratio=1.0, solver="saga", max_iter=3000, random_state=seed)
    )


def _rf(seed):
    return RandomForestClassifier(
        n_estimators=150, max_depth=6, min_samples_leaf=50, max_features="sqrt", n_jobs=-1, random_state=seed
    )


def _hgb(seed):
    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=100, l2_regularization=1.0, random_state=seed
    )


def _lgbm(seed):
    return LGBMClassifier(
        n_estimators=200, learning_rate=0.03, num_leaves=8, min_child_samples=100, subsample=0.8,
        subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, verbose=-1, n_jobs=2,
    )


def _svm(seed):
    return make_pipeline(StandardScaler(), SVC(C=0.5, kernel="rbf", gamma="scale", random_state=seed))


def _mlp(seed):
    return make_pipeline(
        StandardScaler(),
        MLPClassifier(hidden_layer_sizes=(16,), alpha=1e-2, early_stopping=True, max_iter=300, random_state=seed),
    )


BASELINE_NAMES = ("expanding_frequency", "rolling_frequency_500", "training_frequency", "reference_constant")
PRIMARY_BASELINE = "expanding_frequency"

# Off by default, with reasons recorded in the docs:
#   svm_rbf: O(n^2) training cost and no expected benefit on low-signal tabular data.
#   mlp:     neural nets need far more signal/data than a few thousand rounds offer.
OPTIONAL_MODELS = ("svm_rbf", "mlp")


def available_models() -> list[str]:
    names = list(BASELINE_NAMES) + ["markov_conditional", "hmm_regime", "logistic_l2", "logistic_l1",
                                    "random_forest", "hist_gbm"]
    if HAVE_LIGHTGBM:
        names.append("lightgbm")
    names += ["ensemble_mean", *OPTIONAL_MODELS]
    return names


def default_model_names() -> list[str]:
    return [n for n in available_models() if n not in OPTIONAL_MODELS]


def make_model(name: str, seed: int = 0, rtp: float = 0.97) -> BaseModel:
    if name == "reference_constant":
        return ReferenceConstant(rtp=rtp)
    if name == "expanding_frequency":
        return ExpandingFrequency()
    if name == "rolling_frequency_500":
        return RollingFrequency()
    if name == "training_frequency":
        return TrainingFrequency()
    if name == "markov_conditional":
        return MarkovConditional()
    if name == "hmm_regime":
        return BernoulliHMM(seed=seed)
    if name == "logistic_l2":
        return SklearnModel(name=name, family="statistical", estimator_factory=_lr_l2, seed=seed,
                            params={"C": "inner TimeSeriesSplit CV over 1e-4..1e-1", "penalty": "l2",
                                    "scaler": "standard"})
    if name == "logistic_l1":
        return SklearnModel(name=name, family="statistical", estimator_factory=_lr_l1, seed=seed,
                            params={"C": 0.02, "penalty": "l1", "scaler": "standard"})
    if name == "random_forest":
        return SklearnModel(name=name, family="ml", estimator_factory=_rf, calibration="platt", seed=seed,
                            params={"n_estimators": 150, "max_depth": 6, "min_samples_leaf": 50})
    if name == "hist_gbm":
        return SklearnModel(name=name, family="ml", estimator_factory=_hgb, calibration="platt", seed=seed,
                            params={"max_depth": 3, "learning_rate": 0.05, "max_iter": 150, "min_samples_leaf": 100})
    if name == "lightgbm":
        if not HAVE_LIGHTGBM:
            raise ValueError("lightgbm not installed")
        return SklearnModel(name=name, family="ml", estimator_factory=_lgbm, calibration="platt", seed=seed,
                            params={"n_estimators": 200, "learning_rate": 0.03, "num_leaves": 8})
    if name == "svm_rbf":
        return SklearnModel(name=name, family="ml", estimator_factory=_svm, calibration="platt", seed=seed,
                            max_train_rows=3000, params={"C": 0.5, "kernel": "rbf", "max_train_rows": 3000})
    if name == "mlp":
        return SklearnModel(name=name, family="ml", estimator_factory=_mlp, calibration="platt", seed=seed,
                            params={"hidden_layer_sizes": [16], "alpha": 0.01})
    if name == "ensemble_mean":
        return MeanEnsemble(members=[make_model("logistic_l2", seed), make_model("hmm_regime", seed),
                                     make_model("hist_gbm", seed)])
    raise ValueError(f"Unknown model '{name}'")


def is_baseline(name: str) -> bool:
    return name in BASELINE_NAMES

