import { useState } from "react";
import { ago, cents, pct, px, until, usd, usePoll } from "../api";
import { DepthChart, MidHistory } from "../charts";
import { Badge, Empty, ErrorLine, KV, Modal, Panel, SimBadge } from "../ui";

export function MarketDetail({ k, onClose }: { k: string; onClose: () => void }) {
  const d = usePoll<any>(`/markets/${encodeURIComponent(k)}/detail`, 10000, [k]);
  const m = d.data?.market;
  return (
    <Modal title={m ? m.question : k} onClose={onClose}>
      {!d.data ? <div className="text-ink3">Loading…</div> : (
        <div className="space-y-4">
          <KV rows={[
            ["Market", <span>{m.key} {m.is_simulated && <SimBadge />}</span>], ["Category", m.category], ["Status", `${m.status}${m.eligible ? " · eligible" : ""}`],
            ["Closes", `${m.close_time ?? "—"} (${until(m.close_time)})`], ["Flags", (m.flags ?? []).join(", ") || "none"],
            ["Fees", JSON.stringify(m.fee_model)], ["Tick / min size", `${m.tick_size} / ${m.min_order_size}`],
            ["Resolution source", m.resolution_source || "—"],
            ["Book age", d.data.book_age_s != null ? `${d.data.book_age_s}s` : "no live book"],
          ]} />
          <details open><summary className="text-ink3 uppercase text-[10px] tracking-wider cursor-pointer">Resolution criteria</summary>
            <pre className="whitespace-pre-wrap text-[11px] text-ink2 mt-1 max-h-48 overflow-auto">{m.rules || "(none provided)"}</pre></details>
          <div className="grid md:grid-cols-2 gap-3">
            <Panel title="Order book depth (YES)"><DepthChart book={d.data.book} height={200} /></Panel>
            <Panel title="Bid / ask / mid history"><MidHistory data={d.data.history} height={200} /></Panel>
          </div>
          <Panel title="Probability estimates (latest first)">
            <div className="overflow-auto max-h-64">
              <table className="tbl"><thead><tr><th>Time</th><th className="num">P(YES)</th><th className="num">Mkt mid</th><th className="num">σ</th><th className="num">Conf</th><th>Models / reason</th></tr></thead>
                <tbody>{d.data.estimates.map((e: any) => (
                  <tr key={e.id}><td className="text-ink3">{ago(e.ts)}</td><td className="num">{e.abstained ? "—" : px(e.q_yes)}</td><td className="num">{px(e.market_mid)}</td>
                    <td className="num">{px(e.sigma)}</td><td className="num">{e.confidence?.toFixed(2)}</td>
                    <td className="whitespace-normal text-[10.5px]">{e.abstained ? <span className="text-ink3">abstained: {e.reason}</span> :
                      (e.components ?? []).filter((c: any) => c.model).map((c: any) => `${c.model}=${c.q?.toFixed(3)}±${c.sigma?.toFixed(3)}`).join(" · ")}
                      {(e.conflicting ?? []).length > 0 && <div className="text-amber">⚠ {e.conflicting.join("; ")}</div>}</td></tr>))}</tbody></table>
            </div>
          </Panel>
          <Panel title="Evidence (provenance)">
            {d.data.evidence.length === 0 ? <Empty>No external evidence attached to recent estimates.</Empty> : (
              <table className="tbl"><tbody>{d.data.evidence.map((e: any) => (
                <tr key={e.id}><td>{e.kind}</td><td>{e.source} {e.is_simulated && <SimBadge />}</td><td className="text-ink3">{e.observed_at}</td>
                  <td><Badge tone={e.reliability === "high" ? "up" : e.reliability === "low" ? "down" : "muted"}>{e.reliability}</Badge></td>
                  <td className="whitespace-normal text-[10.5px]">{e.summary} <a className="text-series" href={e.source_url.startsWith("http") ? e.source_url : undefined} target="_blank" rel="noreferrer">source</a></td></tr>))}</tbody></table>)}
          </Panel>
        </div>
      )}
    </Modal>
  );
}

export default function Scanner() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [venue, setVenue] = useState("");
  const [eligibleOnly, setEligibleOnly] = useState(true);
  const [sel, setSel] = useState<string | null>(null);
  const q = new URLSearchParams({ limit: "300", ...(search && { search }), ...(category && { category }), ...(venue && { venue }), ...(eligibleOnly && { eligible: "true" }) });
  const d = usePoll<any>(`/markets?${q}`, 8000, [search, category, venue, eligibleOnly]);
  const items: any[] = d.data?.items ?? [];
  return (
    <Panel title={`Market scanner — ${d.data?.total ?? "…"} markets`} right={
      <div className="flex flex-wrap gap-2 items-center">
        <input className="input w-48" placeholder="Search question / ticker" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>
          {(d.data?.categories ?? []).map((c: string) => <option key={c} value={c}>{c}</option>)}</select>
        <select className="input" value={venue} onChange={(e) => setVenue(e.target.value)}><option value="">All venues</option>
          <option value="polymarket">Polymarket</option><option value="kalshi">Kalshi</option><option value="sim">SIM (simulated)</option></select>
        <label className="flex items-center gap-1 text-ink2"><input type="checkbox" checked={eligibleOnly} onChange={(e) => setEligibleOnly(e.target.checked)} /> eligible only</label>
      </div>}>
      <ErrorLine error={d.error} />
      <div className="overflow-auto max-h-[calc(100vh-230px)]">
        <table className="tbl">
          <thead><tr><th>Question</th><th>Cat</th><th>Venue</th><th className="num">YES bid/ask</th><th className="num">NO bid/ask</th><th className="num">Spread</th>
            <th className="num">Fair</th><th className="num">Net edge</th><th className="num">Liquidity</th><th className="num">Conf</th><th className="num">Fresh</th><th className="num">Closes</th><th>Status</th></tr></thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.key} className="cursor-pointer" onClick={() => setSel(m.key)}>
                <td className="max-w-[420px] truncate" title={m.question}>{m.question}{m.outcome_label && <span className="text-ink3"> · {m.outcome_label}</span>}</td>
                <td className="text-ink2">{m.category}</td>
                <td>{m.venue} {m.is_simulated && <SimBadge />}</td>
                <td className="num"><span className="text-up">{px(m.yes_bid, 2)}</span> / <span className="text-down">{px(m.yes_ask, 2)}</span></td>
                <td className="num"><span className="text-up">{px(m.no_bid, 2)}</span> / <span className="text-down">{px(m.no_ask, 2)}</span></td>
                <td className="num">{cents(m.spread)}</td>
                <td className="num">{m.fair_value != null ? px(m.fair_value) : <span className="text-ink3" title={m.estimate_reason ?? ""}>abst.</span>}</td>
                <td className="num">{m.net_edge != null ? <span className={m.net_edge > 0 ? "text-up" : "text-ink3"}>{m.net_edge > 0 ? "+" : ""}{cents(m.net_edge)} {m.edge_side}</span> : "—"}</td>
                <td className="num">{usd(m.liquidity, 0)}</td>
                <td className="num">{m.confidence != null ? pct(m.confidence, 0) : "—"}</td>
                <td className="num">{m.freshness_s != null ? <span className={m.freshness_s > 180 ? "text-down" : "text-ink2"}>{m.freshness_s.toFixed(0)}s</span> : <span className="text-ink3">—</span>}</td>
                <td className="num text-ink2">{until(m.close_time)}</td>
                <td>{m.opportunity_status ? <Badge tone={["qualified", "approved", "executed"].includes(m.opportunity_status) ? "amber" : "muted"}>{m.opportunity_status}</Badge>
                  : !m.eligible ? <span className="text-ink3 text-[10px]" title={(m.flags ?? []).join(", ")}>{(m.flags ?? [])[0] ?? "ineligible"}</span> : null}</td>
              </tr>))}
          </tbody>
        </table>
        {items.length === 0 && <Empty>No markets match. If this is a fresh install, wait for the first discovery run (Diagnostics → scheduler) — or the venues may be unreachable from this network.</Empty>}
      </div>
      {sel && <MarketDetail k={sel} onClose={() => setSel(null)} />}
    </Panel>
  );
}

