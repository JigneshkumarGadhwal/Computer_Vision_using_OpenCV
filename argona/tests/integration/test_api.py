"""HTTP API: authentication, controls actually change backend state, validation, SSE."""
import json

import pytest
from fastapi.testclient import TestClient

from argona.core import Core
from argona.exchanges.registry import ProviderRegistry
from argona.exchanges.sim import SimWorld
from argona.main import InsecureConfig, create_app, validate_security
from tests.conftest import NullSource, RecordingNotifier, make_settings

TOKEN = "test-token-123"


@pytest.fixture
def client(tmp_path):
    s = make_settings(tmp_path, api_token=TOKEN)
    core = Core(s, registry=ProviderRegistry(s, sim_world=SimWorld(seed=3, n_markets=8)), notifier=RecordingNotifier(),
                crypto_source=NullSource(), weather_source=NullSource())
    app = create_app(s, core=core, run_scheduler=False)
    with TestClient(app) as c:
        yield c, core


H = {"Authorization": f"Bearer {TOKEN}"}


def test_health_is_public_rest_requires_token(client):
    c, _ = client
    assert c.get("/api/health").json()["status"] == "ok"
    assert c.get("/api/overview").status_code == 401
    assert c.get("/api/overview", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/overview", headers=H).status_code == 200
    assert c.post("/api/control/emergency-stop", json={}).status_code == 401


def test_refuses_public_bind_without_token(tmp_path):
    with pytest.raises(InsecureConfig):
        validate_security(make_settings(tmp_path, host="0.0.0.0"))
    validate_security(make_settings(tmp_path, host="0.0.0.0", api_token="x"))


def test_controls_change_state(client):
    c, core = client
    assert c.post("/api/control/pause", json={"enabled": True}, headers=H).json() == {"trading_paused": True}
    assert core.state.runtime.trading_paused is True
    assert c.post("/api/control/scanning", json={"enabled": False}, headers=H).status_code == 200
    assert core.state.runtime.scanning_enabled is False
    r = c.post("/api/control/mode", json={"mode": "LIVE", "confirm": "ENABLE LIVE TRADING", "venue": "kalshi"}, headers=H)
    assert r.status_code == 409 and "disabled server-side" in r.json()["detail"]
    assert c.post("/api/control/emergency-stop", json={"reason": "t"}, headers=H).json()["emergency_stop"] is True
    assert c.post("/api/control/mode", json={"mode": "PAPER"}, headers=H).status_code == 409
    c.post("/api/control/emergency-reset", headers=H)
    assert c.get("/api/overview", headers=H).json()["mode"] == "STOPPED"
    assert c.post("/api/control/mode", json={"mode": "PAPER"}, headers=H).json()["mode"] == "PAPER"


def test_settings_validated_and_audited(client):
    c, core = client
    assert c.put("/api/settings", json={"risk": {"kelly_fraction": 3}}, headers=H).status_code == 422
    assert c.put("/api/settings", json={"categories": ["bogus"]}, headers=H).status_code == 422
    r = c.put("/api/settings", json={"risk": {**core.state.runtime.risk.model_dump(), "min_net_edge": 0.05}}, headers=H)
    assert r.status_code == 200 and core.state.runtime.risk.min_net_edge == 0.05
    hist = c.get("/api/settings/history", headers=H).json()
    assert any(h["key"] == "runtime_settings" for h in hist)


def test_cycle_and_read_endpoints(client):
    c, _ = client
    assert c.post("/api/control/run-job/discovery", headers=H).status_code == 200
    r = c.post("/api/control/run-cycle", headers=H).json()
    assert r["snapshots"] > 0
    for ep in ["/api/markets", "/api/opportunities", "/api/spread", "/api/agents", "/api/portfolio", "/api/orders", "/api/events",
               "/api/health/details", "/api/settings", "/api/models/metrics", "/api/backtests", "/api/providers", "/api/portfolio/ledger"]:
        assert c.get(ep, headers=H).status_code == 200, ep
    mk = c.get("/api/markets", headers=H).json()["items"][0]
    assert mk["is_simulated"] is True
    d = c.get(f"/api/markets/{mk['key']}/detail", headers=H).json()
    assert d["market"]["key"] == mk["key"] and d["book"]


def test_sse_requires_token_and_accepts_query_token(client):
    c, _ = client
    assert c.get("/api/stream").status_code == 401
    with c.stream("GET", f"/api/stream?token={TOKEN}") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        first = next(r.iter_lines())
        assert first.startswith("retry")
