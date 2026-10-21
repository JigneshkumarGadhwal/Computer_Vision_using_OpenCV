import { useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, ModeBadge, Table, fmt } from "../components";

export default function HealthPage({ ctx: _ctx }: { ctx: Ctx }) {
  const health = useApi<Json>("/api/health");
  const feeds = useApi<Json[]>("/api/live/status");
  const integrity = useApi<Json>("/api/system/integrity");
  const events = useApi<Json[]>("/api/system/events?limit=100");
  return (
    <div className="grid">
      <Card title="API" actions={<button onClick={() => { health.reload(); feeds.reload(); integrity.reload(); events.reload(); }}>Refresh</button>}>
        <Async state={health}>{(h) => <p>Status <b className="ok">{h.status}</b> · server time {h.time} · running evaluations: {Object.keys(h.running_evaluations).length || "none"}</p>}</Async>
      </Card>
      <Card title="Data feeds">
        <Async state={feeds} isEmpty={(f) => !f.length} empty="No data sources.">
          {(f) => <Table head={["Source", "Mode", "Rounds", "Last round", "Last ingested", "Stale?", "Signals", "Warnings"]}
            rows={f.map((s: Json) => [s.source_name, <ModeBadge key="m" mode={s.data_mode} />, s.n_rounds, s.last_round_id ?? "—", s.last_ingested_at?.slice(0, 19) ?? "—",
              s.stale ? <b key="st" className="bad">STALE</b> : "no", s.signal_generation_enabled ? "enabled" : <b key="d" className="bad">disabled</b>, s.warnings.join(" ") || "—"])} />}
        </Async>
      </Card>
      <Card title="Prediction-ledger integrity">
        <Async state={integrity}>{(i) => <p>{i.passed ? <b className="ok">PASSED</b> : <b className="bad">FAILED</b>} · {i.predictions} predictions, {i.outcomes} outcomes · outcomes recorded before prediction: {i.outcome_before_prediction} · feature cutoff not before target: {i.cutoff_not_before_target}</p>}</Async>
      </Card>
      <Card title="System events">
        <Async state={events} isEmpty={(e) => !e.length} empty="No events.">
          {(e) => <Table head={["Time", "Level", "Category", "Message"]} rows={e.map((x: Json) => [x.created_at.slice(0, 19), x.level, x.category, x.message])} />}
        </Async>
      </Card>
      <p className="muted">Stale threshold for live feeds: {fmt(feeds.data?.[0]?.stale_after_seconds, 0)} s (AVR_STALE_AFTER_SECONDS).</p>
    </div>
  );
}
