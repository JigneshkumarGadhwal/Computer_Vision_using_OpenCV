"""Persistent system state: trading mode, emergency stop, runtime settings (with audit trail)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from .config import RuntimeSettings, Settings
from .database.models import ConfigHistory, SettingKV
from .database.session import Database
from .domain import TradingMode, utcnow

log = logging.getLogger("zigggy.state")


class StateStore:
    KEY_RUNTIME = "runtime_settings"
    KEY_SYSTEM = "system_state"

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.runtime = RuntimeSettings()
        self.mode: TradingMode = TradingMode.STOPPED
        self.live_venue: str | None = None
        self.emergency_stop: bool = False
        self.emergency_reason: str | None = None
        self.previous_mode: TradingMode | None = None

    async def load(self) -> None:
        async with self.db.session() as s:
            rows = {r.key: r.value for r in (await s.execute(select(SettingKV))).scalars()}
        if self.KEY_RUNTIME in rows:
            try:
                self.runtime = RuntimeSettings.model_validate(rows[self.KEY_RUNTIME])
            except Exception:
                log.exception("stored runtime settings invalid; falling back to defaults")
                self.runtime = RuntimeSettings()
        sysstate = rows.get(self.KEY_SYSTEM)
        if sysstate:
            self.previous_mode = TradingMode(sysstate.get("mode", "STOPPED"))
            self.mode = self.previous_mode
            self.live_venue = sysstate.get("live_venue")
            self.emergency_stop = bool(sysstate.get("emergency_stop", False))
            self.emergency_reason = sysstate.get("emergency_reason")
        else:
            self.mode = TradingMode(self.settings.start_mode)
            await self.save_system()
            await self.save_runtime(self.runtime, actor="install")

    def system_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "live_venue": self.live_venue,
            "emergency_stop": self.emergency_stop,
            "emergency_reason": self.emergency_reason,
        }

    async def _put(self, key: str, value: dict[str, Any], actor: str, audit: bool = True) -> None:
        async with self.db.tx() as s:
            row = await s.get(SettingKV, key)
            old = row.value if row else None
            if row:
                row.value = value
                row.updated_at = utcnow()
            else:
                s.add(SettingKV(key=key, value=value))
            if audit and old != value:
                s.add(ConfigHistory(key=key, old_value=old, new_value=value, actor=actor))

    async def save_system(self, actor: str = "system") -> None:
        await self._put(self.KEY_SYSTEM, self.system_dict(), actor)

    async def save_runtime(self, runtime: RuntimeSettings, actor: str = "api") -> None:
        self.runtime = runtime
        await self._put(self.KEY_RUNTIME, runtime.model_dump(mode="json"), actor)

    @property
    def account_name(self) -> str | None:
        if self.mode is TradingMode.PAPER:
            return "paper"
        if self.mode is TradingMode.LIVE and self.live_venue:
            return f"live:{self.live_venue}"
        return None

    @property
    def can_open_new_positions(self) -> tuple[bool, str]:
        if self.emergency_stop:
            return False, "emergency stop active"
        if self.mode is TradingMode.STOPPED:
            return False, "trading mode STOPPED"
        if self.runtime.trading_paused:
            return False, "new trades paused by operator"
        return True, "ok"
