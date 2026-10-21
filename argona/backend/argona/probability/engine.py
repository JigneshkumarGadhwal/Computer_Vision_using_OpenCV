"""Probability engine (used by ILSA): runs applicable models and combines them.

Combination = Gaussian Bayesian update: the market-implied mid is the prior (with an
uncertainty that grows with spread and thin books); each independent model is a measurement.
    q_post = (q_m/s_m^2 + sum q_i/s_i^2) / (1/s_m^2 + sum 1/s_i^2)
This shrinks toward the market unless the evidence is precise - a deliberately conservative
choice: the market is usually well informed.

Abstains when only the market-implied prior is available.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..domain import NormalizedMarket, OrderBook
from .base import QUALITY_WEIGHT, Abstention, ModelContext, ModelEstimate, ProbabilityModel, clamp
from .crypto import CryptoThresholdModel
from .models import CalibrationModel, ConsensusModel, ExternalForecastModel, MarketImpliedModel
from .weather import WeatherHighTempModel

ENGINE_VERSION = "argona-ensemble-1.0"


@dataclass
class CombinedEstimate:
    market_key: str
    q: float | None
    sigma: float | None
    confidence: float
    quality: str
    abstained: bool
    reason: str
    market_mid: float | None
    components: list[dict[str, Any]] = field(default_factory=list)
    abstentions: list[dict[str, str]] = field(default_factory=list)
    evidence: list[Any] = field(default_factory=list)
    supporting: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    model: str = ENGINE_VERSION
    version: str = "1.0"


def default_models() -> list[ProbabilityModel]:
    return [CryptoThresholdModel(), WeatherHighTempModel(), ExternalForecastModel(), ConsensusModel(), CalibrationModel()]


class ProbabilityEngine:
    def __init__(self, models: list[ProbabilityModel] | None = None) -> None:
        self.prior = MarketImpliedModel()
        self.models = models if models is not None else default_models()

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> CombinedEstimate:
        prior = self.prior.estimate(market, book, ctx)
        comps: list[ModelEstimate] = []
        abst: list[dict[str, str]] = []
        for m in self.models:
            if not m.applies(market):
                continue
            r = m.estimate(market, book, ctx)
            if isinstance(r, Abstention):
                abst.append({"model": r.model, "reason": r.reason})
            else:
                comps.append(r)
        mid = book.mid if book else None
        if not comps:
            return CombinedEstimate(market.key, None, None, 0.0, "none", True,
                                    "no independent model applicable (market-implied only)", mid, abstentions=abst)
        terms: list[tuple[float, float]] = []
        if isinstance(prior, ModelEstimate):
            terms.append((prior.q, prior.sigma))
        for c in comps:
            s = c.sigma / max(QUALITY_WEIGHT.get(c.quality, 0.6), 0.1)  # down-weight weaker evidence
            terms.append((c.q, s))
        wsum = sum(1 / (s * s) for _, s in terms)
        q = sum(p / (s * s) for p, s in terms) / wsum
        sigma = math.sqrt(1 / wsum)
        conflicting: list[str] = []
        supporting: list[str] = []
        for c in comps:
            supporting += c.supporting
            conflicting += c.conflicting
        # disagreement between independent models -> lower confidence
        agree = 1.0
        if len(comps) > 1:
            qs = [c.q for c in comps]
            if max(qs) - min(qs) > 2 * max(c.sigma for c in comps):
                agree = 0.6
                conflicting.append(f"independent models disagree: {', '.join(f'{c.model}={c.q:.2f}' for c in comps)}")
        if isinstance(prior, ModelEstimate):
            gap = abs(q - prior.q)
            if gap > 0.25:
                conflicting.append(f"estimate differs from market by {gap:.0%}: check resolution criteria / data freshness")
        best_q = max((QUALITY_WEIGHT.get(c.quality, 0) for c in comps), default=0)
        quality = "high" if best_q >= 1 else "medium" if best_q >= 0.85 else "low"
        confidence = clamp(1 - sigma / 0.12, 0.0, 1.0) * best_q * agree
        if conflicting and agree == 1.0:
            confidence *= 0.9
        evidence = [e for c in comps for e in c.evidence]
        return CombinedEstimate(
            market_key=market.key, q=clamp(q, 0.001, 0.999), sigma=sigma, confidence=round(confidence, 4), quality=quality,
            abstained=False, reason="ok", market_mid=mid,
            components=([prior.to_dict()] if isinstance(prior, ModelEstimate) else []) + [c.to_dict() for c in comps],
            abstentions=abst, evidence=evidence, supporting=supporting, conflicting=conflicting,
        )


def cohere(estimates: list[CombinedEstimate]) -> list[CombinedEstimate]:
    """Mutually exclusive & exhaustive outcome sets must sum to 1: rescale non-abstained estimates."""
    live = [e for e in estimates if not e.abstained and e.q is not None]
    if len(live) < 2 or len(live) != len(estimates):
        return estimates
    total = sum(e.q for e in live)  # type: ignore[misc]
    if total <= 0 or abs(total - 1) < 1e-6:
        return estimates
    for e in live:
        e.q = clamp(e.q / total, 0.001, 0.999)  # type: ignore[operator]
        e.supporting.append(f"renormalised within exclusive outcome set (raw sum {total:.3f})")
    return estimates
