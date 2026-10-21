"""Import workflow: preview -> commit, plus manual / live single-round entry."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import SOURCE_KINDS, DataSource, ImportJob, RawRecord, Round, log_event
from .parsers import parse_file, parse_multiplier
from .validation import validate


class ImportError_(ValueError):
    """Raised for user-facing import problems (bad source kind, mixing data)."""


def get_or_create_source(
    session: Session,
    name: str,
    kind: str,
    *,
    platform: str | None = None,
    game_version: str | None = None,
    description: str | None = None,
) -> DataSource:
    if kind not in SOURCE_KINDS:
        raise ImportError_(f"Unknown source kind '{kind}'. Use one of {SOURCE_KINDS}.")
    src = session.scalar(select(DataSource).where(DataSource.name == name))
    if src is not None:
        if (src.kind == "synthetic") != (kind == "synthetic"):
            raise ImportError_(
                "Refusing to mix synthetic and real data in the same source. Use a separate source name."
            )
        return src
    src = DataSource(
        name=name,
        kind=kind,
        is_synthetic=(kind == "synthetic"),
        platform=platform,
        game_version=game_version,
        description=description,
    )
    session.add(src)
    session.flush()
    return src


def _stored_state(session: Session, source_id: int | None):
    if source_id is None:
        return {}, None, None, 0
    rows = session.execute(
        select(Round.round_id, Round.multiplier, Round.round_timestamp, Round.sequence).where(
            Round.source_id == source_id
        )
    ).all()
    stored = {r[0]: r[1] for r in rows}
    last_ts = max((r[2] for r in rows if r[2] is not None), default=None)
    if last_ts is not None and last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    nums = []
    for r in rows:
        try:
            nums.append(int(r[0]))
        except (TypeError, ValueError):
            pass
    last_num = max(nums) if nums and len(nums) == len(rows) else None
    next_seq = (max((r[3] for r in rows), default=-1)) + 1
    return stored, (pd.Timestamp(last_ts) if last_ts is not None else None), last_num, next_seq


def preview_import(
    session: Session,
    content: bytes,
    filename: str,
    source_name: str | None = None,
    default_timezone: str | None = None,
    preview_rows: int = 20,
) -> dict:
    """Parse and validate without writing anything."""
    settings = get_settings()
    parsed, meta = parse_file(content, filename, default_timezone)
    src = session.scalar(select(DataSource).where(DataSource.name == source_name)) if source_name else None
    stored, last_ts, last_num, _ = _stored_state(session, src.id if src else None)
    checked, report = validate(
        parsed,
        max_multiplier=settings.max_valid_multiplier,
        repeat_run=settings.suspicious_repeat_run,
        stored=stored,
        stored_last_timestamp=last_ts,
        stored_last_numeric_id=last_num,
    )
    report["parse"] = meta
    report["file_sha256"] = hashlib.sha256(content).hexdigest()
    report["preview"] = [
        {
            "row_number": int(r.row_number),
            "round_id": r.round_id,
            "multiplier": None if np.isnan(r.multiplier) else float(r.multiplier),
            "timestamp": r.timestamp.isoformat() if pd.notna(r.timestamp) else None,
            "status": r.status,
            "flags": list(r.flags),
        }
        for r in checked.head(preview_rows).itertuples()
    ]
    report["rejected_examples"] = [
        {"row_number": int(r.row_number), "round_id": r.round_id, "flags": list(r.flags)}
        for r in checked[checked["status"] == "rejected"].head(preview_rows).itertuples()
    ]
    return report


def commit_import(
    session: Session,
    content: bytes,
    filename: str,
    source_name: str,
    source_kind: str,
    *,
    default_timezone: str | None = None,
    platform: str | None = None,
    game_version: str | None = None,
    description: str | None = None,
    mark_verified: bool = False,
) -> dict:
    """Parse, validate and persist raw + cleaned data. Returns job summary."""
    settings = get_settings()
    parsed, meta = parse_file(content, filename, default_timezone)
    src = get_or_create_source(
        session, source_name, source_kind, platform=platform, game_version=game_version, description=description
    )
    stored, last_ts, last_num, next_seq = _stored_state(session, src.id)
    checked, report = validate(
        parsed,
        max_multiplier=settings.max_valid_multiplier,
        repeat_run=settings.suspicious_repeat_run,
        stored=stored,
        stored_last_timestamp=last_ts,
        stored_last_numeric_id=last_num,
    )
    report["parse"] = meta
    sha = hashlib.sha256(content).hexdigest()

    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-120:]
    raw_path = settings.raw_data_dir / f"{stamp}_{sha[:12]}_{safe_name}"
    raw_path.write_bytes(content)  # raw data kept byte-for-byte

    job = ImportJob(
        source_id=src.id,
        filename=filename,
        raw_path=str(raw_path),
        file_sha256=sha,
        status="committed" if report["n_accepted"] else "rejected",
        n_rows=report["n_rows"],
        n_accepted=report["n_accepted"],
        n_rejected=report["n_rejected"],
        n_warnings=report["n_warnings"],
        report=report,
    )
    session.add(job)
    session.flush()

    session.add_all(
        RawRecord(
            job_id=job.id,
            row_number=int(r.row_number),
            payload={k: (str(v) if v is not None else None) for k, v in r.raw.items()},
            validation_status=r.status,
            flags=list(r.flags),
        )
        for r in checked.itertuples()
    )

    order = checked.attrs["accepted_order"]
    seq = next_seq
    now = datetime.now(timezone.utc)
    for idx in order:
        r = checked.loc[idx]
        session.add(
            Round(
                source_id=src.id,
                job_id=job.id,
                round_id=r["round_id"],
                sequence=seq,
                round_timestamp=r["timestamp"].to_pydatetime() if pd.notna(r["timestamp"]) else None,
                original_timezone=r["original_timezone"],
                multiplier=float(r["multiplier"]),
                collected_at=r["collected_at"].to_pydatetime() if pd.notna(r["collected_at"]) else now,
                session_id=r["session_id"],
                quality_status=r["status"],
                quality_flags=list(r["flags"]),
                verified=bool(r["verified"]) or mark_verified,
                server_seed_hash=r["server_seed_hash"],
                server_seed=r["server_seed"],
                client_seeds=r["client_seeds"],
                nonce=r["nonce"],
            )
        )
        seq += 1
    log_event(
        session,
        "info" if report["verdict"] in ("ok", "ok_with_warnings") else "warning",
        "import",
        f"Imported {report['n_accepted']}/{report['n_rows']} rows into source '{src.name}' ({report['verdict']}).",
        job_id=job.id,
        source_id=src.id,
    )
    session.flush()
    return {"job_id": job.id, "source_id": src.id, "source_name": src.name, "is_synthetic": src.is_synthetic, **report}


def add_single_round(
    session: Session,
    source_name: str,
    source_kind: str,
    multiplier,
    round_id: str | None = None,
    round_timestamp: datetime | None = None,
    session_id: str | None = None,
    verified: bool = False,
    server_seed: str | None = None,
    server_seed_hash: str | None = None,
    client_seeds: list[str] | None = None,
    nonce: str | None = None,
) -> Round:
    """Manual or live-feed entry of one observed (already finished) round."""
    if source_kind not in ("manual", "live_feed", "authorized_api", "public_observation", "synthetic"):
        raise ImportError_(
            "Single-round entry is only for manual, live_feed, authorized_api, public_observation or synthetic sources."
        )
    m = parse_multiplier(multiplier)
    if np.isnan(m) or m < 1.0:
        raise ImportError_(f"Invalid multiplier {multiplier!r}: must be a number >= 1.0.")
    if m > get_settings().max_valid_multiplier:
        raise ImportError_("Multiplier above configured maximum; rejected.")
    src = get_or_create_source(session, source_name, source_kind)
    last_seq = session.scalar(select(func.max(Round.sequence)).where(Round.source_id == src.id))
    next_seq = 0 if last_seq is None else last_seq + 1  # NB: last_seq may be 0
    rid = round_id or f"{source_kind}-{next_seq}"
    existing = session.scalar(select(Round).where(Round.source_id == src.id, Round.round_id == rid))
    if existing is not None:
        if abs(existing.multiplier - m) < 1e-9:
            raise ImportError_(f"Round '{rid}' already stored (duplicate).")
        raise ImportError_(f"Round '{rid}' already stored with a different multiplier (conflict).")
    ts = round_timestamp or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    last_ts = session.scalar(select(func.max(Round.round_timestamp)).where(Round.source_id == src.id))
    flags: list[str] = []
    if last_ts is not None:
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if ts < last_ts:
            raise ImportError_("Round timestamp is earlier than the latest stored round (out of order).")
    r = Round(
        source_id=src.id,
        round_id=rid,
        sequence=next_seq,
        round_timestamp=ts,
        original_timezone="UTC",
        multiplier=float(m),
        collected_at=datetime.now(timezone.utc),
        session_id=session_id,
        quality_status="valid" if not flags else "warning",
        quality_flags=flags,
        verified=verified,
        server_seed=server_seed,
        server_seed_hash=server_seed_hash,
        client_seeds=json.dumps(client_seeds) if client_seeds is not None else None,
        nonce=nonce,
    )
    session.add(r)
    session.flush()
    return r


def load_rounds_frame(session: Session, source_id: int) -> pd.DataFrame:
    """Cleaned rounds for a source in chronological sequence order."""
    rows = session.execute(
        select(
            Round.sequence,
            Round.round_id,
            Round.multiplier,
            Round.round_timestamp,
            Round.session_id,
            Round.quality_status,
            Round.verified,
            Round.server_seed_hash,
            Round.server_seed,
            Round.client_seeds,
            Round.nonce,
        )
        .where(Round.source_id == source_id)
        .order_by(Round.sequence)
    ).all()
    df = pd.DataFrame(
        rows,
        columns=["sequence", "round_id", "multiplier", "timestamp", "session_id", "quality_status", "verified",
                 "server_seed_hash", "server_seed", "client_seeds", "nonce"],
    )
    if len(df):
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df
