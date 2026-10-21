import { useState } from "react";
import { useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Notice, Stat, Table, fmt, pct } from "../components";
import { AcfChart, HistogramChart, MultiplierSeriesChart, RollingShareChart, ThresholdFrequencyChart } from "../charts";

export default function HistoryPage({ ctx }: { ctx: Ctx }) {
  const [th, setTh] = useState(2);
  const an = useApi<Json>(ctx.source ? `/api/sources/${ctx.source.id}/analysis?threshold=${th}` : null);
  const rounds = useApi<Json[]>(ctx.source ? `/api/sources/${ctx.source.id}/rounds?limit=50` : null);
  if (!ctx.source) return <Notice>Select or import a data source first.</Notice>;
  return (
    <div className="grid">
      <Card title="Analysis threshold" actions={
        <select aria-label="Analysis threshold" value={th} onChange={(e) => setTh(Number(e.target.value))}>
          {[1.5, 2, 3, 5, 10].map((t) => <option key={t} value={t}>{t}x</option>)}
        </select>}>
        <Notice>Descriptive statistics and dependence tests. A significant anomaly is not, by itself, an exploitable predictive signal.</Notice>
      </Card>
      <Async state={an} isEmpty={(a) => !!a.error} empty="Not enough rounds for analysis (need ≥ 30).">
        {(a) => {
          const s = a.summary;
          const ref = a.threshold_frequencies.find((r: Json) => r.threshold === th)?.reference_share_reaching;
          const key = Object.keys(a.autocorrelation).find((k) => k.startsWith("hit_"))!;
          return (
            <>
              <Card title="Distribution summary">
                <div className="stats">
                  <Stat label="Rounds" value={s.n} />
                  <Stat label="Median" value={`${fmt(s.median, 2)}x`} />
                  <Stat label="Mean (heavy-tailed!)" value={`${fmt(s.mean, 2)}x`} hint={`1% trimmed ${fmt(s.trimmed_mean_1pct, 2)}x`} />
                  <Stat label="p90 / p99" value={`${fmt(s.quantiles.p90, 2)}x / ${fmt(s.quantiles.p99, 2)}x`} />
                  <Stat label="Max" value={`${fmt(s.max, 2)}x`} />
                  <Stat label="Instant crash (1.00x)" value={pct(s.share_exactly_1, 2)} />
                </div>
              </Card>
              <Card title="Multiplier series"><MultiplierSeriesChart series={a.series} /></Card>
              <Card title="Histogram"><HistogramChart bins={a.histogram} /></Card>
              <Card title="Threshold frequencies">
                <ThresholdFrequencyChart rows={a.threshold_frequencies} />
                <Table head={["Threshold", "Share reaching", "95% CI", "Share below", "Reference", "Implied RTP", "Binomial p (Holm)"]}
                  rows={a.threshold_frequencies.map((r: Json) => [`${r.threshold}x`, pct(r.share_reaching, 2), `${pct(r.ci_low, 2)}–${pct(r.ci_high, 2)}`, pct(r.share_below, 2), pct(r.reference_share_reaching, 2), fmt(r.implied_rtp, 3), fmt(r.binomial_p_holm, 4)])} />
                <p className="muted">Goodness of fit vs {a.goodness_of_fit.reference}: χ²={fmt(a.goodness_of_fit.chi2, 2)}, df={a.goodness_of_fit.dof}, p={fmt(a.goodness_of_fit.p_value, 4)}</p>
              </Card>
              <Card title={`Rolling share reaching ${th}x (200 rounds)`}><RollingShareChart series={a.rolling_share_200} reference={ref} threshold={th} /></Card>
              <Card title="Dependence & stationarity tests (Holm-adjusted family)">
                <Table head={["Test", "p-value", "Holm p", "Significant at 5%?"]}
                  rows={a.dependence_tests.map((t: Json) => [t.test, fmt(t.p_value, 4), fmt(t.p_holm, 4), t["significant_at_0.05"] ? "yes" : "no"])} />
                <p className="muted">{a.interpretation_note}</p>
              </Card>
              <Card title="Autocorrelation of log multiplier"><AcfChart acf={a.autocorrelation.log_multiplier.acf} band={a.autocorrelation.log_multiplier.conf_band_95} /></Card>
              <Card title={`Autocorrelation of ${key}`}><AcfChart acf={a.autocorrelation[key].acf} band={a.autocorrelation[key].conf_band_95} /></Card>
              <Card title={`Streaks below ${th}x`}>
                <p>Longest streak: {a.streaks.longest_below_streak} · streaks: {a.streaks.n_streaks}</p>
                <Table head={["After k misses", "n", "P(reach next)", "95% CI"]}
                  rows={a.streaks.conditional_after_misses.map((r: Json) => [r.after_misses, r.n, pct(r.p_hit, 2), `${pct(r.ci_low, 1)}–${pct(r.ci_high, 1)}`])} />
                <Table head={["Length", "Observed", "Expected if independent"]}
                  rows={a.streaks.length_distribution.slice(0, 10).map((r: Json) => [r.length, r.observed, fmt(r.expected_if_independent, 1)])} />
              </Card>
              <Card title="Drift across time blocks">
                <Table head={["Block", "Seq", "n", `Share ≥ ${th}x`, "95% CI", "Median"]}
                  rows={a.drift.blocks.map((b: Json) => [b.block, `${b.start_seq}–${b.end_seq}`, b.n, pct(b.share_reaching, 2), `${pct(b.ci_low, 1)}–${pct(b.ci_high, 1)}`, fmt(b.median, 2)])} />
                <p className="muted">Homogeneity χ² p={fmt(a.drift.homogeneity_p, 4)} · KS first vs second half p={fmt(a.drift.ks_first_vs_second_half?.p_value, 4)} · CUSUM change point at seq {a.drift.cusum.change_point_seq} (p={fmt(a.drift.cusum.p_value, 4)})</p>
              </Card>
              {a.time_of_day && (
                <Card title="Time of day (UTC)">
                  <p className="muted">χ² p = {fmt(a.time_of_day.p_value, 4)} (only meaningful with accurate timestamps)</p>
                </Card>
              )}
              {a.fairness && (
                <Card title="Fairness metadata audit">
                  <p>Status: <b>{a.fairness.status}</b> · commitments checked {a.fairness.commitment_checks}, matched {a.fairness.commitment_matched}, mismatched {a.fairness.commitment_mismatched}</p>
                  <ul>{a.fairness.notes.map((n: string) => <li key={n} className="muted">{n}</li>)}</ul>
                </Card>
              )}
            </>
          );
        }}
      </Async>
      <Card title="Latest stored rounds">
        <Async state={rounds} isEmpty={(r) => !r.length}>
          {(r) => <Table head={["Seq", "Round ID", "Multiplier", "Timestamp", "Quality", "Verified"]}
            rows={r.map((x: Json) => [x.sequence, x.round_id, `${fmt(x.multiplier, 2)}x`, x.timestamp ?? "—", x.quality_status + (x.quality_flags.length ? ` (${x.quality_flags.join(", ")})` : ""), x.verified ? "yes" : "no"])} />}
        </Async>
      </Card>
    </div>
  );
}
