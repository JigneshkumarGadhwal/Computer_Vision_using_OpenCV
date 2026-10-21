import { ago, usePoll } from "../api";
import { Badge, ErrorLine, Panel } from "../ui";

export default function Agents() {
  const d = usePoll<any[]>("/agents", 5000);
  return (
    <div className="space-y-3">
      <ErrorLine error={d.error} />
      <div className="text-ink3 text-[11px] px-1">Flow per cycle: RIGO → SPREAD (assess) → BRAM → ILSA → TESS (veto) → SPREAD (execute), coordinated by ORCHESTRATOR. Only SPREAD can reach the execution service, and only with a TESS approval.</div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {(d.data ?? []).map((a) => (
          <Panel key={a.name} title={<span className="text-amber tracking-[.25em]">{a.name}</span>}
            right={a.status === "error" ? <Badge tone="down">error</Badge> : a.status === "running" ? <Badge tone="series">running</Badge> : <Badge>idle</Badge>}>
            <div className="p-3 space-y-2">
              <div className="text-ink2 text-[11px]">{a.role}</div>
              <div className="grid grid-cols-4 gap-2 text-[11px] tabular">
                <div><div className="text-ink3 text-[10px]">LAST RUN</div>{ago(a.last_run)}</div>
                <div><div className="text-ink3 text-[10px]">DURATION</div>{a.last_duration_ms != null ? `${a.last_duration_ms}ms` : "—"}</div>
                <div><div className="text-ink3 text-[10px]">DECISIONS</div>{a.decisions}</div>
                <div><div className="text-ink3 text-[10px]">API CALLS</div>{a.api_calls}</div>
              </div>
              {a.last_error && <div className="text-down text-[10.5px] break-words">last error: {a.last_error} ({a.errors} total)</div>}
              <div className="text-ink3 text-[10px] uppercase tracking-wider pt-1">Recent findings</div>
              <ul className="text-[11px] space-y-0.5 max-h-36 overflow-auto">
                {(a.findings ?? []).length === 0 && <li className="text-ink3">—</li>}
                {(a.findings ?? []).map((f: any, i: number) => <li key={i}><span className="text-ink3">{ago(f.ts)} </span>{f.text}</li>)}
              </ul>
              <details><summary className="text-ink3 text-[10px] uppercase tracking-wider cursor-pointer">Decision trail ({a.recent_decisions.length})</summary>
                <div className="max-h-48 overflow-auto mt-1">{a.recent_decisions.map((r: any) => (
                  <div key={r.id} className="text-[10.5px] border-b border-line py-0.5"><span className="text-ink3">{ago(r.ts)} </span><span className="text-amber">{r.decision}</span>
                    {r.market_key && <span className="text-ink2"> {r.market_key}</span>} <span className="text-ink3 break-all">{JSON.stringify(r.details).slice(0, 220)}</span></div>))}</div>
              </details>
            </div>
          </Panel>))}
      </div>
    </div>
  );
}
