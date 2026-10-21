import { useState } from "react";
import { apiPost, apiUrl, useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Notice, Table, fmt, pct } from "../components";
import { CumulativePnlChart } from "../charts";
import { RunPicker, useRuns } from "./shared";

export default function BacktestPage({ ctx }: { ctx: Ctx }) {
  const { runs, runId, setRunId } = useRuns(ctx.source?.id);
  const bts = useApi<Json[]>(runId ? `/api/backtests?run_id=${runId}` : null);
  const [th, setTh] = useState("2");
  const [stake, setStake] = useState("1");
  const [margins, setMargins] = useState("0,0.02,0.05,0.1");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!ctx.source) return <Notice>Select a data source first.</Notice>;
  if (!runs.loading && runId === null) return <Notice>Backtests need a completed evaluation run (they replay its stored out-of-sample predictions).</Notice>;
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await apiPost("/api/backtests", { run_id: runId, threshold: Number(th), stake: Number(stake), margins: margins.split(",").map(Number) });
      bts.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid">
      <Card title="Hypothetical backtest (no real bets)" actions={<RunPicker runs={runs.data ?? []} runId={runId} setRunId={setRunId} />}>
        <Notice>Replays the final-test predictions in chronological order: decision from the stored prediction first, outcome revealed afterwards. Expected net return = S × (p × M − 1). Stakes are fixed; martingale or loss-chasing is deliberately not supported. A positive estimate is not proof of a profitable strategy.</Notice>
        <div className="form-row">
          <label>Cash-out target M<input aria-label="Cash-out target" value={th} onChange={(e) => setTh(e.target.value)} /></label>
          <label>Stake S<input aria-label="Stake" value={stake} onChange={(e) => setStake(e.target.value)} /></label>
          <label>EV margins (sensitivity)<input aria-label="Margins" value={margins} onChange={(e) => setMargins(e.target.value)} /></label>
        </div>
        <div className="btn-row"><button className="primary" disabled={busy} onClick={start}>Run backtest</button></div>
        {error && <div className="state state-error" role="alert">{error}</div>}
      </Card>
      <Async state={bts} isEmpty={(b) => !b.length} empty="No backtests for this run yet.">
        {(list) => (
          <>
            {list.slice(0, 3).map((b: Json) => (
              <Card key={b.id} title={`Backtest ${b.id}: ${b.results.model_name} @ ${b.config.threshold}x, stake ${b.config.stake}${b.results.is_synthetic ? " [SYNTHETIC]" : ""}`}
                actions={<a href={apiUrl(`/api/backtests/${b.id}/bets.csv`)}>Export bets CSV</a>}>
                <p>Independent audit: <b className={b.audit.passed ? "ok" : "bad"}>{b.audit.passed ? "PASSED" : "FAILED"}</b> · {b.results.n_rounds} rounds replayed · {b.results.formula}</p>
                <Table head={["Policy", "Bets", "Hit rate", "Staked", "Net P&L", "ROI", "EV/bet (95% CI)", "Model-expected net", "Max drawdown", "Longest losing streak"]}
                  rows={Object.entries(b.results.policies).map(([n, r]: [string, Json]) => [n, r.n_bets, pct(r.hit_rate, 2), fmt(r.total_staked, 0), fmt(r.net_pnl, 2), pct(r.roi, 2),
                    `${fmt(r.ev_per_bet, 4, true)} (${fmt(r.ev_per_bet_ci[0], 3, true)} … ${fmt(r.ev_per_bet_ci[1], 3, true)})`, fmt(r.model_expected_net, 2), fmt(r.max_drawdown, 2), r.losing_streaks.longest])} />
                <CumulativePnlChart policies={b.results.policies} />
                <h3>Losing-streak distribution (model EV gate)</h3>
                <p>{Object.entries(b.results.policies.model_ev_gate.losing_streaks.distribution).map(([k, v]) => `${k}: ${v}`).join(" · ") || "no losses"}</p>
                <h3>Sensitivity to EV margin</h3>
                <Table head={["Margin", "Model gate bets", "Model gate ROI", "Baseline gate bets", "Baseline gate ROI"]}
                  rows={b.results.sensitivity_margin.map((s: Json) => [s.margin, s.model_ev_gate.n_bets, pct(s.model_ev_gate.roi, 2), s.baseline_ev_gate.n_bets, pct(s.baseline_ev_gate.roi, 2)])} />
                <h3>Across evaluated thresholds (margin {b.config.margins[0]})</h3>
                <Table head={["Threshold", "Always-bet ROI", "Reference EV/unit", "Model gate bets", "Model gate ROI"]}
                  rows={b.results.sensitivity_threshold.map((s: Json) => [`${s.threshold}x`, pct(s.always_bet_fixed_threshold.roi, 2), pct(s.reference_ev_per_unit_always_bet, 1), s.model_ev_gate.n_bets, pct(s.model_ev_gate.roi, 2)])} />
                <details><summary>Audit checks</summary><pre>{JSON.stringify(b.audit.checks, null, 2)}</pre></details>
              </Card>
            ))}
          </>
        )}
      </Async>
    </div>
  );
}
