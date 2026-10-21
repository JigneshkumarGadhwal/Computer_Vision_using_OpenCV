"""Walk-forward (expanding-window) evaluation with a commit-before-reveal protocol.

Protocol per fold
-----------------
1. Train on rounds ``[warmup, train_end)`` (expanding window; retrain once per block).
2. Predict rounds ``[pred_start, pred_end)`` where ``pred_start = train_end + purge_gap``.
3. Commit the predictions to a ledger (DB or in-memory).
4. Only then reveal the block's outcomes from the :class:`OutcomeVault`.
5. Advance by one block.

The vault refuses to reveal any outcome for which no prediction has been
committed, and refuses to hand out training labels beyond what has been
revealed. Feature causality (features at t use rounds < t only) is verified
separately by :func:`app.features.lookahead_audit`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .estimators import BaseModel, make_model


class LeakageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int  # exclusive
    pred_start: int
    pred_end: int  # exclusive


def make_folds(start: int, end: int, block: int, purge: int = 0, warmup: int = 0) -> list[Fold]:
    """Folds predicting [start, end) in blocks; training always ends ``purge``
    rounds before the predicted block."""
    if block <= 0:
        raise ValueError("block must be positive")
    folds = []
    s = start
    while s < end:
        e = min(s + block, end)
        train_end = s - purge
        if train_end - warmup <= 0:
            raise ValueError("not enough history before the first predicted block")
        folds.append(Fold(warmup, train_end, s, e))
        s = e
    return folds


class OutcomeVault:
    """Holds labels; reveals them only after predictions are committed."""

    def __init__(self, y: np.ndarray, revealed_until: int):
        self._y = np.asarray(y)
        self._revealed_until = revealed_until  # rounds [0, revealed_until) are history

    @property
    def revealed_until(self) -> int:
        return self._revealed_until

    def history(self, end: int) -> np.ndarray:
        if end > self._revealed_until:
            raise LeakageError(f"requested labels up to {end} but only {self._revealed_until} revealed")
        return self._y[:end]

    def reveal(self, start: int, end: int, committed: set[int]) -> np.ndarray:
        if start != self._revealed_until:
            raise LeakageError("outcomes must be revealed in chronological order without gaps")
        missing = [s for s in range(start, end) if s not in committed]
        if missing:
            raise LeakageError(f"no committed prediction for rounds {missing[:5]}...")
        self._revealed_until = end
        return self._y[start:end]


def prediction_hash(model: str, version: str, threshold: float, seq: int, prob: float, cutoff: int) -> str:
    return hashlib.sha256(f"{model}|{version}|{threshold:g}|{seq}|{prob:.12f}|{cutoff}".encode()).hexdigest()


class InMemoryLedger:
    def __init__(self):
        self.rows: list[dict] = []
        self.outcomes: dict[tuple, float] = {}

    def commit(self, *, model: BaseModel, threshold: float, phase: str, seqs, probs, train_end: int) -> set[int]:
        keys = {(r["model"], r["threshold"], r["seq"]) for r in self.rows}
        for s, p in zip(seqs, probs):
            k = (model.name, threshold, int(s))
            if k in keys:
                raise LeakageError(f"duplicate prediction for round {s}")
            self.rows.append(
                {
                    "model": model.name,
                    "threshold": threshold,
                    "phase": phase,
                    "seq": int(s),
                    "prob": float(p),
                    "data_cutoff_seq": int(s) - 1,
                    "train_end_seq": int(train_end) - 1,
                    "hash": prediction_hash(model.name, model.version, threshold, int(s), float(p), int(s) - 1),
                }
            )
        return {int(s) for s in seqs}

    def record_outcomes(self, *, model: BaseModel, threshold: float, seqs, ys) -> None:
        for s, yv in zip(seqs, ys):
            self.outcomes[(model.name, threshold, int(s))] = float(yv)


@dataclass
class WalkForwardResult:
    model_name: str
    threshold: float
    phase: str
    seqs: np.ndarray
    probs: np.ndarray
    y: np.ndarray
    folds: list[Fold]
    train_probs_last_fold: np.ndarray | None = None
    train_y_last_fold: np.ndarray | None = None
    last_model: BaseModel | None = None
    description: dict = field(default_factory=dict)


def walk_forward(
    F: pd.DataFrame,
    y: np.ndarray,
    model_name: str,
    threshold: float,
    start: int,
    end: int,
    block: int,
    purge: int = 0,
    warmup: int = 0,
    phase: str = "validation",
    ledger=None,
    seed: int = 0,
    rtp: float = 0.97,
) -> WalkForwardResult:
    ledger = ledger if ledger is not None else InMemoryLedger()
    vault = OutcomeVault(y, revealed_until=start)
    folds = make_folds(start, end, block, purge, warmup)
    all_s, all_p, all_y = [], [], []
    model: BaseModel | None = None
    for fold in folds:
        y_train = vault.history(fold.train_end)[fold.train_start:]
        model = make_model(model_name, seed=seed, rtp=rtp)
        model.fit(F.iloc[fold.train_start:fold.train_end], y_train, threshold)
        F_block = F.iloc[fold.pred_start:fold.pred_end]
        p = model.predict(F_block, threshold, y_history=vault.history(fold.pred_start))
        seqs = np.arange(fold.pred_start, fold.pred_end)
        committed = ledger.commit(model=model, threshold=threshold, phase=phase, seqs=seqs, probs=p,
                                  train_end=fold.train_end)
        y_block = vault.reveal(fold.pred_start, fold.pred_end, committed)
        ledger.record_outcomes(model=model, threshold=threshold, seqs=seqs, ys=y_block)
        all_s.append(seqs)
        all_p.append(p)
        all_y.append(y_block)
    res = WalkForwardResult(
        model_name=model_name,
        threshold=threshold,
        phase=phase,
        seqs=np.concatenate(all_s),
        probs=np.concatenate(all_p),
        y=np.concatenate(all_y),
        folds=folds,
        last_model=model,
        description=model.describe() if model else {},
    )
    if model is not None:
        last = folds[-1]
        F_tr = F.iloc[last.train_start:last.train_end]
        res.train_probs_last_fold = model.predict(F_tr, threshold, y_history=y[:last.train_start])
        res.train_y_last_fold = y[last.train_start:last.train_end]
    return res
