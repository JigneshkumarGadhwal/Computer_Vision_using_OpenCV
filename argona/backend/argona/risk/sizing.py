"""Position sizing.

Binary contract: pay cost c per contract (price + fees), receive $1 with probability pi, else 0.
Growth-optimal (Kelly) fraction of bankroll:

    f* = (pi - c) / (1 - c)

This is derived from the actual payoff, so it applies equally to YES and NO contracts (each is a
$1-payout binary on its own outcome). For the simplified YES case with c = p it reduces to the
textbook f* = (q - p) / (1 - p). Multi-outcome / correlated exposure is handled by the risk
engine's event, category and portfolio caps rather than by this single-bet formula.

Applied fraction = kelly_fraction (default 1/4) x confidence x f*. Never levered, never scaled up
after losses (bankroll = current equity).
"""
from __future__ import annotations

import math


def kelly_binary(win_prob: float, cost: float) -> float:
    if not (0 < cost < 1):
        return 0.0
    return max(0.0, (win_prob - cost) / (1 - cost))


def size_contracts(*, equity: float, win_prob: float, cost_per_contract: float, kelly_fraction: float, confidence: float,
                   cap_usd: float, min_size: float = 1.0, lot: float = 1.0) -> tuple[float, float, float]:
    """Returns (contracts, kelly_raw, kelly_applied). Contracts are floored to the lot size."""
    raw = kelly_binary(win_prob, cost_per_contract)
    applied = raw * kelly_fraction * max(0.0, min(confidence, 1.0))
    stake = min(applied * max(equity, 0.0), cap_usd)
    if stake <= 0 or cost_per_contract <= 0:
        return 0.0, raw, applied
    n = math.floor(stake / cost_per_contract / lot) * lot
    if n < min_size:
        return 0.0, raw, applied
    return float(n), raw, applied
