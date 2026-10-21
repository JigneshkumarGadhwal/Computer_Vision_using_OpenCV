"""Observe completed rounds on a publicly accessible game page.

How it works
------------
1. robots.txt is checked; the page is opened once in headless Chromium
   (official Playwright, honest User-Agent, no stealth).
2. Every ``poll_s`` seconds the *already loaded* DOM (including iframes) is
   read -- no extra requests are sent to the server.
3. :class:`HistoryExtractor` (Scrapling parser) finds the "recent results"
   bar: the container holding the most elements whose text is a multiplier
   such as ``2.31x``. No site-specific class names are required; a CSS
   selector can be pinned in config, and is re-located if the markup changes.
4. :func:`new_since` aligns the new snapshot with the previous one and returns
   only rounds that appeared since, oldest first. If the snapshots cannot be
   aligned, a gap is recorded instead of guessing.
5. New rounds are written to a raw JSONL log and ingested through the normal
   validation path as source kind ``public_observation`` (``verified=False``;
   timestamps are observation times, not official round times). WebSocket
   frames can be logged raw for later protocol analysis; they are not decoded.

Collection stops on anti-bot/CAPTCHA pages, robots.txt disallow, network
refusal, repeated page failure, or when the time / round budget is used up.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from lxml import html as lxml_html
from scrapling.parser import Selector

from .browser import browser_context, locate, rendered_html
from .sfs2x import DecodeError, decode_message, extension_event
from .policy import AccessChallenge, AccessPolicy, CollectorError, FetchFailed, NetworkBlocked, classify_network_error, detect_challenge, log

MULT_RE = re.compile(r"^\s*(\d{1,7}(?:[.,]\d{1,2})?)\s*[x×]\s*$", re.I)


def parse_multiplier_text(t: str) -> float | None:
    m = MULT_RE.match(t or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v if v >= 1.0 else None


@dataclass
class Snapshot:
    values: list[float]
    container: str | None
    relocated: bool = False


def _flat_text(el) -> str:
    return re.sub(r"\s+", "", "".join(t for t in el.itertext()))


@dataclass
class HistoryExtractor:
    """Locate the recent-results bar and read its multipliers in DOM order.

    Auto-detection: every element whose *full* text is a multiplier is lifted to
    the outermost ancestor still showing the same text (so ``<div><span>2.31</span>x</div>``
    and ``<div>2.31x</div>`` are treated alike); the parent holding the most such
    items is the bar. Its XPath is pinned so later polls read the same container
    and do not jump to e.g. a list of other players' cash-outs. If the pinned
    container disappears, detection runs again and the event is reported.
    """

    selector: str | None = None  # optional user CSS for the items (parsed with Scrapling)
    min_items: int = 3
    _pinned_xpath: str | None = field(default=None, init=False)

    def _items_auto(self, root) -> tuple[list, object | None]:
        groups: dict = {}
        seen: set = set()
        for el in root.iter():
            if not isinstance(el.tag, str) or el.tag in ("script", "style", "noscript"):
                continue
            if len(el) > 6:  # containers with many children are not a single value
                continue
            t = _flat_text(el)
            if len(t) > 14 or parse_multiplier_text(t) is None:
                continue
            top = el
            while top.getparent() is not None and _flat_text(top.getparent()) == t:
                top = top.getparent()
            if id(top) in seen:
                continue
            seen.add(id(top))
            parent = top.getparent()
            if parent is not None:
                groups.setdefault(parent, []).append(top)
        if not groups:
            return [], None
        parent = max(groups, key=lambda p: len(groups[p]))
        items = groups[parent]
        return (items, parent) if len(items) >= self.min_items else ([], None)

    def extract(self, html: str, url: str = "") -> Snapshot:
        if self.selector:
            sel = Selector(html, url=url or "about:blank")
            vals = [parse_multiplier_text(re.sub(r"\s+", "", str(e.get_all_text(separator="", strip=True))))
                    for e in sel.css(self.selector)]
            return Snapshot([v for v in vals if v is not None], self.selector)
        root = lxml_html.fromstring(html)
        tree = root.getroottree()
        relocated = False
        if self._pinned_xpath:
            found = root.xpath(self._pinned_xpath)
            if found:
                items = [c for c in found[0] if isinstance(c.tag, str)]
                vals = [parse_multiplier_text(_flat_text(c)) for c in items]
                vals = [v for v in vals if v is not None]
                if len(vals) >= self.min_items:
                    return Snapshot(vals, self._pinned_xpath)
            relocated = True
        items, parent = self._items_auto(root)
        if parent is None:
            return Snapshot([], None, relocated)
        self._pinned_xpath = tree.getpath(parent)
        vals = [parse_multiplier_text(_flat_text(e)) for e in items]
        return Snapshot([v for v in vals if v is not None], self._pinned_xpath, relocated)


def new_since(prev: list[float], cur: list[float], newest_first: bool = True,
              min_overlap: int = 3) -> tuple[list[float], str]:
    """Rounds present in ``cur`` but not in ``prev``, returned oldest first.

    Status: ``baseline`` (no previous snapshot), ``no_change``, ``ok`` or ``gap``
    (no alignment -- nothing is emitted rather than guessing)."""
    if not newest_first:
        prev, cur = prev[::-1], cur[::-1]
    if not prev:
        return [], "baseline"
    if not cur:
        return [], "gap"
    need = min(min_overlap, len(prev))
    for k in range(len(cur)):
        m = min(len(prev), len(cur) - k)
        if m < need:
            break
        if cur[k:k + m] == prev[:m]:
            return (cur[:k][::-1], "ok" if k else "no_change")
    return [], "gap"


@dataclass
class RoundEvent:
    multiplier: float
    round_id: str | None
    timestamp: datetime
    origin: str  # "ws" | "dom"


@dataclass
class Reconciler:
    """Decides which channel is authoritative and cross-checks the other.

    * ``ws`` mode: decoded WebSocket results (official round id + multiplier) are
      ingested; history-bar rounds are used only to confirm them.
    * ``dom`` mode: no decodable results arrived, so history-bar rounds are ingested.
    The mode is fixed once decided: DOM rounds are held for ``grace_polls`` polls
    waiting for a WebSocket result before falling back to ``dom``.
    """

    grace_polls: int = 3
    mode: str | None = None
    _held_dom: list = field(default_factory=list)
    _held_age: int = 0
    _ws_unmatched: list = field(default_factory=list)
    _dom_unmatched: list = field(default_factory=list)
    _seen_ids: set = field(default_factory=set)
    stats: dict = field(default_factory=lambda: {"ws_rounds": 0, "dom_rounds": 0, "confirmed": 0, "ws_only": 0,
                                                  "dom_only": 0, "duplicate_ws_results": 0})

    def _match(self) -> None:
        ws, dom = self._ws_unmatched, self._dom_unmatched
        while ws and dom:
            if ws[0].multiplier == dom[0].multiplier:
                ws.pop(0), dom.pop(0)
                self.stats["confirmed"] += 1
                continue
            later_ws = next((k for k in range(1, len(ws)) if ws[k].multiplier == dom[0].multiplier), None)
            later_dom = next((k for k in range(1, len(dom)) if dom[k].multiplier == ws[0].multiplier), None)
            if later_ws is not None and (later_dom is None or later_ws <= later_dom):
                self.stats["ws_only"] += later_ws
                del ws[:later_ws]
            elif later_dom is not None:
                self.stats["dom_only"] += later_dom
                del dom[:later_dom]
            elif len(ws) > 3 or len(dom) > 3:
                self.stats["ws_only"] += 1
                self.stats["dom_only"] += 1
                ws.pop(0), dom.pop(0)
            else:
                break

    def poll(self, ws_results: list[RoundEvent], dom_new: list[RoundEvent]) -> list[RoundEvent]:
        """Feed one poll's events; return the rounds to ingest now."""
        out: list[RoundEvent] = []
        fresh_ws = []
        for e in ws_results:
            if e.round_id in self._seen_ids:
                self.stats["duplicate_ws_results"] += 1
                continue
            self._seen_ids.add(e.round_id)
            fresh_ws.append(e)
        self.stats["ws_rounds"] += len(fresh_ws)
        self.stats["dom_rounds"] += len(dom_new)
        if self.mode is None:
            if fresh_ws:
                self.mode = "ws"
                self._dom_unmatched.extend(self._held_dom)
                self._held_dom = []
            elif dom_new or self._held_dom:
                self._held_dom.extend(dom_new)
                self._held_age += 1
                if self._held_age >= self.grace_polls:
                    self.mode = "dom"
                    out, self._held_dom = list(self._held_dom), []
                return out
        if self.mode == "ws":
            out.extend(fresh_ws)
            self._ws_unmatched.extend(fresh_ws)
            self._dom_unmatched.extend(dom_new)
            self._match()
        elif self.mode == "dom":
            out.extend(dom_new)
        return out

    def summary(self) -> dict:
        return {"mode": self.mode, **self.stats, "pending_ws": len(self._ws_unmatched),
                "pending_dom": len(self._dom_unmatched) + len(self._held_dom)}


@dataclass
class ObservationResult:
    url: str
    source_name: str
    started_at: str
    finished_at: str = ""
    stop_reason: str = ""
    polls: int = 0
    rounds_ingested: int = 0
    gaps: int = 0
    relocations: int = 0
    ws_messages_decoded: int = 0
    ws_decode_errors: int = 0
    raw_frames_logged: int = 0
    rounds_without_result: int = 0
    fairness: dict = field(default_factory=lambda: {"requested": 0, "verified": 0, "mismatch": 0, "unavailable": 0})
    cross_check: dict = field(default_factory=dict)
    container: str | None = None
    errors: list[dict] = field(default_factory=list)
    raw_log: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


RoundSink = Callable[..., None]  # sink(multiplier, round_time, round_id[, fairness=dict])


def make_db_sink(source_name: str, url: str) -> RoundSink:
    """Persist each observed round through the standard ingestion path."""
    from ..db import session_scope
    from ..ingestion.service import add_single_round, get_or_create_source
    from ..signals import resolve_live_outcomes

    created = False

    def sink(value: float, round_time: datetime, round_id: str, fairness: dict | None = None) -> None:
        nonlocal created
        with session_scope() as s:
            if not created:  # create the source only once a real round has been observed
                get_or_create_source(
                    s, source_name, "public_observation",
                    description=(f"Rounds observed on {url} (public interface). Not verified; a demo page may not "
                                 "reflect real-money rounds. Round ids/times come from the game's own messages when "
                                 "decodable, otherwise ids are generated and times are observation times."),
                )
                created = True
            f = fairness or {}
            r = add_single_round(s, source_name, "public_observation", value, round_id=round_id,
                                 round_timestamp=round_time, verified=bool(f.get("verified")),
                                 server_seed=f.get("server_seed"), client_seeds=f.get("client_seeds"))
            resolve_live_outcomes(s, r.source_id)

    return sink


# SFS2X extension commands carrying round-level state (no player data is kept).
_ROUND_RESULT_CMDS = {"roundChartInfo": "maxMultiplier"}
_STATE_CMD = "changeState"
_FAIRNESS_CMD = "roundFairnessResponse"


def observe(url: str, source_name: str, *, minutes: float = 30, poll_s: float = 2.0, max_rounds: int | None = None,
            selector: str | None = None, newest_first: bool = True, min_overlap: int = 3,
            sink: RoundSink | None = None, policy: AccessPolicy | None = None, raw_dir: Path | None = None,
            decode_ws: bool = True, log_raw_frames: bool = False, verify_fairness: bool = False,
            stale_after_s: float = 300,
            max_page_failures: int = 3, challenge_check_every: int = 15) -> ObservationResult:
    """Record completed rounds from a public game page. See module docstring.

    Raw WebSocket frames contain other players' names and bets, so they are not
    stored unless ``log_raw_frames=True``; only round-level fields are kept.
    """
    policy = policy or AccessPolicy()
    raw_dir = raw_dir or Path("data/raw/observations")
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = raw_dir / f"observe_{re.sub(r'[^A-Za-z0-9_-]+', '_', source_name)}_{stamp}.jsonl"
    res = ObservationResult(url=url, source_name=source_name, started_at=datetime.now(timezone.utc).isoformat(),
                            raw_log=str(raw_path))
    extractor = HistoryExtractor(selector=selector)
    reconciler = Reconciler()
    ws_queue: list[RoundEvent] = []
    flight_start: dict[str, datetime] = {}  # round id -> server time the flight started
    awaiting_result: dict[str, str] = {}  # round id -> time its betting phase was seen
    fairness_records: dict[str, dict] = {}  # round id -> revealed seeds (no player data)
    betting_started: dict[str, float] = {}  # round id -> monotonic time its betting phase began
    lock = threading.Lock()

    def raw(event: dict) -> None:
        with raw_path.open("a") as f:
            f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False,
                               default=str) + "\n")

    try:
        with httpx.Client(follow_redirects=True) as client:
            policy.check_allowed(url, client)
        if policy.robots_notes.get(policy.origin(url)):
            raw({"kind": "robots_checked", "origin": policy.origin(url), "note": policy.robots_notes[policy.origin(url)]})
    except CollectorError as exc:
        res.stop_reason, res.finished_at = exc.reason, datetime.now(timezone.utc).isoformat()
        res.errors.append(exc.to_dict())
        raw({"kind": "summary", **res.to_dict()})
        return res
    sink = sink or make_db_sink(source_name, url)  # the source itself is created on the first round

    def on_frame(payload, direction: str, ws_url: str) -> None:
        if log_raw_frames and res.raw_frames_logged < 20000:
            res.raw_frames_logged += 1
            body = ({"b64": base64.b64encode(bytes(payload)).decode()} if isinstance(payload, (bytes, bytearray))
                    else {"text": payload[:4000]})
            raw({"kind": "ws_frame", "dir": direction, "url": ws_url, **body})
        if not decode_ws or direction != "in" or not isinstance(payload, (bytes, bytearray)):
            return
        try:
            cmd, params = extension_event(decode_message(bytes(payload)))
        except DecodeError:
            res.ws_decode_errors += 1
            return
        res.ws_messages_decoded += 1
        now = datetime.now(timezone.utc)
        if cmd == _STATE_CMD and "roundId" in params:
            rid = str(params["roundId"])
            state = params.get("newStateId")
            event = {"kind": "ws_state", "round_id": rid, "state": state}
            if state == 1:
                with lock:
                    betting_started[rid] = time.monotonic()
            if "betStateEndTime" in params:  # betting phase: flight starts when betting ends
                flight_start[rid] = datetime.fromtimestamp(params["betStateEndTime"] / 1000, timezone.utc)
                event["flight_start"] = flight_start[rid].isoformat()
                with lock:
                    for other, seen_at in list(awaiting_result.items()):
                        if other != rid:
                            res.rounds_without_result += 1
                            raw({"kind": "round_without_result", "round_id": other, "betting_seen": seen_at})
                            awaiting_result.pop(other)
                    awaiting_result[rid] = now.isoformat()
            raw(event)
        elif cmd == _FAIRNESS_CMD:
            f = params.get("fairness") or {}
            if "roundId" in f and "serverSeed" in f:
                rid = str(f["roundId"])
                rec = {"server_seed": f["serverSeed"],
                       "client_seeds": [p.get("seed") for p in f.get("playerSeeds") or [] if p.get("seed")],
                       "published_hash": f.get("seedSHA256"), "result": f.get("result")}
                with lock:
                    fairness_records[rid] = rec
                raw({"kind": "ws_fairness", "round_id": rid, **rec})
        elif cmd in _ROUND_RESULT_CMDS and "roundId" in params:
            rid = str(params["roundId"])
            value = params.get(_ROUND_RESULT_CMDS[cmd])
            if isinstance(value, (int, float)) and value >= 1.0:
                with lock:
                    awaiting_result.pop(rid, None)
                    ws_queue.append(RoundEvent(round(float(value), 2), rid, flight_start.get(rid, now), "ws"))
                raw({"kind": "ws_result", "round_id": rid, "multiplier": value})

    def request_fairness(page, container_xpath: str, rid: str, multiplier: float) -> dict | None:
        """Open the round's fairness record in the game (one request), then re-derive the result."""
        from ..analysis.fairness import verify_spribe_record

        res.fairness["requested"] += 1
        frame, inner = locate(page, container_xpath)
        # The game ignores history clicks right after a crash; wait until the next betting
        # phase has started (or 10 s), then click the bubble showing this round's result.
        t_req = time.monotonic()
        while time.monotonic() - t_req < 10:
            with lock:
                if any(t >= t_req - 0.5 and r != rid for r, t in betting_started.items()):
                    break
            page.wait_for_timeout(200)
        rec = None
        for attempt in range(2):
            items = frame.query_selector_all(f"xpath={inner}/*")[:3]
            candidates = [it for it in items
                          if parse_multiplier_text(re.sub(r"\s+", "", it.inner_text() or "")) == multiplier]
            if not candidates:
                break
            try:
                candidates[0].click(timeout=3000)
            except Exception:
                page.wait_for_timeout(1000)
                continue
            t_end = time.monotonic() + 4
            while time.monotonic() < t_end:
                with lock:
                    rec = fairness_records.get(rid)
                if rec:
                    break
                page.wait_for_timeout(200)
            page.keyboard.press("Escape")
            for close in page.query_selector_all(".modal-content .close, .modal-content [aria-label=Close]"):
                try:
                    close.click(timeout=1000)
                except Exception:
                    pass
            if rec:
                break
        if not rec:
            res.fairness["unavailable"] += 1
            raw({"kind": "fairness_unavailable", "round_id": rid})
            return None
        check = verify_spribe_record(rec["server_seed"], rec["client_seeds"], multiplier, rec.get("published_hash"))
        published_ok = rec.get("result") is None or abs(float(rec["result"]) - multiplier) < 1e-9
        verified = check["verified"] and published_ok
        res.fairness["verified" if verified else "mismatch"] += 1
        raw({"kind": "fairness_check", "round_id": rid, **check, "published_result_matches": published_ok})
        return {**rec, "verified": verified}

    deadline = time.monotonic() + minutes * 60
    seq = 0
    try:
        with browser_context(policy.user_agent, target_url=url) as ctx:
            page = ctx.new_page()

            def on_ws(ws):
                raw({"kind": "ws_open", "url": ws.url})
                ws.on("framereceived", lambda p: on_frame(p, "in", ws.url))
                ws.on("framesent", lambda p: on_frame(p, "out", ws.url))

            page.on("websocket", on_ws)

            def load() -> None:
                policy.wait_turn(url)
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=policy.timeout_s * 1000)
                except Exception as exc:
                    cause = classify_network_error(exc) or (
                        "outbound proxy refused the connection" if "ERR_TUNNEL_CONNECTION_FAILED" in str(exc) else None)
                    if cause or "ERR_NAME_NOT_RESOLVED" in str(exc):
                        raise NetworkBlocked(f"cannot open {url} (or a host it redirects to): "
                                             f"{cause or 'DNS resolution failed'}", url) from exc
                    raise FetchFailed(f"cannot open {url}: {exc}", url) from exc
                status = resp.status if resp else 0
                html = rendered_html(page)
                marker = detect_challenge(html, status)
                if marker:
                    raise AccessChallenge(f"{url} shows an anti-bot/CAPTCHA page ({marker!r}); stopped, not bypassed", url)
                if status >= 400:
                    raise FetchFailed(f"{url} -> HTTP {status}", url)
                raw({"kind": "page_loaded", "url": page.url, "status": status})
                if policy.origin(page.url) != policy.origin(url):
                    # Redirected to another host (e.g. a game server): its robots.txt applies too.
                    with httpx.Client(follow_redirects=True) as client:
                        policy.check_allowed(page.url, client)
                    raw({"kind": "robots_checked", "origin": policy.origin(page.url),
                         "note": policy.robots_notes.get(policy.origin(page.url))})

            load()
            prev: list[float] = []
            failures = 0
            last_new = time.monotonic()
            while time.monotonic() < deadline:
                if max_rounds is not None and res.rounds_ingested >= max_rounds:
                    res.stop_reason = "max_rounds"
                    break
                time.sleep(poll_s)
                res.polls += 1
                try:
                    html = rendered_html(page)
                except Exception as exc:
                    failures += 1
                    raw({"kind": "page_error", "error": str(exc)[:300]})
                    if failures > max_page_failures:
                        res.stop_reason = "page_failures"
                        break
                    time.sleep(min(60, 5 * 2 ** failures))
                    load()
                    prev = []
                    continue
                if res.polls % challenge_check_every == 0:
                    marker = detect_challenge(html)
                    if marker:
                        raise AccessChallenge(f"anti-bot/CAPTCHA appeared during observation ({marker!r})", url)
                snap = extractor.extract(html, page.url)
                if snap.relocated:
                    res.relocations += 1
                    raw({"kind": "selector_relocated", "container": snap.container})
                res.container = snap.container or res.container
                new, status = new_since(prev, snap.values, newest_first, min_overlap)
                if status == "gap":
                    res.gaps += 1
                    raw({"kind": "gap", "prev_head": prev[:10], "cur_head": snap.values[:10]})
                if snap.values:
                    prev = snap.values
                now = datetime.now(timezone.utc)
                for v in new:
                    raw({"kind": "dom_round", "multiplier": v})
                with lock:
                    ws_now, ws_queue[:] = list(ws_queue), []
                dom_events = [RoundEvent(v, None, now, "dom") for v in new]
                for ev in reconciler.poll(ws_now, dom_events):
                    seq += 1
                    rid = ev.round_id or f"obs-{now:%Y%m%dT%H%M%S%f}-{seq}"
                    fair = None
                    if verify_fairness and ev.round_id and res.container:
                        fair = request_fairness(page, res.container, ev.round_id, ev.multiplier)
                    raw({"kind": "round", "round_id": rid, "multiplier": ev.multiplier, "origin": ev.origin,
                         "round_time": ev.timestamp.isoformat(),
                         **({"fairness_verified": fair["verified"]} if fair else {})})
                    if fair:
                        sink(ev.multiplier, ev.timestamp, rid, fairness=fair)
                    else:
                        sink(ev.multiplier, ev.timestamp, rid)
                    res.rounds_ingested += 1
                    last_new = time.monotonic()
                res.cross_check = reconciler.summary()
                if time.monotonic() - last_new > stale_after_s:
                    res.stop_reason = "stale_no_new_rounds"
                    break
            else:
                res.stop_reason = res.stop_reason or "time_budget"
    except CollectorError as exc:
        res.stop_reason = exc.reason
        res.errors.append(exc.to_dict())
        log.warning("observation stopped: %s", exc)
    except Exception as exc:
        res.stop_reason = "error"
        res.errors.append({"reason": "error", "message": f"{type(exc).__name__}: {exc}", "url": url})
        log.exception("observation failed")
    res.cross_check = reconciler.summary()
    res.finished_at = datetime.now(timezone.utc).isoformat()
    raw({"kind": "summary", **{k: v for k, v in res.to_dict().items() if k != "errors"}, "errors": res.errors})
    return res
