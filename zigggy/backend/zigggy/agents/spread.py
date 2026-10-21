"""SPREAD - execution & market-microstructure agent. Analyses spreads/depth, screens arbitrage,
re-validates freshness and submits TESS-approved orders through the execution service."""
from __future__ import annotations

import logging
from collections import defaultdict

from ..database.models import Opportunity
from ..domain import TradingMode, utcnow
from ..spread_engine.engine import cross_venue_arbitrage, spread_stats, structural_arbitrage
from .base import Agent, CandidateSet, MicrostructureReport, RiskVerdict

log = logging.getLogger("zigggy.spread")


class Spread(Agent):
    name = "SPREAD"
    role = "Execution & microstructure: spreads, depth, slippage, arbitrage screening, order submission and fill monitoring."

    def __init__(self, core) -> None:
        super().__init__(core)
        self.last_report: MicrostructureReport | None = None

    async def assess(self, cands: CandidateSet) -> MicrostructureReport:
        t0 = self.begin()
        err = None
        try:
            stats = {s.market.key: spread_stats(s.book) for s in cands.snapshots}
            arbs = []
            groups = defaultdict(list)
            for s in cands.snapshots:
                if s.market.mutually_exclusive and s.market.event_id:
                    groups[s.market.event_id].append((s.market, s.book))
            for event_id, members in groups.items():
                total_open = sum(1 for m in self.core.markets.markets.values() if m.event_id == event_id)
                if len(members) == total_open and len(members) > 1:
                    arbs += structural_arbitrage(members, exhaustive=False)
            seen = set()
            snaps = {s.market.key: s for s in cands.snapshots}
            for key, links in self.core.links_by_market.items():
                for other, rel in links:
                    pair = tuple(sorted((key, other)))
                    if pair in seen or rel != "equivalent" or key not in snaps or other not in snaps:
                        continue
                    seen.add(pair)
                    arbs += cross_venue_arbitrage((snaps[key].market, snaps[key].book), (snaps[other].market, snaps[other].book),
                                                  confirmed_equivalent=True)
            wide = [k for k, st in stats.items() if "wide_spread" in st.flags]
            if arbs:
                self.finding(f"{len(arbs)} arbitrage candidate(s) (not risk-free; flagged)")
                for a in arbs[:3]:
                    await self.core.events.emit("opportunity_qualified", f"arbitrage screen: {a.kind} net {a.net_profit_per_set:+.4f}/set "
                                                f"[{', '.join(a.risk_flags)}]", agent=self.name, persist=False, data=a.to_dict())
            if wide:
                self.finding(f"{len(wide)} wide-spread market(s) this cycle")
            report = MicrostructureReport(stats, arbs)
            self.last_report = report
            return report
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)

    async def execute(self, cycle_id: str, verdicts: list[RiskVerdict], account: str, mode: TradingMode) -> int:
        t0 = self.begin()
        err = None
        n = 0
        try:
            core = self.core
            limits = core.state.runtime.risk
            for v in verdicts:
                c = v.candidate
                book = core.markets.books.get(c.market.key)
                allowed, why = core.state.can_open_new_positions
                if not allowed and not (c.action.value == "SELL" and "paused" in why):
                    await self._mark(v.opportunity_id, "expired", f"blocked at execution: {why}")
                    continue
                if book is None or book.age_s() > limits.max_staleness_s:
                    await self._mark(v.opportunity_id, "expired", "book stale at execution time")
                    await core.events.emit("opportunity_expired", f"expired (stale book) before submission: {c.market.question[:70]}",
                                           agent=self.name, market_key=c.market.key)
                    continue
                best_now = book.best_ask(c.outcome) if c.action.value == "BUY" else book.best_bid(c.outcome)
                if best_now is None or (c.action.value == "BUY" and best_now > v.decision.limit_price) or \
                        (c.action.value == "SELL" and best_now < v.decision.limit_price):
                    await self._mark(v.opportunity_id, "expired", "price moved through limit before submission")
                    await core.events.emit("opportunity_expired", f"expired (price moved) before submission: {c.market.question[:70]}",
                                           agent=self.name, market_key=c.market.key)
                    continue
                order = await core.execution.submit(opportunity_id=v.opportunity_id, market=c.market, outcome=c.outcome, action=c.action,
                                                    decision=v.decision, account=account, mode=mode, ttl_s=limits.order_ttl_s,
                                                    risk_assessment_id=v.assessment_id,
                                                    estimate_id=v.estimate_id)
                if order:
                    n += 1
                    await self._mark(v.opportunity_id, "executed", f"order {order.client_order_id} {order.status}")
                    await self.record(cycle_id, "submit", c.market.key, order_id=order.id, client_order_id=order.client_order_id,
                                      status=order.status, qty=order.quantity, limit=order.limit_price)
                    self.finding(f"{order.status}: {order.action} {order.quantity:.0f} {order.outcome} ≤{order.limit_price:.3f}", c.market.key)
            return n
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)

    async def _mark(self, oid: str, status: str, note: str) -> None:
        async with self.core.db.tx() as s:
            op = await s.get(Opportunity, oid)
            if op:
                op.status = status
                op.details = {**(op.details or {}), "execution_note": note, "execution_ts": utcnow().isoformat()}
