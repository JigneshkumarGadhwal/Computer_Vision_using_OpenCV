import { useState } from "react";
import { apiGet, apiPost, useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Card, ModeBadge, Notice, StatusBadge, Table, fmt, pct } from "../components";

export default function SignalsPage({ ctx }: { ctx: Ctx }) {
  const [th, setTh] = useState("2");
  const [sig, setSig] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mult, setMult] = useState("");
  const [busy, setBusy] = useState(false);
  const sid = ctx.source?.id;
  const preds = useApi<Json[]>(sid ? `/api/predictions?source_id=${sid}&mode=live&limit=50` : null);
  if (!ctx.source) return <Notice>Select a data source first.</Notice>;

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      setSig(await apiGet(`/api/signals/${sid}?threshold=${th}`));
      preds.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const addRound = async () => {
    setError(null);
    try {
      await apiPost(`/api/live/${encodeURIComponent(ctx.source.name)}/rounds`, { multiplier: mult, source_kind: ctx.source.kind });
      setMult("");
      ctx.reloadSources();
      preds.reload();
      setSig(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const canEnter = ["manual", "live_feed", "authorized_api"].includes(ctx.source.kind);
  return (
    <div className="grid">
      <Card title="Next-round research signal" actions={
        <>
          <select aria-label="Signal threshold" value={th} onChange={(e) => { setTh(e.target.value); setSig(null); }}>
            {["1.5", "2", "3", "5"].map((t) => <option key={t} value={t}>reach {t}x</option>)}
          </select>
          <button className="primary" onClick={generate} disabled={busy}>Generate & store signal</button>
        </>}>
        <Notice>Each generated signal is timestamped and stored immutably <i>before</i> the round is played, so it can be scored honestly later. Requesting again for the same round returns the stored prediction.</Notice>
        {error && <div className="state state-error" role="alert">{error}</div>}
        {sig && <SignalView s={sig} />}
      </Card>
      {canEnter && (
        <Card title="Record a completed round (manual / live observation)">
          <div className="form-row">
            <label>Crash multiplier<input aria-label="Crash multiplier" value={mult} onChange={(e) => setMult(e.target.value)} placeholder="e.g. 1.84" /></label>
          </div>
          <div className="btn-row"><button disabled={!mult} onClick={addRound}>Add round</button></div>
          <p className="muted">Only enter results of rounds that have already finished. Pending live predictions for that round are scored automatically.</p>
        </Card>
      )}
      <Card title="Stored live predictions">
        {preds.data && preds.data.length ? (
          <Table head={["Created", "Target seq", "Model", "Threshold", "P(reach)", "Status", "Outcome", "Hash"]}
            rows={preds.data.map((p: Json) => [p.created_at.slice(0, 19), p.target_seq, p.model_name, `${p.threshold}x`, fmt(p.probability, 4), p.signal_status ?? "—",
              p.outcome_multiplier === null ? "pending" : `${fmt(p.outcome_multiplier, 2)}x (${p.outcome_hit ? "reached" : "crashed"})`, <code key="h">{p.content_hash.slice(0, 10)}</code>])} />
        ) : <div className="state state-empty">No live predictions stored yet.</div>}
      </Card>
    </div>
  );
}

function SignalView({ s }: { s: Json }) {
  return (
    <div className="signal">
      <div className="signal-head">
        <StatusBadge status={s.status} /> <ModeBadge mode={s.data_mode} />
      </div>
      {s.warnings?.map((w: string) => <Notice key={w} tone="warn">{w}</Notice>)}
      <div className="kv">
        <div>Round (sequence position)</div><div>{s.next_round_sequence} (after {s.data_cutoff_round_id ?? "—"})</div>
        <div>Prediction timestamp</div><div>{s.prediction_created_at ?? s.prediction_timestamp}</div>
        <div>Target</div><div>{s.target} — break-even probability {fmt(s.breakeven_probability, 4)}</div>
        <div>Estimated P(reach {s.threshold}x)</div><div><b>{s.probability_reach === null ? "not shown (insufficient evidence)" : pct(s.probability_reach, 2)}</b></div>
        <div>Estimated P(crash before)</div><div>{s.probability_crash_before === null ? "—" : pct(s.probability_crash_before, 2)}</div>
        <div>Baseline P(reach)</div><div>{s.baseline_probability_reach === undefined ? "—" : pct(s.baseline_probability_reach, 2)}</div>
        <div>Historical frequency</div><div>{s.historical_frequency ? `${pct(s.historical_frequency.share_reaching, 2)} (95% CI ${pct(s.historical_frequency.ci_low, 1)}–${pct(s.historical_frequency.ci_high, 1)}, n=${s.historical_frequency.n})` : "—"}</div>
        <div>Model</div><div>{s.model_name ? `${s.model_name} v${s.model_version} (registry #${s.model_registry_id})` : "—"}</div>
        <div>Training-data cutoff</div><div>{s.training_data_cutoff_round_id ? `${s.training_data_cutoff_round_id} (seq ${s.training_data_cutoff_seq}; ${s.rounds_since_training_cutoff} rounds ago)` : "—"}</div>
        <div>Data quality</div><div>{s.data_quality ? (s.data_quality.gate_passed ? "passed" : "FAILED") : "—"}</div>
        <div>Calibration</div><div>{s.calibration_status ?? "—"}</div>
        <div>Out-of-sample (final test)</div><div>{s.out_of_sample ? `Brier ${fmt(s.out_of_sample.brier_model, 5)} vs baseline ${fmt(s.out_of_sample.brier_baseline, 5)}; skill ${fmt(s.out_of_sample.brier_skill, 5, true)} (CI ${fmt(s.out_of_sample.brier_skill_ci[0], 4, true)} … ${fmt(s.out_of_sample.brier_skill_ci[1], 4, true)}), n=${s.out_of_sample.n_test_rounds}` : "—"}</div>
        <div>Uncertainty (same-bucket hit rate)</div><div>{s.uncertainty?.calibration_bucket ? `${pct(s.uncertainty.calibration_bucket.observed_rate, 1)} (95% CI ${pct(s.uncertainty.calibration_bucket.observed_ci_low, 1)}–${pct(s.uncertainty.calibration_bucket.observed_ci_high, 1)}, n=${s.uncertainty.calibration_bucket.count})` : "—"}</div>
        <div>Recent live performance</div><div>{s.recent_live_performance?.n_resolved ? `${s.recent_live_performance.n_resolved} resolved; Brier ${fmt(s.recent_live_performance.brier_model, 4)} vs ${fmt(s.recent_live_performance.brier_baseline, 4)}` : "no resolved live predictions"}</div>
        <div>Prediction hash</div><div><code>{s.prediction_hash ?? "not stored"}</code></div>
      </div>
      <ul className="reasons">{s.status_reasons?.map((r: string) => <li key={r}>{r}</li>)}</ul>
      <p className="disclaimer">{s.disclaimer}</p>
    </div>
  );
}
