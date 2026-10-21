"""Market-implied prior, external-forecast, cross-venue consensus and calibration models."""
from __future__ import annotations

import math
from typing import Any

from ..domain import NormalizedMarket, OrderBook, Outcome
from .base import Abstention, ModelContext, ModelEstimate, clamp


class MarketImpliedModel:
    """Baseline / prior: the executable mid. Never an independent reason to trade."""

    name = "market_implied"
    version = "1.0"

    def applies(self, market: NormalizedMarket) -> bool:
        return True

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention:
        if book is None or book.mid is None:
            return Abstention(self.name, "no two-sided book")
        sp = book.spread or 0.0
        depth = book.depth_usd(Outcome.YES, "bid") + book.depth_usd(Outcome.YES, "ask")
        sigma = max(sp / 2, ctx.prior_sigma_floor) + (0.03 if depth < 500 else 0.0)
        return ModelEstimate(self.name, self.version, clamp(book.mid, 0.001, 0.999), sigma, "medium", independent=False,
                             inputs={"mid": book.mid, "spread": sp, "depth_usd": round(depth, 2)})


class ExternalForecastModel:
    """Uses a published probability forecast (``forecast`` evidence: simulator forecaster or an
    operator-supplied forecast file). The forecast's own stated uncertainty is used."""

    name = "external_forecast"
    version = "1.0"

    def applies(self, market: NormalizedMarket) -> bool:
        return True

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention:
        fcs = [e for e in ctx.evidence.get("forecast", []) if e.subject == market.key]
        if not fcs:
            return Abstention(self.name, "no published forecast for this market")
        ev = max(fcs, key=lambda e: e.observed_at)
        p = float(ev.payload["p"])
        sigma = float(ev.payload.get("sigma", 0.08))
        if ev.reliability == "low":
            sigma *= 1.5
        return ModelEstimate(self.name, self.version, clamp(p, 0.001, 0.999), clamp(sigma, 0.01, 0.5), ev.reliability,
                             evidence=[ev], inputs={"p": p, "sigma": sigma, "source": ev.source},
                             supporting=[f"{ev.source} forecast {p:.1%} ± {sigma:.1%}"])


class ConsensusModel:
    """Cross-venue consensus from operator-confirmed equivalent (or complementary) markets."""

    name = "cross_venue_consensus"
    version = "1.0"

    def applies(self, market: NormalizedMarket) -> bool:
        return True

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention:
        pts = []
        for m2, b2, relation in ctx.linked:
            if b2 is None or b2.mid is None or b2.spread is None:
                continue
            mid = b2.mid if relation == "equivalent" else 1 - b2.mid
            s = max(b2.spread / 2, 0.02)
            pts.append((mid, s, m2.key))
        if not pts:
            return Abstention(self.name, "no confirmed linked market with a two-sided book")
        w = [1 / (s * s) for _, s, _ in pts]
        q = sum(p * wi for (p, _, _), wi in zip(pts, w)) / sum(w)
        sigma = math.sqrt(1 / sum(w)) + 0.01
        return ModelEstimate(self.name, self.version, clamp(q, 0.001, 0.999), sigma, "medium",
                             inputs={"linked": [{"market": k, "p": p, "sigma": s} for p, s, k in pts]},
                             supporting=[f"linked market {k} implies {p:.1%}" for p, _, k in pts])


class CalibrationModel:
    """Empirical-Bayes correction of market-implied probabilities using ZigGgy's own record of
    resolved markets (price observed >= 1h before resolution -> outcome). Captures systematic
    biases such as favourite-longshot. Active only with enough resolved samples in the bucket."""

    name = "empirical_calibration"
    version = "1.0"
    min_samples = 40
    prior_strength = 20.0

    def applies(self, market: NormalizedMarket) -> bool:
        return True

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention:
        table: dict[str, Any] | None = (ctx.calibration or {}).get(market.category)
        if not table or book is None or book.mid is None:
            return Abstention(self.name, "no calibration history for category")
        idx = min(9, int(book.mid * 10))
        b = table.get(str(idx)) or {}
        n, wins = int(b.get("n", 0)), int(b.get("wins", 0))
        if n < self.min_samples:
            return Abstention(self.name, f"only {n} resolved samples in price bucket {idx / 10:.1f}-{(idx + 1) / 10:.1f}")
        k = self.prior_strength
        a, bb = k * book.mid + wins, k * (1 - book.mid) + (n - wins)
        q = a / (a + bb)
        var = a * bb / ((a + bb) ** 2 * (a + bb + 1))
        return ModelEstimate(self.name, self.version, clamp(q, 0.001, 0.999), math.sqrt(var) + 0.01, "medium",
                             inputs={"bucket": idx, "n": n, "wins": wins, "mid": book.mid},
                             supporting=[f"{wins}/{n} markets priced {idx / 10:.1f}-{(idx + 1) / 10:.1f} resolved YES historically"])
