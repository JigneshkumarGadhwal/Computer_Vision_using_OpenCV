import { useEffect, useState } from "react";
import { useApi, type Json } from "../api";

/** Completed/running evaluation runs for a source, with a selector. */
export function useRuns(sourceId: number | undefined) {
  const runs = useApi<Json[]>(sourceId ? `/api/evaluations?source_id=${sourceId}` : null);
  const [runId, setRunId] = useState<number | null>(null);
  useEffect(() => {
    const completed = (runs.data ?? []).filter((r) => r.status === "completed");
    if (completed.length && (runId === null || !completed.some((r) => r.id === runId))) setRunId(completed[0].id);
    if (!completed.length) setRunId(null);
  }, [runs.data, runId]);
  return { runs, runId, setRunId };
}

export function RunPicker({ runs, runId, setRunId }: { runs: Json[]; runId: number | null; setRunId: (id: number) => void }) {
  const completed = runs.filter((r) => r.status === "completed");
  if (!completed.length) return null;
  return (
    <select aria-label="Evaluation run" value={runId ?? ""} onChange={(e) => setRunId(Number(e.target.value))}>
      {completed.map((r) => (
        <option key={r.id} value={r.id}>
          Run {r.id} — {r.overall_status} ({r.finished_at?.slice(0, 16)})
        </option>
      ))}
    </select>
  );
}

export function ThresholdPicker({ thresholds, value, onChange }: { thresholds: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <select aria-label="Threshold" value={value} onChange={(e) => onChange(e.target.value)}>
      {thresholds.map((t) => (
        <option key={t} value={t}>
          reaches {t}x
        </option>
      ))}
    </select>
  );
}
