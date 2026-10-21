import { useEffect, useState } from "react";
import { useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Notice, StatusBadge, Table, fmt } from "../components";
import { CalibrationChart } from "../charts";
import { RunPicker, ThresholdPicker, useRuns } from "./shared";

export default function ModelsPage({ ctx }: { ctx: Ctx }) {
  const { runs, runId, setRunId } = useRuns(ctx.source?.id);
  const run = useApi<Json>(runId ? `/api/evaluations/${runId}` : null);
  const [th, setTh] = useState<string>("");
  const ths = Object.keys(run.data?.summary?.thresholds ?? {});
  useEffect(() => {
    if (ths.length && !ths.includes(th)) setTh(ths.includes(String(run.data?.protocol?.primary_threshold)) ? String(run.data?.protocol?.primary_threshold) : ths[0]);
  }, [ths.join(","), th, run.data]);
  if (!ctx.source) return <Notice>Select a data source first.</Notice>;
  if (!runs.loading && runId === null) return <Notice>No completed evaluation for this source. Start one on the <a href="#walkforward">Walk-forward</a> page.</Notice>;
  const t = run.data?.summary?.thresholds?.[th];
  return (
    <div className="grid">
      <Card title="Model comparison" actions={<>
        <RunPicker runs={runs.data ?? []} runId={runId} setRunId={setRunId} />
        {ths.length > 0 && <ThresholdPicker thresholds={ths} value={th} onChange={setTh} />}
      </>}>
        <p className="muted">All metrics are from chronological walk-forward predictions on rounds the model had not seen. “Train” is in-sample on the last fold and shown only to expose over-fitting. Models are compared to the expanding-frequency baseline with a one-sided Diebold–Mariano test, Holm-adjusted across all model × threshold comparisons.</p>
      </Card>
      <Async state={run}>
        {() => !t ? <div className="state state-empty">Select a threshold.</div> : (
          <>
            <Card title={`Development period — ${t.target}`} actions={<StatusBadge status={t.status} />}>
              <Table head={["Model", "Family", "Train Brier", "Validation Brier", "Log loss", "AUC", "ECE", "Brier skill", "95% CI", "DM p (Holm)", "Folds"]}
                rows={Object.entries(t.development).map(([name, r]: [string, Json]) => [
                  name === t.selected_model ? <b key="n">{name} ★</b> : name, r.family, fmt(r.train_last_fold?.brier, 5), fmt(r.validation.brier, 5),
                  fmt(r.validation.log_loss, 5), fmt(r.validation.roc_auc, 3), fmt(r.validation.ece, 4),
                  fmt(r.vs_baseline?.brier_skill, 5, true),
                  r.vs_baseline ? `${fmt(r.vs_baseline.brier_skill_ci_low, 4, true)} … ${fmt(r.vs_baseline.brier_skill_ci_high, 4, true)}` : "baseline",
                  fmt(r.vs_baseline?.p_holm_dev_family, 4), r.n_folds,
                ])} />
              <p className="muted">★ selected for the final test: {t.selection_rule}. Baselines are never eligible.</p>
            </Card>
            <Card title={`Final test (${t.test.n} unseen rounds)`}>
              <Table head={["Model", "Brier", "Log loss", "AUC", "ECE", "Hosmer–Lemeshow p", "Calibration gap", "Precision @ break-even", "Recall @ break-even", "Calls"]}
                rows={Object.entries(t.test.metrics).map(([name, m]: [string, Json]) => [
                  name, fmt(m.brier, 5), fmt(m.log_loss, 5), fmt(m.roc_auc, 3), fmt(m.ece, 4), fmt(m.hosmer_lemeshow?.p_value, 3),
                  fmt(m.calibration_in_the_large, 4, true), fmt(m.confusion_at_breakeven?.precision, 4), fmt(m.confusion_at_breakeven?.recall, 4),
                  m.confusion_at_breakeven?.n_positive_calls,
                ])} />
              <p>
                Brier skill of <b>{t.selected_model}</b> vs baseline: <b>{fmt(t.test.comparison_vs_baseline.brier_skill, 5, true)}</b> (95% CI{" "}
                {fmt(t.test.comparison_vs_baseline.brier_skill_ci_low, 5, true)} to {fmt(t.test.comparison_vs_baseline.brier_skill_ci_high, 5, true)}),
                DM p = {fmt(t.test.comparison_vs_baseline.p_value, 4)}, Holm-adjusted = {fmt(t.test.comparison_vs_baseline.p_holm_across_thresholds, 4)}.
              </p>
              <ul>{t.status_reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>
              <h3>Confusion matrix at break-even probability {fmt(t.breakeven_probability, 4)} (selected model)</h3>
              {(() => { const c = t.test.metrics[t.selected_model].confusion_at_breakeven; return (
                <Table head={["", "Reached", "Crashed before"]} rows={[["Predicted p > break-even", c.tp, c.fp], ["Predicted p ≤ break-even", c.fn, c.tn]]} />); })()}
            </Card>
            <Card title="Reliability diagram (final test)">
              <CalibrationChart selected={t.test.calibration_selected} baseline={t.test.calibration_baseline} selectedName={t.selected_model} />
              <Table head={["Bucket p range", "Mean predicted", "Observed", "95% CI", "n"]}
                rows={t.test.calibration_selected.map((b: Json) => [`${fmt(b.p_min, 3)}–${fmt(b.p_max, 3)}`, fmt(b.mean_predicted, 4), fmt(b.observed_rate, 4), `${fmt(b.observed_ci_low, 3)}–${fmt(b.observed_ci_high, 3)}`, b.count])} />
            </Card>
          </>
        )}
      </Async>
    </div>
  );
}
