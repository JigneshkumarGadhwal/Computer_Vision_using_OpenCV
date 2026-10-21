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


def test_label_value_pairs_and_script_buttons():
    from app.collectors.reference import extract_facts, extract_links

    # Structure observed on spribe.co/games/aviator (2026-10-21): label <p>, value <h5>
    f = extract_facts(["Game Type", "Mini Game", "Return to Player (RTP)", "97%", "Devices", "Desktop, Tablet, Mobile"])
    assert f["summary"]["rtp_percent_candidates"] == [97.0]
    assert f["rtp"][0]["excerpt"] == "Return to Player (RTP) 97%"
    assert extract_facts(["Return to Player (RTP)", "Desktop, Tablet, Mobile"])["summary"]["rtp_percent_candidates"] == []
    links = extract_links('<a class="demo-link">Play Demo</a><button>Subscribe</button><a href="/x">x</a>', "https://e.test/")
    assert links == [{"url": None, "text": "Play Demo", "note": "opened by script (no href)"}]


def test_profile_demo_url():
    from app.collectors.profiles import demo_url

    assert demo_url("spribe", "aviator") == (
        "https://demo.spribe.io/launch/aviator?currency=USD&lang=EN&return_url=https://spribe.co/games")


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


def test_multiplier_text_with_thousands_separators():
    from app.collectors.observer import parse_multiplier_text

    # observed live: a 6516.99x round (2026-10-21) was missed because of the separator
    assert parse_multiplier_text("6,516.99x") == 6516.99
    assert parse_multiplier_text("12,345x") == 12345.0
    assert parse_multiplier_text("6\u202f516.99x") == 6516.99
    assert parse_multiplier_text("2,31x") == 2.31  # decimal comma still supported
    assert parse_multiplier_text("1.00x") == 1.0 and parse_multiplier_text("abc") is None


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
    assert res.gaps == 0 and res.cross_check["mode"] == "dom"  # fixture sends no decodable game messages
    seq = site.sent
    # observed rounds are a contiguous, correctly ordered run of what the server actually sent
    k = next(i for i in range(len(seq)) if seq[i:i + len(got)] == got)
    assert seq[k:k + len(got)] == got
    lines = [json.loads(x) for x in open(res.raw_log)]
    kinds = {x["kind"] for x in lines}
    assert {"page_loaded", "ws_open", "round", "summary"} <= kinds and "ws_frame" not in kinds  # raw frames off
    assert [x["multiplier"] for x in lines if x["kind"] == "round"] == got


@needs_browser
def test_observer_feeds_database_as_public_observation(site, env, tmp_path):
    from sqlalchemy import select

    from app.db import session_scope
    from app.live import feed_status
    from app.models import DataSource, Round

    res = observe(f"{site.base}/game", "fixture-observed", minutes=0.08, poll_s=0.5,
                  policy=AccessPolicy(min_interval_s=0), raw_dir=tmp_path)
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


def test_robots_status_handling_follows_rfc9309(monkeypatch):
    import httpx

    def client_for(status, body=""):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text=body)))

    pol = AccessPolicy(min_interval_s=0, robots_4xx="allow")
    pol.check_allowed("https://a.test/page", client_for(403, "Request blocked"))  # 4xx -> allowed
    assert "no restrictions" in pol.robots_notes["https://a.test"]
    strict = AccessPolicy(min_interval_s=0, robots_4xx="disallow")
    with pytest.raises(RobotsDisallowed):
        strict.check_allowed("https://a.test/page", client_for(403))
    with pytest.raises(RobotsDisallowed):
        AccessPolicy(min_interval_s=0).check_allowed("https://b.test/page", client_for(503))  # 5xx -> disallow
    html = AccessPolicy(min_interval_s=0)
    html.check_allowed("https://c.test/page", client_for(200, "<!DOCTYPE html><html><body>app</body></html>"))
    assert "HTML page" in html.robots_notes["https://c.test"]
    monkeypatch.setenv("AVR_ROBOTS_4XX", "disallow")
    assert AccessPolicy().robots_4xx == "disallow"


# ------------------------------------------------------------- SFS2X / reconcile
def test_sfs2x_decoder_roundtrip_and_errors():
    from app.collectors.sfs2x import DecodeError, decode_message, extension_event
    from tests.collect_server import extension

    cmd, params = extension_event(decode_message(extension("roundChartInfo", {"maxMultiplier": 2.5, "roundId": 7})))
    assert cmd == "roundChartInfo" and params == {"maxMultiplier": 2.5, "roundId": 7}
    # A real frame captured from the demo game (2026-10-21), header + extension response
    real = bytes.fromhex("80003e12000300016302010001610300 0d000170120003000163080016"
                         "63757272656e7442657473496e666f48616e646c6572000172 04ffffffff000170120000".replace(" ", ""))
    assert extension_event(decode_message(real)) == ("currentBetsInfoHandler", {})
    for bad in (b"", b"\x00abc", b"\xc0\x00\x01x", extension("x", {"a": 1})[:-2]):
        with pytest.raises((DecodeError, Exception)):
            decode_message(bad)


def test_reconciler_modes_and_cross_check():
    from datetime import datetime, timezone

    from app.collectors.observer import Reconciler, RoundEvent

    t = datetime.now(timezone.utc)
    ws = lambda v, i: RoundEvent(v, str(i), t, "ws")  # noqa: E731
    dom = lambda v: RoundEvent(v, None, t, "dom")  # noqa: E731
    r = Reconciler()
    assert r.poll([], [dom(1.5)]) == []  # held while waiting for a game message
    out = r.poll([ws(1.5, 1)], [])
    assert r.mode == "ws" and [e.round_id for e in out] == ["1"]
    r.poll([ws(2.0, 2), ws(3.0, 3)], [dom(3.0)])  # history bar missed 2.0x
    r.poll([ws(3.0, 3)], [])  # duplicate result message ignored
    s = r.summary()
    assert s["confirmed"] == 2 and s["ws_only"] == 1 and s["duplicate_ws_results"] == 1
    d = Reconciler(grace_polls=2)
    d.poll([], [dom(1.1)])
    out = d.poll([], [dom(2.2)])
    assert d.mode == "dom" and [e.multiplier for e in out] == [1.1, 2.2]


@needs_browser
def test_observer_uses_game_messages_and_cross_checks_dom(tmp_path):
    with FixtureSite(sfs2x=True, drop_result_for={6}) as game:
        got = []
        res = observe(f"{game.base}/game", "fixture-sfs", minutes=0.12, poll_s=0.5,
                      policy=AccessPolicy(min_interval_s=0), raw_dir=tmp_path,
                      sink=lambda v, t, rid: got.append((rid, v)))
        sent = dict(zip(map(str, game.round_ids), game.sent))
    cc = res.cross_check
    assert res.stop_reason == "time_budget" and cc["mode"] == "ws", res.to_dict()
    assert len(got) >= 10 and all(sent[rid] == v for rid, v in got)  # official ids + exact values
    assert cc["confirmed"] >= len(got) - 3 and cc["dom_only"] <= 1
    assert res.rounds_without_result >= 1  # the withheld result is reported
    lines = open(res.raw_log).read()
    assert "demo_1" not in lines  # other players' data is never stored


def test_spribe_derivation_reproduces_real_published_rounds():
    import json
    from pathlib import Path

    from app.analysis.fairness import spribe_aviator_result, spribe_hash, verify_spribe_record

    data = json.loads((Path(__file__).parent / "data" / "spribe_fairness_samples.json").read_text())["rounds"]
    assert len(data) == 16
    for r in data:
        digest = spribe_hash(r["serverSeed"], r["clientSeeds"])
        assert digest == r["sha512"] and digest[:13] == r["hex13"] and int(r["hex13"], 16) == r["decimal"]
        assert spribe_aviator_result(digest) == r["result"]
        assert verify_spribe_record(r["serverSeed"], r["clientSeeds"], r["result"], r["sha512"])["verified"]
    r = data[0]
    assert not verify_spribe_record(r["serverSeed"], r["clientSeeds"], r["result"] + 0.01, r["sha512"])["verified"]
    assert not verify_spribe_record(r["serverSeed"] + "x", r["clientSeeds"], r["result"], r["sha512"])["verified"]
    # implied distribution: instant crash iff h < 3% of 2**52 -> P(X >= m) = 0.97 / m
    assert spribe_aviator_result("0" * 13) == 1.0 and spribe_aviator_result("0" + "8" * 12) == 1.0


def test_fairness_audit_uses_registered_derivation():
    import json
    from pathlib import Path

    import pandas as pd

    from app.analysis.fairness import audit_fairness

    data = json.loads((Path(__file__).parent / "data" / "spribe_fairness_samples.json").read_text())["rounds"]
    df = pd.DataFrame({"round_id": [str(r["roundId"]) for r in data], "multiplier": [r["result"] for r in data],
                       "server_seed": [r["serverSeed"] for r in data],
                       "client_seeds": [json.dumps(r["clientSeeds"]) for r in data],
                       "server_seed_hash": None, "nonce": None})
    a = audit_fairness(df)
    assert a["multiplier_derivation"] == {"matched": 16, "mismatched": 0}
    assert a["status"] == "results_reproduced_from_revealed_seeds"
    df.loc[0, "multiplier"] = 99.0
    assert audit_fairness(df)["status"] == "result_mismatches_found"


@needs_browser
def test_observer_verifies_fairness_per_round(tmp_path):
    with FixtureSite(sfs2x=True, ws_interval=1.5, dom_delay=1.0) as game:  # bubble lags the message, as live
        game.tamper_result_for = {8}
        game.ignore_fair_s = 1.0  # live game ignores history clicks right after a crash
        got = []

        def sink(v, t, rid, fairness=None):
            got.append((rid, v, fairness))

        res = observe(f"{game.base}/game", "fixture-fair", minutes=0.4, poll_s=0.5,
                      policy=AccessPolicy(min_interval_s=0), raw_dir=tmp_path, verify_fairness=True, sink=sink)
        requests = game.fairness_requests
    f = res.fairness
    assert res.cross_check["mode"] == "ws" and len(got) >= 8, res.to_dict()
    assert requests >= f["verified"] + f["mismatch"] >= len(got) - 4  # first rounds come before the bar has 3 items
    verified = [g for g in got if g[2] and g[2]["verified"]]
    assert verified and all(len(g[2]["client_seeds"]) == 3 for g in verified)
    log = open(res.raw_log).read()
    assert "demo_secret_user" not in log  # player names from fairness records are not stored
    if any(r == str(5000 + 3 * 8) for r, _, _ in got):  # the tampered round, if it was observed
        assert f["mismatch"] >= 1
