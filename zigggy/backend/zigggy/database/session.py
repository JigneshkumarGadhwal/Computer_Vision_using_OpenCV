"""Async engine/session management and migration runner."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"  # shipped inside the package


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"timeout": 30}
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        # SQLite allows one writer; serialise writes inside this process to avoid lock churn.
        self.write_lock = asyncio.Lock()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            yield s

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncSession]:
        """Write transaction: commit on success, rollback on error."""
        async with self.write_lock:
            async with self.sessionmaker() as s:
                try:
                    yield s
                    await s.commit()
                except BaseException:
                    await s.rollback()
                    raise

    async def ping(self) -> bool:
        from sqlalchemy import text

        async with self.engine.connect() as c:
            await c.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()


def run_migrations(url: str) -> None:
    """Apply Alembic migrations up to head (blocking; call before the event loop work starts)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
