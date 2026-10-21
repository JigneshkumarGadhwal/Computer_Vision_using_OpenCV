import { useEffect, useState } from "react";
import { api, setToken, usePoll, useEventStream, usd, signedUsd } from "./api";
import { Badge, Modal } from "./ui";
import Overview from "./pages/Overview";
import Scanner from "./pages/Scanner";
import SpreadPage from "./pages/Spread";
import Agents from "./pages/Agents";
import Portfolio from "./pages/Portfolio";
import Backtest from "./pages/Backtest";
import Settings from "./pages/Settings";
import Health from "./pages/Health";

const TABS = ["overview", "scanner", "spread", "agents", "portfolio", "backtest", "settings", "health"] as const;
type Tab = (typeof TABS)[number];
const LABEL: Record<Tab, string> = { overview: "Overview", scanner: "Market Scanner", spread: "Spread Engine", agents: "Agents",
  portfolio: "Portfolio", backtest: "Backtest", settings: "Settings", health: "Diagnostics" };

function useHashTab(): [Tab, (t: Tab) => void] {
  const read = () => (TABS as readonly string[]).includes(location.hash.slice(1)) ? (location.hash.slice(1) as Tab) : "overview";
  const [tab, set] = useState<Tab>(read);
  useEffect(() => {
    const f = () => set(read());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return [tab, (t) => { location.hash = t; set(t); }];
}

function Login({ onDone }: { onDone: () => void }) {
  const [t, setT] = useState("");
  return (
    <div className="h-full flex items-center justify-center p-4">
      <form className="panel p-6 w-full max-w-sm space-y-3" onSubmit={(e) => { e.preventDefault(); setToken(t.trim()); onDone(); }}>
        <div className="text-amber tracking-[.3em] text-lg">▲ ARGONA</div>
        <div className="text-ink2 text-[11px]">This server requires an API token (ARGONA_API_TOKEN). It is stored only in this browser.</div>
        <input className="input w-full" type="password" autoFocus placeholder="API token" value={t} onChange={(e) => setT(e.target.value)} />
        <button className="btn btn-amber w-full" type="submit">Unlock</button>
      </form>
    </div>
  );
}

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  return <span className="tabular text-ink2">{now.toISOString().slice(11, 19)} UTC</span>;
}

export default function App() {
  const [tab, setTab] = useHashTab();
  const [needAuth, setNeedAuth] = useState(false);
  const [authNonce, setAuthNonce] = useState(0);
  const [confirmStop, setConfirmStop] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    const f = () => setNeedAuth(true);
    window.addEventListener("argona:auth", f);
    return () => window.removeEventListener("argona:auth", f);
  }, []);
  const ov = usePoll<any>(needAuth ? null : "/overview", 4000, [authNonce]);
  const stream = useEventStream();
  if (needAuth) return <Login onDone={() => { setNeedAuth(false); setAuthNonce((n) => n + 1); }} />;
  const o = ov.data;
  const pf = o?.portfolio;
  const act = async (fn: () => Promise<any>, msg: string) => {
    setBusy(true);
    try { await fn(); setToast(msg); ov.reload(); } catch (e: any) { setToast(`Error: ${e.message}`); }
    finally { setBusy(false); setTimeout(() => setToast(null), 4000); }
  };
  const mode: string = o?.mode ?? "…";
  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b border-line bg-panel/80 backdrop-blur sticky top-0 z-40">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
          <div className="flex items-center gap-2">
            <span className="text-amber text-[15px] tracking-[.35em] font-semibold">▲ ARGONA</span>
            <span className="text-ink3 text-[10px] hidden sm:inline">PREDICTION-MARKET INTELLIGENCE</span>
          </div>
          <div className="flex items-center gap-2">
            {mode === "LIVE" ? <Badge tone="down"><span className="live-dot">●</span> LIVE · {o?.live_venue} · REAL MONEY</Badge>
              : mode === "PAPER" ? <Badge tone="amber">● PAPER · simulated execution</Badge> : <Badge>■ {mode}</Badge>}
            {o?.emergency_stop && <Badge tone="down">EMERGENCY STOP</Badge>}
            {o?.trading_paused && <Badge tone="amber">NEW TRADES PAUSED</Badge>}
            {o && !o.scanning_enabled && <Badge>SCANNING OFF</Badge>}
            {o?.read_only && <Badge tone="down">READ-ONLY</Badge>}
            <span title={stream.connected ? "live stream connected" : "live stream disconnected"} className={stream.connected ? "text-up" : "text-down"}>{stream.connected ? "◉ stream" : "○ stream"}</span>
          </div>
          <div className="flex items-center gap-4 ml-auto tabular text-[12px]">
            <span><span className="text-ink3">EQUITY </span>{usd(pf?.equity)}</span>
            <span className="hidden md:inline"><span className="text-ink3">DAY </span><span className={(pf?.day_pnl ?? 0) >= 0 ? "text-up" : "text-down"}>{signedUsd(pf?.day_pnl)}</span></span>
            <Clock />
          </div>
          <div className="flex items-center gap-2 w-full lg:w-auto">
            <button className="btn" disabled={busy || !o} onClick={() => act(() => api("/control/scanning", { method: "POST", json: { enabled: !o.scanning_enabled } }), o.scanning_enabled ? "Scanning stopped" : "Scanning started")}>
              {o?.scanning_enabled ? "Stop scan" : "Start scan"}</button>
            <button className="btn btn-amber" disabled={busy || !o} onClick={() => act(() => api("/control/pause", { method: "POST", json: { enabled: !o.trading_paused } }), o.trading_paused ? "Trading resumed" : "New trades paused")}>
              {o?.trading_paused ? "Resume trades" : "Pause trades"}</button>
            <button className="btn btn-red" disabled={busy || !o || o.emergency_stop} onClick={() => setConfirmStop(true)}>■ E-STOP</button>
          </div>
        </div>
        <nav className="flex gap-1 px-3 overflow-x-auto">
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-[11px] uppercase tracking-[.12em] border-b-2 whitespace-nowrap ${tab === t ? "border-amber text-amber" : "border-transparent text-ink3 hover:text-ink2"}`}>{LABEL[t]}</button>
          ))}
        </nav>
      </header>
      {o?.emergency_stop && (
        <div className="bg-down/15 border-b border-down/50 text-[11.5px] px-4 py-2 flex flex-wrap items-center gap-3">
          <span className="text-down">■ EMERGENCY STOP ACTIVE — new orders blocked. {o.emergency_reason}</span>
          <button className="btn" onClick={() => act(() => api("/control/emergency-reset", { method: "POST" }), "Emergency stop cleared; mode STOPPED")}>Reset (mode → STOPPED)</button>
        </div>
      )}
      <main className="flex-1 p-3 md:p-4">
        {ov.error && !o && <div className="panel p-4 text-down mb-3">Backend unreachable: {ov.error}</div>}
        {tab === "overview" && <Overview ov={o} events={stream.events} />}
        {tab === "scanner" && <Scanner />}
        {tab === "spread" && <SpreadPage />}
        {tab === "agents" && <Agents />}
        {tab === "portfolio" && <Portfolio account={o?.account} />}
        {tab === "backtest" && <Backtest />}
        {tab === "settings" && <Settings onChange={ov.reload} />}
        {tab === "health" && <Health />}
      </main>
      <footer className="px-4 py-2 text-[10px] text-ink3 border-t border-line">
        ARGONA v1.0 · Paper results are simulated and not real money. Prediction-market trading carries risk of total loss; check venue eligibility for your jurisdiction.
      </footer>
      {toast && <div className="fixed bottom-4 right-4 z-50 panel px-3 py-2 text-[11.5px] border-amber/60">{toast}</div>}
      {confirmStop && (
        <Modal title="Emergency stop" onClose={() => setConfirmStop(false)}>
          <p className="text-ink2 mb-3">Blocks all new orders immediately and cancels working orders (if enabled in Settings). Trading stays blocked until you reset it.</p>
          <button className="btn btn-red" onClick={() => { setConfirmStop(false); act(() => api("/control/emergency-stop", { method: "POST", json: { reason: "operator emergency stop (dashboard)" } }), "EMERGENCY STOP engaged"); }}>Engage emergency stop</button>
        </Modal>
      )}
    </div>
  );
}
