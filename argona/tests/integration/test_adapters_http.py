"""Exchange adapters against mocked HTTP (httpx.MockTransport) shaped like the documented APIs."""
import base64
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from argona.domain import Action, OrderStatus, Outcome
from argona.exchanges.base import HttpClient, RateLimited, SubmissionUncertain
from argona.exchanges.kalshi import KalshiMarketData, KalshiSigner, KalshiTrading
from argona.exchanges.polymarket import PolymarketMarketData

FIX = Path(__file__).resolve().parents[1] / "fixtures"


async def test_kalshi_discovery_and_batch_fallback():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        if req.url.path.endswith("/events"):
            return httpx.Response(200, json=json.loads((FIX / "kalshi_events.json").read_text()))
        if req.url.path.endswith("/markets/orderbooks"):
            return httpx.Response(403, json={"error": "auth required"})
        if req.url.path.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [["0.24", "10.00"]], "no_dollars": [["0.73", "12.00"]]}})
        return httpx.Response(404)

    a = KalshiMarketData("https://k.test/trade-api/v2", transport=httpx.MockTransport(handler), rps=1000)
    ms = await a.discover()
    assert len(ms) == 2  # combo excluded
    books = await a.fetch_books(ms)
    assert set(books) == {m.key for m in ms} and books[ms[0].key].best_ask() == pytest.approx(0.27)
    assert not a._batch_ok  # fell back to per-market after 403
    await a.close()


async def test_polymarket_discovery_pagination_and_books():
    rows = json.loads((FIX / "polymarket_markets.json").read_text())

    def handler(req: httpx.Request):
        if req.url.path == "/markets":
            off = int(req.url.params.get("offset", 0))
            return httpx.Response(200, json=rows if off == 0 else [])
        if req.url.path == "/books":
            body = json.loads(req.content)
            return httpx.Response(200, json=[{"asset_id": b["token_id"], "bids": [{"price": "0.33", "size": "100"}],
                                              "asks": [{"price": "0.35", "size": "80"}], "timestamp": "1761000000000"} for b in body])
        return httpx.Response(404)

    a = PolymarketMarketData("https://g.test", "https://c.test", transport=httpx.MockTransport(handler), rps=1000)
    ms = await a.discover()
    assert {m.venue_market_id for m in ms} == {"501", "502", "503"}
    books = await a.fetch_books([m for m in ms if m.status.value == "open"])
    assert books["polymarket:501"].best_bid() == 0.33
    await a.close()


async def test_get_retries_on_429_then_succeeds():
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}) if n["c"] < 3 else httpx.Response(200, json={"ok": True})

    c = HttpClient("https://x.test", transport=httpx.MockTransport(handler), rps=1000)
    assert await c.request("GET", "/a") == {"ok": True}
    assert c.stats.rate_limited == 2
    await c.close()


async def test_post_timeout_is_uncertain_not_retried():
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        raise httpx.ReadTimeout("slow", request=req)

    c = HttpClient("https://x.test", transport=httpx.MockTransport(handler), rps=1000)
    with pytest.raises(SubmissionUncertain):
        await c.request("POST", "/order", json={})
    assert n["c"] == 1  # never blindly resubmitted
    n["c"] = 0
    from argona.exchanges.base import ProviderError

    with pytest.raises(ProviderError):
        await c.request("GET", "/x")
    assert n["c"] == 4  # idempotent GETs are retried with backoff
    await c.close()


async def test_post_429_raises_rate_limited_without_retry():
    c = HttpClient("https://x.test", transport=httpx.MockTransport(lambda r: httpx.Response(429)), rps=1000)
    with pytest.raises(RateLimited):
        await c.request("POST", "/order", json={})
    await c.close()


def _key():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return k, pem


async def test_kalshi_signing_and_order_body():
    k, pem = _key()
    signer = KalshiSigner("key-id", pem)
    captured = {}

    def handler(req: httpx.Request):
        ts = req.headers["KALSHI-ACCESS-TIMESTAMP"]
        sig = base64.b64decode(req.headers["KALSHI-ACCESS-SIGNATURE"])
        # signature covers timestamp + METHOD + full path (incl. /trade-api/v2), no query string
        k.public_key().verify(sig, (ts + req.method + req.url.path).encode(),
                              padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        if req.url.path.endswith("/portfolio/balance"):
            return httpx.Response(200, json={"balance": 12345, "balance_dollars": "123.45"})
        if req.url.path.endswith("/portfolio/events/orders"):
            captured.update(json.loads(req.content))
            return httpx.Response(201, json={"order_id": "o1", "client_order_id": captured["client_order_id"], "fill_count": "0.00",
                                             "remaining_count": "10.00", "ts_ms": 1})
        return httpx.Response(404)

    t = KalshiTrading("https://k.test/trade-api/v2", signer, transport=httpx.MockTransport(handler))
    assert (await t.verify())["balance"] == pytest.approx(123.45)
    from argona.domain import MarketStatus, NormalizedMarket, Venue

    m = NormalizedMarket(Venue.KALSHI, "KXTEST-1", "Q", "other", MarketStatus.OPEN, None)
    ack = await t.place_limit_order(m, Outcome.NO, Action.BUY, 0.30, 10, "ARG-abc", None)
    assert ack.status is OrderStatus.OPEN and ack.venue_order_id == "o1"
    # buying NO at 0.30 == selling YES at 0.70 on the YES book
    assert captured["side"] == "ask" and captured["price"] == "0.7000" and captured["count"] == "10.00"
    assert captured["client_order_id"] == "ARG-abc"
    await t.close()


async def test_proxy_and_connect_errors_become_provider_errors():
    from argona.exchanges.base import ProviderError

    def handler(req):
        raise httpx.ProxyError("403 Forbidden", request=req)

    c = HttpClient("https://x.test", transport=httpx.MockTransport(handler), rps=1000, max_retries=1)
    with pytest.raises(ProviderError):
        await c.request("GET", "/markets")
    assert c.stats.errors == 2 and "ProxyError" in c.stats.last_error
    await c.close()


async def test_one_failing_venue_does_not_block_discovery(settings):
    """Polymarket unreachable (proxy 403) must not stop Kalshi/SIM discovery, and must emit a disconnect event."""
    from argona.exchanges.registry import ProviderRegistry
    from tests.conftest import build_core

    def deny(req):
        raise httpx.ProxyError("403 Forbidden", request=req)

    settings.providers = "polymarket,sim"
    reg = ProviderRegistry(settings, transports={"polymarket": httpx.MockTransport(deny)})
    from argona.core import Core
    from tests.conftest import NullSource, RecordingNotifier

    core = Core(settings, registry=reg, notifier=RecordingNotifier(), crypto_source=NullSource(), weather_source=NullSource())
    await core.start(run_scheduler=False)
    try:
        summary = await core.rigo.discover()
        assert "error" in summary["polymarket"] and summary["sim"]["listed"] > 0
        assert any(e["type"] == "provider_disconnected" for e in core.notifier.events)
        assert core.registry.status["polymarket"].connected is False and core.registry.status["sim"].connected is True
    finally:
        await core.stop()
