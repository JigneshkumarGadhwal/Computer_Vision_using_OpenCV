"""Persistent in-process scheduler: interval jobs, no overlapping runs, exponential backoff with jitter
on failure, health stats. Runs inside the backend process (systemd/Docker keeps the process alive)."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from ..domain import utcnow

log = logging.getLogger("argona.scheduler")


@dataclass
class Job:
    name: str
    interval_s: float
    fn: Callable[[], Awaitable[Any]]
    initial_delay_s: float = 0.0
    enabled: Callable[[], bool] = lambda: True
    running: bool = False
    runs: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    skipped_overlap: int = 0
    last_start: datetime | None = None
    last_ok: datetime | None = None
    last_error: str | None = None
    last_duration_ms: float | None = None
    next_run: datetime | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    trigger: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"name": self.name, "interval_s": self.interval_s, "running": self.running, "runs": self.runs,
                "failures": self.failures, "consecutive_failures": self.consecutive_failures,
                "skipped_overlap": self.skipped_overlap, "last_start": iso(self.last_start), "last_ok": iso(self.last_ok),
                "last_error": self.last_error, "last_duration_ms": self.last_duration_ms, "next_run": iso(self.next_run),
                "enabled": self.enabled()}


class Scheduler:
    def __init__(self, on_error: Callable[[Job, Exception], Awaitable[None]] | None = None) -> None:
        self.jobs: dict[str, Job] = {}
        self.started_at: datetime | None = None
        self._stopping = False
        self.on_error = on_error

    def add(self, name: str, interval_s: float, fn: Callable[[], Awaitable[Any]], *, initial_delay_s: float = 0.0,
            enabled: Callable[[], bool] | None = None) -> Job:
        j = Job(name, interval_s, fn, initial_delay_s, enabled or (lambda: True))
        self.jobs[name] = j
        return j

    async def run_now(self, name: str) -> Any:
        """Run a job immediately (respecting the no-overlap rule)."""
        j = self.jobs[name]
        if j.running:
            j.skipped_overlap += 1
            return {"skipped": "already running"}
        return await self._execute(j)

    def poke(self, name: str) -> None:
        j = self.jobs.get(name)
        if j:
            j.trigger.set()

    async def _execute(self, j: Job) -> Any:
        import time

        j.running = True
        j.last_start = utcnow()
        t0 = time.perf_counter()
        try:
            res = await j.fn()
            j.runs += 1
            j.last_ok = utcnow()
            j.consecutive_failures = 0
            j.last_error = None
            return res
        except asyncio.CancelledError:
            raise
        except Exception as e:  # recoverable: log, back off, keep the loop alive
            j.failures += 1
            j.consecutive_failures += 1
            j.last_error = f"{type(e).__name__}: {e}"[:300]
            log.exception("job %s failed", j.name)
            if self.on_error:
                try:
                    await self.on_error(j, e)
                except Exception:
                    log.exception("scheduler on_error hook failed")
            return None
        finally:
            j.running = False
            j.last_duration_ms = round((time.perf_counter() - t0) * 1000, 1)

    async def _loop(self, j: Job) -> None:
        if j.initial_delay_s:
            try:  # interruptible, so shutdown never waits on an initial delay
                await asyncio.wait_for(j.trigger.wait(), timeout=j.initial_delay_s)
            except asyncio.TimeoutError:
                pass
        while not self._stopping:
            if j.enabled():
                await self._execute(j)
            delay = j.interval_s
            if j.consecutive_failures:
                delay = min(j.interval_s * (2 ** min(j.consecutive_failures, 6)), 1800) * random.uniform(0.8, 1.2)
            else:
                delay *= random.uniform(0.97, 1.03)  # small jitter avoids synchronised bursts
            from datetime import timedelta

            j.next_run = utcnow() + timedelta(seconds=delay)
            j.trigger.clear()
            try:
                await asyncio.wait_for(j.trigger.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self.started_at = utcnow()
        self._stopping = False
        for j in self.jobs.values():
            j.task = asyncio.create_task(self._loop(j), name=f"job:{j.name}")

    async def stop(self, timeout: float = 20.0) -> None:
        """Safe shutdown: stop scheduling, let running jobs finish (bounded), then cancel."""
        self._stopping = True
        for j in self.jobs.values():
            j.trigger.set()
        tasks = [j.task for j in self.jobs.values() if j.task]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def describe(self) -> dict[str, Any]:
        return {"started_at": self.started_at.isoformat() if self.started_at else None, "running": not self._stopping and bool(self.started_at),
                "jobs": [j.to_dict() for j in self.jobs.values()]}
