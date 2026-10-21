import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from zigggy.backtesting.importer import import_polymarket_history
from zigggy.database.models import Market, OrderBookSnapshot
from zigggy.research.llm import LLMReviewer


async def test_importer_never_imports_post_resolution_prices(core):
    closed = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
    market = {"id": "900", "question": "Will X happen?", "conditionId": "0x9", "description": "rules", "endDate": closed.isoformat(),
              "closedTime": closed.isoformat(), "outcomes": "[\"Yes\",\"No\"]", "outcomePrices": "[\"1\",\"0\"]",
              "clobTokenIds": "[\"tok9\",\"tokN\"]", "active": True, "closed": True, "umaResolutionStatus": "resolved"}
    t_before, t_after = int(closed.timestamp()) - 7200, int(closed.timestamp()) + 600

    def handler(req: httpx.Request):
        if req.url.path == "/markets":
            assert req.url.params["closed"] == "true"
            return httpx.Response(200, json=[market])
        if req.url.path == "/prices-history":
            return httpx.Response(200, json={"history": [{"t": t_before, "p": 0.62}, {"t": t_after, "p": 0.999}]})
        return httpx.Response(404)

    stats = await import_polymarket_history(core.db, "https://g.test", "https://c.test", n_markets=5, transport=httpx.MockTransport(handler))
    assert stats == {"markets": 1, "snapshots": 1, "skipped": 0}
    async with core.db.session() as s:
        snaps = (await s.execute(select(OrderBookSnapshot))).scalars().all()
        m = await s.get(Market, "polymarket:900")
    assert len(snaps) == 1 and "price-only" in snaps[0].source and snaps[0].ts < closed
    assert m.result == "YES" and m.resolved_at == closed


class FakeMessages:
    def __init__(self, payload=None, fail=False, stop="end_turn"):
        self.payload, self.fail, self.stop, self.calls = payload, fail, stop, []

    async def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("network down")
        return SimpleNamespace(stop_reason=self.stop, usage=SimpleNamespace(input_tokens=100, output_tokens=20),
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


async def test_llm_review_structured_cached_rate_limited_and_failure_safe():
    r = LLMReviewer(None, "claude-opus-5-5", max_per_hour=1)
    assert not r.available and await r.review("q", "rules", "") is None
    fake = FakeMessages({"ambiguous": True, "flags": ["discretionary_resolution"], "resolution_source": "x", "notes": "n"})
    r.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    out = await r.review("q", "rules", "src")
    assert out["flags"] == ["discretionary_resolution"]
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
    assert await r.review("q", "rules", "src") == out and len(fake.calls) == 1  # cached
    assert await r.review("q2", "rules", "src") is None  # hourly cap reached
    r2 = LLMReviewer(None, "m", 5)
    r2.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(fail=True)))
    assert await r2.review("q", "r", "s") is None and r2.usage["errors"] == 1
    r3 = LLMReviewer(None, "m", 5)
    r3.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages({"ambiguous": False}, stop="refusal")))
    assert await r3.review("q", "r", "s") is None
