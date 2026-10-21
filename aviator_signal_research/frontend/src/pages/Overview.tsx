import { useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, ModeBadge, Notice, Stat, StatusBadge, fmt, pct } from "../components";
import { CalibrationChart, CumulativePnlChart, MultiplierSeriesChart, ThresholdFrequencyChart } from "../charts";

export default function OverviewPage({ ctx }: { ctx: Ctx }) {
  const sid = ctx.source?.id;
  const ov = useApi<Json>(sid ? `/api/overview/${sid}` : null);
  const an = useApi<Json>(sid ? `/api/sources/${sid}/analysis` : null);
  const runId = ov.data?.latest_run?.id;
  const run = useApi<Json>(runId ? `/api/evaluations/${runId}` : null);
  const bts = useApi<Json[]>(runId ? `/api/backtests?run_id=${runId}` : null);

  if (!ctx.source)
    return (
      <Notice>
        No data source yet. Go to <a href="#import">Data import</a> to upload real historical rounds, or load the clearly
        labelled synthetic demo.
      </Notice>
    );

  return (
    <div className="grid">
      <Async state={ov}>
        {(o) => {
          const th = o.selected_threshold;
          return (
            <Card title="Research status" actions={<ModeBadge mode={o.feed?.data_mode} />}>
              <div className="stats">
                <Stat label="Total verified rounds" value={`${o.source.n_verified} / ${o.source.n_rounds}`} hint="verified / stored" />
                <Stat label="Data period" value={o.source.first_timestamp ? `${o.source.first_timestamp.slice(0, 10)} → ${o.source.last_timestamp.slice(0, 10)}` : "no timestamps"} />
                <Stat label="Most recent observation" value={o.most_recent_observation ? `${fmt(o.most_recent_observation.multiplier, 2)}x` : "n/a"} hint={o.most_recent_observation?.round_id} />
                <Stat label="Selected threshold" value={`${th}x`} hint={`break-even p = ${fmt(1 / th, 4)}`} />
                <Stat label="Model status" value={<StatusBadge status={o.model_status} />} hint={o.selected_model} />
                <Stat label="Baseline Brier (test)" value={fmt(o.baseline_performance?.brier, 5)} hint="expanding frequency" />
                <Stat label="Model Brier (test)" value={fmt(o.model_performance?.brier, 5)} hint={o.n_test_rounds ? `${o.n_test_rounds} unseen rounds` : "no evaluation"} />
                <Stat label="Brier skill vs baseline" value={fmt(o.comparison?.brier_skill, 5, true)}
                  hint={o.comparison ? `95% CI ${fmt(o.comparison.brier_skill_ci_low, 5, true)} to ${fmt(o.comparison.brier_skill_ci_high, 5, true)}` : undefined} />
                <Stat label="Calibration" value={o.calibration_reason?.startsWith("calibration OK") ? "OK" : o.calibration_reason ? "FAILED" : "n/a"} hint={o.calibration_reason} />
                <Stat label="Predictive improvement demonstrated?" value={o.demonstrated_improvement ? "Yes (research signal)" : "No"} />
              </div>
              {!o.latest_run && <Notice tone="warn">No completed evaluation yet — run one on the <a href="#walkforward">Walk-forward</a> page.</Notice>}
              {o.latest_run?.conclusion && <p className="conclusion">{o.latest_run.conclusion}</p>}
            </Card>
          );
        }}
      </Async>
      <Async state={an} isEmpty={(a) => !!a.error}>
        {(a) => (
          <>
            <Card title="Historical crash multipliers (log scale, sampled)">
              <MultiplierSeriesChart series={a.series} />
            </Card>
            <Card title="Threshold frequencies vs reference hypothesis">
              <ThresholdFrequencyChart rows={a.threshold_frequencies} />
              <p className="muted">Share of all stored rounds reaching each multiplier: {a.threshold_frequencies.map((r: Json) => `≥${r.threshold}x ${pct(r.share_reaching)}`).join(" · ")}</p>
            </Card>
          </>
        )}
      </Async>
      <Async state={run} empty="Calibration appears after an evaluation run.">
        {(r) => {
          const t = r.summary?.thresholds?.[String(ov.data?.selected_threshold ?? 2)];
          if (!t) return <div className="state state-empty">Primary threshold not evaluated.</div>;
          return (
            <Card title={`Probability calibration on unseen rounds — ${t.target}`}>
              <CalibrationChart selected={t.test.calibration_selected} baseline={t.test.calibration_baseline} selectedName={t.selected_model} />
            </Card>
          );
        }}
      </Async>
      <Async state={bts} isEmpty={(b) => !b.length} empty="Cumulative hypothetical performance appears after a backtest.">
        {(b) => (
          <Card title={`Cumulative hypothetical P&L — backtest ${b[0].id} (${b[0].config.threshold}x, stake ${b[0].config.stake})`}>
            <CumulativePnlChart policies={b[0].results.policies} />
            <p className="muted">Hypothetical simulation on out-of-sample predictions. No real bets are placed.</p>
          </Card>
        )}
      </Async>
    </div>
  );
}
