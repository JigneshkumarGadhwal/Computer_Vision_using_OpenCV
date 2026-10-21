import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { Async, StatusBadge, fmt } from "../components";
import SignalsPage from "../pages/Signals";
import type { Ctx } from "../App";

type Route = (url: string, init?: RequestInit) => { status?: number; body: unknown };

function mockFetch(route: Route) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const { status = 200, body } = route(String(url), init);
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("formatting and badges", () => {
  it("never renders NaN or undefined as a number", () => {
    expect(fmt(undefined)).toBe("n/a");
    expect(fmt(Number.NaN)).toBe("n/a");
    expect(fmt(0.123456, 3, true)).toBe("+0.123");
  });

  it("defaults a missing status to INSUFFICIENT EVIDENCE", () => {
    render(<StatusBadge status={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("INSUFFICIENT EVIDENCE");
  });
});

describe("Async wrapper states", () => {
  it("shows an error with retry", () => {
    const reload = vi.fn();
    render(<Async state={{ data: null, error: "boom", loading: false, reload }}>{() => <p>data</p>}</Async>);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    fireEvent.click(screen.getByText("Retry"));
    expect(reload).toHaveBeenCalled();
  });
  it("shows loading and empty states", () => {
    const { rerender } = render(<Async state={{ data: null, error: null, loading: true, reload: () => {} }}>{() => <p>x</p>}</Async>);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    rerender(<Async state={{ data: [], error: null, loading: false, reload: () => {} }} isEmpty={(d: unknown[]) => !d.length} empty="Nothing here">{() => <p>x</p>}</Async>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});

describe("App", () => {
  it("reports an unreachable API instead of rendering fake data", async () => {
    mockFetch(() => ({ status: 500, body: { detail: "database unavailable" } }));
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole("alert")[0]).toHaveTextContent("database unavailable"));
    expect(screen.getByText(/No data source yet/)).toBeInTheDocument();
  });

  it("shows the synthetic banner for synthetic sources", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/sources")) return { body: [{ id: 1, name: "demo", n_rounds: 10, is_synthetic: true, kind: "synthetic" }] };
      if (url.includes("/api/overview/1")) return { body: { source: { n_verified: 0, n_rounds: 10 }, feed: { data_mode: "synthetic" }, selected_threshold: 2, model_status: "INSUFFICIENT EVIDENCE", latest_run: null } };
      if (url.includes("/analysis")) return { body: { error: "insufficient data", n: 10 } };
      return { body: [] };
    });
    render(<App />);
    expect(await screen.findByText(/SYNTHETIC DEMO DATA/)).toBeInTheDocument();
    expect(await screen.findByText(/No completed evaluation yet/)).toBeInTheDocument();
  });
});

describe("Signals page", () => {
  const ctx: Ctx = { source: { id: 3, name: "real", kind: "user_upload", is_synthetic: false }, sources: [], setSourceId: () => {}, reloadSources: () => {} };

  it("does not invent a probability when evidence is insufficient", async () => {
    mockFetch((url) => {
      if (url.includes("/api/signals/3"))
        return { body: { status: "INSUFFICIENT EVIDENCE", status_reasons: ["No completed evaluation run for this source/threshold."], probability_reach: null, probability_crash_before: null, data_mode: "historical", threshold: 2, target: "reaches_2x", breakeven_probability: 0.5, next_round_sequence: 5, warnings: [], disclaimer: "Probability estimate only" } };
      return { body: [] };
    });
    render(<SignalsPage ctx={ctx} />);
    await act(async () => {
      fireEvent.click(screen.getByText("Generate & store signal"));
    });
    expect(await screen.findByText(/not shown \(insufficient evidence\)/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("INSUFFICIENT EVIDENCE");
    expect(screen.getByText(/No completed evaluation run/)).toBeInTheDocument();
  });

  it("surfaces API errors when generating a signal", async () => {
    mockFetch((url) => (url.includes("/api/signals/") ? { status: 404, body: { detail: "source 3 not found" } } : { body: [] }));
    render(<SignalsPage ctx={ctx} />);
    await act(async () => {
      fireEvent.click(screen.getByText("Generate & store signal"));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("source 3 not found");
  });
});

describe("Reference documentation collector", () => {
  it("shows the collector's network_blocked reason instead of fake results", async () => {
    const { default: ImportPage } = await import("../pages/Import");
    mockFetch((url, init) => {
      if (url.endsWith("/api/reference/collect") && init?.method === "POST")
        return { body: { documents: [], errors: [{ reason: "network_blocked", message: "cannot reach https://spribe.co: outbound proxy refused the connection", url: "https://spribe.co/robots.txt" }], context: "" } };
      if (url.endsWith("/api/reference")) return { body: [] };
      return { body: [] };
    });
    const ctx: Ctx = { source: null, sources: [], setSourceId: () => {}, reloadSources: () => {} };
    render(<ImportPage ctx={ctx} />);
    expect(await screen.findByText("No documentation collected yet.")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByText("Collect"));
    });
    expect(await screen.findByText("network_blocked")).toBeInTheDocument();
    expect(screen.getByText(/outbound proxy refused/)).toBeInTheDocument();
  });
});
