import { useEffect, useMemo, useState } from "react";
import { useApi, type Json } from "./api";
import { SyntheticBanner } from "./components";
import OverviewPage from "./pages/Overview";
import ImportPage from "./pages/Import";
import HistoryPage from "./pages/History";
import ModelsPage from "./pages/Models";
import WalkForwardPage from "./pages/WalkForward";
import SignalsPage from "./pages/Signals";
import BacktestPage from "./pages/Backtest";
import RegistryPage from "./pages/Registry";
import HealthPage from "./pages/Health";
import ConfigPage from "./pages/Config";

export interface Ctx {
  source: Json | null;
  sources: Json[];
  setSourceId: (id: number) => void;
  reloadSources: () => void;
}

const PAGES = [
  { id: "overview", label: "Overview", el: OverviewPage },
  { id: "import", label: "Data import", el: ImportPage },
  { id: "history", label: "History & distribution", el: HistoryPage },
  { id: "models", label: "Model comparison", el: ModelsPage },
  { id: "walkforward", label: "Walk-forward", el: WalkForwardPage },
  { id: "signals", label: "Signals", el: SignalsPage },
  { id: "backtest", label: "Backtesting", el: BacktestPage },
  { id: "registry", label: "Model registry", el: RegistryPage },
  { id: "health", label: "System health", el: HealthPage },
  { id: "config", label: "Config & docs", el: ConfigPage },
] as const;

function readHash(): string {
  const h = window.location.hash.replace("#", "");
  return PAGES.some((p) => p.id === h) ? h : "overview";
}

export default function App() {
  const [page, setPage] = useState<string>(readHash());
  const sourcesState = useApi<Json[]>("/api/sources");
  const [sourceId, setSourceId] = useState<number | null>(null);

  useEffect(() => {
    const onHash = () => setPage(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const sources = sourcesState.data ?? [];
  useEffect(() => {
    if (sourceId === null && sources.length) setSourceId(sources[sources.length - 1].id);
  }, [sources, sourceId]);

  const source = useMemo(() => sources.find((s) => s.id === sourceId) ?? null, [sources, sourceId]);
  const ctx: Ctx = { source, sources, setSourceId, reloadSources: sourcesState.reload };
  const Page = PAGES.find((p) => p.id === page)!.el;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-title">Aviator Signal Research</span>
          <span className="brand-sub">Does the data contain out-of-sample predictive information?</span>
        </div>
        <label className="source-picker">
          <span>Data source</span>
          <select
            aria-label="Data source"
            value={sourceId ?? ""}
            onChange={(e) => setSourceId(Number(e.target.value))}
            disabled={!sources.length}
          >
            {!sources.length && <option value="">No sources — import data first</option>}
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.is_synthetic ? "[SYNTHETIC] " : ""}
                {s.name} ({s.n_rounds} rounds)
              </option>
            ))}
          </select>
        </label>
      </header>
      <nav className="tabs" aria-label="Sections">
        {PAGES.map((p) => (
          <a key={p.id} href={`#${p.id}`} className={p.id === page ? "tab active" : "tab"}>
            {p.label}
          </a>
        ))}
      </nav>
      {source?.is_synthetic && <SyntheticBanner />}
      {sourcesState.error && (
        <div className="state state-error" role="alert">
          Cannot reach the API: {sourcesState.error}. Is the backend running on port 8000?
        </div>
      )}
      <main className="content">
        <Page ctx={ctx} />
      </main>
      <footer className="footer">
        Research tool only. Outputs are probability estimates, never guarantees. No bets are placed.
      </footer>
    </div>
  );
}
