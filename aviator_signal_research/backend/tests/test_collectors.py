import json

import pytest

from app.collectors.http import fetch_static
from app.collectors.observer import HistoryExtractor, new_since, observe
from app.collectors.policy import (
    AccessChallenge,
    AccessPolicy,
    FetchFailed,
    NetworkBlocked,
    RobotsDisallowed,
    detect_challenge,
)
from app.collectors.reference import collect_many, collect_reference
from tests.collect_server import FixtureSite


def _browser_available() -> bool:
    try:
        from app.collectors.browser import browser_context

        with browser_context() as ctx:
            ctx.new_page().set_content("<p>ok</p>")
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _browser_available(), reason="headless Chromium not available")


@pytest.fixture(scope="module")
def site():
    with FixtureSite() as s:
        yield s


@pytest.fixture()
def policy():
    return AccessPolicy(min_interval_s=0)


# ------------------------------------------------------------- static / policy
def test_reference_extraction_is_compact_and_sourced(site, policy, tmp_path):
    rec = collect_reference(f"{site.base}/games/crash", policy, render="never", out_dir=tmp_path)
    s = rec["facts"]["summary"]
    assert s["rtp_percent_candidates"] == [97.0]
    assert 10000.0 in s["max_multiplier_candidates"]
    assert s["mentions_provably_fair"]
    assert any("SHA-512" in e["excerpt"] for e in rec["facts"]["provably_fair"])
    urls = {link["url"] for link in rec["links"]}
    assert f"{site.base}/provably-fair" in urls and f"{site.base}/demo" in urls
    assert rec["text"].count("Auto cash out is available.") == 1  # duplicates removed
    assert rec["context"].startswith(f"Source: {site.base}/games/crash") and len(rec["context"]) <= 3000
    assert "Home" not in rec["context"]  # navigation boilerplate not passed on
    stored = json.loads(open(rec["stored_at"]).read())
    assert stored["sha256"] == rec["sha256"] and not rec["unchanged"]
    again = collect_reference(f"{site.base}/games/crash", policy, render="never", out_dir=tmp_path)
    assert again["unchanged"] and len(list(tmp_path.glob("*.json"))) == 1


def test_collect_many_follows_only_documentation_links(site, policy, tmp_path):
    out = collect_many([f"{site.base}/games/crash"], policy, render="never", out_dir=tmp_path)
    got = [d["final_url"] for d in out["documents"]]
    assert got == [f"{site.base}/games/crash", f"{site.base}/provably-fair"]
    assert not out["errors"] and "SHA-512" in out["context"]


def test_robots_disallow_is_obeyed(site, policy):
    before = list(site.requests)
    with pytest.raises(RobotsDisallowed):
        fetch_static(f"{site.base}/private/data", policy)
    assert "/private/data" not in site.requests[len(before):]


def test_challenge_pages_stop_collection(site, policy):
    with pytest.raises(AccessChallenge):
        fetch_static(f"{site.base}/challenge", policy)
    assert detect_challenge("<p>Our support team can help if access denied errors appear.</p>", 200) is None
    assert detect_challenge("<p>Access denied</p>", 403)


def test_retry_on_503_but_not_on_404(site, policy):
    res = fetch_static(f"{site.base}/flaky", policy, backoff_s=0.01)
    assert res.status == 200 and site.flaky_hits == 2
    n = len(site.requests)
    with pytest.raises(FetchFailed):
        fetch_static(f"{site.base}/missing", policy, backoff_s=0.01)
    assert site.requests[n:].count("/missing") == 1


def test_unreachable_host_reported_as_network_blocked(policy):
    with pytest.raises(NetworkBlocked) as exc:
        fetch_static("http://127.0.0.1:9/page", policy, retries=0)  # nothing listens on port 9
    assert exc.value.to_dict()["reason"] == "network_blocked"


def test_rate_limit_spaces_requests(site):
    import time

    pol = AccessPolicy(min_interval_s=0.4)
    t = time.monotonic()
    fetch_static(f"{site.base}/games/crash", pol)
    fetch_static(f"{site.base}/provably-fair", pol)
    assert time.monotonic() - t >= 0.4


# ------------------------------------------------------------ extraction logic
def test_history_extractor_ignores_other_lists_and_handles_split_text():
    html = """<div class="balance">1000.00 USD</div>
    <div id="h"><div><span>2</span>.31x</div><div>1.00x</div><div><b>14.20</b><i>x</i></div><div>3.5x</div></div>
    <table><tr><td>u1</td><td>2.00x</td></tr><tr><td>u2</td><td>5.00x</td></tr><tr><td>u3</td><td>1.10x</td></tr></table>"""
    snap = HistoryExtractor().extract(html)
    assert snap.values == [2.31, 1.0, 14.2, 3.5]
    assert HistoryExtractor().extract("<p>2.31x</p><p>no list</p>").values == []


def test_history_extractor_relocates_when_markup_changes():
    h = HistoryExtractor()
    h.extract('<div id="a"><p>1.5x</p><p>2.5x</p><p>3.5x</p></div>')
    snap = h.extract('<section><ul><li>9.9x</li><li>1.5x</li><li>2.5x</li><li>3.5x</li></ul></section>')
    assert snap.relocated and snap.values == [9.9, 1.5, 2.5, 3.5]


def test_new_since_alignment():
    assert new_since([], [1.0, 2.0]) == ([], "baseline")
    assert new_since([2.0, 3.0, 4.0], [2.0, 3.0, 4.0]) == ([], "no_change")
    assert new_since([2.0, 3.0, 4.0, 5.0], [9.0, 8.0, 2.0, 3.0, 4.0]) == ([8.0, 9.0], "ok")  # bar capped at 5
    assert new_since([2.0, 3.0, 4.0], [7.0, 6.0, 5.0]) == ([], "gap")  # never guess across a gap
    assert new_since([1.0, 1.0, 2.0], [1.0, 1.0, 1.0, 2.0]) == ([1.0], "ok")  # repeated values still align
    assert new_since([3.0, 2.0, 1.0][::-1], [1.0, 2.0, 3.0, 4.0], newest_first=False) == ([4.0], "ok")


# ----------------------------------------------------------- browser features
@needs_browser
def test_js_rendered_page_falls_back_to_browser(site, policy, tmp_path):
    rec = collect_reference(f"{site.base}/js-only", policy, render="auto", out_dir=tmp_path)
    assert rec["method"] == "rendered" and rec["facts"]["summary"]["rtp_percent_candidates"] == [97.5]


@needs_browser
def test_observer_records_exact_round_sequence(site, policy, tmp_path):
    got = []
    res = observe(f"{site.base}/game", "fixture", minutes=0.12, poll_s=0.5, policy=policy, raw_dir=tmp_path,
                  sink=lambda v, t, rid: got.append(v))
    assert res.stop_reason == "time_budget", res.errors
    assert res.rounds_ingested == len(got) >= 10
    assert res.gaps == 0 and res.ws_frames_logged > 0
    seq = site.sent
    # observed rounds are a contiguous, correctly ordered run of what the server actually sent
    k = next(i for i in range(len(seq)) if seq[i:i + len(got)] == got)
    assert seq[k:k + len(got)] == got
    lines = [json.loads(x) for x in open(res.raw_log)]
    kinds = {x["kind"] for x in lines}
    assert {"page_loaded", "ws_open", "ws_frame", "round", "summary"} <= kinds
    assert [x["multiplier"] for x in lines if x["kind"] == "round"] == got


@needs_browser
def test_observer_feeds_database_as_public_observation(site, env, tmp_path):
    from sqlalchemy import select

    from app.db import session_scope
    from app.live import feed_status
    from app.models import DataSource, Round

    res = observe(f"{site.base}/game", "fixture-observed", minutes=0.08, poll_s=0.5,
                  policy=AccessPolicy(min_interval_s=0), raw_dir=tmp_path, log_websockets=False)
    assert res.rounds_ingested >= 5, res.to_dict()
    with session_scope() as s:
        src = s.scalars(select(DataSource).where(DataSource.name == "fixture-observed")).one()
        assert src.kind == "public_observation" and not src.is_synthetic
        rows = s.scalars(select(Round).where(Round.source_id == src.id).order_by(Round.sequence)).all()
        assert [r.sequence for r in rows] == list(range(len(rows))) and not any(r.verified for r in rows)
        assert feed_status(s, src)["data_mode"] == "live"


@needs_browser
def test_observer_stops_on_challenge_and_robots(site, policy, tmp_path):
    res = observe(f"{site.base}/game-challenge", "x", minutes=0.05, poll_s=0.5, policy=policy, raw_dir=tmp_path,
                  sink=lambda *a: None)
    assert res.stop_reason == "access_challenge" and res.rounds_ingested == 0
    res = observe(f"{site.base}/blocked-game", "y", minutes=0.05, policy=policy, raw_dir=tmp_path,
                  sink=lambda *a: None)
    assert res.stop_reason == "robots_disallowed" and res.polls == 0


def test_reference_api(site, env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.api.main import app

    monkeypatch.setenv("AVR_REFERENCE_DIR", str(tmp_path / "ref"))
    monkeypatch.setenv("AVR_COLLECTOR_MIN_INTERVAL", "0")
    with TestClient(app) as c:
        r = c.post("/api/reference/collect", json={"urls": [f"{site.base}/games/crash"], "render": "never"})
        assert r.status_code == 200 and r.json()["documents"][0]["summary"]["rtp_percent_candidates"] == [97.0]
        assert c.get("/api/reference").json()[0]["summary"]["mentions_provably_fair"]
        assert c.post("/api/reference/collect", json={"urls": ["ftp://x"]}).status_code == 422
        assert c.post("/api/reference/collect", json={"urls": []}).status_code == 422
