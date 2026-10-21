import { useState } from "react";
import { ago, api, pct, px, signedUsd, until, usd, usePoll } from "../api";
import { EquityChart, ExposureBars } from "../charts";
import { Badge, Empty, ErrorLine, KV, Modal, Panel, Signed, SimBadge, Stat } from "../ui";

function Trace({ id, onClose }: { id: number; onClose: () => void }) {
  const d = usePoll<any>(`/orders/${id}/trace`, 0, [id]);
  const t = d.data;
  return (
    <Modal title={`Order #${id} — audit trail`} onClose={onClose}>
      {!t ? <div className="text-ink3">Loading…</div> : (
        <div className="space-y-4 text-[11px]">
          <KV rows={[
            ["Market", t.market?.question], ["Client order id", t.order.client_order_id], ["Venue order id", t.order.venue_order_id ?? "—"],
            ["Order", `${t.order.action} ${t.order.quantity} ${t.order.outcome} limit ${t.order.limit_price} · ${t.order.status}${t.order.is_simulated ? " · SIMULATED" : ""}`],
            ["Filled", `${t.order.filled_qty} @ ${px(t.order.avg_fill_price)} · fees ${usd(t.order.fees, 4)}`],
            ["Model", t.estimate ? `${t.estimate.model} v${t.estimate.model_version} → P(YES)=${px(t.estimate.q_yes)} (mkt ${px(t.estimate.market_mid)}, conf ${t.estimate.confidence})` : "—"],
            ["Opportunity", t.opportunity ? `net EV ${px(t.opportunity.net_ev_per_contract, 4)}/contract, gross ${px(t.opportunity.gross_edge, 4)}, fee ${px(t.opportunity.fee_per_contract, 4)}` : "—"],
            ["Risk", t.risk ? `${t.risk.approved ? "APPROVED" : "VETOED"} ${t.risk.contracts} ct, stake ${usd(t.risk.stake_usd)}, kelly raw ${px(t.risk.kelly_raw, 4)} → applied ${px(t.risk.kelly_applied, 4)}` : "—"],
            ["Snapshot", t.snapshot ? `#${t.snapshot.id} at ${t.snapshot.ts} (${t.snapshot.source})` : "—"],
          ]} />
          {t.risk && <div><div className="text-ink3 uppercase text-[10px] mb-1">Risk checks</div>
            {t.risk.checks.map((c: any) => <div key={c.name}><span className={c.passed ? "text-up" : "text-down"}>{c.passed ? "✓" : "✗"}</span> {c.name} <span className="text-ink3">{c.detail}</span></div>)}</div>}
          <div><div className="text-ink3 uppercase text-[10px] mb-1">Evidence</div>{t.evidence.length ? t.evidence.map((e: any) => <div key={e.id}>{e.source}: {e.summary} <span className="text-ink3">({e.observed_at})</span></div>) : "—"}</div>
          <div><div className="text-ink3 uppercase text-[10px] mb-1">Fills</div>{t.fills.length ? t.fills.map((f: any) => <div key={f.id}>{f.ts} {f.action} {f.qty} @ {f.price} fee {f.fee} {f.is_simulated && <SimBadge />}</div>) : "none"}</div>
          {t.estimate?.supporting?.length > 0 && <div><div className="text-ink3 uppercase text-[10px] mb-1">Supporting</div>{t.estimate.supporting.map((s: string, i: number) => <div key={i}>• {s}</div>)}</div>}
          {t.estimate?.conflicting?.length > 0 && <div><div className="text-ink3 uppercase text-[10px] mb-1">Conflicting</div>{t.estimate.conflicting.map((s: string, i: number) => <div key={i} className="text-amber">• {s}</div>)}</div>}
        </div>)}
    </Modal>
  );
}

export default function Portfolio({ account }: { account?: string }) {
  const acct = account ?? "paper";
  const p = usePoll<any>(`/portfolio?account=${acct}`, 5000, [acct]);
  const orders = usePoll<any[]>(`/orders?account=${acct}&limit=200`, 5000, [acct]);
  const eq = usePoll<any[]>(`/portfolio/equity?account=${acct}`, 30000, [acct]);
  const ledger = usePoll<any[]>(`/portfolio/ledger?account=${acct}&limit=300`, 10000, [acct]);
  const [trace, setTrace] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const st = p.data?.state;
  const perf = p.data?.performance ?? {};
  const working = (orders.data ?? []).filter((o) => ["PENDING_SUBMIT", "OPEN", "PARTIALLY_FILLED", "UNKNOWN"].includes(o.status));
  const cancel = async (id: number) => { try { await api(`/orders/${id}/cancel`, { method: "POST" }); setMsg(`order ${id} canceled`); orders.reload(); } catch (e: any) { setMsg(e.message); } };
  return (
    <div className="space-y-3">
      <ErrorLine error={p.error || msg} />
      {st && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
          <Stat label={`Equity · ${acct}`} value={usd(st.equity)} />
          <Stat label="Cash" value={usd(st.cash)} sub={`available ${usd(st.available)}`} />
          <Stat label="Reserved (orders)" value={usd(st.reserved)} />
          <Stat label="Positions value" value={usd(st.positions_value)} />
          <Stat label="Realized" value={signedUsd(st.realized_pnl)} tone={st.realized_pnl > 0 ? "up" : st.realized_pnl < 0 ? "down" : undefined} />
          <Stat label="Unrealized" value={signedUsd(st.unrealized_pnl)} tone={st.unrealized_pnl > 0 ? "up" : st.unrealized_pnl < 0 ? "down" : undefined} sub="marked at best bid" />
          <Stat label="Fees paid" value={usd(st.fees_paid)} />
          <Stat label="Drawdown" value={pct(st.drawdown)} sub={`peak ${usd(st.peak_equity)}`} />
        </div>)}
      <div className="grid grid-cols-1 xl:grid-cols-[1.4fr_1fr] gap-3">
        <Panel title="Equity curve"><div className="p-2"><EquityChart data={eq.data ?? []} start={st?.starting_capital} height={240} /></div></Panel>
        <Panel title="Capital at risk by category"><div className="p-2">{st && <ExposureBars data={st.exposure_by_category} equity={st.equity} />}</div></Panel>
      </div>
      <Panel title="Performance">
        <table className="tbl"><thead><tr><th>Window</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">Net realized</th><th className="num">Gross profit</th><th className="num">Gross loss</th><th className="num">Profit factor</th><th className="num">Avg return</th><th className="num">Fees</th><th className="num">Slippage</th><th className="num">Max DD</th></tr></thead>
          <tbody>{Object.entries(perf).map(([w, v]: any) => (
            <tr key={w}><td>{w}</td><td className="num">{v.trades}</td><td className="num">{pct(v.win_rate, 0)}</td><td className="num"><Signed v={v.net_realized}>{signedUsd(v.net_realized)}</Signed></td>
              <td className="num">{usd(v.gross_profit)}</td><td className="num">{usd(v.gross_loss)}</td><td className="num">{v.profit_factor == null ? "—" : Number(v.profit_factor).toFixed(2)}</td>
              <td className="num">{pct(v.avg_return_per_trade)}</td><td className="num">{usd(v.fees)}</td><td className="num">{usd(v.slippage)}</td><td className="num">{pct(v.max_drawdown)}</td></tr>))}</tbody></table>
      </Panel>
      <Panel title={`Open positions — ${st?.positions?.length ?? 0}`}>
        <div className="overflow-auto">
          <table className="tbl"><thead><tr><th>Market</th><th>Side</th><th className="num">Contracts</th><th className="num">Avg cost</th><th className="num">Mark (bid)</th><th className="num">Cost basis</th><th className="num">Value</th><th className="num">Unrealized</th><th>Category</th><th className="num">Resolves</th></tr></thead>
            <tbody>{(st?.positions ?? []).map((x: any) => (
              <tr key={x.id}><td className="max-w-[360px] truncate" title={x.question}>{x.question} {x.is_simulated && <SimBadge />}</td><td>{x.outcome}</td>
                <td className="num">{x.contracts}</td><td className="num">{px(x.avg_price)}</td><td className="num">{px(x.mark)}</td><td className="num">{usd(x.cost_basis)}</td>
                <td className="num">{usd(x.value)}</td><td className="num"><Signed v={x.unrealized}>{signedUsd(x.unrealized)}</Signed></td><td>{x.category}</td><td className="num">{until(x.close_time)}</td></tr>))}</tbody></table>
          {!st?.positions?.length && <Empty>No open positions.</Empty>}
        </div>
      </Panel>
      <Panel title={`Working orders — ${working.length}`}>
        <table className="tbl"><thead><tr><th>#</th><th>Market</th><th>Order</th><th className="num">Filled</th><th>Status</th><th>Age</th><th /></tr></thead>
          <tbody>{working.map((o) => (
            <tr key={o.id}><td>{o.id}</td><td className="max-w-[340px] truncate">{o.question}</td><td>{o.action} {o.quantity} {o.outcome} ≤{px(o.limit_price)}</td>
              <td className="num">{o.filled_qty}</td><td><Badge tone={o.status === "UNKNOWN" ? "down" : "amber"}>{o.status}</Badge></td><td>{ago(o.created_at)}</td>
              <td className="flex gap-1"><button className="btn" onClick={() => setTrace(o.id)}>Trace</button><button className="btn" onClick={() => cancel(o.id)}>Cancel</button></td></tr>))}</tbody></table>
        {!working.length && <Empty>No working orders.</Empty>}
      </Panel>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Order history">
          <div className="overflow-auto max-h-96"><table className="tbl"><thead><tr><th>#</th><th>Time</th><th>Market</th><th>Order</th><th className="num">Avg fill</th><th>Status</th><th /></tr></thead>
            <tbody>{(orders.data ?? []).map((o) => (
              <tr key={o.id}><td>{o.id}</td><td className="text-ink3">{ago(o.created_at)}</td><td className="max-w-[220px] truncate" title={o.question}>{o.question}</td>
                <td>{o.action} {o.filled_qty}/{o.quantity} {o.outcome}</td><td className="num">{px(o.avg_fill_price)}</td>
                <td><Badge tone={o.status === "FILLED" ? "up" : o.status === "REJECTED" ? "down" : "muted"}>{o.status}</Badge> {o.is_simulated && <SimBadge />}</td>
                <td><button className="btn" onClick={() => setTrace(o.id)}>Trace</button></td></tr>))}</tbody></table></div>
        </Panel>
        <Panel title="Trade ledger (fills & settlements)">
          <div className="overflow-auto max-h-96"><table className="tbl"><thead><tr><th>Time</th><th>Type</th><th>Market</th><th>Action</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Fee</th><th className="num">Cash Δ</th><th className="num">Realized</th></tr></thead>
            <tbody>{(ledger.data ?? []).map((r, i) => (
              <tr key={i}><td className="text-ink3">{ago(r.ts)}</td><td>{r.type}{r.simulated && <> <SimBadge /></>}</td><td className="max-w-[200px] truncate" title={r.question}>{r.question}</td>
                <td>{r.action} {r.outcome}</td><td className="num">{r.qty}</td><td className="num">{px(r.price)}</td><td className="num">{usd(r.fee, 4)}</td>
                <td className="num"><Signed v={r.cash_delta}>{signedUsd(r.cash_delta)}</Signed></td><td className="num">{r.realized_pnl == null ? "" : <Signed v={r.realized_pnl}>{signedUsd(r.realized_pnl)}</Signed>}</td></tr>))}</tbody></table>
            {!(ledger.data ?? []).length && <Empty>No fills yet.</Empty>}</div>
        </Panel>
      </div>
      {trace != null && <Trace id={trace} onClose={() => setTrace(null)} />}
    </div>
  );
}
