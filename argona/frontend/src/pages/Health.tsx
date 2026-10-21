import { useState } from "react";
import { ago, api, usePoll } from "../api";
import { Badge, Empty, ErrorLine, KV, Panel } from "../ui";

export default function Health() {
  const d = usePoll<any>("/health/details", 5000);
  const m = usePoll<any>("/models/metrics", 30000);
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (j: string) => { try { await api(`/control/run-job/${j}`, { method: "POST" }); setMsg(`${j} executed`); d.reload(); } catch (e: any) { setMsg(`${j}: ${e.message}`); } };
  const h = d.data;
  if (!h) return <div className="text-ink3 p-4">{d.error ?? "Loading…"}</div>;
  return (
    <div className="space-y-3">
      {msg && <div className="panel px-3 py-2 text-[11.5px]">{msg}</div>}
      <ErrorLine error={d.error} />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Panel title="Backend"><div className="p-3"><KV rows={[["Status", <Badge tone="up">{h.backend.status}</Badge>], ["Instance", h.backend.instance],
          ["Read-only", h.backend.read_only ? <Badge tone="down">yes (lock held elsewhere)</Badge> : "no"], ["Live switch", h.execution.live_trading_enabled ? "enabled" : "disabled"]]} /></div></Panel>
        <Panel title="Database"><div className="p-3"><KV rows={[["Status", h.database.ok ? <Badge tone="up">ok</Badge> : <Badge tone="down">{h.database.error}</Badge>], ["Engine", h.database.url_kind], ["Events stored", h.database.events]]} /></div></Panel>
        <Panel title="Execution"><div className="p-3"><KV rows={[["Stats", JSON.stringify(h.execution.stats)], ["Live venues", Object.keys(h.execution.live).length ? JSON.stringify(h.execution.live) : "none verified"],
          ["LLM", h.llm.enabled ? `${h.llm.model} · ${h.llm.calls} calls · ${h.llm.errors} errors` : "disabled"]]} /></div></Panel>
      </div>
      <Panel title={`Scheduler — started ${ago(h.scheduler.started_at)} ago`} right={h.scheduler.running ? <Badge tone="up">running</Badge> : <Badge tone="down">stopped</Badge>}>
        <div className="overflow-auto"><table className="grid"><thead><tr><th>Job</th><th className="num">Interval</th><th>State</th><th className="num">Runs</th><th className="num">Failures</th><th>Last OK</th><th className="num">Duration</th><th>Next</th><th>Last error</th><th /></tr></thead>
          <tbody>{h.scheduler.jobs.map((j: any) => (
            <tr key={j.name}><td>{j.name}</td><td className="num">{j.interval_s}s</td>
              <td>{!j.enabled ? <Badge>disabled</Badge> : j.running ? <Badge tone="series">running</Badge> : j.consecutive_failures ? <Badge tone="down">backoff ×{j.consecutive_failures}</Badge> : <Badge tone="up">ok</Badge>}</td>
              <td className="num">{j.runs}</td><td className="num">{j.failures}</td><td>{ago(j.last_ok)}</td><td className="num">{j.last_duration_ms ?? "—"}ms</td>
              <td className="text-ink3">{j.next_run ? new Date(j.next_run).toISOString().slice(11, 19) : "—"}</td>
              <td className="max-w-[260px] truncate text-down" title={j.last_error ?? ""}>{j.last_error}</td>
              <td><button className="btn" onClick={() => run(j.name)}>Run now</button></td></tr>))}</tbody></table></div>
      </Panel>
      <Panel title="Market-data connectivity">
        <table className="grid"><thead><tr><th>Venue</th><th>Connected</th><th className="num">Markets</th><th>Last sync</th><th className="num">Requests</th><th className="num">Errors</th><th className="num">429s</th><th>Last error</th><th>Notes</th></tr></thead>
          <tbody>{h.providers.map((p: any) => (
            <tr key={p.venue}><td>{p.venue}{p.is_simulated && <> <Badge tone="sim">sim</Badge></>}</td><td>{p.connected ? <Badge tone="up">yes</Badge> : <Badge tone="down">no</Badge>}</td>
              <td className="num">{p.markets}</td><td>{ago(p.last_sync)}</td><td className="num">{p.http.requests}</td><td className="num">{p.http.errors}</td><td className="num">{p.http.rate_limited}</td>
              <td className="max-w-[260px] truncate text-down" title={p.last_error ?? p.http.last_error ?? ""}>{p.last_error ?? p.http.last_error}</td>
              <td className="max-w-[300px] truncate text-ink3" title={p.capabilities.notes.join(" ")}>{p.capabilities.notes[0]}</td></tr>))}</tbody></table>
        {h.rate_limit_warnings.length > 0 && <div className="px-3 py-2 text-amber">{h.rate_limit_warnings.join(" · ")}</div>}
      </Panel>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Agent failures">
          <table className="grid"><tbody>{h.agents.map((a: any) => (
            <tr key={a.name}><td>{a.name}</td><td className="num">{a.errors}</td><td className="max-w-[360px] truncate text-down">{a.last_error ?? ""}</td></tr>))}</tbody></table>
        </Panel>
        <Panel title="Model calibration (resolved markets, point-in-time)">
          {(m.data?.metrics ?? []).length === 0 ? <Empty>No resolved markets with prior estimates yet. Calibration needs markets to resolve after ARGONA recorded estimates ≥1h before resolution.</Empty> : (
            <table className="grid"><thead><tr><th>Model</th><th>Category</th><th className="num">n</th><th className="num">Brier</th><th className="num">Market Brier</th><th className="num">Log loss</th></tr></thead>
              <tbody>{m.data.metrics.map((x: any, i: number) => <tr key={i}><td>{x.model}</td><td>{x.category}</td><td className="num">{x.n}</td><td className="num">{x.brier?.toFixed(4) ?? "—"}</td><td className="num">{x.market_brier?.toFixed(4) ?? "—"}</td><td className="num">{x.log_loss?.toFixed(4) ?? "—"}</td></tr>)}</tbody></table>)}
        </Panel>
      </div>
      <Panel title="Recent warnings & errors">
        <div className="overflow-auto max-h-80"><table className="grid"><tbody>{h.recent_errors.map((e: any) => (
          <tr key={e.id}><td className="text-ink3">{ago(e.ts)}</td><td><Badge tone={e.severity === "critical" ? "down" : "amber"}>{e.severity}</Badge></td><td>{e.type}</td><td className="whitespace-normal">{e.message}</td></tr>))}</tbody></table>
          {!h.recent_errors.length && <Empty>No warnings.</Empty>}</div>
      </Panel>
    </div>
  );
}
