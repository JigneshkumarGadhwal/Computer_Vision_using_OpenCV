"""Live-observation support: data-mode classification and feed health.

This project ships NO connector to any gambling website. Live data arrives by
*push*: an authorised collector (your own script, an official API client)
POSTs completed rounds to ``/api/live/{source}/rounds``. To add a pull-based
integration, subclass :class:`FeedAdapter` for a documented, authorised API.
"""
from __future__ import annotations

import abc
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import DataSource, Round, SystemEvent, log_event

LIVE_KINDS = ("live_feed", "authorized_api", "public_observation")
DELAY_THRESHOLD_SECONDS = 60


class FeedAdapter(abc.ABC):
    """Interface for a documented, authorised round feed."""

    name: str = "abstract"

    @abc.abstractmethod
    def fetch_completed_rounds(self, since_round_id: str | None) -> list[dict]:
        """Return completed rounds after ``since_round_id`` as dicts with
        round_id, multiplier, timestamp. Must never return unfinished rounds."""


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def feed_status(session: Session, source: DataSource, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    last = session.scalars(
        select(Round).where(Round.source_id == source.id).order_by(Round.sequence.desc()).limit(1)
    ).first()
    n = session.scalar(select(func.count()).select_from(Round).where(Round.source_id == source.id)) or 0
    if source.is_synthetic:
        mode = "synthetic"
    elif source.kind in LIVE_KINDS:
        mode = "live"
    elif source.kind == "manual":
        mode = "manual"
    else:
        mode = "historical"
    status = {
        "source_id": source.id,
        "source_name": source.name,
        "kind": source.kind,
        "data_mode": mode,
        "n_rounds": int(n),
        "last_round_id": last.round_id if last else None,
        "last_round_timestamp": _aware(last.round_timestamp).isoformat() if last and last.round_timestamp else None,
        "last_ingested_at": _aware(last.ingested_at).isoformat() if last else None,
        "stale": False,
        "stale_after_seconds": settings.stale_after_seconds,
        "signal_generation_enabled": n > 0,
        "warnings": [],
    }
    if last is None:
        status["warnings"].append("No rounds stored for this source.")
        return status
    if mode == "live":
        age = (now - _aware(last.ingested_at)).total_seconds()
        status["seconds_since_last_round"] = age
        lag = None
        if last.round_timestamp and last.collected_at:
            lag = (_aware(last.collected_at) - _aware(last.round_timestamp)).total_seconds()
            status["collection_lag_seconds"] = lag
        if lag is not None and lag > DELAY_THRESHOLD_SECONDS:
            status["data_mode"] = "delayed"
            status["warnings"].append(f"Rounds arrive {lag:.0f}s after they occur (delayed data).")
        if age > settings.stale_after_seconds:
            status["stale"] = True
            status["signal_generation_enabled"] = False
            status["warnings"].append(
                f"Feed stale: no new round for {age:.0f}s (> {settings.stale_after_seconds}s). Signals disabled."
            )
            _log_once(session, source.id, "feed_stale", f"Live feed '{source.name}' is stale; signals disabled.")
    return status


def _log_once(session: Session, source_id: int, category: str, message: str) -> None:
    last = session.scalars(
        select(SystemEvent).where(SystemEvent.category == category).order_by(SystemEvent.id.desc()).limit(1)
    ).first()
    if last is None or last.details.get("source_id") != source_id or last.message != message:
        log_event(session, "warning", category, message, source_id=source_id)
