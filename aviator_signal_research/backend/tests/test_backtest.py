import numpy as np
import pandas as pd
import pytest

from app.backtest import PayoutRules, run_backtest, simulate
from app.modeling import evaluation as ev
from tests.conftest import FAST_MODELS, FAST_PROTOCOL, import_synthetic


def test_settlement_and_expected_value_formula():
    r = PayoutRules()
    assert r.settle(10, 2.0, 2.0) == 10  # tie counts as reaching M: net = S(M-1)
    assert r.settle(10, 2.0, 1.99) == -10
    assert PayoutRules(tie_counts_as_win=False).settle(10, 2.0, 2.0) == -10
    assert PayoutRules(max_net_win=5).settle(10, 3.0, 9.0) == 5
    assert PayoutRules(fee_per_bet=0.1).settle(1, 2.0, 1.0) == pytest.approx(-1.1)


def _ledger(probs_model, probs_base, crashes):
    rows = []
    for i, (pm, pb, c) in enumerate(zip(probs_model, probs_base, crashes)):
        for name, p in (("m", pm), ("expanding_frequency", pb)):
            rows.append({"pid": i, "model_name": name, "target_seq": 100 + i, "probability": p, "data_cutoff_seq": 99 + i,
                         "created_at": "2026-01-01 00:00:00", "oid": i, "recorded_at": "2026-01-01 00:00:01",
                         "actual_multiplier": c})
    return pd.DataFrame(rows)


def test_simulation_by_hand():
    led = _ledger([0.6, 0.4, 0.7, 0.55], [0.49, 0.49, 0.49, 0.49], [2.5, 3.0, 1.1, 2.0])
    out = simulate(led, "m", 2.0, 1.0, 0.0, PayoutRules())
    pol = out["policies"]
    # always bet: wins on 2.5, 3.0, 2.0 -> +3, loses 1.1 -> -1
    assert pol["always_bet_fixed_threshold"]["net_pnl"] == 2 and pol["always_bet_fixed_threshold"]["n_bets"] == 4
    # model gate p*2-1>0 -> bets rounds 0, 2, 3 -> +1 -1 +1
    assert pol["model_ev_gate"]["n_bets"] == 3 and pol["model_ev_gate"]["net_pnl"] == 1
    assert pol["model_ev_gate"]["model_expected_net"] == pytest.approx((0.2 + 0.4 + 0.1))
    # baseline gate never bets (0.49*2 < 1)
    assert pol["baseline_ev_gate"]["n_bets"] == 0
    assert pol["always_bet_fixed_threshold"]["max_drawdown"] == 1
    assert pol["always_bet_fixed_threshold"]["losing_streaks"]["longest"] == 1


def test_latency_marks_rounds_not_actionable():
    led = _ledger([0.6, 0.6], [0.6, 0.6], [2.5, 2.5])
    ts = {99: pd.Timestamp("2026-01-01 00:00:00"), 100: pd.Timestamp("2026-01-01 00:00:02"), 101: pd.Timestamp("2026-01-01 00:00:30")}
    out = simulate(led, "m", 2.0, 1.0, 0.0, PayoutRules(), ts, decision_latency_s=5)
    assert out["skipped_not_actionable"] == 1 and out["policies"]["always_bet_fixed_threshold"]["n_bets"] == 1


def test_backtest_on_stored_predictions_with_independent_audit(session):
    src = import_synthetic(session, "independent", n=2400, seed=3)
    run = ev.run_evaluation(session, src["source_id"], FAST_PROTOCOL, FAST_MODELS, include_regression=False)
    session.commit()
    bt = run_backtest(session, run.id, 2.0, stake=2.0)
    assert bt.audit["passed"], bt.audit
    n_test = run.summary["test_period"]["n"]
    assert bt.results["n_rounds"] == n_test  # no unfavourable rounds removed
    always = bt.results["policies"]["always_bet_fixed_threshold"]
    assert always["n_bets"] == n_test and always["total_staked"] == 2.0 * n_test
    lo, hi = always["ev_per_bet_ci"]
    assert lo < always["ev_per_bet"] < hi
    assert len(bt.results["sensitivity_margin"]) == 4
    with pytest.raises(ValueError):
        run_backtest(session, run.id, 7.0)
    with pytest.raises(ValueError):
        run_backtest(session, run.id, 2.0, stake=0)


def test_audit_detects_tampered_settlement(session, monkeypatch):
    import app.backtest as bt_mod

    src = import_synthetic(session, "independent", n=2400, seed=3)
    run = ev.run_evaluation(session, src["source_id"], FAST_PROTOCOL, FAST_MODELS, include_regression=False)
    session.commit()

    class Rigged(PayoutRules):
        def settle(self, stake, target, crash):  # pays out even on losses
            return stake * (target - 1)

    monkeypatch.setattr(bt_mod, "PayoutRules", Rigged)
    bt = run_backtest(session, run.id, 2.0, rules=Rigged())
    assert not bt.audit["passed"]
    assert not bt.audit["checks"]["pnl_recomputed_always_bet_fixed_threshold"]["match"]
    assert np.isfinite(bt.results["policies"]["always_bet_fixed_threshold"]["net_pnl"])
