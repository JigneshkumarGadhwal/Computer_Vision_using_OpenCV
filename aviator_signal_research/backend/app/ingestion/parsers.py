"""Parsing of uploaded CSV / JSON round files into a normalised DataFrame.

Parsing never invents values: unparseable fields become NaN/None and are
flagged by the validator.
"""
from __future__ import annotations

import io
import json
import re

import numpy as np
import pandas as pd

# Canonical column -> accepted aliases (case-insensitive).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "round_id": ("round_id", "roundid", "round", "id", "round_number", "game_id"),
    "multiplier": ("multiplier", "crash", "crash_point", "crashpoint", "result", "odds", "coefficient", "coef", "x"),
    "timestamp": ("timestamp", "time", "datetime", "date", "created_at", "round_time", "played_at"),
    "timezone": ("timezone", "tz"),
    "session_id": ("session_id", "session", "game_session"),
    "collected_at": ("collected_at", "observed_at", "scraped_at", "collection_time"),
    "verified": ("verified", "is_verified"),
    "server_seed_hash": ("server_seed_hash", "hashed_server_seed", "seed_hash"),
    "server_seed": ("server_seed",),
    "client_seeds": ("client_seeds", "client_seed"),
    "nonce": ("nonce",),
}

_MULT_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*[xX×]?\s*$")


class ParseError(ValueError):
    pass


def _read_raw(content: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    text = content.decode("utf-8-sig", errors="strict")
    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid JSON: {exc}") from exc
        if isinstance(data, dict):
            for key in ("rounds", "data", "results", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise ParseError("JSON must be a list of round objects or {'rounds': [...]}.")
        if data and not all(isinstance(r, dict) for r in data):
            # Allow a plain list of multipliers (manual entry).
            data = [{"multiplier": r} for r in data]
        return pd.DataFrame(data, dtype=object)
    if name.endswith(".csv") or name.endswith(".txt"):
        try:
            return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
        except Exception as exc:  # pandas raises several error types
            raise ParseError(f"Invalid CSV: {exc}") from exc
    raise ParseError("Unsupported file type. Use .csv or .json.")


def _rename_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    lower = {c: str(c).strip().lower() for c in df.columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for original, low in lower.items():
            if low in aliases and original not in mapping:
                mapping[original] = canonical
                break
    return df.rename(columns=mapping), mapping


def parse_multiplier(value) -> float:
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value) if np.isfinite(value) else np.nan
    s = str(value).strip().replace(",", ".")
    if not s:
        return np.nan
    m = _MULT_RE.match(s)
    return float(m.group(1)) if m else np.nan


def _parse_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "verified"}


def _parse_timestamps(raw: pd.Series, tz_col: pd.Series | None, default_tz: str | None):
    """Return (utc timestamps, original tz labels, assumed-utc mask)."""
    out = []
    tz_labels = []
    assumed = []
    for i, v in enumerate(raw.tolist()):
        if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
            out.append(pd.NaT)
            tz_labels.append(None)
            assumed.append(False)
            continue
        try:
            sv = str(v).strip()
            if re.fullmatch(r"\d{9,13}(\.\d+)?", sv):  # epoch seconds / ms
                num = float(sv)
                ts = pd.Timestamp(num, unit="ms" if num > 1e11 else "s", tz="UTC")
                tz_labels.append("UTC(epoch)")
                assumed.append(False)
            else:
                ts = pd.Timestamp(sv)
                if ts.tzinfo is None:
                    tz = None
                    if tz_col is not None:
                        tv = tz_col.iloc[i]
                        tz = str(tv).strip() if tv not in (None, "") else None
                    tz = tz or default_tz
                    if tz:
                        ts = ts.tz_localize(tz)
                        tz_labels.append(tz)
                        assumed.append(False)
                    else:
                        ts = ts.tz_localize("UTC")
                        tz_labels.append("UTC(assumed)")
                        assumed.append(True)
                else:
                    tz_labels.append(str(ts.tzinfo))
                    assumed.append(False)
                ts = ts.tz_convert("UTC")
            out.append(ts)
        except Exception:
            out.append(pd.NaT)
            tz_labels.append(None)
            assumed.append(False)
    return pd.Series(pd.to_datetime(out, utc=True), index=raw.index), tz_labels, assumed


def parse_file(content: bytes, filename: str, default_timezone: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Parse a file into a normalised frame plus parse metadata.

    Columns of the result: row_number, round_id, multiplier, multiplier_raw,
    timestamp (UTC), original_timezone, tz_assumed, session_id, collected_at,
    verified, server_seed_hash, server_seed, client_seeds, nonce, raw (dict).
    """
    raw_df = _read_raw(content, filename)
    if raw_df.empty:
        raise ParseError("File contains no rows.")
    raw_records = raw_df.to_dict(orient="records")
    df, mapping = _rename_columns(raw_df.copy())
    if "multiplier" not in df.columns:
        raise ParseError(
            "No multiplier column found. Expected one of: " + ", ".join(COLUMN_ALIASES["multiplier"])
        )
    meta: dict = {"column_mapping": mapping, "ignored_columns": [c for c in raw_df.columns if c not in mapping]}

    out = pd.DataFrame(index=df.index)
    out["row_number"] = np.arange(1, len(df) + 1)
    if "round_id" in df.columns:
        out["round_id"] = [None if v is None or str(v).strip() == "" else str(v).strip() for v in df["round_id"]]
        meta["round_id_generated"] = False
    else:
        out["round_id"] = [f"row-{i}" for i in out["row_number"]]
        meta["round_id_generated"] = True
    out["multiplier_raw"] = df["multiplier"].astype(object)
    out["multiplier"] = [parse_multiplier(v) for v in df["multiplier"]]

    if "timestamp" in df.columns:
        ts, tz_labels, assumed = _parse_timestamps(
            df["timestamp"], df["timezone"] if "timezone" in df.columns else None, default_timezone
        )
        out["timestamp"] = ts
        out["original_timezone"] = tz_labels
        out["tz_assumed"] = assumed
        out["timestamp_raw_present"] = [str(v).strip() != "" and v is not None for v in df["timestamp"]]
    else:
        out["timestamp"] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
        out["original_timezone"] = None
        out["tz_assumed"] = False
        out["timestamp_raw_present"] = False

    if "collected_at" in df.columns:
        c, _, _ = _parse_timestamps(df["collected_at"], None, default_timezone)
        out["collected_at"] = c
    else:
        out["collected_at"] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")

    for col in ("session_id", "server_seed_hash", "server_seed", "client_seeds", "nonce"):
        out[col] = [None if (v is None or str(v).strip() == "") else str(v).strip() for v in df[col]] if col in df.columns else None
    out["verified"] = [_parse_bool(v) for v in df["verified"]] if "verified" in df.columns else False
    out["raw"] = [{str(k): (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in r.items()} for r in raw_records]
    return out, meta
