import { useState } from "react";
import { apiPost, apiUpload, useApi, type Json } from "../api";
import type { Ctx } from "../App";
import { Async, Card, Notice, Table, fmt } from "../components";

const KINDS = ["user_upload", "historical_export", "authorized_api", "manual", "live_feed"];

export default function ImportPage({ ctx }: { ctx: Ctx }) {
  const [file, setFile] = useState<File | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [kind, setKind] = useState("user_upload");
  const [tz, setTz] = useState("");
  const [preview, setPreview] = useState<Json | null>(null);
  const [result, setResult] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const quality = useApi<Json>(ctx.source ? `/api/sources/${ctx.source.id}/quality` : null);

  const form = () => {
    const f = new FormData();
    f.append("file", file!);
    if (sourceName) f.append("source_name", sourceName);
    f.append("source_kind", kind);
    if (tz) f.append("default_timezone", tz);
    return f;
  };
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const doPreview = () => run(async () => setPreview(await apiUpload("/api/import/preview", form())));
  const doCommit = () =>
    run(async () => {
      const r = await apiUpload("/api/import/commit", form());
      setResult(r);
      ctx.reloadSources();
      ctx.setSourceId(r.source_id);
    });
  const loadDemo = (generator: string) =>
    run(async () => {
      const r = await apiPost("/api/demo/load", { generator, n_rounds: 8000, seed: generator === "independent" ? 7 : 11 });
      setResult(r);
      ctx.reloadSources();
      ctx.setSourceId(r.source_id);
    });

  return (
    <div className="grid">
      <Card title="Import historical rounds (CSV or JSON)">
        <p className="muted">
          Required column: <code>multiplier</code> (aliases: crash, crash_point, result, odds, coefficient). Recommended:{" "}
          <code>round_id</code>, <code>timestamp</code> (ISO-8601 or epoch), <code>timezone</code>, <code>session_id</code>,{" "}
          <code>verified</code>, and public fairness fields <code>server_seed_hash</code>, <code>server_seed</code>,{" "}
          <code>client_seeds</code>, <code>nonce</code>. Missing or invalid results are rejected — never filled in.
        </p>
        <div className="form-row">
          <label>
            File
            <input type="file" accept=".csv,.json,.txt" aria-label="Round file" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setResult(null); }} />
          </label>
          <label>
            Source name
            <input value={sourceName} onChange={(e) => setSourceName(e.target.value)} placeholder="e.g. operator-X-export-2026" />
          </label>
          <label>
            Source kind
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k}>{k}</option>)}
            </select>
          </label>
          <label>
            Timezone for naive timestamps
            <input value={tz} onChange={(e) => setTz(e.target.value)} placeholder="e.g. Europe/Berlin (default: UTC, flagged)" />
          </label>
        </div>
        <div className="btn-row">
          <button disabled={!file || busy} onClick={doPreview}>Preview & validate</button>
          <button disabled={!file || !sourceName || busy || !preview} onClick={doCommit} className="primary">Commit import</button>
        </div>
        {error && <div className="state state-error" role="alert">{error}</div>}
      </Card>

      {preview && <QualityReport title="Import preview (nothing stored yet)" report={preview} />}
      {result && <QualityReport title={`Committed to source “${result.source_name}”${result.is_synthetic ? " [SYNTHETIC]" : ""}`} report={result} />}

      <Card title="Demo mode (synthetic, clearly labelled)">
        <p className="muted">
          Generates data with KNOWN properties. <b>Independent</b>: i.i.d. rounds with P(X≥m)=0.97/m — no model should beat the
          baseline. <b>Regime</b>: a hidden two-state process with real serial dependence — a planted signal the framework
          should detect. Synthetic sources are always flagged and cannot be mixed with real data.
        </p>
        <div className="btn-row">
          <button disabled={busy} onClick={() => loadDemo("independent")}>Load independent demo (8,000 rounds)</button>
          <button disabled={busy} onClick={() => loadDemo("regime")}>Load regime demo (8,000 rounds)</button>
        </div>
      </Card>

      <ReferenceCollector />

      {ctx.source && (
        <Card title={`Data-quality history — ${ctx.source.name}`}>
          <Async state={quality} isEmpty={(q) => !q.jobs.length} empty="No import jobs for this source (manual / live entries only).">
            {(q) => (
              <Table
                head={["Job", "File", "Verdict", "Rows", "Accepted", "Rejected", "Warnings", "Flags", "SHA-256"]}
                rows={q.jobs.map((j: Json) => [
                  j.id, j.filename, j.verdict, j.n_rows, j.n_accepted, j.n_rejected, j.n_warnings,
                  Object.entries(j.flag_counts ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "—",
                  <code key="sha">{j.file_sha256.slice(0, 12)}</code>,
                ])}
              />
            )}
          </Async>
        </Card>
      )}
    </div>
  );
}

function QualityReport({ title, report }: { title: string; report: Json }) {
  return (
    <Card title={title}>
      <div className="stats">
        <div className="stat"><div className="stat-label">Verdict</div><div className="stat-value">{report.verdict}</div></div>
        <div className="stat"><div className="stat-label">Rows</div><div className="stat-value">{report.n_rows}</div></div>
        <div className="stat"><div className="stat-label">Accepted</div><div className="stat-value">{report.n_accepted}</div></div>
        <div className="stat"><div className="stat-label">Rejected</div><div className="stat-value">{report.n_rejected}</div></div>
        <div className="stat"><div className="stat-label">Warnings</div><div className="stat-value">{report.n_warnings}</div></div>
        <div className="stat"><div className="stat-label">Ordering used</div><div className="stat-value">{report.ordering}</div></div>
        <div className="stat"><div className="stat-label">Missing-id gaps</div><div className="stat-value">{report.missing_id_gaps}</div></div>
        <div className="stat"><div className="stat-label">Median multiplier</div><div className="stat-value">{fmt(report.multiplier_summary?.median, 2)}</div></div>
      </div>
      {report.dataset_warnings?.map((w: string) => <Notice key={w} tone="warn">{w}</Notice>)}
      {report.parse?.round_id_generated && <Notice tone="warn">No round_id column: identifiers were generated from row order.</Notice>}
      <h3>Flags</h3>
      <p>{Object.entries(report.flag_counts ?? {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "none"}</p>
      {report.preview && (
        <>
          <h3>First rows</h3>
          <Table head={["Row", "Round", "Multiplier", "Timestamp (UTC)", "Status", "Flags"]}
            rows={report.preview.map((r: Json) => [r.row_number, r.round_id ?? "—", fmt(r.multiplier, 2), r.timestamp ?? "—", r.status, r.flags.join(", ")])} />
        </>
      )}
    </Card>
  );
}

function ReferenceCollector() {
  const [url, setUrl] = useState("https://spribe.co/games/aviator");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stored = useApi<Json[]>("/api/reference");
  const collect = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await apiPost("/api/reference/collect", { urls: [url], render: "auto" }));
      stored.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Collect official game documentation">
      <p className="muted">
        Fetches rules / RTP / provably-fair pages so the assumptions used in the analysis can be checked against the
        operator&apos;s own wording. robots.txt is obeyed, requests are rate-limited, and collection stops on CAPTCHA or
        anti-bot pages (never bypassed). Extracted values are candidates only — nothing changes automatically.
        Live rounds from a public game page are recorded with the CLI: <code>research_cli.py observe URL --source NAME</code>.
      </p>
      <div className="form-row">
        <label>Page URL<input aria-label="Documentation URL" value={url} onChange={(e) => setUrl(e.target.value)} /></label>
      </div>
      <div className="btn-row"><button disabled={busy || !url} onClick={collect}>{busy ? "Collecting…" : "Collect"}</button></div>
      {error && <div className="state state-error" role="alert">{error}</div>}
      {result?.errors?.map((e: Json) => (
        <div key={e.message} className="state state-error" role="alert"><b>{e.reason}</b>: {e.message}</div>
      ))}
      <Async state={stored} isEmpty={(d) => !d.length} empty="No documentation collected yet.">
        {(docs) => (
          <Table head={["Page", "Fetched", "Method", "RTP candidates", "Max multiplier", "Provably fair text", "Key links"]}
            rows={docs.map((d: Json) => [
              <a key="u" href={d.final_url} target="_blank" rel="noreferrer">{d.title || d.final_url}</a>,
              d.fetched_at?.slice(0, 16), d.method,
              d.summary?.rtp_percent_candidates?.join(", ") || "—",
              d.summary?.max_multiplier_candidates?.join(", ") || "—",
              d.summary?.mentions_provably_fair ? "yes" : "no",
              d.links.slice(0, 4).map((l: Json) => l.text || l.url).join(" · ") || "—",
            ])} />
        )}
      </Async>
    </Card>
  );
}
