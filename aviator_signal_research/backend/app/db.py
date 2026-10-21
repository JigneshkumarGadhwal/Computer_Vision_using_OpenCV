"""Database engine, session handling and schema creation."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

SCHEMA_VERSION = 1


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None

# Database-level immutability guards. The ORM also refuses updates (see
# models.py), but triggers protect against raw SQL as well.
_SQLITE_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS predictions_no_update
       BEFORE UPDATE ON predictions
       BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;""",
    """CREATE TRIGGER IF NOT EXISTS predictions_no_delete
       BEFORE DELETE ON predictions
       BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;""",
    """CREATE TRIGGER IF NOT EXISTS outcomes_no_update
       BEFORE UPDATE ON prediction_outcomes
       BEGIN SELECT RAISE(ABORT, 'prediction outcomes are immutable'); END;""",
]

_POSTGRES_TRIGGERS = [
    """CREATE OR REPLACE FUNCTION avr_reject_change() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'row is immutable'; END; $$ LANGUAGE plpgsql;""",
    "DROP TRIGGER IF EXISTS predictions_immutable ON predictions;",
    """CREATE TRIGGER predictions_immutable BEFORE UPDATE OR DELETE ON predictions
       FOR EACH ROW EXECUTE FUNCTION avr_reject_change();""",
    "DROP TRIGGER IF EXISTS outcomes_immutable ON prediction_outcomes;",
    """CREATE TRIGGER outcomes_immutable BEFORE UPDATE ON prediction_outcomes
       FOR EACH ROW EXECUTE FUNCTION avr_reject_change();""",
]


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


def init_engine(database_url: str | None = None) -> Engine:
    """(Re)initialise the global engine. Tests call this with a temp URL."""
    global _engine, _SessionLocal
    url = database_url or get_settings().database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url.startswith("sqlite:///") and ":memory:" not in url:
            from pathlib import Path

            Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    create_schema(engine)
    return engine


def create_schema(engine: Engine) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    triggers = _SQLITE_TRIGGERS if engine.dialect.name == "sqlite" else (
        _POSTGRES_TRIGGERS if engine.dialect.name == "postgresql" else []
    )
    with engine.begin() as conn:
        for stmt in triggers:
            conn.execute(text(stmt))
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        )
        current = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        if current is None:
            conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": SCHEMA_VERSION})
        elif current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current} is newer than this code ({SCHEMA_VERSION})."
            )


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
