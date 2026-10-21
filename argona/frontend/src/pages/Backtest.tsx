import { useState } from "react";
import { ago, api, pct, signedUsd, usd, usePoll } from "../api";
import { EquityChart } from "../charts";
import { Badge, Empty, ErrorLine, Panel, Stat } from "../ui";

export default function Backtest() {
  const runs = usePoll<any[]>("/backtests", 5000);
  const [sel, setSel] = useState<number | null>(null);
  const cur = usePoll<any>(sel ? `/backtests/${sel}` : null, 0, [sel]);
  const [form, setForm] = useState({ start: "", end: "", step_s: 300, starting_capital: 10000, include_simulated: true });
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setErr(null);
    try {
      await api("/backtests", { method: "POST", json: { ...form, start: form.start || null, end: form.end || null } });
      setTimeout(runs.reload, 800);
    } catch (e: any) { setErr(e.message); }
  };
  const r = cur.data;
  const m = r?.metrics ?? {};
  return (
    <div className="space-y-3">
      <Panel title="Run a backtest over recorded data">
        <div className="p-3 flex flex-wrap gap-3 items-end text-[11px]">
          <label className="flex flex-col gap-1"><span className="text-ink3">Start (UTC ISO, optional)</span><input className="input" placeholder="2026-10-01T00:00:00+00:00" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
          <label className="flex flex-col gap-1"><span className="text-ink3">End</span><input className="input" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
          <label className="flex flex-col gap-1"><span className="text-ink3">Step (s)</span><input className="input w-24" type="number" value={form.step_s} onChange={(e) => setForm({ ...form, step_s: +e.target.value })} /></label>
          <label className="flex flex-col gap-1"><span className="text-ink3">Capital</span><input className="input w-28" type="number" value={form.starting_capital} onChange={(e) => setForm({ ...form, starting_capital: +e.target.value })} /></label>
          <label className="flex items-center gap-1 text-ink2"><input type="checkbox" checked={form.include_simulated} onChange={(e) => setForm({ ...form, include_simulated: e.target.checked })} /> include SIM data</label>
          <button className="btn btn-amber" onClick={submit}>Run backtest</button>
        </div>
        <div className="px-3 pb-3 text-ink3 text-[10.5px]">Replays only order-book snapshots, evidence and resolutions ARGONA recorded (or imported with <code>argona import-history</code>), strictly point-in-time. Current risk settings are used. Results are hypothetical.</div>
        <ErrorLine error={err} />
      </Panel>
      <div className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-3">
        <Panel title="Runs">
          {(runs.data ?? []).length === 0 ? <Empty>No backtests yet.</Empty> : (
            <table className="tbl"><tbody>{runs.data!.map((x) => (
              <tr key={x.id} onClick={() => setSel(x.id)} className={`cursor-pointer ${sel === x.id ? "bg-[#161b22]" : ""}`}>
                <td>#{x.id}</td><td className="text-ink3">{ago(x.created_at)}</td>
                <td><Badge tone={x.status === "completed" ? "up" : x.status === "failed" ? "down" : "amber"}>{x.status}</Badge></td>
                <td className="num">{x.metrics?.total_return != null ? pct(x.metrics.total_return) : ""}</td></tr>))}</tbody></table>)}
        </Panel>
        <div className="space-y-3 min-w-0">
          {!r ? <Panel title="Result"><Empty>Select a run.</Empty></Panel> : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
                <Stat label="Total return" value={pct(m.total_return)} tone={(m.total_return ?? 0) >= 0 ? "up" : "down"} />
                <Stat label="Net P&L" value={signedUsd(m.net_pnl)} sub={`realized ${signedUsd(m.realized_pnl)}`} />
                <Stat label="Max drawdown" value={pct(m.max_drawdown)} />
                <Stat label="Win rate" value={pct(m.win_rate, 0)} sub={`${m.round_trips ?? 0} round trips · ${m.trades ?? 0} fills`} />
                <Stat label="Profit factor" value={m.profit_factor == null ? "—" : Number(m.profit_factor).toFixed(2)} sub={`expectancy ${signedUsd(m.expectancy)}`} />
                <Stat label="Fees / slippage" value={usd(m.fees)} sub={`slippage ${usd(m.slippage)}`} />
                <Stat label="Brier (model)" value={m.brier_model == null ? "—" : m.brier_model.toFixed(4)} sub={`market ${m.brier_market == null ? "—" : m.brier_market.toFixed(4)} · n=${m.calibration_samples ?? 0}`} />
                <Stat label="Data" value={`${m.markets ?? 0} mkts`} sub={`${m.snapshots ?? 0} snapshots`} />
              </div>
              <Panel title="Notes & limitations"><ul className="p-3 text-[11px] space-y-1">{(r.notes ?? []).map((n: string, i: number) => <li key={i} className="text-amber">• {n}</li>)}</ul></Panel>
              <Panel title="Equity (simulated fills)"><div className="p-2"><EquityChart data={r.equity_curve ?? []} start={m.starting_capital} /></div></Panel>
              <Panel title="By category"><table className="tbl"><thead><tr><th>Category</th><th className="num">Round trips</th><th className="num">P&L</th></tr></thead>
                <tbody>{Object.entries(m.by_category ?? {}).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{v.trades}</td><td className="num">{signedUsd(v.pnl)}</td></tr>)}</tbody></table></Panel>
              <Panel title={`Simulated fills (${(r.trades ?? []).length})`}><div className="overflow-auto max-h-80"><table className="tbl"><thead><tr><th>Time</th><th>Market</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Fair</th><th className="num">Net EV</th></tr></thead>
                <tbody>{(r.trades ?? []).slice(0, 500).map((t: any, i: number) => <tr key={i}><td className="text-ink3">{t.ts.slice(5, 16)}</td><td className="max-w-[300px] truncate">{t.question}</td><td>{t.action} {t.outcome}</td><td className="num">{t.qty}</td><td className="num">{t.price}</td><td className="num">{t.fair}</td><td className="num">{t.net_ev}</td></tr>)}</tbody></table></div></Panel>
            </>)}
        </div>
      </div>
    </div>
  );
}
