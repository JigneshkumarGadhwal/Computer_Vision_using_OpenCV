"""TESS - portfolio & risk agent. Sizes positions, enforces limits, vetoes anything that fails."""
from __future__ import annotations

import logging

from sqlalchemy import update

from ..database.models import Opportunity, RiskAssessment
from ..domain import TradingMode
from ..risk.engine import RiskContext, RiskEngine
from ..spread_engine.opportunity import Candidate
from .base import Agent, RiskVerdict

log = logging.getLogger("zigggy.tess")


class Tess(Agent):
    name = "TESS"
    role = "Portfolio & risk: Kelly sizing, exposure/drawdown/daily-loss limits, capital checks. Veto authority over every trade."

    async def review(self, cycle_id: str, candidates: list[Candidate], opp_ids: dict[int, str], books, account: str,
                     mode: TradingMode) -> list[RiskVerdict]:
        t0 = self.begin()
        err = None
        approved: list[RiskVerdict] = []
        try:
            core = self.core
            engine = RiskEngine(core.state.runtime.risk)
            allowed, why = core.state.can_open_new_positions
            pf = await core.portfolio.state(account)
            if pf is None:
                return []
            await self._limit_alerts(pf)
            working = {o.market_key for o in await core.execution.working_orders(account)}
            recent = await core.execution.recent_entry_markets(account, core.state.runtime.risk.reentry_cooldown_s)
            held = {(p.market_key, p.outcome): p.contracts for p in pf.positions}
            rows = []
            for c in candidates:
                if not c.qualified or id(c) not in opp_ids:
                    continue
                tradable, vreason = core.venue_tradable(c.market, mode)
                ctx = RiskContext(mode=mode, trading_allowed=allowed, trading_block_reason=why, working_order_markets=working,
                                  held=held, venue_tradable=tradable, venue_reason=vreason, recent_entries=recent)
                d = engine.assess(c, books[c.market.key], pf, ctx)
                oid = opp_ids[id(c)]
                async with core.db.tx() as s:
                    ra = RiskAssessment(opportunity_id=oid, account=account, approved=d.approved, contracts=d.contracts,
                                        stake_usd=d.stake_usd, kelly_raw=d.kelly_raw, kelly_applied=d.kelly_applied, checks=d.checks,
                                        reasons=d.reasons, limits=core.state.runtime.risk.model_dump())
                    s.add(ra)
                    await s.flush()
                    ra_id = ra.id
                    await s.execute(update(Opportunity).where(Opportunity.id == oid).values(
                        status="approved" if d.approved else "vetoed", reasons=c.reasons + d.reasons))
                rows.append(("approve" if d.approved else "veto", c.market.key,
                             {"opportunity_id": oid, "contracts": d.contracts, "reasons": d.reasons[:4]}))
                if d.approved:
                    approved.append(RiskVerdict(c, d, oid, ra_id))
                    working.add(c.market.key)
                    recent.add(c.market.key)
                    await core.events.emit("risk_approved", f"approved {d.contracts:.0f} × {c.outcome.value} ≤{d.limit_price:.3f} "
                                           f"(${d.stake_usd:.2f}, kelly {d.kelly_applied or 0:.3f}): {c.market.question[:70]}",
                                           agent=self.name, market_key=c.market.key, data={"opportunity_id": oid, "checks": d.checks})
                    # reserve exposure for subsequent candidates in this cycle
                    if c.action.value == "BUY":
                        pf.exposure += d.stake_usd
                        pf.available -= d.stake_usd
                        pf.exposure_by_market[c.market.key] = pf.exposure_by_market.get(c.market.key, 0) + d.stake_usd
                        ev = c.market.event_id or c.market.key
                        pf.exposure_by_event[ev] = pf.exposure_by_event.get(ev, 0) + d.stake_usd
                        pf.exposure_by_category[c.market.category] = pf.exposure_by_category.get(c.market.category, 0) + d.stake_usd
                        pf.open_orders += 1
                else:
                    await core.events.emit("risk_rejected", f"veto: {d.reasons[0] if d.reasons else 'failed checks'} — {c.market.question[:70]}",
                                           agent=self.name, market_key=c.market.key, data={"opportunity_id": oid, "reasons": d.reasons})
            await self.record_many(cycle_id, rows)
            if rows:
                self.finding(f"reviewed {len(rows)}: {len(approved)} approved, {len(rows) - len(approved)} vetoed")
            return approved
        except Exception as e:
            err = e
            raise
        finally:
            self.end(t0, err)

    async def _limit_alerts(self, pf) -> None:
        L = self.core.state.runtime.risk
        flags = self.core.risk_alerts
        if pf.day_pnl <= -L.max_daily_loss_usd and not flags.get("daily_loss"):
            flags["daily_loss"] = True
            await self.core.events.emit("risk_limit_breached", f"daily loss limit hit: {pf.day_pnl:.2f} ≤ -{L.max_daily_loss_usd:.2f}; "
                                        "new entries blocked until next UTC day", severity="critical", agent=self.name)
        elif pf.day_pnl > -L.max_daily_loss_usd:
            flags["daily_loss"] = False
        if pf.drawdown >= L.max_drawdown_pct and not flags.get("drawdown"):
            flags["drawdown"] = True
            await self.core.events.emit("risk_limit_breached", f"max drawdown hit: {pf.drawdown:.1%} ≥ {L.max_drawdown_pct:.0%}; new entries blocked",
                                        severity="critical", agent=self.name)
        elif pf.drawdown < L.max_drawdown_pct:
            flags["drawdown"] = False
