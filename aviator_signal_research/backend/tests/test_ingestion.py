import json

import pytest
from sqlalchemy import func, select

from app.ingestion.parsers import ParseError, parse_file, parse_multiplier
from app.ingestion.service import ImportError_, add_single_round, commit_import, load_rounds_frame, preview_import
from app.models import ImportJob, RawRecord, Round

GOOD = b"""round_id,multiplier,timestamp
1,1.50,2026-03-01T10:00:00Z
2,2.00x,2026-03-01T10:00:20Z
3,13.37,2026-03-01T10:00:40Z
4,1.00,2026-03-01T10:01:00Z
"""


def test_parse_aliases_and_formats():
    df, meta = parse_file(b"Round,Crash,Time\n7,2.5x,1767261600\n8,1.01,1767261620\n", "x.csv")
    assert list(df["round_id"]) == ["7", "8"]
    assert list(df["multiplier"]) == [2.5, 1.01]
    assert df["timestamp"].iloc[0].year == 2026  # epoch seconds parsed as UTC
    assert meta["column_mapping"] == {"Round": "round_id", "Crash": "multiplier", "Time": "timestamp"}
    assert parse_multiplier("3,5") == 3.5
    assert parse_multiplier("abc") != parse_multiplier("abc")  # NaN


def test_parse_json_variants():
    df, _ = parse_file(json.dumps({"rounds": [{"id": "a", "crash_point": 1.2}]}).encode(), "r.json")
    assert df["multiplier"].iloc[0] == 1.2
    df, meta = parse_file(b"[1.5, 2.0, 3.1]", "manual.json")
    assert meta["round_id_generated"] and list(df["multiplier"]) == [1.5, 2.0, 3.1]


def test_parse_errors():
    with pytest.raises(ParseError):
        parse_file(b"foo,bar\n1,2\n", "x.csv")
    with pytest.raises(ParseError):
        parse_file(b"{not json", "x.json")
    with pytest.raises(ParseError):
        parse_file(b"a", "x.xlsx")


def test_invalid_rows_rejected_never_filled(session):
    content = b"""round_id,multiplier,timestamp
1,2.0,2026-01-01T00:00:00Z
2,,2026-01-01T00:00:10Z
3,abc,2026-01-01T00:00:20Z
4,0.7,2026-01-01T00:00:30Z
5,5000000,2026-01-01T00:00:40Z
,2.0,2026-01-01T00:00:50Z
6,1.3,not-a-date
"""
    rep = commit_import(session, content, "bad.csv", "src", "user_upload")
    fc = rep["flag_counts"]
    assert fc["missing_multiplier"] == 1 and fc["invalid_multiplier"] == 1
    assert fc["below_minimum_multiplier"] == 1 and fc["above_maximum_multiplier"] == 1
    assert fc["missing_round_id"] == 1 and fc["unparseable_timestamp"] == 1
    assert rep["n_accepted"] == 1
    stored = session.scalars(select(Round.multiplier)).all()
    assert stored == [2.0]  # nothing imputed
    # all raw rows kept separately, byte-for-byte file saved
    assert session.scalar(select(func.count()).select_from(RawRecord)) == 7
    job = session.scalars(select(ImportJob)).one()
    assert open(job.raw_path, "rb").read() == content


def test_duplicates_conflicts_and_ordering(session):
    content = b"""round_id,multiplier,timestamp
10,2.0,2026-01-01T00:00:00Z
11,3.0,2026-01-01T00:00:20Z
11,3.0,2026-01-01T00:00:20Z
12,1.5,2026-01-01T00:00:40Z
12,4.0,2026-01-01T00:00:41Z
14,1.2,2026-01-01T00:00:30Z
"""
    rep = preview_import(session, content, "d.csv")
    fc = rep["flag_counts"]
    assert fc["duplicate_round"] == 1
    assert fc["conflicting_round"] == 2  # both conflicting rows rejected: truth unknown
    assert fc["out_of_order_timestamp"] == 1
    assert fc["inconsistent_identifier_order"] >= 1
    assert rep["missing_id_gaps"] >= 1
    assert rep["verdict"] == "needs_review"
    assert session.scalar(select(func.count()).select_from(Round)) == 0  # preview writes nothing


def test_chronological_sequence_and_idempotent_reimport(session):
    shuffled = b"""round_id,multiplier,timestamp
3,13.37,2026-03-01T10:00:40Z
1,1.50,2026-03-01T10:00:00Z
2,2.00,2026-03-01T10:00:20Z
"""
    commit_import(session, shuffled, "a.csv", "s", "user_upload")
    df = load_rounds_frame(session, 1)
    assert list(df["round_id"]) == ["1", "2", "3"] and list(df["sequence"]) == [0, 1, 2]
    rep = commit_import(session, GOOD, "b.csv", "s", "user_upload")
    assert rep["flag_counts"]["already_stored"] == 3 and rep["n_accepted"] == 1  # only round 4 is new
    df = load_rounds_frame(session, 1)
    assert list(df["round_id"]) == ["1", "2", "3", "4"] and list(df["sequence"]) == [0, 1, 2, 3]


def test_conflict_with_stored_and_backfill_rejected(session):
    commit_import(session, GOOD, "a.csv", "s", "user_upload")
    rep = commit_import(session, b"round_id,multiplier,timestamp\n2,9.99,2026-03-01T10:00:20Z\n0,1.1,2026-03-01T09:59:00Z\n",
                        "b.csv", "s", "user_upload")
    assert rep["flag_counts"]["conflict_with_stored_round"] == 1
    assert rep["flag_counts"]["backfill_before_stored_data"] == 1
    assert rep["n_accepted"] == 0


def test_suspicious_repeats_and_timezone_flags(session):
    rows = "round_id,multiplier,timestamp\n" + "".join(f"{i},1.23,2026-01-01 00:{i:02d}:00\n" for i in range(6))
    rep = preview_import(session, rows.encode(), "r.csv")
    assert rep["flag_counts"]["suspicious_repeated_value"] == 6
    assert rep["flag_counts"]["timezone_assumed_utc"] == 6
    rep2 = preview_import(session, rows.encode(), "r.csv", default_timezone="Europe/Berlin")
    assert "timezone_assumed_utc" not in rep2["flag_counts"]
    assert rep2["preview"][0]["timestamp"].startswith("2025-12-31T23:00")  # converted to UTC


def test_synthetic_and_real_never_mixed(session):
    commit_import(session, GOOD, "a.csv", "mixed", "synthetic")
    with pytest.raises(ImportError_):
        commit_import(session, GOOD, "a.csv", "mixed", "user_upload")
    with pytest.raises(ImportError_):
        add_single_round(session, "mixed", "manual", 2.0)


def test_manual_entry_validation(session):
    r = add_single_round(session, "man", "manual", "2.5x", round_id="r1")
    assert r.sequence == 0 and r.multiplier == 2.5
    with pytest.raises(ImportError_):
        add_single_round(session, "man", "manual", 0.5)
    with pytest.raises(ImportError_):
        add_single_round(session, "man", "manual", 2.5, round_id="r1")  # duplicate
    with pytest.raises(ImportError_):
        add_single_round(session, "man", "manual", 3.0, round_id="r1")  # conflict
    with pytest.raises(ImportError_):
        add_single_round(session, "man", "historical_export", 3.0)
