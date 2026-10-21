import { useState } from "react";
import { useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Notice, Table } from "../components";

export default function RegistryPage({ ctx }: { ctx: Ctx }) {
  const [selectedOnly, setSelectedOnly] = useState(true);
  const models = useApi<Json[]>(ctx.source ? `/api/models?source_id=${ctx.source.id}&selected_only=${selectedOnly}` : null);
  if (!ctx.source) return <Notice>Select a data source first.</Notice>;
  return (
    <Card title="Model registry" actions={<label className="check"><input type="checkbox" checked={selectedOnly} onChange={(e) => setSelectedOnly(e.target.checked)} />final selected models only</label>}>
      <p className="muted">Every model × threshold evaluated in each run, with its configuration, features, training period, seed and (for final models) artifact checksum.</p>
      <Async state={models} isEmpty={(m) => !m.length} empty="No registered models yet.">
        {(ms) => <Table head={["ID", "Run", "Model", "Family", "Target", "Training seq", "Data cutoff", "Calibration", "Seed", "Params", "Artifact SHA-256"]}
          rows={ms.map((m: Json) => [m.id, m.run_id, m.is_selected ? <b key="n">{m.name} ★</b> : m.name, m.family, m.target, `${m.train_start_seq}–${m.train_end_seq}`,
            m.data_cutoff_round_id ?? "—", m.calibration ?? "none", m.random_seed, <code key="p" className="small">{JSON.stringify(m.params).slice(0, 120)}</code>,
            m.artifact_sha256 ? <code key="s">{m.artifact_sha256.slice(0, 12)}</code> : "—"])} />}
      </Async>
    </Card>
  );
}
