import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  ErrorBar,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Json } from "./api";

const C = {
  a: "var(--series-1)",
  b: "var(--series-2)",
  c: "var(--series-3)",
  grid: "var(--grid)",
  ref: "var(--ref)",
};

export function MultiplierSeriesChart({ series }: { series: Json[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={series} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="seq" tick={{ fontSize: 11 }} label={{ value: "round sequence", position: "insideBottom", offset: -2, fontSize: 11 }} />
        <YAxis scale="log" domain={[1, "auto"]} allowDataOverflow tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}x`} />
        <Tooltip formatter={(v: number) => `${v.toFixed(2)}x`} />
        <Line dataKey="value" name="crash multiplier (sampled)" dot={false} stroke={C.a} strokeWidth={1} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RollingShareChart({ series, reference, threshold }: { series: Json[]; reference?: number; threshold: number }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={series} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="seq" tick={{ fontSize: 11 }} />
        <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
        <Tooltip formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
        {reference !== undefined && <ReferenceLine y={reference} stroke={C.ref} strokeDasharray="4 4" label={{ value: "reference", fontSize: 10 }} />}
        <Line dataKey="value" name={`rolling 200-round share ≥ ${threshold}x`} dot={false} stroke={C.b} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function ThresholdFrequencyChart({ rows }: { rows: Json[] }) {
  const data = rows.map((r) => ({
    name: `≥${r.threshold}x`,
    observed: r.share_reaching,
    reference: r.reference_share_reaching,
    err: [r.share_reaching - r.ci_low, r.ci_high - r.share_reaching],
  }));
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
        <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
        <Tooltip formatter={(v: number) => (typeof v === "number" ? `${(v * 100).toFixed(2)}%` : String(v))} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="observed" name="observed share (95% CI)" fill={C.a} isAnimationActive={false}>
          <ErrorBar dataKey="err" width={4} stroke="var(--text)" />
        </Bar>
        <Bar dataKey="reference" name="reference RTP/m (unverified)" fill={C.c} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function HistogramChart({ bins }: { bins: Json[] }) {
  const data = bins.map((b) => ({ name: b.to ? `${b.from}–${b.to}` : `${b.from}+`, count: b.count }));
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={2} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip />
        <Bar dataKey="count" fill={C.a} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function AcfChart({ acf, band }: { acf: number[]; band: number }) {
  const data = acf.map((v, i) => ({ lag: i + 1, acf: v }));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="lag" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} domain={[-Math.max(band * 3, 0.05), Math.max(band * 3, 0.05)]} />
        <Tooltip formatter={(v: number) => v.toFixed(4)} />
        <ReferenceLine y={band} stroke={C.ref} strokeDasharray="4 4" />
        <ReferenceLine y={-band} stroke={C.ref} strokeDasharray="4 4" />
        <Bar dataKey="acf" fill={C.b} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Reliability diagram: mean predicted vs observed hit rate per equal-frequency bucket. */
export function CalibrationChart({ selected, baseline, selectedName }: { selected: Json[]; baseline?: Json[]; selectedName: string }) {
  const all = [...selected, ...(baseline ?? [])];
  const lo = Math.max(0, Math.min(...all.map((r) => Math.min(r.mean_predicted, r.observed_ci_low))) - 0.02);
  const hi = Math.min(1, Math.max(...all.map((r) => Math.max(r.mean_predicted, r.observed_ci_high))) + 0.02);
  const toPts = (rows: Json[]) =>
    rows.map((r) => ({ x: r.mean_predicted, y: r.observed_rate, err: [r.observed_rate - r.observed_ci_low, r.observed_ci_high - r.observed_rate], n: r.count }));
  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis type="number" dataKey="x" domain={[lo, hi]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(2)} name="predicted"
          label={{ value: "mean predicted probability", position: "insideBottom", offset: -4, fontSize: 11 }} />
        <YAxis type="number" dataKey="y" domain={[lo, hi]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(2)} name="observed" />
        <Tooltip formatter={(v: number) => (typeof v === "number" ? v.toFixed(4) : String(v))} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <ReferenceLine segment={[{ x: lo, y: lo }, { x: hi, y: hi }]} stroke={C.ref} strokeDasharray="4 4" ifOverflow="extendDomain" />
        <Scatter data={toPts(selected)} name={`${selectedName} (95% CI)`} fill={C.a}>
          <ErrorBar dataKey="err" direction="y" width={4} stroke={C.a} />
        </Scatter>
        {baseline && <Scatter data={toPts(baseline)} name="expanding-frequency baseline" fill={C.c} />}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function CumulativePnlChart({ policies }: { policies: Record<string, Json> }) {
  const names = Object.keys(policies);
  const colors = [C.a, C.b, C.c];
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis type="number" dataKey="seq" domain={["auto", "auto"]} tick={{ fontSize: 11 }} allowDuplicatedCategory={false} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v: number) => v.toFixed(2)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <ReferenceLine y={0} stroke={C.ref} />
        {names.map((n, i) => (
          <Line key={n} data={policies[n].cumulative} dataKey="pnl" name={n} dot={false} stroke={colors[i % 3]} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function PeriodChart({ rows }: { rows: Json[] }) {
  const data = rows.map((r) => ({ name: `${r.start_seq}–${r.end_seq}`, model: r.brier_model, baseline: r.brier_baseline }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
        <XAxis dataKey="name" tick={{ fontSize: 10 }} />
        <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(3)} />
        <Tooltip formatter={(v: number) => v.toFixed(5)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="model" name="Brier — selected model" fill={C.a} isAnimationActive={false} />
        <Bar dataKey="baseline" name="Brier — baseline" fill={C.c} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
