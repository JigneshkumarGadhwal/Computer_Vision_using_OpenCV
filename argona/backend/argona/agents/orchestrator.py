"""ORCHESTRATOR - central decision coordinator.

One cycle:  RIGO.refresh -> SPREAD.assess -> BRAM.research -> ILSA.evaluate -> TESS.review -> SPREAD.execute
Each stage has a timeout; outputs are validated before being passed on; every stage decision is
persisted. Disagreement policy: TESS veto is final; SPREAD may only expire (never upsize) an
approved order; ILSA abstention means no trade.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any

from ..domain import TradingMode, utcnow
from .base import Agent, CandidateSet, EvidenceBundle, Valuation

log = logging.getLogger("argona.orchestrator")

STAGE_TIMEOUT_S = {"refresh": 120, "assess": 30, "research": 90, "evaluate": 60, "review": 60, "execute": 90}


class ValidationError(Exception):
    pass


class Orchestrator(Agent):
    name = "ORCHESTRATOR"
    role = "Central coordinator: schedules agents, validates messages, enforces timeouts and mandatory risk approval, keeps the decision trail."

    def __init__(self, core) -> None:
        super().__init__(core)
        self.cycles = 0
        self.last_cycle: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def _stage(self, name: str, coro):
        try:
            return await asyncio.wait_for(coro, timeout=STAGE_TIMEOUT_S[name])
        except asyncio.TimeoutError as e:
            raise ValidationError(f"stage {name} timed out after {STAGE_TIMEOUT_S[name]}s") from e

    @staticmethod
    def _validate_candidates(c: CandidateSet) -> None:
        for s in c.snapshots:
            if s.book.mid is not None and not (0 < s.book.mid < 1):
                raise ValidationError(f"invalid book mid for {s.market.key}")

    @staticmethod
    def _validate_valuation(v: Valuation) -> None:
        for k, e in v.estimates.items():
            if not e.abstained and (e.q is None or not (0 < e.q < 1) or e.sigma is None or math.isnan(e.sigma)):
                raise ValidationError(f"invalid estimate for {k}")
        for c in v.candidates:
            if c.qualified and (c.quote.vwap is None or c.net_ev <= 0):
                raise ValidationError(f"qualified candidate without positive executable EV: {c.market.key}")

    async def run_cycle(self) -> dict[str, Any]:
        if self._lock.locked():
            return {"skipped": "cycle already running"}
        async with self._lock:
            return await self._run()

    async def _run(self) -> dict[str, Any]:
        core = self.core
        t0 = self.begin()
        cycle_id = uuid.uuid4().hex[:12]
        result: dict[str, Any] = {"cycle_id": cycle_id, "started": utcnow().isoformat()}
        err = None
        try:
            mode = core.state.mode
            account = core.state.account_name
            cands = await self._stage("refresh", core.rigo.refresh(cycle_id))
            self._validate_candidates(cands)
            result["snapshots"] = len(cands.snapshots)
            await self._stage("assess", core.spread.assess(cands))
            ev: EvidenceBundle = await self._stage("research", core.bram.research(cands))
            held: dict[tuple[str, str], float] = {}
            pf = await core.portfolio.state(account) if account else None
            if pf:
                held = {(p.market_key, p.outcome): p.contracts for p in pf.positions}
            val: Valuation = await self._stage("evaluate", core.ilsa.evaluate(cands, ev, held))
            self._validate_valuation(val)
            opp_ids = await core.ilsa.persist_opportunities(cycle_id, val, mode.value)
            qualified = [c for c in val.candidates if c.qualified]
            rejected = [c for c in val.candidates if not c.qualified]
            result.update(estimated=core.markets.stats.estimated, qualified=len(qualified), rejected=len(rejected))
            if rejected:
                reasons: dict[str, int] = {}
                for c in rejected:
                    if c.reasons:
                        r = c.reasons[0].split(":")[0].split(" ")[0:3]
                        k = " ".join(r)
                        reasons[k] = reasons.get(k, 0) + 1
                top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
                await core.events.emit("opportunity_rejected", f"{len(rejected)} candidate(s) rejected; top reasons: "
                                       + "; ".join(f"{k} ×{n}" for k, n in top), agent="ILSA", persist=False,
                                       data={"n": len(rejected), "reasons": dict(top)})
            executed = 0
            if account and mode is not TradingMode.STOPPED and qualified:
                verdicts = await self._stage("review", core.tess.review(cycle_id, qualified, opp_ids, core.markets.books, account, mode))
                for v in verdicts:  # mandatory: every order must carry an approved TESS verdict
                    if not v.decision.approved or v.decision.contracts <= 0:
                        raise ValidationError("unapproved verdict reached execution")
                    v.estimate_id = val.estimate_ids.get(v.candidate.market.key)
                result["approved"] = len(verdicts)
                executed = await self._stage("execute", core.spread.execute(cycle_id, verdicts, account, mode))
            result["orders"] = executed
            self.cycles += 1
            result["finished"] = utcnow().isoformat()
            self.last_cycle = result
            await self.record(cycle_id, "cycle", None, summary=result)
            if executed or self.cycles % 20 == 1:
                await core.events.emit("cycle_completed", f"cycle {cycle_id}: {result.get('snapshots', 0)} books, "
                                       f"{result.get('estimated', 0)} estimates, {result.get('qualified', 0)} qualified, {executed} order(s)",
                                       agent=self.name, persist=bool(executed), data=result)
            return result
        except Exception as e:
            err = e
            result["error"] = f"{type(e).__name__}: {e}"
            self.last_cycle = result
            await core.events.emit("scheduler_error", f"cycle {cycle_id} failed: {result['error'][:200]}", severity="warning", agent=self.name)
            raise
        finally:
            self.end(t0, err)
