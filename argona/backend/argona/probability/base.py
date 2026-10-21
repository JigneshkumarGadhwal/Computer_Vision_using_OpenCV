"""Probability-model interfaces and shared math."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..domain import NormalizedMarket, OrderBook

QUALITY_WEIGHT = {"high": 1.0, "medium": 0.85, "low": 0.6, "none": 0.0}


def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class EvidenceItem:
    kind: str
    subject: str
    source: str
    source_url: str
    observed_at: datetime
    reliability: str
    payload: dict[str, Any]
    summary: str = ""
    is_simulated: bool = False
    id: int | None = None

    def ref(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "source": self.source, "url": self.source_url,
                "observed_at": self.observed_at.isoformat(), "reliability": self.reliability, "summary": self.summary,
                "simulated": self.is_simulated}


@dataclass
class ModelEstimate:
    model: str
    version: str
    q: float  # P(YES)
    sigma: float  # standard error of q
    quality: str  # high | medium | low
    evidence: list[EvidenceItem] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    supporting: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    independent: bool = True  # False for the market-implied prior

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "version": self.version, "q": round(self.q, 6), "sigma": round(self.sigma, 6),
                "quality": self.quality, "independent": self.independent, "inputs": self.inputs,
                "evidence": [e.ref() for e in self.evidence]}


@dataclass
class Abstention:
    model: str
    reason: str


@dataclass
class ModelContext:
    now: datetime
    evidence: dict[str, list[EvidenceItem]]  # keyed by evidence kind
    linked: list[tuple[NormalizedMarket, OrderBook, str]] = field(default_factory=list)  # (market, book, relation)
    calibration: dict[str, Any] | None = None  # category -> bucket table
    prior_sigma_floor: float = 0.06


class ProbabilityModel(Protocol):
    name: str
    version: str

    def applies(self, market: NormalizedMarket) -> bool: ...

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention: ...
