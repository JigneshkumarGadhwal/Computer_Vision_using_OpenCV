"""Agent base class + typed inter-agent messages.

Agents never call each other directly: the ORCHESTRATOR passes validated messages between them
and persists every decision (``agent_decisions``). Only SPREAD can reach the execution service,
and only with a TESS-approved ``RiskVerdict``.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..database.models import AgentDecision
from ..domain import NormalizedMarket, OrderBook, utcnow
from ..probability.base import EvidenceItem
from ..probability.engine import CombinedEstimate
from ..risk.engine import RiskDecision
from ..spread_engine.engine import ArbCandidate, SpreadStats
from ..spread_engine.opportunity import Candidate

if TYPE_CHECKING:
    from ..core import Core

log = logging.getLogger("argona.agents")


# ------------------------------------------------------------------ messages
@dataclass
class MarketSnapshot:
    market: NormalizedMarket
    book: OrderBook
    snapshot_id: int | None = None


@dataclass
class CandidateSet:  # RIGO -> ORCHESTRATOR
    cycle_id: str
    snapshots: list[MarketSnapshot]
    held: list[str]
    stale: list[str]
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class MicrostructureReport:  # SPREAD -> ORCHESTRATOR
    stats: dict[str, SpreadStats]
    arbitrage: list[ArbCandidate]


@dataclass
class EvidenceBundle:  # BRAM -> ILSA
    by_kind: dict[str, list[EvidenceItem]]
    contradictions: list[str]
    sources_used: list[str]


@dataclass
class Valuation:  # ILSA -> TESS
    estimates: dict[str, CombinedEstimate]
    estimate_ids: dict[str, int]
    candidates: list[Candidate]


@dataclass
class RiskVerdict:  # TESS -> SPREAD
    candidate: Candidate
    decision: RiskDecision
    opportunity_id: str
    assessment_id: int | None
    estimate_id: int | None = None


# --------------------------------------------------------------------- base
class Agent:
    name = "AGENT"
    role = ""

    def __init__(self, core: "Core") -> None:
        self.core = core
        self.status = "idle"
        self.last_run: datetime | None = None
        self.last_duration_ms: float | None = None
        self.decisions = 0
        self.errors = 0
        self.last_error: str | None = None
        self.findings: deque[dict[str, Any]] = deque(maxlen=25)
        self.api_calls = 0

    def finding(self, text: str, market_key: str | None = None, **data: Any) -> None:
        self.findings.appendleft({"ts": utcnow().isoformat(), "text": text, "market_key": market_key, **data})

    async def record(self, cycle_id: str | None, decision: str, market_key: str | None = None, **details: Any) -> None:
        self.decisions += 1
        async with self.core.db.tx() as s:
            s.add(AgentDecision(agent=self.name, cycle_id=cycle_id, market_key=market_key, decision=decision, details=details))

    async def record_many(self, cycle_id: str | None, rows: list[tuple[str, str | None, dict[str, Any]]]) -> None:
        if not rows:
            return
        self.decisions += len(rows)
        async with self.core.db.tx() as s:
            for decision, mk, details in rows:
                s.add(AgentDecision(agent=self.name, cycle_id=cycle_id, market_key=mk, decision=decision, details=details))

    def begin(self) -> float:
        import time

        self.status = "running"
        return time.perf_counter()

    def end(self, t0: float, error: Exception | None = None) -> None:
        import time

        self.last_run = utcnow()
        self.last_duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        if error:
            self.errors += 1
            self.last_error = f"{type(error).__name__}: {error}"[:300]
            self.status = "error"
        else:
            self.status = "idle"

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "status": self.status,
                "last_run": self.last_run.isoformat() if self.last_run else None, "last_duration_ms": self.last_duration_ms,
                "decisions": self.decisions, "errors": self.errors, "last_error": self.last_error,
                "api_calls": self.api_calls, "findings": list(self.findings)[:10]}
