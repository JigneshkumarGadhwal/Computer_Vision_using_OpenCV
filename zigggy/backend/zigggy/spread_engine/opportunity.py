"""Expected-value evaluation and opportunity ranking.

For buying a binary contract (YES or NO - both pay $1 on their outcome) at executable VWAP v,
with win probability pi and per-contract fee f and execution buffer b:

    gross edge        = pi - best_price
    fee-adjusted      = pi - best_price - f
    execution-adjusted= pi - v - f - b         (= net EV per contract)
    est. return       = net EV / (v + f)

For exiting (selling) a held contract at executable VWAP v: net = v - f - pi
(the value of selling now vs. holding to settlement).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import RiskLimits
from ..domain import Action, NormalizedMarket, OrderBook, Outcome
from ..probability.engine import CombinedEstimate
from .engine import ExecQuote, quote


@dataclass
class Candidate:
    market: NormalizedMarket
    outcome: Outcome
    action: Action
    kind: str
    win_prob: float  # probability the purchased (or held) contract pays $1
    fair_value: float  # P(YES)
    quote: ExecQuote
    ref_qty: float
    gross_edge: float
    fee_adj_edge: float
    net_ev: float
    est_return: float
    confidence: float
    quality: str
    liquidity_usd: float
    hours_to_resolution: float | None
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    qualified: bool = False
    score: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "market": self.market.key, "outcome": self.outcome.value, "action": self.action.value, "kind": self.kind,
            "fair_value": round(self.fair_value, 4), "win_prob": round(self.win_prob, 4), "best": self.quote.best,
            "vwap": self.quote.vwap, "fee": round(self.quote.fee_per_contract, 5), "gross_edge": round(self.gross_edge, 4),
            "net_ev": round(self.net_ev, 4), "return": round(self.est_return, 4), "confidence": self.confidence,
            "liquidity_usd": round(self.liquidity_usd, 2), "flags": self.flags, "reasons": self.reasons, "score": round(self.score, 6),
        }


def _hours(market: NormalizedMarket, now: datetime) -> float | None:
    if not market.close_time:
        return None
    return (market.close_time - now).total_seconds() / 3600


def evaluate_entry(market: NormalizedMarket, book: OrderBook, est: CombinedEstimate, limits: RiskLimits, now: datetime,
                   ref_usd: float = 100.0) -> list[Candidate]:
    if est.abstained or est.q is None:
        return []
    out = []
    for outcome in (Outcome.YES, Outcome.NO):
        best = book.best_ask(outcome)
        if best is None:
            continue
        win = est.q if outcome is Outcome.YES else 1 - est.q
        ref_qty = max(market.min_order_size, round(ref_usd / max(best, 0.01)))
        q = quote(book, outcome, Action.BUY, ref_qty, market.fee_model)
        if q.vwap is None:
            continue
        gross = win - best
        fee_adj = gross - q.fee_per_contract
        net = win - q.vwap - q.fee_per_contract - limits.slippage_buffer
        cost = q.vwap + q.fee_per_contract
        c = Candidate(market, outcome, Action.BUY, "directional", win, est.q, q, ref_qty, gross, fee_adj, net,
                      net / cost if cost > 0 else 0.0, est.confidence, est.quality,
                      book.depth_usd(outcome, "ask", 0.05), _hours(market, now))
        _qualify(c, book, limits, now)
        out.append(c)
    return out


def evaluate_exit(market: NormalizedMarket, book: OrderBook, est: CombinedEstimate, outcome: Outcome, held: float,
                  limits: RiskLimits, now: datetime) -> Candidate | None:
    if est.abstained or est.q is None or held <= 0:
        return None
    hold_value = est.q if outcome is Outcome.YES else 1 - est.q
    q = quote(book, outcome, Action.SELL, held, market.fee_model)
    if q.vwap is None or q.fillable <= 0:
        return None
    net = q.vwap - q.fee_per_contract - hold_value - limits.slippage_buffer
    c = Candidate(market, outcome, Action.SELL, "exit", hold_value, est.q, q, q.fillable, (q.best or 0) - hold_value,
                  (q.best or 0) - hold_value - q.fee_per_contract, net, net / max(hold_value, 0.01), est.confidence,
                  est.quality, book.depth_usd(outcome, "bid", 0.05), _hours(market, now))
    if net >= limits.min_exit_edge and est.confidence >= limits.min_confidence:
        c.qualified = True
        c.reasons.append(f"selling at {q.vwap:.3f} beats hold value {hold_value:.3f} by {net:.3f} after costs")
        c.score = net * est.confidence
    else:
        c.reasons.append("hold: exit does not beat expected settlement value after costs")
    return c


def _qualify(c: Candidate, book: OrderBook, limits: RiskLimits, now: datetime) -> None:
    ok = True

    def fail(msg: str, flag: str | None = None) -> None:
        nonlocal ok
        ok = False
        c.reasons.append(msg)
        if flag:
            c.flags.append(flag)

    if c.gross_edge <= 0:
        fail(f"no gross edge: fair {c.win_prob:.3f} <= best ask {c.quote.best:.3f}")
    elif c.fee_adj_edge <= 0:
        fail(f"edge {c.gross_edge:.3f} consumed by fees {c.quote.fee_per_contract:.3f}")
    if c.net_ev < limits.min_net_edge:
        fail(f"net EV {c.net_ev:.3f} < min {limits.min_net_edge:.3f}")
    if c.confidence < limits.min_confidence:
        fail(f"confidence {c.confidence:.2f} < min {limits.min_confidence:.2f}", "low_confidence")
    if c.quote.best is not None and not (limits.min_price <= c.quote.best <= limits.max_price):
        fail(f"price {c.quote.best:.3f} outside [{limits.min_price}, {limits.max_price}]", "extreme_price")
    sp = book.spread
    if sp is None or sp > limits.max_spread:
        fail(f"spread {sp if sp is not None else 'n/a'} > max {limits.max_spread}", "wide_spread")
    if c.liquidity_usd < limits.min_liquidity_usd:
        fail(f"liquidity ${c.liquidity_usd:.0f} < min ${limits.min_liquidity_usd:.0f}", "thin_book")
    age = book.age_s(now)
    if age > limits.max_staleness_s:
        fail(f"book {age:.0f}s old > {limits.max_staleness_s}s", "stale_data")
    h = c.hours_to_resolution
    if h is None:
        c.flags.append("unknown_resolution_time")
    elif h < limits.min_hours_to_resolution:
        fail(f"resolves in {h:.2f}h < min {limits.min_hours_to_resolution}h", "too_close_to_resolution")
    elif h > limits.max_days_to_resolution * 24:
        fail(f"resolves in {h / 24:.0f}d > max {limits.max_days_to_resolution:.0f}d", "long_dated")
    if not c.quote.complete:
        c.flags.append("partial_depth")
    if c.market.is_simulated:
        c.flags.append("SIMULATED")
    c.qualified = ok
    if ok:
        c.reasons.append(f"net EV {c.net_ev:.3f}/contract ({c.est_return:.1%}) after fees & slippage, confidence {c.confidence:.2f}")
    liq = min(1.0, c.liquidity_usd / 1000.0)
    tf = 1.0 / (1.0 + (max(h or 0, 0) / 24) / 30.0)
    c.score = max(c.net_ev, 0) * c.confidence * (0.5 + 0.5 * liq) * tf * (1 + c.est_return)


def rank(cands: list[Candidate]) -> list[Candidate]:
    return sorted(cands, key=lambda c: (c.qualified, c.score), reverse=True)
