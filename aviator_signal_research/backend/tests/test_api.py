import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FAST_MODELS


@pytest.fixture()
def client(env):
    from app.api.main import app

    with TestClient(app) as c:
        yield c


def test_health_config_and_empty_states(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["protocol"]["primary_threshold"] == 2.0 and "expanding_frequency" in cfg["available_models"]
    assert client.get("/api/sources").json() == []
    assert client.get("/api/sources/99").status_code == 404
    assert client.get("/api/evaluations/99").status_code == 404
    assert client.get("/api/signals/99").status_code == 404
    assert client.get("/api/backtests/99").status_code == 404
    assert client.get("/api/docs/nope").status_code == 404
    assert client.get("/api/system/integrity").json()["passed"] is True


def test_import_contract(client):
    bad = client.post("/api/import/preview", files={"file": ("x.csv", b"foo\n1\n", "text/csv")})
    assert bad.status_code == 422 and "multiplier" in bad.json()["detail"]
    csv = b"round_id,multiplier,timestamp\n1,2.0,2026-01-01T00:00:00Z\n2,x,2026-01-01T00:00:10Z\n"
    pv = client.post("/api/import/preview", files={"file": ("a.csv", csv, "text/csv")}).json()
    assert pv["n_accepted"] == 1 and pv["n_rejected"] == 1 and len(pv["preview"]) == 2
    assert client.get("/api/sources").json() == []  # preview stores nothing
    cm = client.post("/api/import/commit", files={"file": ("a.csv", csv, "text/csv")},
                     data={"source_name": "real-export", "source_kind": "historical_export"}).json()
    assert cm["n_accepted"] == 1 and cm["is_synthetic"] is False
    bad_kind = client.post("/api/import/commit", files={"file": ("a.csv", csv, "text/csv")},
                           data={"source_name": "z", "source_kind": "scraped"})
    assert bad_kind.status_code == 422
    q = client.get(f"/api/sources/{cm['source_id']}/quality").json()
    assert q["jobs"][0]["n_rejected"] == 1


def test_validation_errors(client):
    r = client.post("/api/demo/load", json={"generator": "magic"})
    assert r.status_code == 422
    sid = client.post("/api/demo/load", json={"generator": "independent", "n_rounds": 300}).json()["source_id"]
    assert client.post("/api/evaluations", json={"source_id": sid, "models": ["nope"]}).status_code == 422
    assert client.post("/api/evaluations", json={"source_id": sid, "thresholds": [0.5]}).status_code == 422
    assert client.post("/api/live/x/rounds", json={"multiplier": "abc"}).status_code == 422
    assert client.post("/api/backtests", json={"run_id": 123}).status_code == 422


def test_full_workflow_via_api(client):
    """Import -> analysis -> evaluation -> signal -> live round -> backtest -> exports."""
    demo = client.post("/api/demo/load", json={"generator": "independent", "n_rounds": 2400, "seed": 4}).json()
    sid = demo["source_id"]
    src = client.get(f"/api/sources/{sid}").json()
    assert src["is_synthetic"] and src["n_rounds"] == 2400 and "SYNTHETIC" in src["name"]
    an = client.get(f"/api/sources/{sid}/analysis").json()
    assert an["n"] == 2400 and an["is_synthetic"] and len(an["dependence_tests"]) >= 6
    assert client.get(f"/api/signals/{sid}").json()["status"] == "INSUFFICIENT EVIDENCE"

    r = client.post("/api/evaluations", json={"source_id": sid, "thresholds": [2.0], "initial_train_size": 800,
                                              "block_size": 300, "min_test_rounds": 300, "models": FAST_MODELS,
                                              "include_regression": False})
    assert r.status_code == 202
    rid = r.json()["run_id"]
    for _ in range(120):
        run = client.get(f"/api/evaluations/{rid}").json()
        if run["status"] != "running":
            break
        time.sleep(0.5)
    assert run["status"] == "completed", run.get("error")
    assert run["summary"]["thresholds"]["2"]["status"] in ("NO DEMONSTRATED EDGE", "RESEARCH SIGNAL")
    assert "Evaluation report" in client.get(f"/api/evaluations/{rid}/report").text
    assert {v["phase"] for v in client.get(f"/api/evaluations/{rid}/results").json()} == {"train", "validation", "test"}

    sig = client.get(f"/api/signals/{sid}?threshold=2").json()
    assert sig["prediction_id"] and sig["is_synthetic"] and any("SYNTHETIC" in w for w in sig["warnings"])
    ov = client.get(f"/api/overview/{sid}").json()
    assert ov["model_status"] == run["summary"]["thresholds"]["2"]["status"]
    assert ov["demonstrated_improvement"] == (ov["model_status"] == "RESEARCH SIGNAL")

    bt = client.post("/api/backtests", json={"run_id": rid, "threshold": 2.0, "stake": 1}).json()
    assert bt["audit"]["passed"] and "bets" not in bt["results"]
    csv = client.get(f"/api/backtests/{bt['id']}/bets.csv").text
    assert csv.startswith("policy,target_seq")
    preds = client.get(f"/api/predictions/export.csv?run_id={rid}&phase=test").text
    assert preds.count("\n") > 300
    assert len(client.get(f"/api/models?run_id={rid}&selected_only=true").json()) == 1
    assert client.get("/api/system/integrity").json()["passed"]
    assert client.get("/api/system/events").json()

    # manual live mode on a separate, non-synthetic source
    lr = client.post("/api/live/manual-obs/rounds", json={"multiplier": "1.87x"}).json()
    assert lr["sequence"] == 0
    st = client.get(f"/api/live/status/{lr['source_id']}").json()
    assert st["data_mode"] == "manual"
