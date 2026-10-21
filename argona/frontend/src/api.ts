// Thin API client. Token (if the server requires one) is kept in localStorage on this device only.
import { useCallback, useEffect, useRef, useState } from "react";

const TOKEN_KEY = "argona.token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}
export function setToken(t: string) {
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* storage unavailable */
  }
}

export class AuthError extends Error {}

export async function api<T = any>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(`/api${path}`, { ...opts, headers: { ...headers, ...(opts.headers as any) }, body });
  if (res.status === 401) {
    window.dispatchEvent(new Event("argona:auth"));
    throw new AuthError("unauthorized");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const d = data?.detail;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d ?? res.statusText));
  }
  return data as T;
}

export function usePoll<T>(path: string | null, intervalMs = 5000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
        setUpdatedAt(Date.now());
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    alive.current = true;
    load();
    const id = intervalMs > 0 ? setInterval(load, intervalMs) : undefined;
    return () => {
      alive.current = false;
      if (id) clearInterval(id);
    };
  }, [load, intervalMs]);
  return { data, error, reload: load, updatedAt };
}

export type ArgonaEvent = {
  id?: number;
  ts: string;
  type: string;
  severity: "info" | "warning" | "critical";
  agent?: string | null;
  market_key?: string | null;
  mode?: string | null;
  message: string;
  data?: any;
};

/** Live activity: initial page from /api/events, then server-sent events.
 *  `session` = null while unauthenticated; changing it (e.g. after login) reopens the stream with the new token. */
export function useEventStream(session: number | null, max = 400) {
  const [events, setEvents] = useState<ArgonaEvent[]>([]);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (session === null) return;
    let es: EventSource | null = null;
    let closed = false;
    api<ArgonaEvent[]>(`/events?limit=150`).then((e) => !closed && setEvents(e)).catch(() => {});
    const open = () => {
      const tok = getToken();
      es = new EventSource(`/api/stream${tok ? `?token=${encodeURIComponent(tok)}` : ""}`);
      es.addEventListener("argona", (m: MessageEvent) => {
        const ev = JSON.parse(m.data) as ArgonaEvent;
        setEvents((prev) => [ev, ...prev].slice(0, max));
      });
      es.onopen = () => setConnected(true);
      es.onerror = () => {
        setConnected(false);
        // a 401 closes the stream for good; probe once so the app can show the login form
        if (es?.readyState === EventSource.CLOSED) api("/overview").catch(() => {});
      };
    };
    open();
    return () => {
      closed = true;
      es?.close();
      setConnected(false);
    };
  }, [max, session]);
  return { events, connected };
}

// ---------- formatting ----------
export const usd = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
export const signedUsd = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v >= 0 ? "+" : "-"}$${Math.abs(v).toFixed(d)}`);
export const pct = (v: number | null | undefined, d = 1) => (v == null || !isFinite(v) ? "—" : `${(v * 100).toFixed(d)}%`);
export const px = (v: number | null | undefined, d = 3) => (v == null ? "—" : v.toFixed(d));
export const cents = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}¢`);
export const ago = (iso?: string | null) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "now";
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
};
export const until = (iso?: string | null) => {
  if (!iso) return "—";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "closed";
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(0)}d`;
};
export const hhmmss = (iso: string) => new Date(iso).toISOString().slice(11, 19);
