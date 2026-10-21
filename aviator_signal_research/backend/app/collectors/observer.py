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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from lxml import html as lxml_html
from scrapling.parser import Selector

from .browser import browser_context, rendered_html
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
    ws_frames_logged: int = 0
    container: str | None = None
    errors: list[dict] = field(default_factory=list)
    raw_log: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


RoundSink = Callable[[float, datetime, str], None]


def make_db_sink(source_name: str, url: str) -> RoundSink:
    """Persist each observed round through the standard ingestion path."""
    from ..db import session_scope
    from ..ingestion.service import add_single_round, get_or_create_source
    from ..signals import resolve_live_outcomes

    created = False

    def sink(value: float, observed_at: datetime, round_id: str) -> None:
        nonlocal created
        with session_scope() as s:
            if not created:  # create the source only once a real round has been observed
                get_or_create_source(
                    s, source_name, "public_observation",
                    description=(f"Rounds observed on {url} (public interface). Not verified; timestamps are "
                                 "observation times. A demo page may not reflect real-money rounds."),
                )
                created = True
            r = add_single_round(s, source_name, "public_observation", value, round_id=round_id,
                                 round_timestamp=observed_at, verified=False)
            resolve_live_outcomes(s, r.source_id)

    return sink


def observe(url: str, source_name: str, *, minutes: float = 30, poll_s: float = 2.0, max_rounds: int | None = None,
            selector: str | None = None, newest_first: bool = True, min_overlap: int = 3,
            sink: RoundSink | None = None, policy: AccessPolicy | None = None, raw_dir: Path | None = None,
            log_websockets: bool = True, stale_after_s: float = 300, max_page_failures: int = 3,
            challenge_check_every: int = 15) -> ObservationResult:
    policy = policy or AccessPolicy()
    raw_dir = raw_dir or Path("data/raw/observations")
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = raw_dir / f"observe_{re.sub(r'[^A-Za-z0-9_-]+', '_', source_name)}_{stamp}.jsonl"
    res = ObservationResult(url=url, source_name=source_name, started_at=datetime.now(timezone.utc).isoformat(),
                            raw_log=str(raw_path))
    extractor = HistoryExtractor(selector=selector)

    def raw(event: dict) -> None:
        with raw_path.open("a") as f:
            f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False) + "\n")

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
    sink = sink or make_db_sink(source_name, url)  # only create the source once access is allowed

    deadline = time.monotonic() + minutes * 60
    seq = 0
    try:
        with browser_context(policy.user_agent, target_url=url) as ctx:
            page = ctx.new_page()

            if log_websockets:
                def on_ws(ws):
                    raw({"kind": "ws_open", "url": ws.url})

                    def frame(payload, direction):
                        if res.ws_frames_logged >= 20000:
                            return
                        res.ws_frames_logged += 1
                        if isinstance(payload, (bytes, bytearray)):
                            body = {"b64": base64.b64encode(bytes(payload[:1024])).decode(), "len": len(payload)}
                        else:
                            body = {"text": payload[:2000], "len": len(payload)}
                        raw({"kind": "ws_frame", "dir": direction, "url": ws.url, **body})

                    ws.on("framereceived", lambda p: frame(p, "in"))
                    ws.on("framesent", lambda p: frame(p, "out"))
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
                    seq += 1
                    rid = f"obs-{now:%Y%m%dT%H%M%S%f}-{seq}"
                    raw({"kind": "round", "round_id": rid, "multiplier": v, "container": snap.container})
                    sink(v, now, rid)
                    res.rounds_ingested += 1
                    last_new = time.monotonic()
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
    res.finished_at = datetime.now(timezone.utc).isoformat()
    raw({"kind": "summary", **{k: v for k, v in res.to_dict().items() if k != "errors"}, "errors": res.errors})
    return res
