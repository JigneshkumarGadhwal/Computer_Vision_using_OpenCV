"""Adapter interfaces, capability registry and a resilient HTTP client.

Market-data adapters are public/read-only. Trading adapters hold credentials and are only
ever instantiated inside the execution service (see ``execution/service.py``).
"""
from __future__ import annotations

import abc
import asyncio
import logging
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import httpx

from ..domain import Action, NormalizedMarket, OrderBook, Outcome, VenueFill, VenueOrderAck, VenuePosition, utcnow

log = logging.getLogger("argona.exchanges")


@dataclass
class Capabilities:
    discover_markets: bool = False
    market_metadata: bool = False
    orderbook: bool = False
    batch_orderbook: bool = False
    streaming: bool = False
    place_orders: bool = False
    cancel_orders: bool = False
    order_status: bool = False
    fills: bool = False
    balances: bool = False
    positions: bool = False
    client_order_id: bool = False
    market_orders: bool = False
    demo_environment: bool = False
    requires_credentials_for_data: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderError(Exception):
    """Recoverable provider failure (network, 5xx, malformed payload)."""


class RateLimited(ProviderError):
    pass


class AuthError(ProviderError):
    pass


class SubmissionUncertain(ProviderError):
    """Order submission may or may not have reached the venue. Never blindly retry."""


@dataclass
class HttpStats:
    requests: int = 0
    errors: int = 0
    rate_limited: int = 0
    last_ok: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["last_ok"] = self.last_ok.isoformat() if self.last_ok else None
        d["last_error_at"] = self.last_error_at.isoformat() if self.last_error_at else None
        return d


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float | None = None) -> None:
        self.rate = max(rate_per_s, 0.1)
        self.capacity = burst or max(1.0, rate_per_s)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(cap, base * (2 ** attempt)))


class HttpClient:
    """httpx wrapper: rate limiting, retries with backoff+jitter for idempotent requests, stats."""

    def __init__(self, base_url: str, *, rps: float = 8.0, timeout: float = 15.0, max_retries: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = TokenBucket(rps)
        self.max_retries = max_retries
        self.stats = HttpStats()
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport,
                                        headers={"User-Agent": "ARGONA/1.0", **(headers or {})})

    async def close(self) -> None:
        await self.client.aclose()

    def _ok(self) -> None:
        self.stats.last_ok = utcnow()
        self.stats.consecutive_failures = 0

    def _fail(self, msg: str) -> None:
        self.stats.errors += 1
        self.stats.consecutive_failures += 1
        self.stats.last_error = msg[:300]
        self.stats.last_error_at = utcnow()

    async def request(self, method: str, path: str, *, params: Any = None, json: Any = None,
                      headers: dict[str, str] | None = None, idempotent: bool | None = None,
                      header_factory: Any = None) -> Any:
        idempotent = method.upper() in ("GET", "HEAD") if idempotent is None else idempotent
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        attempts = self.max_retries + 1 if idempotent else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            await self.bucket.acquire()
            self.stats.requests += 1
            hdrs = dict(headers or {})
            if header_factory:
                hdrs.update(header_factory(method, url))
            try:
                resp = await self.client.request(method, url, params=params, json=json, headers=hdrs)
            except httpx.TransportError as e:  # timeouts, connection/proxy/protocol errors
                self._fail(f"{type(e).__name__}: {e}")
                last_exc = e
                if not idempotent:
                    raise SubmissionUncertain(f"{method} {path}: {type(e).__name__}") from e
                await asyncio.sleep(backoff_delay(attempt))
                continue
            if resp.status_code == 429:
                self.stats.rate_limited += 1
                self._fail("429 rate limited")
                last_exc = RateLimited(f"{method} {path}: 429")
                if not idempotent:
                    raise last_exc
                retry_after = resp.headers.get("Retry-After")
                await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else backoff_delay(attempt + 1))
                continue
            if resp.status_code in (401, 403):
                self._fail(f"{resp.status_code} auth")
                raise AuthError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}")
            if resp.status_code >= 500:
                self._fail(f"{resp.status_code} server error")
                last_exc = ProviderError(f"{method} {path}: HTTP {resp.status_code}")
                if not idempotent:
                    raise SubmissionUncertain(f"{method} {path}: HTTP {resp.status_code}")
                await asyncio.sleep(backoff_delay(attempt))
                continue
            if resp.status_code >= 400:
                self._fail(f"{resp.status_code}")
                raise ProviderError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
            self._ok()
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError as e:
                raise ProviderError(f"{method} {path}: invalid JSON") from e
        if isinstance(last_exc, ProviderError):
            raise last_exc
        raise ProviderError(f"{method} {path}: {type(last_exc).__name__}: {last_exc}") from last_exc


class MarketDataAdapter(abc.ABC):
    venue: str
    is_simulated: bool = False
    capabilities: Capabilities

    def __init__(self) -> None:
        self.http_clients: list[HttpClient] = []

    @abc.abstractmethod
    async def discover(self) -> list[NormalizedMarket]:
        """Return currently tradable (or recently changed) markets, normalised."""

    @abc.abstractmethod
    async def fetch_books(self, markets: list[NormalizedMarket]) -> dict[str, OrderBook]:
        """Return YES-side order books keyed by market key."""

    @abc.abstractmethod
    async def refresh_market(self, market: NormalizedMarket) -> NormalizedMarket | None:
        """Fetch the latest metadata/status/resolution for one market."""

    def stats(self) -> dict[str, Any]:
        agg = HttpStats()
        for c in self.http_clients:
            agg.requests += c.stats.requests
            agg.errors += c.stats.errors
            agg.rate_limited += c.stats.rate_limited
            agg.consecutive_failures = max(agg.consecutive_failures, c.stats.consecutive_failures)
            if c.stats.last_ok and (agg.last_ok is None or c.stats.last_ok > agg.last_ok):
                agg.last_ok = c.stats.last_ok
            if c.stats.last_error_at and (agg.last_error_at is None or c.stats.last_error_at > agg.last_error_at):
                agg.last_error_at, agg.last_error = c.stats.last_error_at, c.stats.last_error
        return agg.to_dict()

    async def close(self) -> None:
        for c in self.http_clients:
            await c.close()


class TradingAdapter(abc.ABC):
    """Authenticated order routing. Only the execution service owns instances of this class."""

    venue: str
    is_simulated: bool = False

    @abc.abstractmethod
    async def verify(self) -> dict[str, Any]:
        """Check credentials/connectivity; returns e.g. {"balance": 123.45}. Raises AuthError."""

    @abc.abstractmethod
    async def place_limit_order(self, market: NormalizedMarket, outcome: Outcome, action: Action, price: float,
                                qty: float, client_order_id: str, expires_at: datetime | None) -> VenueOrderAck: ...

    @abc.abstractmethod
    async def cancel_order(self, market: NormalizedMarket, venue_order_id: str) -> VenueOrderAck: ...

    @abc.abstractmethod
    async def get_order(self, market: NormalizedMarket, venue_order_id: str | None,
                        client_order_id: str | None) -> VenueOrderAck | None:
        """Look an order up by venue id or client id. None means the venue has no such order."""

    @abc.abstractmethod
    async def list_open_orders(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def get_fills(self, since: datetime | None) -> list[VenueFill]: ...

    @abc.abstractmethod
    async def get_balance(self) -> float: ...

    @abc.abstractmethod
    async def get_positions(self) -> list[VenuePosition]: ...

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None


def to_float(v: Any, default: float | None = None) -> float | None:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_ts(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e12:
            x /= 1000.0
        from datetime import timezone

        return datetime.fromtimestamp(x, tz=timezone.utc)
    s = str(v).strip()
    if s.isdigit():
        return parse_ts(int(s))
    try:
        from datetime import timezone

        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
