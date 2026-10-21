"""Operator notifications: Telegram (Bot API) and SMTP email, plus the log channel.

Messages carry the event text only (no strategy internals, no credentials, no account ids) and
are prefixed [PAPER] / [LIVE] / [SYSTEM] so simulated activity is never mistaken for real money.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import time
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from ..config import NotificationPrefs, Settings
from ..logging_setup import redact

log = logging.getLogger("zigggy.notify")

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
CATEGORY = {
    "opportunity_qualified": "opportunities",
    "order_submitted": "orders", "order_rejected": "orders", "order_unknown": "orders", "order_canceled": "orders",
    "order_expired": "orders", "order_acknowledged": "orders",
    "order_filled": "fills", "order_partially_filled": "fills", "pnl_realized": "fills", "market_resolved": "fills",
    "risk_limit_breached": "risk", "emergency_stop": "risk", "reconciliation_mismatch": "risk", "risk_rejected": "risk",
    "provider_disconnected": "provider", "provider_recovered": "provider", "provider_error": "provider",
    "scheduler_error": "system", "system": "system", "mode_changed": "system", "trading_paused": "system",
    "notification_failed": None,
}
ALWAYS = {"emergency_stop", "risk_limit_breached", "reconciliation_mismatch", "mode_changed", "system"}


class Channel(Protocol):
    name: str

    async def send(self, text: str) -> None: ...


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.client = httpx.AsyncClient(timeout=10, transport=transport)

    async def send(self, text: str) -> None:
        r = await self.client.post(self._url, json={"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True})
        if r.status_code >= 400:
            raise RuntimeError(f"telegram HTTP {r.status_code}")


class EmailChannel:
    name = "email"

    def __init__(self, s: Settings) -> None:
        self.s = s

    def _send_sync(self, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = "ZigGgy: " + text[:80]
        msg["From"] = self.s.smtp_from or self.s.smtp_user or "zigggy@localhost"
        msg["To"] = self.s.smtp_to
        msg.set_content(text)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=15) as smtp:
            if self.s.smtp_starttls:
                smtp.starttls()
            if self.s.smtp_user and self.s.smtp_password:
                smtp.login(self.s.smtp_user, self.s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def send(self, text: str) -> None:
        await asyncio.to_thread(self._send_sync, text)


class NotificationService:
    def __init__(self, settings: Settings, prefs_provider, channels: list[Channel] | None = None) -> None:
        self.prefs_provider = prefs_provider
        self.channels: list[Channel] = channels if channels is not None else self._from_settings(settings)
        self._recent: dict[str, float] = {}
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None
        self.history: list[dict[str, Any]] = []

    @staticmethod
    def _from_settings(s: Settings) -> list[Channel]:
        ch: list[Channel] = []
        if s.telegram_bot_token and s.telegram_chat_id:
            ch.append(TelegramChannel(s.telegram_bot_token.get_secret_value(), s.telegram_chat_id))
        if s.smtp_host and s.smtp_to:
            ch.append(EmailChannel(s))
        return ch

    def describe(self) -> dict[str, Any]:
        return {"channels": [c.name for c in self.channels], "sent": self.sent, "failed": self.failed,
                "last_error": self.last_error, "recent": self.history[-20:]}

    def _wanted(self, ev: dict[str, Any], prefs: NotificationPrefs) -> bool:
        if not prefs.enabled:
            return False
        t = ev["type"]
        if t in ALWAYS:
            return True
        cat = CATEGORY.get(t)
        if cat is None:
            return False
        if cat == "opportunities" and not prefs.notify_opportunities:
            return False
        if cat == "orders" and not prefs.notify_orders:
            return False
        if cat == "fills" and not prefs.notify_fills:
            return False
        if cat == "risk" and not prefs.notify_risk:
            return False
        if cat == "provider" and not prefs.notify_provider:
            return False
        if cat in ("orders", "fills"):
            return True  # explicitly enabled categories ignore the severity floor
        return SEVERITY_RANK.get(ev.get("severity", "info"), 0) >= SEVERITY_RANK[prefs.min_severity]

    @staticmethod
    def format(ev: dict[str, Any]) -> str:
        mode = ev.get("mode")
        msg = ev["message"]
        if msg.startswith("[PAPER]") or msg.startswith("[LIVE]"):
            tag = ""
        else:
            tag = "[LIVE] " if mode == "LIVE" else "[PAPER] " if mode == "PAPER" else "[SYSTEM] "
        icon = {"critical": "🛑 ", "warning": "⚠️ "}.get(ev.get("severity", "info"), "")
        return redact(f"{icon}{tag}{msg}")

    async def on_event(self, ev: dict[str, Any]) -> None:
        if not self.channels or not self._wanted(ev, self.prefs_provider()):
            return
        key = f"{ev['type']}:{ev['message'][:60]}"
        now = time.monotonic()
        if now - self._recent.get(key, 0) < 300:
            return
        self._recent[key] = now
        await self.send(self.format(ev))

    async def send(self, text: str) -> dict[str, Any]:
        results = {}
        for ch in self.channels:
            try:
                await ch.send(text)
                self.sent += 1
                results[ch.name] = "ok"
            except Exception as e:  # delivery failures are logged, never crash the system
                self.failed += 1
                self.last_error = f"{ch.name}: {type(e).__name__}"
                results[ch.name] = f"error: {type(e).__name__}"
                log.warning("notification via %s failed: %s", ch.name, type(e).__name__)
        self.history.append({"ts": time.time(), "text": text[:200], "results": results})
        self.history = self.history[-100:]
        return results
