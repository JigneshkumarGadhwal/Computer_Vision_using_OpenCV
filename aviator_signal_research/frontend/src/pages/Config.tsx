import { useState } from "react";
import { useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Table } from "../components";

const DOCS = ["methodology", "limitations", "api", "troubleshooting", "readme"];

export default function ConfigPage({ ctx: _ctx }: { ctx: Ctx }) {
  const cfg = useApi<Json>("/api/config");
  const [doc, setDoc] = useState("methodology");
  const docState = useApi<string>(`/api/docs/${doc}`);
  return (
    <div className="grid">
      <Async state={cfg}>
        {(c) => (
          <>
            <Card title="Pre-registered evaluation protocol">
              <Table head={["Setting", "Value"]} rows={Object.entries(c.protocol).map(([k, v]) => [k, JSON.stringify(v)])} />
              <p className="muted">Reference distribution RTP: {c.reference_rtp}. {c.reference_note}</p>
              <p className="muted">Settings are environment variables (prefix AVR_); see .env.example.</p>
            </Card>
            <Card title="Feature definitions (all computed from rounds strictly before the target)">
              <p className="muted">Available: {c.feature_available_at}</p>
              <Table head={["Feature", "Description", "Look-back", "Model input"]} rows={c.features.map((f: Json) => [f.name, f.description, f.lookback_rounds || "expanding", f.model_input ? "yes" : "baseline only"])} />
            </Card>
          </>
        )}
      </Async>
      <Card title="Documentation" actions={<select aria-label="Document" value={doc} onChange={(e) => setDoc(e.target.value)}>{DOCS.map((d) => <option key={d}>{d}</option>)}</select>}>
        <Async state={docState}>{(t) => <pre className="doc">{t}</pre>}</Async>
      </Card>
    </div>
  );
}
