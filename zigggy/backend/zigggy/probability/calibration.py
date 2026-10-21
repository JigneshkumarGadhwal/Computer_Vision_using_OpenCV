"""Forecast-quality metrics: Brier score, log loss, reliability table."""
from __future__ import annotations

import math
from typing import Iterable


def brier(pairs: Iterable[tuple[float, int]]) -> float | None:
    pts = list(pairs)
    if not pts:
        return None
    return sum((p - y) ** 2 for p, y in pts) / len(pts)


def log_loss(pairs: Iterable[tuple[float, int]], eps: float = 1e-6) -> float | None:
    pts = list(pairs)
    if not pts:
        return None
    s = 0.0
    for p, y in pts:
        p = min(max(p, eps), 1 - eps)
        s += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return s / len(pts)


def reliability(pairs: Iterable[tuple[float, int]], bins: int = 10) -> list[dict]:
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        buckets[min(bins - 1, int(p * bins))].append((p, y))
    out = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        out.append({"bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}", "n": len(b),
                    "mean_forecast": sum(p for p, _ in b) / len(b), "observed": sum(y for _, y in b) / len(b)})
    return out


def calibration_table(pairs: Iterable[tuple[float, int]]) -> dict[str, dict[str, int]]:
    """Bucket (price decile) -> {n, wins}; consumed by CalibrationModel."""
    t: dict[str, dict[str, int]] = {}
    for p, y in pairs:
        k = str(min(9, int(p * 10)))
        b = t.setdefault(k, {"n": 0, "wins": 0})
        b["n"] += 1
        b["wins"] += int(y)
    return t
