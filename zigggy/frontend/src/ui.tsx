import { type ReactNode } from "react";

export function Panel({ title, right, children, className = "", bodyClass = "" }: {
  title: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={`panel flex flex-col min-w-0 ${className}`}>
      <div className="panel-h"><span>{title}</span>{right && <span className="normal-case tracking-normal">{right}</span>}</div>
      <div className={`min-h-0 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "up" | "down" | "amber" | "muted" }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "amber" ? "text-amber" : "text-ink";
  return (
    <div className="panel px-3 py-2.5 min-w-0">
      <div className="text-[10px] uppercase tracking-[.14em] text-ink3 truncate">{label}</div>
      <div className={`text-[19px] leading-7 tabular ${color} truncate`}>{value}</div>
      {sub && <div className="text-[10.5px] text-ink3 truncate">{sub}</div>}
    </div>
  );
}

export function Badge({ children, tone = "muted", title }: { children: ReactNode; tone?: "up" | "down" | "amber" | "muted" | "sim" | "series"; title?: string }) {
  const map: Record<string, string> = {
    up: "border-up/50 text-up", down: "border-down/60 text-down", amber: "border-amber/60 text-amber",
    muted: "border-line2 text-ink2", sim: "border-sim/60 text-sim", series: "border-series/60 text-series",
  };
  return <span title={title} className={`inline-flex items-center gap-1 border rounded-[3px] px-1.5 py-[1px] text-[10px] uppercase tracking-wider ${map[tone]}`}>{children}</span>;
}

export const SimBadge = () => <Badge tone="sim" title="Simulated venue / simulated execution — not real market data">SIM</Badge>;

/** Sign-coloured number with explicit +/- (colour is never the only cue). */
export function Signed({ v, children }: { v: number | null | undefined; children: ReactNode }) {
  if (v == null) return <span className="text-ink3">—</span>;
  return <span className={v > 0 ? "text-up" : v < 0 ? "text-down" : "text-ink2"}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-6 text-center text-ink3 text-[11px]">{children}</div>;
}

export function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="px-3 py-2 text-down text-[11px]">⚠ {error}</div>;
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-start justify-center p-4 overflow-auto" onClick={onClose}>
      <div className="panel w-full max-w-3xl mt-10" onClick={(e) => e.stopPropagation()}>
        <div className="panel-h"><span>{title}</span><button className="btn" onClick={onClose}>Close</button></div>
        <div className="p-4 max-h-[75vh] overflow-auto">{children}</div>
      </div>
    </div>
  );
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1 text-[11.5px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink3 uppercase text-[10px] tracking-wider pt-[2px]">{k}</dt>
          <dd className="text-ink break-words min-w-0">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
