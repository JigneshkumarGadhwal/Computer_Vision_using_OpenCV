import { useEffect, useState } from "react";
import { ago, api, usePoll } from "../api";
import { Badge, ErrorLine, Modal, Panel } from "../ui";

const RISK_FIELDS: [string, string, number, string][] = [
  ["min_net_edge", "Min net edge ($/contract)", 0.005, "after fees, slippage & buffer"],
  ["min_confidence", "Min confidence", 0.05, "0–1"],
  ["kelly_fraction", "Fractional Kelly", 0.05, "0.25 = quarter-Kelly"],
  ["max_position_usd", "Max position ($)", 10, "per order"],
  ["max_daily_loss_usd", "Max daily loss ($)", 10, "blocks new entries"],
  ["max_drawdown_pct", "Max drawdown", 0.01, "fraction of peak equity"],
  ["max_market_exposure_pct", "Max per-market exposure", 0.01, "fraction of equity"],
  ["max_event_exposure_pct", "Max per-event exposure", 0.01, "correlated outcomes"],
  ["max_category_exposure_pct", "Max per-category exposure", 0.01, ""],
  ["max_total_exposure_pct", "Max total exposure", 0.05, ""],
  ["max_open_orders", "Max open orders", 1, ""],
  ["min_liquidity_usd", "Min liquidity ($ within 5¢)", 25, ""],
  ["max_spread", "Max spread", 0.01, ""],
  ["max_staleness_s", "Max data staleness (s)", 10, ""],
  ["min_hours_to_resolution", "Min hours to resolution", 0.25, ""],
  ["max_days_to_resolution", "Max days to resolution", 1, ""],
  ["slippage_buffer", "Slippage buffer ($/contract)", 0.001, ""],
  ["order_ttl_s", "Order TTL (s)", 30, ""],
  ["reentry_cooldown_s", "Re-entry cooldown (s)", 60, "per market"],
  ["market_prior_sigma", "Market prior σ", 0.01, "lower = defer more to market price"],
  ["min_exit_edge", "Min exit edge", 0.005, ""],
];

export default function Settings({ onChange }: { onChange: () => void }) {
  const s = usePoll<any>("/settings", 0);
  const hist = usePoll<any[]>("/settings/history?limit=30", 15000);
  const [rt, setRt] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [liveOpen, setLiveOpen] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [venue, setVenue] = useState("kalshi");
  useEffect(() => { if (s.data && !rt) setRt(structuredClone(s.data.runtime)); }, [s.data, rt]);
  if (!s.data || !rt) return <div className="text-ink3 p-4">Loading…</div>;
  const d = s.data;
  const mode = d.system.mode;
  const save = async () => {
    try {
      const r = await api("/settings", { method: "PUT", json: { categories: rt.categories, risk: rt.risk, notifications: rt.notifications, emergency_cancel_orders: rt.emergency_cancel_orders } });
      setRt(r); setMsg("Settings saved"); hist.reload(); onChange();
    } catch (e: any) { setMsg(`Error: ${e.message}`); }
  };
  const setMode = async (m: string, extra: any = {}) => {
    try { await api("/control/mode", { method: "POST", json: { mode: m, ...extra } }); setMsg(`Mode → ${m}`); s.reload(); onChange(); setLiveOpen(false); }
    catch (e: any) { setMsg(`Error: ${e.message}`); }
  };
  const test = async () => { try { const r = await api("/notifications/test", { method: "POST" }); setMsg(`Notification: ${JSON.stringify(r)}`); } catch (e: any) { setMsg(`Error: ${e.message}`); } };
  const verify = async (v: string) => { try { const r = await api(`/control/verify/${v}`, { method: "POST" }); setMsg(`${v}: ${r.verified ? "verified" : r.error}`); s.reload(); } catch (e: any) { setMsg(`Error: ${e.message}`); } };
  return (
    <div className="space-y-3">
      {msg && <div className="panel px-3 py-2 text-[11.5px]">{msg}</div>}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <Panel title="Trading mode">
          <div className="p-3 space-y-3 text-[11.5px]">
            <div>Current: {mode === "LIVE" ? <Badge tone="down">LIVE · {d.system.live_venue}</Badge> : mode === "PAPER" ? <Badge tone="amber">PAPER</Badge> : <Badge>STOPPED</Badge>}
              {d.system.emergency_stop && <> <Badge tone="down">E-STOP</Badge></>}</div>
            <div className="flex gap-2 flex-wrap">
              <button className="btn" disabled={mode === "STOPPED"} onClick={() => setMode("STOPPED")}>Stop trading</button>
              <button className="btn btn-amber" disabled={mode === "PAPER"} onClick={() => setMode("PAPER")}>Paper mode</button>
              <button className="btn btn-red" disabled={mode === "LIVE"} onClick={() => setLiveOpen(true)}>Live mode…</button>
            </div>
            <div className="text-ink3 text-[10.5px]">STOPPED still scans and evaluates; no orders. PAPER uses real market data with simulated fills and a separate simulated portfolio. LIVE sends real orders and requires server-side enablement, credentials, verified connectivity, reconciliation and a typed confirmation. LIVE is never resumed silently after a restart.</div>
            <div className="text-ink3 text-[10.5px]">Server live switch: {d.static.live_trading_enabled ? <span className="text-down">ENABLED</span> : <span>disabled (ARGONA_LIVE_TRADING_ENABLED=false)</span>}</div>
          </div>
        </Panel>
        <Panel title="Market categories">
          <div className="p-3 grid grid-cols-2 gap-1.5 text-[11.5px]">
            {d.categories.map((c: string) => (
              <label key={c} className="flex items-center gap-2"><input type="checkbox" checked={rt.categories.includes(c)}
                onChange={(e) => setRt({ ...rt, categories: e.target.checked ? [...rt.categories, c] : rt.categories.filter((x: string) => x !== c) })} />{c}</label>))}
          </div>
        </Panel>
        <Panel title="Notifications">
          <div className="p-3 space-y-1.5 text-[11.5px]">
            <div className="text-ink3">Channels: {d.notifications.channels.length ? d.notifications.channels.join(", ") : "none configured (set Telegram or SMTP in .env)"}</div>
            {[["enabled", "Enabled"], ["notify_opportunities", "Qualified opportunities"], ["notify_orders", "Orders & failures"], ["notify_fills", "Fills & settlements"], ["notify_risk", "Risk limits"], ["notify_provider", "Provider disconnects"]].map(([k, l]) => (
              <label key={k} className="flex items-center gap-2"><input type="checkbox" checked={!!rt.notifications[k]} onChange={(e) => setRt({ ...rt, notifications: { ...rt.notifications, [k]: e.target.checked } })} />{l}</label>))}
            <label className="flex items-center gap-2">Min severity
              <select className="input" value={rt.notifications.min_severity} onChange={(e) => setRt({ ...rt, notifications: { ...rt.notifications, min_severity: e.target.value } })}>
                <option>info</option><option>warning</option><option>critical</option></select></label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={rt.emergency_cancel_orders} onChange={(e) => setRt({ ...rt, emergency_cancel_orders: e.target.checked })} />Emergency stop cancels working orders</label>
            <button className="btn" onClick={test}>Send test notification</button>
            {d.notifications.last_error && <div className="text-down">last error: {d.notifications.last_error}</div>}
          </div>
        </Panel>
      </div>
      <Panel title="Risk limits & thresholds (enforced by TESS on every order)" right={<button className="btn btn-amber" onClick={save}>Save settings</button>}>
        <div className="p-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-[11px]">
          {RISK_FIELDS.map(([k, label, step, hint]) => (
            <label key={k} className="flex flex-col gap-1"><span className="text-ink2">{label}</span>
              <input className="input tabular" type="number" step={step} value={rt.risk[k]} onChange={(e) => setRt({ ...rt, risk: { ...rt.risk, [k]: e.target.value === "" ? "" : Number(e.target.value) } })} />
              {hint && <span className="text-ink3 text-[10px]">{hint}</span>}</label>))}
          {[["exit_on_edge_reversal", "Exit when edge reverses"], ["allow_market_orders", "Allow market orders (off: limit only)"]].map(([k, l]) => (
            <label key={k} className="flex items-center gap-2 text-ink2"><input type="checkbox" checked={!!rt.risk[k]} onChange={(e) => setRt({ ...rt, risk: { ...rt.risk, [k]: e.target.checked } })} />{l}</label>))}
        </div>
      </Panel>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Data & execution providers">
          <table className="tbl"><thead><tr><th>Venue</th><th>Market data</th><th>Trading credentials</th><th>Verified</th><th /></tr></thead>
            <tbody>{d.providers.map((p: any) => (
              <tr key={p.venue}><td>{p.venue} {p.is_simulated && <Badge tone="sim">sim</Badge>}</td><td><Badge tone="up">enabled</Badge></td>
                <td>{p.is_simulated ? <span className="text-ink3">n/a (never live)</span> : p.trading_configured ? <Badge tone="amber">configured</Badge> : <span className="text-ink3">not configured</span>}</td>
                <td>{p.trading_verified ? <Badge tone="up">yes</Badge> : "—"}</td>
                <td>{!p.is_simulated && p.trading_configured && <button className="btn" onClick={() => verify(p.venue)}>Verify</button>}</td></tr>))}</tbody></table>
          <div className="px-3 py-2 text-ink3 text-[10.5px]">Static configuration (providers, credentials, intervals) lives in <code>.env</code>; see docs/OPERATIONS.md. Providers: {d.static.providers.join(", ")} · DB: {d.static.database} · auth: {d.static.auth_enabled ? "token" : "loopback only"}</div>
        </Panel>
        <Panel title="Configuration history">
          <div className="overflow-auto max-h-72"><table className="tbl"><tbody>{(hist.data ?? []).map((h) => (
            <tr key={h.id}><td className="text-ink3">{ago(h.ts)}</td><td>{h.key}</td><td>{h.actor}</td></tr>))}</tbody></table></div>
        </Panel>
      </div>
      <ErrorLine error={s.error} />
      {liveOpen && (
        <Modal title="Activate LIVE trading (real money)" onClose={() => setLiveOpen(false)}>
          <div className="space-y-3 text-[11.5px]">
            <p className="text-down">LIVE mode submits real orders with real funds. Losses can be total. Check that you are legally eligible to trade on the venue from your jurisdiction.</p>
            <ul className="text-ink2 list-disc pl-5 space-y-0.5">
              <li>Server switch ARGONA_LIVE_TRADING_ENABLED must be true: {d.static.live_trading_enabled ? <span className="text-up">yes</span> : <span className="text-down">NO</span>}</li>
              <li>Venue credentials configured & connectivity verified (checked on activation)</li>
              <li>Local state reconciled against the venue (checked on activation)</li>
              <li>Current limits: max position ${rt.risk.max_position_usd}, max daily loss ${rt.risk.max_daily_loss_usd}, Kelly ×{rt.risk.kelly_fraction}</li>
            </ul>
            <label className="flex items-center gap-2">Venue <select className="input" value={venue} onChange={(e) => setVenue(e.target.value)}><option value="kalshi">Kalshi</option><option value="polymarket">Polymarket</option></select></label>
            <label className="flex flex-col gap-1">Type <code className="text-amber">{d.live_confirm_phrase}</code> to confirm
              <input className="input" value={phrase} onChange={(e) => setPhrase(e.target.value)} /></label>
            <button className="btn btn-red" disabled={phrase !== d.live_confirm_phrase} onClick={() => setMode("LIVE", { confirm: phrase, venue })}>Activate LIVE</button>
          </div>
        </Modal>)}
    </div>
  );
}
