"""Data-quality validation for parsed round data.

Every row ends up with a status:
  * ``valid``    – accepted, no issues
  * ``warning``  – accepted, but flagged (e.g. timezone assumed, repeated run)
  * ``rejected`` – not stored as a cleaned round (still kept in raw_records)

Nothing is imputed. Missing or conflicting results are rejected, never filled.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
import pandas as pd

REJECT_FLAGS = {
    "missing_multiplier",
    "invalid_multiplier",
    "below_minimum_multiplier",
    "above_maximum_multiplier",
    "missing_round_id",
    "duplicate_round",
    "conflicting_round",
    "conflict_with_stored_round",
    "already_stored",
    "backfill_before_stored_data",
    "unparseable_timestamp",
}


def _missing(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _numeric_ids(ids: pd.Series) -> pd.Series | None:
    try:
        nums = pd.to_numeric(ids, errors="raise")
    except (ValueError, TypeError):
        return None
    if nums.isna().any() or (nums % 1 != 0).any():
        return None
    return nums.astype("int64")


def validate(
    df: pd.DataFrame,
    *,
    max_multiplier: float = 1_000_000.0,
    repeat_run: int = 5,
    stored: dict[str, float] | None = None,
    stored_last_timestamp: pd.Timestamp | None = None,
    stored_last_numeric_id: int | None = None,
    min_multiplier: float = 1.0,
) -> tuple[pd.DataFrame, dict]:
    """Validate a parsed frame. Returns (frame with status/flags, report)."""
    df = df.copy()
    flags: list[list[str]] = [[] for _ in range(len(df))]
    pos = {idx: i for i, idx in enumerate(df.index)}
    stored = stored or {}
    dataset_warnings: list[str] = []

    # --- row-level value checks -------------------------------------------
    for idx, row in df.iterrows():
        f = flags[pos[idx]]
        raw_m = row["multiplier_raw"]
        m = row["multiplier"]
        if raw_m is None or (isinstance(raw_m, float) and np.isnan(raw_m)) or str(raw_m).strip() == "":
            f.append("missing_multiplier")
        elif m is None or np.isnan(m):
            f.append("invalid_multiplier")
        elif m < min_multiplier:
            f.append("below_minimum_multiplier")
        elif m > max_multiplier:
            f.append("above_maximum_multiplier")
        if _missing(row["round_id"]):
            f.append("missing_round_id")
        if bool(row.get("timestamp_raw_present", False)) and pd.isna(row["timestamp"]):
            f.append("unparseable_timestamp")
        if bool(row.get("tz_assumed", False)):
            f.append("timezone_assumed_utc")

    # --- duplicates / conflicts inside the file ---------------------------
    by_id: dict[str, list] = defaultdict(list)
    for idx, row in df.iterrows():
        if not _missing(row["round_id"]) and not _missing(row["multiplier"]):
            by_id[row["round_id"]].append(idx)
    n_conflict_ids = 0
    for rid, idxs in by_id.items():
        if len(idxs) < 2:
            continue
        values = {round(float(df.at[i, "multiplier"]), 6) for i in idxs}
        if len(values) > 1:
            n_conflict_ids += 1
            for i in idxs:
                flags[pos[i]].append("conflicting_round")
        else:
            for i in idxs[1:]:
                flags[pos[i]].append("duplicate_round")

    # --- against already-stored data --------------------------------------
    for idx, row in df.iterrows():
        rid = row["round_id"]
        if not _missing(rid) and rid in stored and not _missing(row["multiplier"]):
            if abs(stored[rid] - float(row["multiplier"])) < 1e-6:
                flags[pos[idx]].append("already_stored")
            else:
                flags[pos[idx]].append("conflict_with_stored_round")

    # --- ordering ----------------------------------------------------------
    has_ts = df["timestamp"].notna()
    num_ids = _numeric_ids(df["round_id"].dropna()) if df["round_id"].notna().all() else None
    ordering = "file_order"
    if has_ts.all() and len(df):
        ordering = "timestamp"
    elif num_ids is not None:
        ordering = "numeric_round_id"
    elif has_ts.any():
        dataset_warnings.append("Some rows lack timestamps; using file order for sequencing.")

    if has_ts.sum() > 1:
        ts = df.loc[has_ts, "timestamp"]
        backwards = ts.diff().dt.total_seconds() < 0
        for idx in ts.index[backwards.fillna(False).to_numpy()]:
            flags[pos[idx]].append("out_of_order_timestamp")
        dup_ts = ts.duplicated(keep=False)
        for idx in ts.index[dup_ts.to_numpy()]:
            flags[pos[idx]].append("duplicate_timestamp")
    if num_ids is not None and has_ts.all() and len(df) > 1:
        # Identifiers should increase with time.
        order_by_ts = df.sort_values("timestamp", kind="stable").index
        ids_in_time = num_ids.loc[order_by_ts]
        bad = ids_in_time.diff() < 0
        for idx in ids_in_time.index[bad.fillna(False).to_numpy()]:
            flags[pos[idx]].append("inconsistent_identifier_order")

    if stored_last_timestamp is not None and ordering == "timestamp":
        for idx in df.index[(df["timestamp"] <= stored_last_timestamp).to_numpy()]:
            if "already_stored" not in flags[pos[idx]] and "conflict_with_stored_round" not in flags[pos[idx]]:
                flags[pos[idx]].append("backfill_before_stored_data")
    elif stored_last_numeric_id is not None and ordering == "numeric_round_id" and num_ids is not None:
        for idx in num_ids.index[(num_ids <= stored_last_numeric_id).to_numpy()]:
            if "already_stored" not in flags[pos[idx]] and "conflict_with_stored_round" not in flags[pos[idx]]:
                flags[pos[idx]].append("backfill_before_stored_data")

    # --- statuses ----------------------------------------------------------
    statuses = []
    for f in flags:
        if any(x in REJECT_FLAGS for x in f):
            statuses.append("rejected")
        elif f:
            statuses.append("warning")
        else:
            statuses.append("valid")
    df["status"] = statuses
    df["flags"] = flags

    # --- sequencing of accepted rows --------------------------------------
    accepted = df[df["status"] != "rejected"].copy()
    if ordering == "timestamp":
        accepted = accepted.sort_values(["timestamp", "row_number"], kind="stable")
    elif ordering == "numeric_round_id":
        accepted["_nid"] = pd.to_numeric(accepted["round_id"])
        accepted = accepted.sort_values(["_nid", "row_number"], kind="stable").drop(columns="_nid")
    else:
        accepted = accepted.sort_values("row_number", kind="stable")

    # --- suspicious repeated values (on accepted, in chronological order) --
    mults = accepted["multiplier"].to_numpy()
    run_start = 0
    acc_idx = list(accepted.index)
    for i in range(1, len(mults) + 1):
        if i == len(mults) or mults[i] != mults[run_start]:
            if i - run_start >= repeat_run:
                for j in range(run_start, i):
                    flags[pos[acc_idx[j]]].append("suspicious_repeated_value")
            run_start = i
    # repeated blocks of 10 identical consecutive multipliers
    block = 10
    seen: dict[tuple, int] = {}
    repeated_blocks = 0
    for i in range(0, max(len(mults) - block + 1, 0)):
        key = tuple(np.round(mults[i:i + block], 4))
        if key in seen and i - seen[key] >= block:
            repeated_blocks += 1
            for j in range(i, i + block):
                if "suspicious_repeated_block" not in flags[pos[acc_idx[j]]]:
                    flags[pos[acc_idx[j]]].append("suspicious_repeated_block")
        else:
            seen.setdefault(key, i)
    # refresh statuses for newly-flagged warnings
    for idx in accepted.index:
        f = flags[pos[idx]]
        if f and df.at[idx, "status"] == "valid":
            df.at[idx, "status"] = "warning"
    df["flags"] = flags
    accepted = df.loc[accepted.index]

    # --- gaps in numeric ids -----------------------------------------------
    gaps = 0
    if ordering == "numeric_round_id" or (num_ids is not None):
        acc_nums = pd.to_numeric(accepted["round_id"], errors="coerce").dropna().astype("int64").sort_values()
        if len(acc_nums) > 1:
            d = acc_nums.diff().dropna()
            gaps = int((d[d > 1] - 1).sum())
    if gaps:
        dataset_warnings.append(
            f"{gaps} round identifiers are missing between consecutive accepted rounds. "
            "Lag features treat the sequence as contiguous; missing rounds are NOT filled."
        )

    flag_counts = Counter(x for f in flags for x in f)
    accepted_m = accepted["multiplier"].astype(float)
    report = {
        "n_rows": int(len(df)),
        "n_accepted": int(len(accepted)),
        "n_rejected": int((df["status"] == "rejected").sum()),
        "n_warnings": int((df["status"] == "warning").sum()),
        "n_conflicting_ids": n_conflict_ids,
        "missing_id_gaps": gaps,
        "repeated_blocks": repeated_blocks,
        "ordering": ordering,
        "flag_counts": dict(flag_counts),
        "dataset_warnings": dataset_warnings,
        "period_start": accepted["timestamp"].min().isoformat() if accepted["timestamp"].notna().any() else None,
        "period_end": accepted["timestamp"].max().isoformat() if accepted["timestamp"].notna().any() else None,
        "multiplier_summary": (
            {
                "min": float(accepted_m.min()),
                "median": float(accepted_m.median()),
                "max": float(accepted_m.max()),
            }
            if len(accepted_m)
            else None
        ),
    }
    reject_share = report["n_rejected"] / max(report["n_rows"], 1)
    if report["n_accepted"] == 0:
        report["verdict"] = "failed"
    elif reject_share > 0.2 or n_conflict_ids > 0 or repeated_blocks > 0:
        report["verdict"] = "needs_review"
    elif report["n_warnings"] or dataset_warnings:
        report["verdict"] = "ok_with_warnings"
    else:
        report["verdict"] = "ok"
    df.attrs["accepted_order"] = list(accepted.index)
    return df, report
