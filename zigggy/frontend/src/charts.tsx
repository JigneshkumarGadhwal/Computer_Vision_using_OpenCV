// Charts: single y-axis, thin marks, recessive grid, crosshair tooltips. Colours from theme tokens.
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { usd } from "./api";

const C = { grid: "#1a1f26", axis: "#6c727c", series: "#3987e5", up: "#1fbf4f", down: "#e5484d", amber: "#f2a516", surface: "#0f1115", ink: "#e7e6e2" };
const tick = { fill: C.axis, fontSize: 10, fontFamily: "inherit" };

function Tip({ active, payload, label, fmt, labelFmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="panel px-2.5 py-1.5 text-[11px] shadow-lg">
      <div className="text-ink3 mb-0.5">{labelFmt ? labelFmt(label) : label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 tabular">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: p.color || p.stroke || p.fill }} />
          <span className="text-ink2">{p.name}</span>
          <span className="text-ink ml-auto pl-3">{fmt ? fmt(p.value) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

const timeFmt = (iso: string) => {
  const d = new Date(iso);
  return `${d.toISOString().slice(5, 10)} ${d.toISOString().slice(11, 16)}`;
};

export function EquityChart({ data, start, height = 220 }: { data: { ts: string; equity: number }[]; start?: number; height?: number }) {
  if (!data.length) return <div className="text-ink3 text-[11px] p-6 text-center">No account snapshots yet — the equity curve fills in as the scheduler records snapshots (every 60s).</div>;
  const vals = data.map((d) => d.equity);
  const lo = Math.min(...vals, start ?? Infinity), hi = Math.max(...vals, start ?? -Infinity);
  const pad = Math.max((hi - lo) * 0.1, hi * 0.0005, 1);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <defs>
          <linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={C.series} stopOpacity={0.28} />
            <stop offset="100%" stopColor={C.series} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="ts" tick={tick} tickFormatter={timeFmt} minTickGap={60} axisLine={{ stroke: C.grid }} tickLine={false} />
        <YAxis domain={[lo - pad, hi + pad]} tick={tick} width={70} tickFormatter={(v) => usd(v, 0)} axisLine={false} tickLine={false} />
        {start != null && <ReferenceLine y={start} stroke={C.axis} strokeDasharray="3 3" label={{ value: "start", fill: C.axis, fontSize: 10, position: "insideTopLeft" }} />}
        <Tooltip content={<Tip fmt={(v: number) => usd(v)} labelFmt={timeFmt} />} cursor={{ stroke: C.axis, strokeDasharray: "2 2" }} />
        <Area type="linear" dataKey="equity" name="Equity" stroke={C.series} strokeWidth={2} fill="url(#eqfill)" dot={false} activeDot={{ r: 4, stroke: C.surface, strokeWidth: 2 }} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Cumulative depth: bids (left of mid) and asks (right). Labelled series; position also encodes side. */
export function DepthChart({ book, height = 220 }: { book: { yes_bids: [number, number][]; yes_asks: [number, number][] } | null; height?: number }) {
  if (!book || (!book.yes_bids.length && !book.yes_asks.length)) return <div className="text-ink3 text-[11px] p-6 text-center">No order book.</div>;
  const bids = [...book.yes_bids].sort((a, b) => b[0] - a[0]);
  const asks = [...book.yes_asks].sort((a, b) => a[0] - b[0]);
  let cb = 0, ca = 0;
  const bp = bids.map(([p, s]) => ({ price: p, bid: (cb += s) })).reverse();
  const ap = asks.map(([p, s]) => ({ price: p, ask: (ca += s) }));
  const data = [...bp, ...ap];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="price" type="number" domain={["dataMin", "dataMax"]} tick={tick} tickFormatter={(v) => v.toFixed(2)} axisLine={{ stroke: C.grid }} tickLine={false} />
        <YAxis tick={tick} width={56} axisLine={false} tickLine={false} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v)} />
        <Tooltip content={<Tip fmt={(v: number) => `${v.toLocaleString()} ct`} labelFmt={(l: number) => `YES @ ${Number(l).toFixed(3)}`} />} cursor={{ stroke: C.axis, strokeDasharray: "2 2" }} />
        <Area type="stepAfter" dataKey="bid" name="Bids (cum.)" stroke={C.up} fill={C.up} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} connectNulls={false} />
        <Area type="stepBefore" dataKey="ask" name="Asks (cum.)" stroke={C.down} fill={C.down} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} connectNulls={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SpreadHistory({ data, height = 140 }: { data: [string, number | null, number | null][]; height?: number }) {
  const rows = data.map(([ts, mid, sp]) => ({ ts, mid, spread: sp }));
  if (rows.length < 2) return <div className="text-ink3 text-[11px] p-4 text-center">Spread history builds up as books are refreshed.</div>;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="ts" tick={tick} tickFormatter={(v) => new Date(v).toISOString().slice(11, 16)} minTickGap={50} axisLine={{ stroke: C.grid }} tickLine={false} />
        <YAxis tick={tick} width={44} tickFormatter={(v) => `${(v * 100).toFixed(0)}¢`} axisLine={false} tickLine={false} />
        <Tooltip content={<Tip fmt={(v: number) => `${(v * 100).toFixed(1)}¢`} labelFmt={(l: string) => new Date(l).toISOString().slice(11, 19)} />} />
        <Line type="stepAfter" dataKey="spread" name="Spread" stroke={C.series} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function MidHistory({ data, height = 160 }: { data: { ts: string; mid: number | null; best_bid: number | null; best_ask: number | null }[]; height?: number }) {
  if (data.length < 2) return <div className="text-ink3 text-[11px] p-4 text-center">Not enough price observations yet.</div>;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="ts" tick={tick} tickFormatter={(v) => new Date(v).toISOString().slice(11, 16)} minTickGap={50} axisLine={{ stroke: C.grid }} tickLine={false} />
        <YAxis domain={[0, 1]} tick={tick} width={40} tickFormatter={(v) => v.toFixed(2)} axisLine={false} tickLine={false} />
        <Tooltip content={<Tip fmt={(v: number) => v?.toFixed(3)} labelFmt={(l: string) => new Date(l).toISOString().slice(11, 19)} />} />
        <Line type="stepAfter" dataKey="best_bid" name="Best bid" stroke={C.up} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <Line type="stepAfter" dataKey="best_ask" name="Best ask" stroke={C.down} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="mid" name="Mid" stroke={C.series} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function ExposureBars({ data, equity, height = 180 }: { data: Record<string, number>; equity: number; height?: number }) {
  const rows = Object.entries(data).map(([k, v]) => ({ k, v, pct: equity ? v / equity : 0 })).sort((a, b) => b.v - a.v);
  if (!rows.length) return <div className="text-ink3 text-[11px] p-6 text-center">No open exposure.</div>;
  return (
    <ResponsiveContainer width="100%" height={Math.max(height, rows.length * 28 + 20)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 40, bottom: 0, left: 4 }} barCategoryGap={6}>
        <CartesianGrid stroke={C.grid} horizontal={false} />
        <XAxis type="number" tick={tick} tickFormatter={(v) => usd(v, 0)} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="k" tick={{ ...tick, fill: "#a6abb3" }} width={90} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "#ffffff08" }} content={<Tip fmt={(v: number) => `${usd(v)} (${((v / (equity || 1)) * 100).toFixed(1)}% of equity)`} />} />
        <Bar dataKey="v" name="Capital at risk" radius={[0, 4, 4, 0]} isAnimationActive={false} label={{ position: "right", fill: "#a6abb3", fontSize: 10, formatter: (v: any) => usd(Number(v), 0) }}>
          {rows.map((r) => <Cell key={r.k} fill={C.series} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
