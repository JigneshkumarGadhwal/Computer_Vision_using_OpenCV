"""Crypto price-threshold model.

Terminal markets ("above K at T"):     P = N(d2),  d2 = (ln(S/K) - sigma^2 T / 2) / (sigma sqrt(T))
Range markets ("between A and B"):     P = N(d2(A)) - N(d2(B))
Touch markets ("reach K by T"):        P = min(1, 2 * (1 - N(ln(K/S) / (sigma sqrt(T)))))  (driftless reflection)

Zero drift; sigma is realised volatility from recent candles. Uncertainty is propagated by
re-evaluating with sigma x0.8 and x1.25 (vol-of-vol band) plus a basis term for reference-index
mismatch (e.g. Coinbase spot vs. the venue's settlement index).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

from ..domain import NormalizedMarket, OrderBook, Venue, as_utc
from .base import Abstention, ModelContext, ModelEstimate, clamp, ncdf

YEAR_S = 365.25 * 24 * 3600
ASSETS = {
    "BTC": [r"(?i)\bbitcoin\b", r"\bBTC\b", r"KXBTC"],
    "ETH": [r"(?i)\bethereum\b", r"\bETH\b", r"KXETH"],
    "SOL": [r"(?i)\bsolana\b", r"\bSOL\b", r"KXSOL"],
    "XRP": [r"(?i)\bXRP\b", r"KXXRP"],
    "DOGE": [r"(?i)\bdogecoin\b", r"\bDOGE\b", r"KXDOGE"],
    "SIMBTC": [r"\bSIMBTC\b"],
}
_NUM = r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM])?"


def _num(s: str, suffix: str | None) -> float:
    v = float(s.replace(",", ""))
    if suffix and suffix.lower() == "k":
        v *= 1_000
    elif suffix and suffix.lower() == "m":
        v *= 1_000_000
    return v


@dataclass
class CryptoSpec:
    asset: str
    kind: str  # above | below | between | touch_up | touch_down
    lo: float | None
    hi: float | None
    expiry: datetime


def detect_asset(text: str) -> str | None:
    for a in ("SIMBTC", "BTC", "ETH", "SOL", "XRP", "DOGE"):
        if any(re.search(p, text) for p in ASSETS[a]):
            return a
    return None


def parse_spec(m: NormalizedMarket) -> CryptoSpec | None:
    text = f"{m.question} {m.outcome_label or ''} {m.venue_market_id} {m.meta.get('series_ticker') or ''}"
    asset = (m.strike or {}).get("underlying") or detect_asset(text)
    if not asset or not m.close_time:
        return None
    exp_raw = m.meta.get("expected_expiration_time") or m.meta.get("expiry")
    expiry = as_utc(datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))) if isinstance(exp_raw, str) else m.close_time
    st = (m.strike or {}).get("strike_type")
    if st:  # structured strikes (Kalshi / SIM)
        lo, hi = m.strike.get("floor_strike"), m.strike.get("cap_strike")
        if st in ("greater", "greater_or_equal") and lo is not None:
            return CryptoSpec(asset, "above", float(lo), None, expiry)
        if st in ("less", "less_or_equal") and hi is not None:
            return CryptoSpec(asset, "below", None, float(hi), expiry)
        if st == "between" and lo is not None and hi is not None:
            return CryptoSpec(asset, "between", float(lo), float(hi), expiry)
        return None
    q = m.question
    label = m.outcome_label or ""
    rng = re.search(_NUM + r"\s*(?:-|–|to)\s*" + _NUM, label or q)
    if rng and ("between" in q.lower() or label):
        return CryptoSpec(asset, "between", _num(rng.group(1), rng.group(2)), _num(rng.group(3), rng.group(4)), expiry)
    lower = q.lower()
    nums = re.findall(_NUM, label) or re.findall(_NUM, q)
    nums = [_num(a, b) for a, b in nums if a and float(a.replace(",", "")) > 0]
    if not nums:
        return None
    k = max(nums) if asset in ("BTC", "ETH", "SIMBTC") else nums[0]
    if any(w in lower for w in ("reach", "hit", "touch")):
        return CryptoSpec(asset, "touch_up", k, None, expiry)
    if any(w in lower for w in ("dip to", "fall to", "drop to")):
        return CryptoSpec(asset, "touch_down", None, k, expiry)
    if any(w in lower for w in ("above", "greater than", "higher than", "over ")) or label.startswith(">") or label.startswith("↑"):
        return CryptoSpec(asset, "above", k, None, expiry)
    if any(w in lower for w in ("below", "less than", "lower than", "under ")) or label.startswith("<") or label.startswith("↓"):
        return CryptoSpec(asset, "below", None, k, expiry)
    return None


def terminal_prob(spot: float, k: float, sigma: float, t: float) -> float:
    if t <= 0:
        return 1.0 if spot > k else 0.0
    d2 = (math.log(spot / k) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return ncdf(d2)


def spec_prob(spec: CryptoSpec, spot: float, sigma: float, t: float) -> float:
    if spec.kind == "above":
        return terminal_prob(spot, spec.lo, sigma, t)  # type: ignore[arg-type]
    if spec.kind == "below":
        return 1.0 - terminal_prob(spot, spec.hi, sigma, t)  # type: ignore[arg-type]
    if spec.kind == "between":
        return max(0.0, terminal_prob(spot, spec.lo, sigma, t) - terminal_prob(spot, spec.hi, sigma, t))  # type: ignore[arg-type]
    if spec.kind == "touch_up":
        k = spec.lo or spot
        if spot >= k:
            return 1.0
        return min(1.0, 2.0 * (1.0 - ncdf(math.log(k / spot) / (sigma * math.sqrt(max(t, 1e-9))))))
    if spec.kind == "touch_down":
        k = spec.hi or spot
        if spot <= k:
            return 1.0
        return min(1.0, 2.0 * ncdf(math.log(k / spot) / (sigma * math.sqrt(max(t, 1e-9)))))
    raise ValueError(spec.kind)


class CryptoThresholdModel:
    name = "crypto_lognormal"
    version = "1.1"
    max_spot_age_s = 600

    def applies(self, market: NormalizedMarket) -> bool:
        return market.category == "crypto"

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention:
        spec = parse_spec(market)
        if not spec:
            return Abstention(self.name, "could not parse asset/strike/expiry from market definition")
        spots = [e for e in ctx.evidence.get("crypto_spot", []) if e.subject == spec.asset]
        vols = [e for e in ctx.evidence.get("crypto_vol", []) if e.subject == spec.asset]
        if not spots or not vols:
            return Abstention(self.name, f"no spot/volatility evidence for {spec.asset}")
        spot_ev = max(spots, key=lambda e: e.observed_at)
        vol_ev = max(vols, key=lambda e: e.observed_at)
        age = (ctx.now - spot_ev.observed_at).total_seconds()
        if age > self.max_spot_age_s:
            return Abstention(self.name, f"spot evidence stale ({age:.0f}s)")
        spot = float(spot_ev.payload["price"])
        sigma = float(vol_ev.payload["sigma_annual"])
        t = (spec.expiry - ctx.now).total_seconds() / YEAR_S
        if t <= 0:
            return Abstention(self.name, "market past expiry")
        q = spec_prob(spec, spot, sigma, t)
        q_lo = spec_prob(spec, spot, sigma * 0.8, t)
        q_hi = spec_prob(spec, spot, sigma * 1.25, t)
        # reference-index basis: settlement index differs from our spot source by ~bps
        basis = 0.0 if market.is_simulated else 0.0007
        q_b1 = spec_prob(spec, spot * (1 + basis), sigma, t)
        q_b2 = spec_prob(spec, spot * (1 - basis), sigma, t)
        se = math.sqrt(((q_hi - q_lo) / 2) ** 2 + ((q_b1 - q_b2) / 2) ** 2) + 0.015
        quality = "high" if market.is_simulated or market.venue is Venue.KALSHI else "medium"
        if market.venue is not Venue.SIM and not (market.strike or {}).get("strike_type"):
            quality = "medium"  # parsed from free text
        return ModelEstimate(
            model=self.name, version=self.version, q=clamp(q, 0.001, 0.999), sigma=clamp(se, 0.01, 0.5), quality=quality,
            evidence=[spot_ev, vol_ev],
            inputs={"asset": spec.asset, "kind": spec.kind, "lo": spec.lo, "hi": spec.hi, "spot": spot, "sigma_annual": sigma,
                    "t_years": t, "hours_to_expiry": t * YEAR_S / 3600, "expiry": spec.expiry.isoformat()},
            supporting=[f"{spec.asset} spot {spot:,.2f} from {spot_ev.source} ({age:.0f}s old); realised vol {sigma:.0%}"],
        )
