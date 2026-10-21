import { useState } from "react";
import { ago, api, cents, px, usd, usePoll } from "../api";
import { DepthChart, SpreadHistory } from "../charts";
import { Badge, Empty, ErrorLine, Panel, SimBadge } from "../ui";

export default function SpreadPage() {
  const d = usePoll<any>("/spread", 6000);
  const opps = usePoll<any[]>("/opportunities?hours=6&limit=150", 8000);
  const [sel, setSel] = useState<string | null>(null);
  const stats: any[] = d.data?.stats ?? [];
  const cur = stats.find((s) => s.key === sel) ?? stats[0];
  const detail = usePoll<any>(cur ? `/markets/${encodeURIComponent(cur.key)}/detail` : null, 8000, [cur?.key]);
  const linkAct = async (id: number, status: string) => { await api(`/links/${id}`, { method: "POST", json: { status } }); d.reload(); };
  const qualified = (opps.data ?? []).filter((o) => ["qualified", "approved", "executed"].includes(o.status));
  const rejected = (opps.data ?? []).filter((o) => ["rejected", "vetoed", "expired"].includes(o.status));
  return (
    <div className="space-y-3">
      <ErrorLine error={d.error} />
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_1.1fr] gap-3">
        <Panel title="Bid / ask spreads (this cycle)" right={<span className="text-ink3">{stats.length} books</span>}>
          <div className="overflow-auto max-h-[520px]">
            <table className="tbl"><thead><tr><th>Market</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spread</th><th className="num">Bid $ depth</th><th className="num">Ask $ depth</th><th className="num">Imbal.</th><th>Flags</th></tr></thead>
              <tbody>{stats.map((s) => (
                <tr key={s.key} onClick={() => setSel(s.key)} className={`cursor-pointer ${cur?.key === s.key ? "bg-[#161b22]" : ""}`}>
                  <td className="max-w-[300px] truncate" title={s.question}>{s.question} {s.is_simulated && <SimBadge />}</td>
                  <td className="num text-up">{px(s.best_bid, 2)}</td><td className="num text-down">{px(s.best_ask, 2)}</td><td className="num">{cents(s.spread)}</td>
                  <td className="num">{usd(s.bid_depth_usd, 0)}</td><td className="num">{usd(s.ask_depth_usd, 0)}</td>
                  <td className="num">{s.imbalance == null ? "—" : `${s.imbalance > 0 ? "+" : ""}${(s.imbalance * 100).toFixed(0)}%`}</td>
                  <td>{s.flags.map((f: string) => <Badge key={f} tone={f === "wide_spread" ? "amber" : "muted"}>{f.replace("_", " ")}</Badge>)}</td></tr>))}</tbody></table>
            {!stats.length && <Empty>No books evaluated yet.</Empty>}
          </div>
        </Panel>
        <div className="space-y-3 min-w-0">
          <Panel title={cur ? `Depth — ${cur.question}` : "Depth"}>
            <div className="p-2"><DepthChart book={detail.data?.book ?? null} height={230} /></div>
            <div className="px-3 pb-2 text-[10.5px] text-ink3">Cumulative contracts. YES bids left of the mid, YES asks right. NO prices are the complement (NO ask = 1 − YES bid).</div>
          </Panel>
          <Panel title="Spread history"><div className="p-2"><SpreadHistory data={cur?.history ?? []} /></div></Panel>
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Arbitrage screen" right={<span className="text-ink3">never labelled risk-free: legs fill sequentially</span>}>
          {(d.data?.arbitrage ?? []).length === 0 ? <Empty>No structural or cross-venue price inconsistencies that survive fees and depth this cycle.</Empty> : (
            <table className="tbl"><thead><tr><th>Kind</th><th className="num">Cost+fees</th><th className="num">Payout</th><th className="num">Net/set</th><th className="num">Max sets</th><th>Risk flags</th></tr></thead>
              <tbody>{d.data.arbitrage.map((a: any, i: number) => (
                <tr key={i} title={a.explanation}><td>{a.kind}</td><td className="num">{(a.cost + a.fees).toFixed(4)}</td><td className="num">{a.guaranteed_payout.toFixed(2)}</td>
                  <td className="num text-up">+{a.net_profit_per_set.toFixed(4)}</td><td className="num">{a.max_sets.toFixed(0)}</td><td className="whitespace-normal">{a.risk_flags.join(", ")}</td></tr>))}</tbody></table>)}
        </Panel>
        <Panel title="Related-market comparisons (cross-venue links)" right={<span className="text-ink3">operator confirmation required</span>}>
          {(d.data?.comparisons ?? []).length === 0 ? <Empty>No candidate links yet. The maintenance job proposes cross-venue pairs (same numbers, similar titles, close times within 36h).</Empty> : (
            <div className="overflow-auto max-h-72"><table className="tbl"><thead><tr><th>A</th><th className="num">mid</th><th>B</th><th className="num">mid</th><th className="num">Δ</th><th>Status</th><th /></tr></thead>
              <tbody>{d.data.comparisons.map((c: any) => (
                <tr key={c.id}><td className="max-w-[200px] truncate" title={c.a_question}>{c.a_question}</td><td className="num">{px(c.a_mid, 2)}</td>
                  <td className="max-w-[200px] truncate" title={c.b_question}>{c.b_question}</td><td className="num">{px(c.b_mid, 2)}</td>
                  <td className="num">{c.a_mid != null && c.b_mid != null ? cents(Math.abs(c.a_mid - c.b_mid)) : "—"}</td>
                  <td><Badge tone={c.status === "confirmed" ? "up" : c.status === "rejected" ? "down" : "muted"}>{c.status}</Badge></td>
                  <td className="flex gap-1">{c.status !== "confirmed" && <button className="btn" onClick={() => linkAct(c.id, "confirmed")}>Confirm</button>}
                    {c.status !== "rejected" && <button className="btn" onClick={() => linkAct(c.id, "rejected")}>Reject</button>}</td></tr>))}</tbody></table></div>)}
        </Panel>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <OppTable title={`Qualified opportunities (6h) — ${qualified.length}`} rows={qualified} />
        <OppTable title={`Rejected / vetoed / expired (6h) — ${rejected.length}`} rows={rejected} />
      </div>
    </div>
  );
}

function OppTable({ title, rows }: { title: string; rows: any[] }) {
  return (
    <Panel title={title}>
      <div className="overflow-auto max-h-96">
        <table className="tbl"><thead><tr><th>Age</th><th>Market</th><th>Side</th><th className="num">Fair</th><th className="num">Entry</th><th className="num">Gross</th><th className="num">Fee</th><th className="num">Net EV</th><th className="num">Conf</th><th>Status / why</th></tr></thead>
          <tbody>{rows.map((o) => (
            <tr key={o.id}><td className="text-ink3">{ago(o.ts)}</td><td className="max-w-[220px] truncate" title={o.question}>{o.question}</td>
              <td>{o.action} {o.outcome}</td><td className="num">{px(o.exit_value)}</td><td className="num">{px(o.entry_price)}</td>
              <td className="num">{cents(o.gross_edge)}</td><td className="num">{cents(o.fee_per_contract)}</td>
              <td className={`num ${o.net_ev_per_contract > 0 ? "text-up" : "text-down"}`}>{cents(o.net_ev_per_contract)}</td>
              <td className="num">{o.confidence?.toFixed(2)}</td>
              <td className="max-w-[260px] truncate" title={(o.reasons ?? []).join(" | ")}><Badge tone={o.status === "executed" ? "up" : o.status === "approved" || o.status === "qualified" ? "amber" : "muted"}>{o.status}</Badge> <span className="text-ink3">{(o.reasons ?? []).slice(-1)[0]}</span></td></tr>))}</tbody></table>
        {!rows.length && <Empty>None in window.</Empty>}
      </div>
    </Panel>
  );
}
