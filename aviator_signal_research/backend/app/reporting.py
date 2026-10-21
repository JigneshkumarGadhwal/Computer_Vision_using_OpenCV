"""Markdown evaluation report generated from a stored evaluation run.

Every number in the report is read from the database record of the run;
nothing is typed in by hand.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from .config import get_settings
from .models import BacktestRun, EvaluationRun


def _f(x, nd=4, signed=False):
    if x is None:
        return "n/a"
    if isinstance(x, (int,)) and not isinstance(x, bool):
        return str(x)
    try:
        return f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def render_report(run: EvaluationRun, backtests: list[BacktestRun] | None = None) -> str:
    s = run.summary or {}
    src = s.get("source", {})
    lines = [
        f"# Evaluation report — run {run.id}",
        "",
    ]
    if run.is_synthetic:
        lines += ["> **SYNTHETIC DATA.** This run used generated demo data, not real Aviator results.", ""]
    lines += [
        f"- **Data source:** {src.get('name')} (kind: {src.get('kind')})",
        f"- **Rounds:** {s.get('n_rounds')} (warm-up {s.get('warmup_rounds')})",
        f"- **Development period:** seq {s.get('dev_period', {}).get('start_seq')}–{s.get('dev_period', {}).get('end_seq')} "
        f"({s.get('dev_period', {}).get('n')} rounds)",
        f"- **Final test period:** seq {s.get('test_period', {}).get('start_seq')}–{s.get('test_period', {}).get('end_seq')} "
        f"({s.get('test_period', {}).get('n')} rounds; {s.get('test_period', {}).get('start_time')} → "
        f"{s.get('test_period', {}).get('end_time')})",
        f"- **Data fingerprint:** `{run.data_fingerprint}`",
        f"- **Code version:** `{run.code_version}`",
        f"- **Started / finished:** {run.started_at} / {run.finished_at}",
        f"- **Status:** {run.status}",
        "",
        "## Conclusion",
        "",
        f"**{s.get('overall_status', 'n/a')}** — {run.conclusion or ''}",
        "",
    ]
    if run.error:
        lines += ["## Error", "", "```", run.error, "```", ""]
    if s.get("reason"):
        lines += [f"Reason: {s['reason']}", ""]
    p = run.protocol or {}
    lines += [
        "## Pre-registered protocol",
        "",
        f"- Thresholds: {p.get('thresholds')} (primary {p.get('primary_threshold')})",
        f"- Development fraction: {p.get('dev_fraction')}; initial train {p.get('initial_train_size')}; "
        f"block (retrain schedule) {p.get('block_size')}; purge gap {p.get('purge_gap')}",
        f"- Signal criteria: Brier skill > {p.get('min_brier_skill')}, Holm-adjusted one-sided DM p < {p.get('alpha')}, "
        f"Hosmer–Lemeshow p ≥ {p.get('calibration_alpha')} and |mean-prediction gap| ≤ {p.get('max_calibration_gap')}, "
        f"≥ {p.get('min_test_rounds')} test rounds",
        "- Baseline: expanding-window Laplace-smoothed historical frequency",
        f"- Random seed: {p.get('random_seed')}",
        "",
    ]
    if "leakage_audit" in s:
        a = s["leakage_audit"]
        lines += [f"**Look-ahead audit:** {'PASSED' if a['passed'] else 'FAILED'} "
                  f"({a['checked_positions']} positions checked)", ""]
    if "data_quality" in s:
        dq = s["data_quality"]
        lines += [f"**Data quality gate:** {'passed' if dq['gate_passed'] else 'FAILED'} "
                  f"(warning share {dq['warning_share']:.3%}, verified share {dq['verified_share']:.1%})", ""]

    for key, t in (s.get("thresholds") or {}).items():
        test = t["test"]
        cmp = test["comparison_vs_baseline"]
        lines += [
            f"## Target: {t['target']} (break-even probability {t['breakeven_probability']:.4f})",
            "",
            f"**Status: {t['status']}** — " + "; ".join(t["status_reasons"]),
            "",
            f"Selected model ({t['selection_rule']}): **{t['selected_model']}**",
            "",
            "### Development walk-forward (validation)",
            "",
            "| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for name, row in t["development"].items():
            v = row["validation"]
            tr = row.get("train_last_fold") or {}
            vb = row.get("vs_baseline") or {}
            lines.append(
                f"| {name} | {row.get('family')} | {_f(tr.get('brier'), 5)} | {_f(v['brier'], 5)} | "
                f"{_f(v['log_loss'], 5)} | {_f(v['roc_auc'], 3)} | {_f(v['ece'], 4)} | "
                f"{_f(vb.get('brier_skill'), 5, True)} | {_f(vb.get('p_holm_dev_family'), 4)} |"
            )
        lines += [
            "",
            f"### Final test ({test['n']} unseen rounds; dev base rate {test['dev_base_rate']:.4f}, "
            f"test base rate {test['test_base_rate']:.4f})",
            "",
            "| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for name, m in test["metrics"].items():
            cb = m.get("confusion_at_breakeven") or {}
            lines.append(
                f"| {name} | {_f(m['brier'], 5)} | {_f(m['log_loss'], 5)} | {_f(m['roc_auc'], 3)} | "
                f"{_f(m['ece'], 4)} | {_f((m.get('hosmer_lemeshow') or {}).get('p_value'), 3)} | "
                f"{_f(m.get('calibration_in_the_large'), 4, True)} | "
                f"{_f(cb.get('precision'), 4)} | {_f(cb.get('recall'), 4)} | {cb.get('n_positive_calls')} |"
            )
        lines += [
            "",
            f"Brier skill vs baseline: **{_f(cmp['brier_skill'], 5, True)}** "
            f"(95% block-bootstrap CI {_f(cmp['brier_skill_ci_low'], 5, True)} to {_f(cmp['brier_skill_ci_high'], 5, True)}); "
            f"DM p = {_f(cmp['p_value'])}, Holm-adjusted across thresholds = {_f(cmp.get('p_holm_across_thresholds'))}.",
            "",
            "Performance by test sub-period:",
            "",
            "| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |",
            "|---|---|---|---|---|---|",
        ]
        for r in test["per_period"]:
            lines.append(
                f"| {r['start_seq']}–{r['end_seq']} | {r['n']} | {r['base_rate']:.4f} | {r['shift_vs_dev_rate']:+.4f} | "
                f"{r['brier_model']:.5f} | {r['brier_baseline']:.5f} |"
            )
        lines.append("")

    reg = s.get("exact_multiplier")
    if reg:
        lines += ["## Secondary: exact multiplier (log scale)", "", reg.get("note", ""), ""]
        for phase, ph in reg["phases"].items():
            lines += [f"**{phase}** ({ph['n']} rounds)", "", "| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |",
                      "|---|---|---|---|---|"]
            for name, m in ph["point"].items():
                lines.append(f"| {name} | {_f(m['mae_log'])} | {_f(m['rmse_log'])} | "
                             f"{_f(m['median_abs_error_multiplier'])} | "
                             f"{_f(m['dm_vs_train_median_abs_error'].get('p_value_one_sided'))} |")
            lines.append("")
    for bt in backtests or []:
        lines += [f"## Backtest {bt.id}", "", f"Config: `{bt.config}`", "",
                  "| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |",
                  "|---|---|---|---|---|---|---|"]
        for name, r in bt.results.get("policies", {}).items():
            ci = r.get("ev_per_bet_ci") or [None, None]
            lines.append(f"| {name} | {r['n_bets']} | {_f(r.get('hit_rate'))} | {_f(r['net_pnl'], 2)} | "
                         f"{_f(r.get('roi'), 4, True)} | {_f(ci[0], 4, True)} to {_f(ci[1], 4, True)} | "
                         f"{_f(r['max_drawdown'], 2)} |")
        lines += ["", f"Independent audit: **{'PASSED' if bt.audit.get('passed') else 'FAILED'}**", ""]
    lines += [
        "## Limitations",
        "",
        "- Results apply only to this data source and period; other operators may use different software or rules.",
        "- The reference distribution P(X≥m)=RTP/m and its RTP are unverified assumptions.",
        "- A positive Brier skill is a statistical statement about probability estimates, not proof of a profitable strategy.",
        "- Hypothetical backtests ignore limits, latency and rule details unless explicitly configured.",
    ]
    return "\n".join(lines) + "\n"


def write_report(session: Session, run: EvaluationRun) -> Path:
    backtests = session.query(BacktestRun).filter(BacktestRun.run_id == run.id).all()
    out = get_settings().report_dir / f"evaluation_run_{run.id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(run, backtests))
    return out
