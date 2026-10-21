"""Event-driven hypothetical backtest over stored, out-of-sample predictions.

The engine walks the final-test rounds in chronological order. For each round
it (1) reads the predictions that were committed to the immutable ledger
before the outcome was recorded, (2) makes every policy's bet decision, and
only then (3) reads the outcome and settles. Nothing is ever placed on a real
platform. Unfavourable rounds are never removed.

Simplified settlement (configurable via PayoutRules):
    win  (crash >= M): net = S * (M - 1)       (gross payout S*M includes stake)
    loss (crash <  M): net = -S
    Expected net return = S * (p * M - 1)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .models import BacktestRun, EvaluationRun, Round, log_event
from .modeling.estimators import PRIMARY_BASELINE

DEFAULT_MARGINS = (0.0, 0.02, 0.05, 0.10)


@dataclass(frozen=True)
class PayoutRules:
    tie_counts_as_win: bool = True  # crash == M counts as reaching M
    max_net_win: float | None = None  # per-bet cap on net winnings, if the platform has one
    fee_per_bet: float = 0.0

    def settle(self, stake: float, target: float, crash: float) -> float:
        win = crash >= target if self.tie_counts_as_win else crash > target
        if win:
            net = stake * (target - 1)
            if self.max_net_win is not None:
                net = min(net, self.max_net_win)
        else:
            net = -stake
        return net - self.fee_per_bet


def _load_ledger(session: Session, run_id: int, threshold: float) -> pd.DataFrame:
    rows = session.execute(
        text(
            """SELECT p.id AS pid, p.model_name, p.target_seq, p.probability, p.data_cutoff_seq,
                      p.created_at, o.id AS oid, o.recorded_at, o.actual_multiplier
               FROM predictions p JOIN prediction_outcomes o ON o.prediction_id = p.id
               WHERE p.run_id = :run AND p.phase = 'test' AND p.mode = 'walk_forward' AND p.threshold = :th
               ORDER BY p.target_seq"""
        ),
        {"run": run_id, "th": threshold},
    ).mappings().all()
    return pd.DataFrame(rows)


def _drawdown(cum: np.ndarray) -> float:
    if not len(cum):
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))
    return float((peak[1:] - cum).max())


def _losing_streaks(wins: list[bool]) -> dict:
    streaks = []
    run = 0
    for w in wins:
        if not w:
            run += 1
        elif run:
            streaks.append(run)
            run = 0
    if run:
        streaks.append(run)
    dist: dict[str, int] = {}
    for s in streaks:
        k = str(s) if s < 10 else "10+"
        dist[k] = dist.get(k, 0) + 1
    return {"longest": max(streaks) if streaks else 0, "distribution": dist}


def _block_ci(x: np.ndarray, block: int = 50, n_boot: int = 1000, seed: int = 0):
    if len(x) < 10:
        return [None, None]
    rng = np.random.default_rng(seed)
    n = len(x)
    block = min(block, max(1, n // 4))
    nb = math.ceil(n / block)
    means = np.empty(n_boot)
    for i in range(n_boot):
        st = rng.integers(0, n - block + 1, size=nb)
        idx = (st[:, None] + np.arange(block)[None]).ravel()[:n]
        means[i] = x[idx].mean()
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def simulate(ledger: pd.DataFrame, model_name: str, threshold: float, stake: float, margin: float,
             rules: PayoutRules, timestamps: dict[int, pd.Timestamp] | None = None,
             decision_latency_s: float | None = None) -> dict:
    """Event loop. Returns per-policy results and the bet log."""
    piv = {name: g.set_index("target_seq") for name, g in ledger.groupby("model_name")}
    if model_name not in piv or PRIMARY_BASELINE not in piv:
        raise ValueError("ledger lacks model or baseline predictions")
    seqs = sorted(set(piv[model_name].index) & set(piv[PRIMARY_BASELINE].index))
    policies = {
        "always_bet_fixed_threshold": lambda p_m, p_b: True,
        "model_ev_gate": lambda p_m, p_b: p_m * threshold - 1 > margin,
        "baseline_ev_gate": lambda p_m, p_b: p_b * threshold - 1 > margin,
    }
    log = {k: [] for k in policies}
    skipped_not_actionable = 0
    for seq in seqs:
        # 1) decisions from committed predictions only
        p_m = float(piv[model_name].at[seq, "probability"])
        p_b = float(piv[PRIMARY_BASELINE].at[seq, "probability"])
        actionable = True
        if decision_latency_s is not None and timestamps:
            t, tp = timestamps.get(seq), timestamps.get(seq - 1)
            if t is not None and tp is not None and (t - tp).total_seconds() < decision_latency_s:
                actionable = False
        decisions = {k: (f(p_m, p_b) and actionable) for k, f in policies.items()}
        if not actionable:
            skipped_not_actionable += 1
        # 2) reveal outcome, 3) settle
        crash = float(piv[model_name].at[seq, "actual_multiplier"])
        for k, bet in decisions.items():
            if bet:
                p_used = p_b if k != "model_ev_gate" else p_m
                log[k].append({"seq": int(seq), "stake": stake, "p": p_used, "crash": crash,
                               "net": rules.settle(stake, threshold, crash)})
    results = {}
    for k, bets in log.items():
        net = np.array([b["net"] for b in bets])
        wins = [b["net"] > 0 for b in bets]
        cum = np.cumsum(net) if len(net) else np.array([])
        staked = stake * len(bets)
        exp_ev = float(sum(stake * (b["p"] * threshold - 1) for b in bets))
        idx = np.linspace(0, len(cum) - 1, min(len(cum), 400)).astype(int) if len(cum) else []
        results[k] = {
            "n_bets": len(bets),
            "wins": int(sum(wins)),
            "hit_rate": float(np.mean(wins)) if bets else None,
            "total_staked": staked,
            "net_pnl": float(net.sum()) if len(net) else 0.0,
            "roi": float(net.sum() / staked) if staked else None,
            "ev_per_bet": float(net.mean()) if len(net) else None,
            "ev_per_bet_ci": _block_ci(net) if len(net) else [None, None],
            "model_expected_net": exp_ev,
            "max_drawdown": _drawdown(cum),
            "losing_streaks": _losing_streaks(wins),
            "cumulative": [{"bet": int(i + 1), "seq": bets[i]["seq"], "pnl": float(cum[i])} for i in idx],
        }
    return {"policies": results, "bets": log, "n_rounds": len(seqs), "skipped_not_actionable": skipped_not_actionable}


def audit(session: Session, run: EvaluationRun, ledger: pd.DataFrame, sim: dict, threshold: float,
          rules: PayoutRules) -> dict:
    """Independent re-computation from the raw ``rounds`` table plus ledger integrity checks."""
    checks = {}
    rounds = dict(session.execute(
        select(Round.sequence, Round.multiplier).where(Round.source_id == run.source_id)).all())
    # (a) P&L recomputed with an independent formula from the rounds table
    mism = 0
    for policy, bets in sim["bets"].items():
        engine_total = sum(b["net"] for b in bets)
        indep = 0.0
        for b in bets:
            m = rounds[b["seq"]]
            reached = (m >= threshold) if rules.tie_counts_as_win else (m > threshold)
            gross = b["stake"] * threshold if reached else 0.0
            net = gross - b["stake"]
            if reached and rules.max_net_win is not None:
                net = min(net, rules.max_net_win)
            indep += net - rules.fee_per_bet
        if abs(indep - engine_total) > 1e-6:
            mism += 1
        checks[f"pnl_recomputed_{policy}"] = {"engine": engine_total, "independent": indep,
                                              "match": abs(indep - engine_total) <= 1e-6}
    # (b) every prediction committed before its outcome was recorded
    order_ok = bool((ledger["pid"] > 0).all() and (pd.to_datetime(ledger["created_at"], utc=True, format="mixed")
                                               <= pd.to_datetime(ledger["recorded_at"], utc=True, format="mixed")).all())
    checks["predictions_before_outcomes"] = order_ok
    # (c) feature cutoff strictly before target
    checks["cutoff_before_target"] = bool((ledger["data_cutoff_seq"] < ledger["target_seq"]).all())
    # (d) no duplicate bets / round reuse
    dup = any(len({b["seq"] for b in bets}) != len(bets) for bets in sim["bets"].values())
    checks["no_duplicate_bets"] = not dup
    # (e) every final-test round present (nothing removed)
    s = run.summary["test_period"]
    expected = s["end_seq"] - s["start_seq"] + 1
    checks["all_test_rounds_included"] = {"expected": expected, "simulated": sim["n_rounds"],
                                          "match": expected == sim["n_rounds"]}
    # (f) recorded outcomes equal the stored round multipliers
    mm = sum(abs(rounds[int(r.target_seq)] - float(r.actual_multiplier)) > 1e-9 for r in ledger.itertuples())
    checks["outcomes_match_rounds"] = mm == 0
    passed = (mism == 0 and order_ok and checks["cutoff_before_target"] and not dup
              and checks["all_test_rounds_included"]["match"] and mm == 0)
    return {"passed": bool(passed), "checks": checks}


def run_backtest(session: Session, run_id: int, threshold: float, stake: float = 1.0,
                 margins: tuple[float, ...] = DEFAULT_MARGINS, rules: PayoutRules | None = None,
                 decision_latency_s: float | None = None) -> BacktestRun:
    rules = rules or PayoutRules()
    run = session.get(EvaluationRun, run_id)
    if run is None or run.status != "completed":
        raise ValueError("evaluation run not found or not completed")
    tinfo = (run.summary or {}).get("thresholds", {}).get(f"{threshold:g}")
    if not tinfo:
        raise ValueError(f"threshold {threshold:g} not evaluated in run {run_id}")
    if stake <= 0:
        raise ValueError("stake must be positive")
    model_name = tinfo["selected_model"]
    ledger = _load_ledger(session, run_id, threshold)
    ts = dict(session.execute(select(Round.sequence, Round.round_timestamp).where(Round.source_id == run.source_id)).all())
    ts = {k: pd.Timestamp(v) for k, v in ts.items() if v is not None}
    main = simulate(ledger, model_name, threshold, stake, margins[0], rules, ts, decision_latency_s)
    sensitivity = []
    for m in margins:
        r = simulate(ledger, model_name, threshold, stake, m, rules, ts, decision_latency_s)["policies"]
        sensitivity.append({"margin": m, **{k: {"n_bets": v["n_bets"], "roi": v["roi"], "net_pnl": v["net_pnl"]}
                                             for k, v in r.items() if k != "always_bet_fixed_threshold"}})
    other_thresholds = []
    for key, t in run.summary["thresholds"].items():
        led = _load_ledger(session, run_id, t["threshold"])
        r = simulate(led, t["selected_model"], t["threshold"], stake, margins[0], rules, ts, decision_latency_s)["policies"]
        other_thresholds.append({"threshold": t["threshold"],
                                 "reference_ev_per_unit_always_bet": get_settings().reference_rtp - 1,
                                 **{k: {"n_bets": v["n_bets"], "roi": v["roi"]} for k, v in r.items()}})
    aud = audit(session, run, ledger, main, threshold, rules)
    results = {
        "model_name": model_name,
        "threshold": threshold,
        "n_rounds": main["n_rounds"],
        "skipped_not_actionable": main["skipped_not_actionable"],
        "policies": main["policies"],
        "sensitivity_margin": sensitivity,
        "sensitivity_threshold": other_thresholds,
        "is_synthetic": run.is_synthetic,
        "formula": "Expected net return = S × (p × M − 1); win pays S×(M−1) net, loss costs S.",
        "notes": [
            "Hypothetical simulation on out-of-sample predictions. No real bets are placed.",
            "A positive estimated return is not proof of a profitable strategy; see the CI on EV per bet.",
            "Martingale / loss-chasing stake increases are deliberately not supported.",
        ],
        "bets": {k: [[b["seq"], b["p"], b["crash"], b["net"]] for b in v] for k, v in main["bets"].items()},
    }
    bt = BacktestRun(
        run_id=run_id,
        config={"threshold": threshold, "stake": stake, "margins": list(margins), "rules": asdict(rules),
                "decision_latency_s": decision_latency_s},
        results=results,
        audit=aud,
    )
    session.add(bt)
    session.flush()
    try:
        from .reporting import write_report

        write_report(session, run)
    except Exception as exc:
        log_event(session, "warning", "report", f"report regeneration failed: {exc}", run_id=run_id)
    return bt
