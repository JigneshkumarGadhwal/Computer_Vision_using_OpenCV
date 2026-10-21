import { useMemo, useState } from "react";
import { type ArgonaEvent, ago, hhmmss, pct, signedUsd, usd, usePoll } from "../api";
import { EquityChart } from "../charts";
import { Badge, Panel, Stat } from "../ui";

const TYPE_STYLE: Record<string, { tag: string; cls: string }> = {
  market_discovered: { tag: "DISCOVER", cls: "text-ink2" }, market_updated: { tag: "MARKET", cls: "text-ink2" },
  market_rejected: { tag: "REJECT", cls: "text-ink3" }, research_completed: { tag: "RESEARCH", cls: "text-series" },
  probability_updated: { tag: "PROB", cls: "text-series" }, opportunity_qualified: { tag: "EDGE", cls: "text-amber" },
  opportunity_rejected: { tag: "REJECT", cls: "text-ink3" }, opportunity_expired: { tag: "EXPIRED", cls: "text-ink3" },
  risk_approved: { tag: "RISK ✓", cls: "text-up" }, risk_rejected: { tag: "RISK ✗", cls: "text-down" },
  order_submitted: { tag: "ORDER", cls: "text-amber" }, order_acknowledged: { tag: "ACK", cls: "text-amber" },
  order_filled: { tag: "FILL", cls: "text-up" }, order_partially_filled: { tag: "PARTIAL", cls: "text-up" },
  order_canceled: { tag: "CANCEL", cls: "text-ink2" }, order_expired: { tag: "EXPIRED", cls: "text-ink3" },
  order_rejected: { tag: "REJECTED", cls: "text-down" }, order_unknown: { tag: "UNKNOWN", cls: "text-down" },
  market_resolved: { tag: "RESOLVED", cls: "text-ink" }, pnl_realized: { tag: "P&L", cls: "text-amber" },
  provider_disconnected: { tag: "PROVIDER", cls: "text-down" }, provider_recovered: { tag: "PROVIDER", cls: "text-up" },
  emergency_stop: { tag: "E-STOP", cls: "text-down" }, risk_limit_breached: { tag: "LIMIT", cls: "text-down" },
  mode_changed: { tag: "MODE", cls: "text-amber" }, cycle_completed: { tag: "CYCLE", cls: "text-ink3" },
};

const FILTERS: Record<string, string[] | null> = {
  all: null,
  trades: ["order_submitted", "order_acknowledged", "order_filled", "order_partially_filled", "order_canceled", "order_expired", "order_rejected", "order_unknown", "pnl_realized", "market_resolved"],
  decisions: ["opportunity_qualified", "opportunity_rejected", "opportunity_expired", "risk_approved", "risk_rejected", "probability_updated", "research_completed"],
  system: ["provider_disconnected", "provider_recovered", "emergency_stop", "risk_limit_breached", "mode_changed", "system", "scheduler_error", "settings_changed", "trading_paused", "trading_resumed", "reconciliation", "reconciliation_mismatch"],
};

export function ActivityLog({ events, height = 460 }: { events: ArgonaEvent[]; height?: number }) {
  const [f, setF] = useState("all");
  const list = useMemo(() => (FILTERS[f] ? events.filter((e) => FILTERS[f]!.includes(e.type)) : events), [events, f]);
  return (
    <Panel title="Live activity log" right={
      <span className="flex gap-1">{Object.keys(FILTERS).map((k) => (
        <button key={k} onClick={() => setF(k)} className={`px-1.5 text-[10px] uppercase ${f === k ? "text-amber" : "text-ink3"}`}>{k}</button>))}</span>}>
      <div className="overflow-auto font-mono text-[11px] leading-[1.55]" style={{ height }}>
        {list.length === 0 && <div className="p-4 text-ink3">Waiting for backend events…</div>}
        {list.map((e, i) => {
          const st = TYPE_STYLE[e.type] ?? { tag: e.type.toUpperCase().slice(0, 9), cls: "text-ink2" };
          const sev = e.severity === "critical" ? "bg-down/10" : e.severity === "warning" ? "bg-amber/5" : "";
          return (
            <div key={(e.id ?? "m") + e.ts + i} className={`grid grid-cols-[62px_74px_1fr] gap-2 px-3 py-[1px] border-b border-[#12161b] ${sev}`}>
              <span className="text-ink3 tabular">{hhmmss(e.ts)}</span>
              <span className={`${st.cls} truncate`}>{st.tag}</span>
              <span className="text-ink min-w-0 break-words">
                {e.agent && <span className="text-ink3">{e.agent} › </span>}{e.message}
                {e.data?.simulated === true && <span className="text-sim"> [sim]</span>}
              </span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export default function Overview({ ov, events }: { ov: any; events: ArgonaEvent[] }) {
  const eq = usePoll<any[]>(`/portfolio/equity?account=${ov?.account ?? "paper"}`, 30000, [ov?.account]);
  if (!ov) return <div className="text-ink3 p-6">Loading…</div>;
  const pf = ov.portfolio ?? {};
  const perf = ov.performance ?? {};
  const sc = ov.scanner ?? {};
  const isPaper = ov.mode !== "LIVE";
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <Stat label={`Equity ${isPaper ? "(paper)" : "(live)"}`} value={usd(pf.equity)} sub={`start ${usd(pf.starting_capital, 0)}`} />
        <Stat label="Available capital" value={usd(pf.available)} sub={`reserved ${usd(pf.reserved)}`} />
        <Stat label="Day net P&L" value={signedUsd(pf.day_pnl)} tone={(pf.day_pnl ?? 0) > 0 ? "up" : (pf.day_pnl ?? 0) < 0 ? "down" : undefined} sub="realized + unrealized since 00:00 UTC" />
        <Stat label="Realized P&L" value={signedUsd(pf.realized_pnl)} tone={(pf.realized_pnl ?? 0) > 0 ? "up" : (pf.realized_pnl ?? 0) < 0 ? "down" : undefined} sub={`unrealized ${signedUsd(pf.unrealized_pnl)}`} />
        <Stat label="Exposure" value={usd(pf.exposure)} sub={pf.equity ? `${pct(pf.exposure / pf.equity)} of equity` : undefined} />
        <Stat label="Win rate · drawdown" value={<>{pct(perf.win_rate, 0)} <span className="text-ink3 text-[13px]">· {pct(pf.drawdown)}</span></>} sub={`${perf.trades ?? 0} closed trades · PF ${perf.profit_factor == null ? "—" : Number(perf.profit_factor).toFixed(2)}`} />
        <Stat label="Open positions" value={pf.positions?.length ?? 0} sub={`fees paid ${usd(pf.fees_paid)}`} />
        <Stat label="Active orders" value={pf.open_orders ?? 0} />
        <Stat label="Markets scanned" value={sc.evaluated ?? 0} sub={`${sc.eligible ?? 0} eligible / ${sc.open ?? 0} open / ${sc.discovered ?? 0} known`} />
        <Stat label="Estimated" value={sc.estimated ?? 0} sub={`${(sc.evaluated ?? 0) - (sc.estimated ?? 0)} abstained (no independent model)`} />
        <Stat label="Qualified now · 24h" value={<>{sc.qualified ?? 0} <span className="text-ink3 text-[13px]">· {sc.qualified_24h ?? 0}</span></>} tone="amber" sub={`rejected open markets: ${sc.rejected ?? 0}`} />
        <Stat label="Last scan" value={ago(sc.last_scan)} sub={`discovery ${ago(sc.last_discovery)} ago`} />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-[1.25fr_1fr] gap-3">
        <div className="space-y-3 min-w-0">
          <Panel title={`Portfolio equity — ${ov.account}`} right={isPaper && <Badge tone="amber">simulated</Badge>}>
            <div className="p-2"><EquityChart data={eq.data ?? []} start={pf.starting_capital} /></div>
          </Panel>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Panel title="Agents">
              <table className="tbl"><tbody>
                {(ov.agents ?? []).map((a: any) => (
                  <tr key={a.name}><td className="text-amber">{a.name}</td>
                    <td>{a.status === "error" ? <Badge tone="down">error</Badge> : a.status === "running" ? <Badge tone="series">running</Badge> : <Badge>idle</Badge>}</td>
                    <td className="text-ink3">{ago(a.last_run)} ago</td><td className="num text-ink3">{a.errors ? `${a.errors} err` : ""}</td></tr>))}
              </tbody></table>
            </Panel>
            <Panel title="Data providers">
              <table className="tbl"><tbody>
                {(ov.providers ?? []).map((p: any) => (
                  <tr key={p.venue}><td>{p.venue} {p.is_simulated && <Badge tone="sim">sim</Badge>}</td>
                    <td>{p.connected ? <Badge tone="up">connected</Badge> : <Badge tone="down">down</Badge>}</td>
                    <td className="num">{p.markets}</td><td className="text-ink3">{ago(p.last_sync)}</td></tr>))}
                {(ov.providers ?? []).some((p: any) => p.last_error) && (
                  <tr><td colSpan={4} className="text-down whitespace-normal text-[10.5px]">{ov.providers.filter((p: any) => p.last_error).map((p: any) => `${p.venue}: ${p.last_error}`).join(" · ")}</td></tr>)}
              </tbody></table>
            </Panel>
          </div>
        </div>
        <ActivityLog events={events} height={520} />
      </div>
    </div>
  );
}
