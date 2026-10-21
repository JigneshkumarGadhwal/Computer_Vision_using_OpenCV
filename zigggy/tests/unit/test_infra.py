import asyncio

import pytest

from zigggy.config import NotificationPrefs
from zigggy.logging_setup import redact
from zigggy.notifications.service import NotificationService
from zigggy.scheduler.scheduler import Scheduler


def test_redaction():
    s = redact('api_key="sk-123456" token: abcdef password=hunter2 0x' + "a" * 64)
    assert "sk-123456" not in s and "abcdef" not in s and "hunter2" not in s and "a" * 64 not in s
    assert "REDACTED" in redact("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----")


class Chan:
    name = "test"

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, text):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(text)


async def test_notifications_prefix_filter_dedupe():
    ch = Chan()
    prefs = NotificationPrefs()
    n = NotificationService(None, lambda: prefs, channels=[ch])  # type: ignore[arg-type]
    await n.on_event({"type": "order_filled", "severity": "info", "mode": "PAPER", "message": "BUY 10 YES filled"})
    await n.on_event({"type": "order_filled", "severity": "info", "mode": "PAPER", "message": "BUY 10 YES filled"})  # dup
    await n.on_event({"type": "probability_updated", "severity": "info", "mode": "PAPER", "message": "noise"})
    await n.on_event({"type": "emergency_stop", "severity": "critical", "mode": "LIVE", "message": "EMERGENCY STOP"})
    assert len(ch.sent) == 2
    assert ch.sent[0].startswith("[PAPER]") and "[LIVE]" in ch.sent[1]


async def test_notification_failure_is_contained():
    n = NotificationService(None, lambda: NotificationPrefs(), channels=[Chan(fail=True)])  # type: ignore[arg-type]
    res = await n.send("x")
    assert "error" in res["test"] and n.failed == 1


async def test_scheduler_no_overlap_and_backoff():
    sch = Scheduler()
    calls = {"n": 0}
    gate = asyncio.Event()

    async def slow():
        calls["n"] += 1
        await gate.wait()

    j = sch.add("slow", 0.01, slow)
    t = asyncio.create_task(sch.run_now("slow"))
    await asyncio.sleep(0.01)
    assert (await sch.run_now("slow")) == {"skipped": "already running"}
    gate.set()
    await t
    assert calls["n"] == 1 and j.skipped_overlap == 1

    async def boom():
        raise ValueError("x")

    jb = sch.add("boom", 0.01, boom)
    await sch.run_now("boom")
    await sch.run_now("boom")
    assert jb.consecutive_failures == 2 and jb.last_error.startswith("ValueError")


async def test_scheduler_recovers_after_failure():
    sch = Scheduler()
    state = {"fail": True, "ok": 0}

    async def flaky():
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("transient")
        state["ok"] += 1

    j = sch.add("flaky", 0.01, flaky)
    await sch.run_now("flaky")
    await sch.run_now("flaky")
    assert j.consecutive_failures == 0 and state["ok"] == 1 and j.failures == 1
