"""Risk engine (TESS). Deterministic, independent of any LLM output; veto over every order."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..config import RiskLimits
from ..domain import Action, OrderBook, TradingMode
from ..portfolio.service import PortfolioState
from ..spread_engine.engine import quote
from ..spread_engine.opportunity import Candidate
from .sizing import size_contracts


@dataclass
class RiskDecision:
    approved: bool
    contracts: float = 0.0
    limit_price: float | None = None
    vwap: float | None = None
    stake_usd: float = 0.0
    expected_fee: float = 0.0
    net_ev_at_size: float | None = None
    kelly_raw: float | None = None
    kelly_applied: float | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.reasons.append(f"{name}: {detail}")
        return passed


@dataclass
class RiskContext:
    mode: TradingMode
    trading_allowed: bool
    trading_block_reason: str
    working_order_markets: set[str]
    held: dict[tuple[str, str], float]  # (market_key, outcome) -> contracts
    venue_tradable: bool
    venue_reason: str = ""


def _round_down(x: float, tick: float) -> float:
    return math.floor(round(x / tick, 8)) * tick


def _round_up(x: float, tick: float) -> float:
    return math.ceil(round(x / tick, 8)) * tick


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def assess(self, c: Candidate, book: OrderBook, pf: PortfolioState, ctx: RiskContext) -> RiskDecision:
        L = self.limits
        d = RiskDecision(approved=False)
        m = c.market
        is_exit = c.action is Action.SELL
        allowed = ctx.trading_allowed or (is_exit and "paused" in ctx.trading_block_reason)
        d.check("trading_allowed", allowed, ctx.trading_block_reason if not allowed else "ok")
        d.check("venue_execution", ctx.venue_tradable, ctx.venue_reason or "ok")
        d.check("opportunity_qualified", c.qualified, "; ".join(c.reasons[-2:]) if not c.qualified else "ok")
        if ctx.mode is TradingMode.LIVE and m.is_simulated:
            d.check("no_simulated_markets_live", False, "simulated market cannot be traded live")
        d.check("book_fresh", book.age_s() <= L.max_staleness_s, f"book age {book.age_s():.0f}s")
        d.check("no_duplicate_order", m.key not in ctx.working_order_markets, "working order already exists for market")
        if not is_exit:
            d.check("daily_loss_limit", pf.day_pnl > -L.max_daily_loss_usd, f"day P&L {pf.day_pnl:.2f} vs limit -{L.max_daily_loss_usd:.2f}")
            d.check("drawdown_limit", pf.drawdown < L.max_drawdown_pct, f"drawdown {pf.drawdown:.1%} vs {L.max_drawdown_pct:.0%}")
            d.check("max_open_orders", pf.open_orders < L.max_open_orders, f"{pf.open_orders} open orders")
            opp = ctx.held.get((m.key, c.outcome.other.value), 0.0)
            d.check("no_opposite_position", opp <= 0, f"holding {opp} {c.outcome.other.value} in same market")
        if any(not ch["passed"] for ch in d.checks):
            return d

        if is_exit:
            held = ctx.held.get((m.key, c.outcome.value), 0.0)
            qty = min(held, c.quote.fillable)
            q = quote(book, c.outcome, Action.SELL, qty, m.fee_model)
            if not d.check("exit_size", qty >= m.min_order_size and q.vwap is not None, f"qty {qty}"):
                return d
            d.approved = True
            d.contracts, d.vwap = qty, q.vwap
            d.limit_price = _round_down(q.worst or q.vwap, m.tick_size)
            d.expected_fee = q.fee_per_contract * qty
            d.net_ev_at_size = q.vwap - q.fee_per_contract - c.win_prob
            return d

        equity = pf.equity
        caps = {
            "max_position_usd": L.max_position_usd,
            "market_cap": L.max_market_exposure_pct * equity - pf.exposure_by_market.get(m.key, 0.0),
            "event_cap": L.max_event_exposure_pct * equity - pf.exposure_by_event.get(m.event_id or m.key, 0.0),
            "category_cap": L.max_category_exposure_pct * equity - pf.exposure_by_category.get(m.category, 0.0),
            "portfolio_cap": L.max_total_exposure_pct * equity - pf.exposure,
            "available_cash": pf.available,
        }
        binding = min(caps, key=lambda k: caps[k])
        cap_usd = max(0.0, caps[binding])
        if not d.check("exposure_headroom", cap_usd > 0, f"no headroom ({binding}={caps[binding]:.2f})"):
            return d
        cost0 = (c.quote.vwap or 0) + c.quote.fee_per_contract
        n, kraw, kapp = size_contracts(equity=equity, win_prob=c.win_prob, cost_per_contract=cost0, kelly_fraction=L.kelly_fraction,
                                       confidence=c.confidence, cap_usd=cap_usd, min_size=m.min_order_size)
        d.kelly_raw, d.kelly_applied = kraw, kapp
        if not d.check("kelly_positive", n > 0, f"kelly raw {kraw:.4f}, applied {kapp:.4f}, cap ${cap_usd:.2f} -> 0 contracts"):
            return d
        # size-dependent execution: shrink until net EV at VWAP clears the threshold and the order fits caps
        q = None
        for _ in range(12):
            q = quote(book, c.outcome, Action.BUY, n, m.fee_model)
            if q.vwap is not None and q.complete:
                net = c.win_prob - q.vwap - q.fee_per_contract - L.slippage_buffer
                if net >= L.min_net_edge and q.total_cost <= cap_usd + 1e-9:
                    break
            n = math.floor(n * 0.7)
            if n < m.min_order_size:
                q = None
                break
        if not d.check("edge_survives_size", q is not None and n >= m.min_order_size,
                       "net EV falls below threshold at any size meeting min order size"):
            return d
        assert q is not None
        limit = _round_up(q.worst or q.vwap, m.tick_size)
        if not d.check("price_on_tick", 0 < limit < 1, f"limit {limit}"):
            return d
        d.approved = True
        d.contracts = float(n)
        d.vwap = q.vwap
        d.limit_price = round(limit, 6)
        d.expected_fee = q.fee_per_contract * n
        d.stake_usd = q.total_cost
        d.net_ev_at_size = c.win_prob - q.vwap - q.fee_per_contract - L.slippage_buffer
        d.check("sized", True, f"{n:.0f} contracts @ ≤{limit:.3f} (${q.total_cost:.2f}); binding cap {binding}")
        return d
