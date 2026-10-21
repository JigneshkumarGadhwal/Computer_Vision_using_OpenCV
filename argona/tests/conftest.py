"""Shared fixtures: isolated temp database, SIM venue with a controllable clock, no network."""
from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from argona.config import Settings
from argona.core import Core
from argona.database.session import run_migrations
from argona.exchanges.registry import ProviderRegistry
from argona.exchanges.sim import SimWorld


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        # starts at real time so wall-clock based checks (staleness, time-to-resolution) agree at t0
        self.now = start or datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class NullSource:
    """Stands in for network evidence sources (Coinbase/Kraken/Open-Meteo) in tests."""

    async def spot(self, asset):
        return []

    async def vol(self, asset):
        return []

    async def forecast(self, st):
        return []

    async def close(self):
        return None


class RecordingNotifier:
    def __init__(self) -> None:
        self.channels = []
        self.events = []

    async def on_event(self, ev):
        self.events.append(ev)

    def describe(self):
        return {"channels": [], "sent": 0, "failed": 0, "last_error": None, "recent": []}

    async def send(self, text):
        return {}


PG_URL = os.environ.get("ARGONA_TEST_DATABASE_URL")  # e.g. postgresql+asyncpg://argona@127.0.0.1:55432/argona_test


def _reset_pg(url: str) -> None:
    """Drop every table so each test starts from an empty, freshly migrated PostgreSQL schema."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def go():
        eng = create_async_engine(url)
        async with eng.begin() as c:
            await c.execute(text("DROP SCHEMA public CASCADE"))
            await c.execute(text("CREATE SCHEMA public"))
        await eng.dispose()

    t = threading.Thread(target=lambda: asyncio.run(go()))
    t.start()
    t.join()


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(_env_file=None, providers="sim", data_dir=tmp_path / "data", log_json=False, log_level="WARNING",
                sim_markets=16, paper_starting_capital=10_000.0)
    if PG_URL:
        _reset_pg(PG_URL)
        base["database_url"] = PG_URL
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    s = make_settings(tmp_path)
    run_migrations(s.resolved_database_url)
    return s


async def build_core(settings: Settings, world: SimWorld | None = None) -> Core:
    reg = ProviderRegistry(settings, sim_world=world)
    core = Core(settings, registry=reg, notifier=RecordingNotifier(), crypto_source=NullSource(), weather_source=NullSource())
    await core.start(run_scheduler=False)
    return core


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def core(settings, clock):
    world = SimWorld(seed=11, n_markets=16, clock=clock)
    c = await build_core(settings, world)
    yield c
    await c.stop()
