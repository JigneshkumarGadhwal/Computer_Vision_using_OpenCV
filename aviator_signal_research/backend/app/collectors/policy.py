"""Access policy shared by all collectors.

Rules enforced here (never bypassed):

* robots.txt is fetched and obeyed for every host before any page request,
  following RFC 9309 (4xx = no rules; 5xx/unreachable = full disallow;
  ``AVR_ROBOTS_4XX=disallow`` for a stricter reading).
* A per-host minimum interval between requests (rate limit).
* The collector identifies itself honestly via its User-Agent.
* Anti-bot challenges, CAPTCHAs and login walls STOP collection. There is no
  stealth mode, fingerprint spoofing or challenge solving in this project.
* Network-level refusals (DNS failure, proxy/egress policy) are reported as
  such instead of being retried endlessly.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("avr.collectors")

USER_AGENT = os.environ.get(
    "AVR_COLLECTOR_USER_AGENT",
    "AviatorSignalResearch/1.0 (statistical research; respects robots.txt; low request rate)",
)


def default_min_interval() -> float:
    return float(os.environ.get("AVR_COLLECTOR_MIN_INTERVAL", "5"))

# Markers of interstitial bot checks / CAPTCHAs. Seeing one ends collection.
STRONG_CHALLENGE = [
    re.compile(p, re.I)
    for p in (
        r"cf-chl-|/cdn-cgi/challenge-platform|cf_chl_opt",
        r"<title>\s*just a moment\.{0,3}\s*</title>",
        r"attention required!? \| cloudflare",
        r"google\.com/recaptcha/api\.js|hcaptcha\.com/1/api\.js|challenges\.cloudflare\.com/turnstile",
    )
]
# Generic phrases; only meaningful on an error/blocked response.
WEAK_CHALLENGE = [
    re.compile(p, re.I)
    for p in (r"verify (that )?you are (a )?human", r"access denied", r"request blocked", r"unusual traffic")
]


class CollectorError(RuntimeError):
    """Base class; ``reason`` is a stable machine-readable code."""

    reason = "collector_error"

    def __init__(self, message: str, url: str | None = None):
        super().__init__(message)
        self.url = url

    def to_dict(self) -> dict:
        return {"reason": self.reason, "message": str(self), "url": self.url}


class NetworkBlocked(CollectorError):
    reason = "network_blocked"


class RobotsDisallowed(CollectorError):
    reason = "robots_disallowed"


class AccessChallenge(CollectorError):
    reason = "access_challenge"


class FetchFailed(CollectorError):
    reason = "fetch_failed"


def detect_challenge(html: str, status: int = 200) -> str | None:
    head = html[:200_000]
    for pat in STRONG_CHALLENGE + (WEAK_CHALLENGE if status != 200 else []):
        m = pat.search(head)
        if m:
            return m.group(0)
    return None


def classify_network_error(exc: Exception) -> str | None:
    """Return a human-readable cause if ``exc`` is an environment/egress refusal."""
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, httpx.ProxyError) or "CONNECT tunnel failed" in text or "407" in text:
        return "outbound proxy refused the connection (host not allowed by the network policy)"
    if re.search(r"Name or service not known|nodename nor servname|getaddrinfo|ENOTFOUND|Temporary failure in name", text):
        return "DNS resolution failed (host unreachable from this environment)"
    if isinstance(exc, httpx.ConnectError):
        return f"connection failed: {exc}"
    return None


@dataclass
class AccessPolicy:
    min_interval_s: float = field(default_factory=default_min_interval)
    user_agent: str = USER_AGENT
    timeout_s: float = 20.0
    obey_robots: bool = True
    robots_4xx: str = field(default_factory=lambda: os.environ.get("AVR_ROBOTS_4XX", "allow"))  # allow | disallow
    robots_notes: dict[str, str] = field(default_factory=dict)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict)
    _last_request: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def wait_turn(self, url: str) -> None:
        """Block until this host may be requested again."""
        host = urlsplit(url).netloc
        with self._lock:
            last = self._last_request.get(host)
            now = time.monotonic()
            if last is not None and now - last < self.min_interval_s:
                time.sleep(self.min_interval_s - (now - last))
            self._last_request[host] = time.monotonic()

    def robots_for(self, url: str, client: httpx.Client) -> RobotFileParser | None:
        origin = self.origin(url)
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        rp: RobotFileParser | None = RobotFileParser(robots_url)
        try:
            resp = client.get(robots_url, headers={"User-Agent": self.user_agent}, timeout=self.timeout_s)
        except httpx.HTTPError as exc:
            cause = classify_network_error(exc)
            if cause:
                raise NetworkBlocked(f"cannot reach {origin}: {cause}", robots_url) from exc
            raise FetchFailed(f"robots.txt request failed: {exc}", robots_url) from exc
        if 500 <= resp.status_code or resp.status_code == 429:
            # RFC 9309 §2.3.1.4: robots.txt unreachable (5xx) -> assume complete disallow.
            rp.parse(["User-agent: *", "Disallow: /"])
            self.robots_notes[origin] = f"robots.txt HTTP {resp.status_code}: treated as full disallow (RFC 9309)"
        elif 400 <= resp.status_code < 500:
            # RFC 9309 §2.3.1.3: robots.txt unavailable (4xx, incl. 401/403) -> crawlers MAY access the site.
            # ``robots_4xx="disallow"`` keeps the stricter behaviour if preferred.
            if self.robots_4xx == "disallow":
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                rp = None
            self.robots_notes[origin] = (f"robots.txt HTTP {resp.status_code}: treated as "
                                         f"{'full disallow (strict mode)' if self.robots_4xx == 'disallow' else 'no restrictions (RFC 9309)'}")
        else:
            body = resp.text
            if "<html" in body[:500].lower():
                self.robots_notes[origin] = "robots.txt returned an HTML page (no robots rules published)"
            rp.parse(body.splitlines())
        self._robots[origin] = rp
        return rp

    def check_allowed(self, url: str, client: httpx.Client) -> None:
        if not self.obey_robots:
            return
        rp = self.robots_for(url, client)
        if rp is not None and not rp.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt of {self.origin(url)} disallows {url}", url)
        if rp is not None:
            delay = rp.crawl_delay(self.user_agent)
            if delay and delay > self.min_interval_s:
                self.min_interval_s = float(delay)
