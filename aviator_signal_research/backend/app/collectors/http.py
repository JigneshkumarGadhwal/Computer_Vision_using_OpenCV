"""Polite static-page fetching (robots.txt, rate limit, retries, challenge stop)."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .policy import AccessChallenge, AccessPolicy, FetchFailed, NetworkBlocked, classify_network_error, detect_challenge, log

RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    fetched_at: str
    content_type: str
    text: str
    sha256: str


def fetch_static(url: str, policy: AccessPolicy | None = None, client: httpx.Client | None = None,
                 retries: int = 3, backoff_s: float = 2.0) -> FetchResult:
    """GET ``url`` respecting ``policy``. Raises a CollectorError subclass on failure."""
    policy = policy or AccessPolicy()
    own = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        policy.check_allowed(url, client)
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            policy.wait_turn(url)
            try:
                resp = client.get(url, headers={"User-Agent": policy.user_agent,
                                                "Accept": "text/html,application/xhtml+xml"},
                                  timeout=policy.timeout_s)
            except httpx.HTTPError as exc:
                cause = classify_network_error(exc)
                if cause and "connection failed" not in cause:
                    raise NetworkBlocked(f"cannot reach {url}: {cause}", url) from exc
                last_exc = exc
                log.warning("fetch %s failed (attempt %d): %s", url, attempt + 1, exc)
            else:
                if resp.status_code in RETRY_STATUSES and attempt < retries:
                    wait = backoff_s * (2 ** attempt)
                    ra = resp.headers.get("Retry-After")
                    if ra and ra.isdigit():
                        wait = max(wait, float(ra))
                    log.warning("fetch %s -> HTTP %d, retrying in %.1fs", url, resp.status_code, wait)
                    time.sleep(wait)
                    continue
                text = resp.text
                marker = detect_challenge(text, resp.status_code)
                if marker:
                    raise AccessChallenge(
                        f"{url} returned an anti-bot/CAPTCHA page ({marker!r}); collection stopped, not bypassed", url)
                if resp.status_code >= 400:
                    raise FetchFailed(f"{url} -> HTTP {resp.status_code}", url)
                return FetchResult(
                    url=url,
                    final_url=str(resp.url),
                    status=resp.status_code,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    content_type=resp.headers.get("content-type", ""),
                    text=text,
                    sha256=hashlib.sha256(resp.content).hexdigest(),
                )
            if attempt < retries:
                time.sleep(backoff_s * (2 ** attempt))
        if last_exc is not None:
            cause = classify_network_error(last_exc)
            raise NetworkBlocked(f"cannot reach {url}: {cause}", url) if cause else FetchFailed(str(last_exc), url)
        raise FetchFailed(f"{url}: retries exhausted", url)
    finally:
        if own:
            client.close()
