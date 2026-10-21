import { useEffect, useState } from "react";
import { apiPost, apiUrl, useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Notice, StatusBadge, Table, fmt } from "../components";
import { PeriodChart } from "../charts";
import { RunPicker, useRuns } from "./shared";

export default function WalkForwardPage({ ctx }: { ctx: Ctx }) {
  const cfg = useApi<Json>("/api/config");
  const { runs, runId, setRunId } = useRuns(ctx.source?.id);
  const run = useApi<Json>(runId ? `/api/evaluations/${runId}` : null);
  const [thresholds, setThresholds] = useState("1.5,2,3,5");
  const [models, setModels] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = (runs.data ?? []).filter((r) => r.status === "running");

  useEffect(() => {
    if (cfg.data && models === null) setModels(cfg.data.default_models);
  }, [cfg.data, models]);
  useEffect(() => {
    if (!running.length) return;
    const t = setInterval(runs.reload, 3000);
    return () => clearInterval(t);
  }, [running.length, runs.reload]);

  if (!ctx.source) return <Notice>Select a data source first.</Notice>;
  const start = async () => {
    setError(null);
    try {
      const ths = thresholds.split(",").map((x) => Number(x.trim())).filter((x) => Number.isFinite(x));
      await apiPost("/api/evaluations", { source_id: ctx.source.id, thresholds: ths, models });
      runs.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const p = cfg.data?.protocol;
  return (
    <div className="grid">
      <Card title="Run a pre-registered walk-forward evaluation">
        {p && (
          <p className="muted">
            Protocol: first {Math.round(p.dev_fraction * 100)}% of rounds for development (walk-forward from {p.initial_train_size} rounds,
            retrain every {p.block_size}); remaining rounds are an untouched final test. Signal criteria fixed in advance: Brier skill &gt; {p.min_brier_skill},
            Holm-adjusted DM p &lt; {p.alpha}, Hosmer–Lemeshow p ≥ {p.calibration_alpha}, calibration gap ≤ {p.max_calibration_gap}, ≥ {p.min_test_rounds} test rounds.
          </p>
        )}
        <div className="form-row">
          <label>Thresholds (comma-separated)<input aria-label="Thresholds" value={thresholds} onChange={(e) => setThresholds(e.target.value)} /></label>
        </div>
        {cfg.data && models && (
          <fieldset className="checks">
            <legend>Models</legend>
            {cfg.data.available_models.map((m: string) => (
              <label key={m} className="check">
                <input type="checkbox" checked={models.includes(m)} disabled={m === "expanding_frequency"}
                  onChange={(e) => setModels(e.target.checked ? [...models, m] : models.filter((x) => x !== m))} />
                {m}{["svm_rbf", "mlp"].includes(m) ? " (optional, slow)" : ""}
              </label>
            ))}
          </fieldset>
        )}
        <div className="btn-row"><button className="primary" onClick={start} disabled={running.length > 0}>Start evaluation</button></div>
        {running.map((r) => <Notice key={r.id}>Run {r.id} running — {r.progress ?? "starting"}…</Notice>)}
        {error && <div className="state state-error" role="alert">{error}</div>}
      </Card>
      <Card title="Evaluation runs" actions={<RunPicker runs={runs.data ?? []} runId={runId} setRunId={setRunId} />}>
        <Async state={runs} isEmpty={(r) => !r.length} empty="No evaluation runs yet.">
          {(rs) => <Table head={["Run", "Status", "Result", "Rounds", "Started", "Finished", "Report"]}
            rows={rs.map((r: Json) => [r.id, r.status + (r.error ? " (error)" : ""), r.overall_status ? <StatusBadge key="s" status={r.overall_status} /> : "—", r.n_rounds,
              r.started_at.slice(0, 19), r.finished_at?.slice(0, 19) ?? "—",
              r.status === "completed" ? <a key="a" href={apiUrl(`/api/evaluations/${r.id}/report`)} target="_blank" rel="noreferrer">markdown</a> : "—"])} />}
        </Async>
      </Card>
      <Async state={run} empty="Select a completed run.">
        {(r) => {
          const s = r.summary;
          return (
            <>
              <Card title={`Run ${r.id} — ${s.overall_status}`}>
                <p className="conclusion">{r.conclusion}</p>
                <div className="kv">
                  <div>Development period</div><div>seq {s.dev_period?.start_seq}–{s.dev_period?.end_seq} ({s.dev_period?.n} rounds)</div>
                  <div>Final test period</div><div>seq {s.test_period?.start_seq}–{s.test_period?.end_seq} ({s.test_period?.n} rounds) {s.test_period?.start_time ? `${s.test_period.start_time.slice(0, 16)} → ${s.test_period.end_time?.slice(0, 16)}` : ""}</div>
                  <div>Look-ahead audit</div><div>{s.leakage_audit ? (s.leakage_audit.passed ? `PASSED (${s.leakage_audit.checked_positions} positions)` : "FAILED") : "n/a"}</div>
                  <div>Data quality gate</div><div>{s.data_quality ? (s.data_quality.gate_passed ? "passed" : "FAILED") : "n/a"}</div>
                  <div>Predictions committed</div><div>{s.predictions_committed ?? "n/a"} (stored before outcomes)</div>
                  <div>Data fingerprint</div><div><code>{r.data_fingerprint.slice(0, 16)}…</code></div>
                  <div>Code version</div><div><code>{r.code_version}</code></div>
                </div>
                {r.error && <pre className="error-pre">{r.error}</pre>}
              </Card>
              {Object.values(s.thresholds ?? {}).map((t: Json) => (
                <Card key={t.threshold} title={`${t.target}: ${t.selected_model} vs baseline`} actions={<StatusBadge status={t.status} />}>
                  <p>Brier skill {fmt(t.test.comparison_vs_baseline.brier_skill, 5, true)} (CI {fmt(t.test.comparison_vs_baseline.brier_skill_ci_low, 5, true)} … {fmt(t.test.comparison_vs_baseline.brier_skill_ci_high, 5, true)}) · base rate dev {fmt(t.test.dev_base_rate, 4)} → test {fmt(t.test.test_base_rate, 4)}</p>
                  <PeriodChart rows={t.test.per_period} />
                  <Table head={["Sub-period", "n", "Base rate", "Shift vs dev", "Brier model", "Brier baseline"]}
                    rows={t.test.per_period.map((x: Json) => [`${x.start_seq}–${x.end_seq}`, x.n, fmt(x.base_rate, 4), fmt(x.shift_vs_dev_rate, 4, true), fmt(x.brier_model, 5), fmt(x.brier_baseline, 5)])} />
                </Card>
              ))}
              {s.exact_multiplier && (
                <Card title="Secondary task: exact multiplier (log scale)">
                  <p className="muted">{s.exact_multiplier.note}</p>
                  {Object.entries(s.exact_multiplier.phases).map(([phase, ph]: [string, Json]) => (
                    <div key={phase}>
                      <h3>{phase} ({ph.n} rounds)</h3>
                      <Table head={["Model", "MAE(log)", "RMSE(log)", "Median abs error (x)", "DM p vs median"]}
                        rows={Object.entries(ph.point).map(([n, m]: [string, Json]) => [n, fmt(m.mae_log), fmt(m.rmse_log), fmt(m.median_abs_error_multiplier), fmt(m.dm_vs_train_median_abs_error?.p_value_one_sided)])} />
                      <Table head={["Quantile model", "Pinball q0.1", "Pinball q0.5", "Pinball q0.9", "Coverage q0.1–q0.9"]}
                        rows={Object.entries(ph.quantile).map(([n, m]: [string, Json]) => [n, fmt(m["pinball_q0.1"]), fmt(m["pinball_q0.5"]), fmt(m["pinball_q0.9"]), fmt(m["coverage_q0.1_q0.9"], 3)])} />
                    </div>
                  ))}
                </Card>
              )}
            </>
          );
        }}
      </Async>
    </div>
  );
}
