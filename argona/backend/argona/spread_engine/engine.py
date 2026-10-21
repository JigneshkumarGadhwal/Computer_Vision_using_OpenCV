"""Spread engine: executable prices, slippage, spread analytics and arbitrage screening.

Everything here is a pure function of order books + fee models, so it is deterministic and
unit-testable. Prices are per $1-payout contract.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..domain import Action, BookLevel, FeeModel, NormalizedMarket, OrderBook, Outcome


@dataclass
class ExecQuote:
    outcome: Outcome
    action: Action
    requested: float
    fillable: float
    best: float | None
    vwap: float | None
    worst: float | None
    fee_per_contract: float
    slippage_per_contract: float  # |vwap - best|
    total_cost: float  # buy: cash out incl. fees; sell: cash in net of fees

    @property
    def complete(self) -> bool:
        return self.fillable + 1e-9 >= self.requested


def walk(levels: list[BookLevel], qty: float, limit: float | None = None, side: str = "buy") -> tuple[float, float | None, float | None]:
    """Consume ``qty`` from best-first ``levels``. Returns (filled, vwap, worst_price)."""
    remaining, notional, worst = qty, 0.0, None
    for lvl in levels:
        if remaining <= 1e-12:
            break
        if limit is not None and ((side == "buy" and lvl.price > limit + 1e-12) or (side == "sell" and lvl.price < limit - 1e-12)):
            break
        take = min(remaining, lvl.size)
        notional += take * lvl.price
        remaining -= take
        worst = lvl.price
    filled = qty - remaining
    return filled, (notional / filled if filled > 0 else None), worst


def quote(book: OrderBook, outcome: Outcome, action: Action, qty: float, fee: FeeModel,
          limit: float | None = None) -> ExecQuote:
    levels = book.asks(outcome) if action is Action.BUY else book.bids(outcome)
    side = "buy" if action is Action.BUY else "sell"
    filled, vwap, worst = walk(levels, qty, limit, side)
    best = levels[0].price if levels else None
    if vwap is None or filled <= 0:
        return ExecQuote(outcome, action, qty, 0.0, best, None, None, 0.0, 0.0, 0.0)
    fee_total = _fee_for_walk(levels, filled, fee, limit, side)
    fpc = fee_total / filled
    slip = abs(vwap - best) if best is not None else 0.0
    total = filled * vwap + fee_total if action is Action.BUY else filled * vwap - fee_total
    return ExecQuote(outcome, action, qty, filled, best, vwap, worst, fpc, slip, total)


def _fee_for_walk(levels: list[BookLevel], qty: float, fee: FeeModel, limit: float | None, side: str) -> float:
    """Fees are charged per fill at each fill price (non-linear schedules), rounded on the order total."""
    remaining, raw = qty, 0.0
    for lvl in levels:
        if remaining <= 1e-12:
            break
        if limit is not None and ((side == "buy" and lvl.price > limit + 1e-12) or (side == "sell" and lvl.price < limit - 1e-12)):
            break
        take = min(remaining, lvl.size)
        raw += fee.per_contract(lvl.price) * take
        remaining -= take
    if fee.round_up_cents and raw > 0:
        return math.ceil(round(raw * 100, 6)) / 100
    return raw


@dataclass
class SpreadStats:
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread: float | None
    relative_spread: float | None
    bid_depth_usd: float
    ask_depth_usd: float
    imbalance: float | None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def spread_stats(book: OrderBook, *, wide: float = 0.08, narrow: float = 0.01) -> SpreadStats:
    bb, ba, mid, sp = book.best_bid(), book.best_ask(), book.mid, book.spread
    bd = book.depth_usd(Outcome.YES, "bid")
    ad = book.depth_usd(Outcome.YES, "ask")
    flags = []
    if bb is None or ba is None:
        flags.append("one_sided")
    elif sp is not None:
        if sp <= 0:
            flags.append("crossed_or_locked")
        elif sp >= wide:
            flags.append("wide_spread")
        elif sp <= narrow + 1e-9:
            flags.append("tight_spread")
    imb = (bd - ad) / (bd + ad) if (bd + ad) > 0 else None
    rel = (sp / mid) if (sp is not None and mid) else None
    return SpreadStats(bb, ba, mid, sp, rel, bd, ad, imb, flags)


@dataclass
class ArbCandidate:
    kind: str  # "structural_buy_all_yes" | "structural_buy_all_no" | "cross_venue"
    markets: list[str]
    legs: list[dict[str, Any]]
    cost: float
    guaranteed_payout: float
    fees: float
    net_profit_per_set: float
    max_sets: float
    risk_flags: list[str]
    is_risk_free: bool
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def structural_arbitrage(group: list[tuple[NormalizedMarket, OrderBook]], *, exhaustive: bool, sets: float = 1.0) -> list[ArbCandidate]:
    """Mutually exclusive outcome sets: buying every YES pays exactly 1 if the set is exhaustive;
    buying every NO pays n-1. Fees and executable depth included. Never labelled risk-free unless
    exclusivity *and* exhaustiveness are known and every leg is fully fillable."""
    out: list[ArbCandidate] = []
    n = len(group)
    if n < 2:
        return out
    for outcome, payout in ((Outcome.YES, 1.0), (Outcome.NO, float(n - 1))):
        legs, cost, fees, fillable = [], 0.0, 0.0, math.inf
        ok = True
        for m, b in group:
            q = quote(b, outcome, Action.BUY, sets, m.fee_model)
            if not q.complete or q.vwap is None:
                ok = False
                break
            legs.append({"market": m.key, "outcome": outcome.value, "vwap": q.vwap, "fee": q.fee_per_contract * sets})
            cost += q.vwap * sets
            fees += q.fee_per_contract * sets
            depth = sum(l.size for l in b.asks(outcome)[:3])
            fillable = min(fillable, depth)
        if not ok:
            continue
        net = payout * sets - cost - fees
        if net <= 0:
            continue
        flags = []
        if not exhaustive:
            flags.append("exhaustiveness_unverified")
        flags.append("multi_leg_execution_risk")
        risk_free = False  # legs execute sequentially (leg risk), so never claimed risk-free
        out.append(ArbCandidate(
            kind=f"structural_buy_all_{outcome.value.lower()}", markets=[m.key for m, _ in group], legs=legs, cost=cost,
            guaranteed_payout=payout * sets, fees=fees, net_profit_per_set=net / sets, max_sets=fillable, risk_flags=flags,
            is_risk_free=risk_free,
            explanation=(f"Buying {outcome.value} on all {n} outcomes costs {cost + fees:.4f} incl. fees and pays {payout * sets:.2f} "
                         f"if exactly one outcome resolves YES. Not risk-free: legs fill sequentially"
                         + ("" if exhaustive else " and the outcome set may not be exhaustive") + "."),
        ))
    return out


def cross_venue_arbitrage(a: tuple[NormalizedMarket, OrderBook], b: tuple[NormalizedMarket, OrderBook], *,
                          confirmed_equivalent: bool, sets: float = 1.0) -> list[ArbCandidate]:
    """For two markets on the same proposition: YES on one + NO on the other pays exactly 1."""
    out = []
    for (m1, b1), (m2, b2) in ((a, b), (b, a)):
        y = quote(b1, Outcome.YES, Action.BUY, sets, m1.fee_model)
        n = quote(b2, Outcome.NO, Action.BUY, sets, m2.fee_model)
        if not (y.complete and n.complete) or y.vwap is None or n.vwap is None:
            continue
        cost = (y.vwap + n.vwap) * sets
        fees = (y.fee_per_contract + n.fee_per_contract) * sets
        net = sets - cost - fees
        if net <= 0:
            continue
        flags = ["multi_leg_execution_risk", "cross_venue_settlement_risk"]
        if not confirmed_equivalent:
            flags.append("resolution_equivalence_unconfirmed")
        if m1.close_time and m2.close_time and abs((m1.close_time - m2.close_time).total_seconds()) > 86400:
            flags.append("different_resolution_times")
        out.append(ArbCandidate(
            kind="cross_venue", markets=[m1.key, m2.key],
            legs=[{"market": m1.key, "outcome": "YES", "vwap": y.vwap}, {"market": m2.key, "outcome": "NO", "vwap": n.vwap}],
            cost=cost, guaranteed_payout=sets, fees=fees, net_profit_per_set=net / sets,
            max_sets=min(sum(l.size for l in b1.asks(Outcome.YES)[:3]), sum(l.size for l in b2.asks(Outcome.NO)[:3])),
            risk_flags=flags, is_risk_free=False,
            explanation=(f"YES on {m1.venue.value} at {y.vwap:.3f} + NO on {m2.venue.value} at {n.vwap:.3f} = {cost + fees:.4f} incl. fees "
                         "for a $1 payout IF both venues resolve identically. Different rules, sources or timing break the hedge."),
        ))
    return out
