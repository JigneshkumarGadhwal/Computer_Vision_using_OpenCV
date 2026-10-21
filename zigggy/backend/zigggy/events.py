"""Event bus: every activity-log entry is a persisted backend event, fanned out to SSE clients."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Awaitable, Callable

from .database.models import AppEvent
from .database.session import Database
from .domain import utcnow
from .logging_setup import redact

log = logging.getLogger("zigggy.events")

# Canonical event types (the dashboard colours by these)
EVENT_TYPES = {
    "market_discovered", "market_updated", "market_rejected", "research_completed", "probability_updated",
    "opportunity_qualified", "opportunity_rejected", "opportunity_expired", "risk_approved", "risk_rejected",
    "order_submitted", "order_acknowledged", "order_partially_filled", "order_filled", "order_canceled",
    "order_rejected", "order_expired", "order_unknown", "market_resolved", "pnl_realized", "provider_disconnected",
    "provider_recovered", "provider_error", "rate_limited", "mode_changed", "emergency_stop", "trading_paused",
    "trading_resumed", "settings_changed", "reconciliation", "reconciliation_mismatch", "risk_limit_breached",
    "scheduler_error", "system", "backtest", "notification_failed", "cycle_completed",
}


def _scrub(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(redact(json.dumps(data, default=str)))
    except Exception:  # pragma: no cover
        return {}


class EventBus:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._subscribers: set[asyncio.Queue] = set()
        self._hooks: list[Callable[[dict[str, Any]], Awaitable[None]]] = []
        self.recent: deque[dict[str, Any]] = deque(maxlen=500)
        self.mode_provider: Callable[[], str] | None = None

    def add_hook(self, fn: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._hooks.append(fn)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def emit(
        self,
        type: str,
        message: str,
        *,
        severity: str = "info",
        agent: str | None = None,
        market_key: str | None = None,
        data: dict[str, Any] | None = None,
        mode: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        if type not in EVENT_TYPES:
            log.warning("unknown event type %s", type)
        mode = mode or (self.mode_provider() if self.mode_provider else None)
        payload = {
            "ts": utcnow().isoformat(),
            "type": type,
            "severity": severity,
            "agent": agent,
            "market_key": market_key,
            "mode": mode,
            "message": redact(message),
            "data": _scrub(data or {}),
        }
        if persist:
            try:
                async with self.db.tx() as s:
                    ev = AppEvent(type=type, severity=severity, agent=agent, market_key=market_key, mode=mode,
                                  message=payload["message"], data=payload["data"])
                    s.add(ev)
                    await s.flush()
                    payload["id"] = ev.id
            except Exception:  # never let event persistence break trading flow
                log.exception("failed to persist event %s", type)
        self.recent.append(payload)
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        for hook in self._hooks:
            try:
                await hook(payload)
            except Exception:
                log.exception("event hook failed")
        lvl = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.ERROR}.get(severity, logging.INFO)
        log.log(lvl, "%s: %s", type, payload["message"], extra={"ctx": {"market": market_key, "agent": agent}})
        return payload
