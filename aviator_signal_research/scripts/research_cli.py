"""Command-line entry points for reproducible research runs.

Examples (run from the project root):
    python scripts/research_cli.py make-demo                       # writes data/demo/*.csv
    python scripts/research_cli.py import data/my_export.csv --source my-export --kind historical_export
    python scripts/research_cli.py evaluate --source my-export
    python scripts/research_cli.py backtest --run 1 --threshold 2
    python scripts/research_cli.py demo-report                     # synthetic end-to-end, writes reports/
    python scripts/research_cli.py collect-docs https://spribe.co/games/aviator
    python scripts/research_cli.py observe URL --source spribe-demo --minutes 60
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.backtest import run_backtest  # noqa: E402
from app.config import DEFAULT_PROTOCOL  # noqa: E402
from app.db import init_engine, session_scope  # noqa: E402
from app.ingestion.service import commit_import  # noqa: E402
from app.models import DataSource  # noqa: E402
from app.modeling.evaluation import run_evaluation  # noqa: E402
from app.reporting import write_report  # noqa: E402
from app.synthetic import generate_independent, generate_regime, to_csv_bytes  # noqa: E402

DEMO_DIR = ROOT / "data" / "demo"
DEMO_FILES = {
    "independent": ("synthetic_demo_independent_SYNTHETIC_NOT_REAL.csv", 10000, 2026),
    "regime": ("synthetic_demo_regime_SYNTHETIC_NOT_REAL.csv", 10000, 2027),
}


def make_demo(_args=None) -> None:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    for gen, (fname, n, seed) in DEMO_FILES.items():
        df = generate_independent(n, seed=seed) if gen == "independent" else generate_regime(n, seed=seed)
        (DEMO_DIR / fname).write_bytes(to_csv_bytes(df))
        print(f"wrote {DEMO_DIR / fname} ({n} SYNTHETIC rounds, generator={gen}, seed={seed})")


def _source_id(session, name: str) -> int:
    src = session.scalar(select(DataSource).where(DataSource.name == name))
    if src is None:
        raise SystemExit(f"source '{name}' not found")
    return src.id


def do_import(args) -> None:
    init_engine()
    with session_scope() as s:
        res = commit_import(s, Path(args.file).read_bytes(), Path(args.file).name, args.source, args.kind,
                            default_timezone=args.timezone)
    print(json.dumps({k: v for k, v in res.items() if k != "parse"}, indent=2, default=str))


def do_evaluate(args) -> int:
    init_engine()
    with session_scope() as s:
        sid = _source_id(s, args.source)
        t = time.time()
        run = run_evaluation(s, sid, DEFAULT_PROTOCOL, args.models.split(",") if args.models else None,
                             progress=lambda m: print(f"  [{time.time() - t:6.1f}s] {m}", flush=True))
        print(f"\nrun {run.id}: {run.status}\n{run.conclusion or run.error}")
        return run.id


def do_backtest(args) -> None:
    init_engine()
    with session_scope() as s:
        bt = run_backtest(s, args.run, args.threshold, args.stake)
        for name, r in bt.results["policies"].items():
            print(f"{name:28s} bets={r['n_bets']:5d} roi={r['roi'] if r['roi'] is None else round(r['roi'], 4)} "
                  f"ev/bet CI={r['ev_per_bet_ci']}")
        print("audit passed:", bt.audit["passed"])


def demo_report(_args) -> None:
    """Synthetic end-to-end run with the full default protocol and model set."""
    make_demo()
    init_engine()
    for gen, (fname, _, _) in DEMO_FILES.items():
        name = f"SYNTHETIC demo file ({gen})"
        with session_scope() as s:
            if s.scalar(select(DataSource).where(DataSource.name == name)) is None:
                commit_import(s, (DEMO_DIR / fname).read_bytes(), fname, name, "synthetic",
                              description="Bundled synthetic demo dataset. NOT real Aviator results.")
        with session_scope() as s:
            sid = _source_id(s, name)
            t = time.time()
            run = run_evaluation(s, sid, DEFAULT_PROTOCOL,
                                 progress=lambda m: print(f"  [{gen} {time.time() - t:6.1f}s] {m}", flush=True))
            run_id = run.id
            print(f"\n{gen}: run {run.id} {run.status}: {run.conclusion or run.error}\n")
        with session_scope() as s:
            from app.models import EvaluationRun

            run = s.get(EvaluationRun, run_id)
            if run.status == "completed" and run.summary.get("thresholds"):
                run_backtest(s, run_id, DEFAULT_PROTOCOL.primary_threshold, 1.0)
                print("report:", write_report(s, run))


def collect_docs(args) -> None:
    from app.collectors.policy import AccessPolicy
    from app.collectors.reference import collect_many

    out = collect_many(args.urls, AccessPolicy(min_interval_s=args.interval), render=args.render,
                       follow_doc_links=not args.no_follow, max_pages=args.max_pages)
    for d in out["documents"]:
        print(f"[{d['method']}] {d['final_url']}  ({'unchanged' if d['unchanged'] else 'stored'}: {d['stored_at']})")
        print(f"   summary: {d['summary']}")
        for link in d["links"][:10]:
            print(f"   link: {link['url']}  {link['text']}")
    for e in out["errors"]:
        print(f"ERROR {e['reason']}: {e['message']}")
    if out["context"]:
        print("\n--- context ---\n" + out["context"])
    if out["errors"] and not out["documents"]:
        raise SystemExit(2)


def observe_cmd(args) -> None:
    from app.collectors.observer import observe
    from app.collectors.policy import AccessPolicy

    init_engine()
    if not args.url and not args.profile:
        raise SystemExit("give a URL or --profile")
    if args.profile:
        from app.collectors.profiles import PROFILES, demo_url

        prof = PROFILES[args.profile]
        if prof.get("age_gate") and not args.confirm_legal_age:
            raise SystemExit(f"The {args.profile} site asks visitors to confirm they are of legal age before opening "
                             "the demo. Re-run with --confirm-legal-age only if that applies to you.")
        args.url = args.url or demo_url(args.profile, args.game)
        print(f"observing {args.url}")
    res = observe(args.url, args.source, minutes=args.minutes, poll_s=args.poll, max_rounds=args.max_rounds,
                  selector=args.selector, newest_first=not args.oldest_first,
                  policy=AccessPolicy(min_interval_s=args.interval),
                  raw_dir=ROOT / "data" / "raw" / "observations", decode_ws=not args.no_ws_decode,
                  log_raw_frames=args.log_raw_frames, verify_fairness=args.verify_fairness)
    print(json.dumps(res.to_dict(), indent=2))
    if res.rounds_ingested == 0 and res.errors:
        raise SystemExit(2)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make-demo").set_defaults(fn=make_demo)
    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--source", required=True)
    i.add_argument("--kind", default="historical_export")
    i.add_argument("--timezone", default=None)
    i.set_defaults(fn=do_import)
    e = sub.add_parser("evaluate")
    e.add_argument("--source", required=True)
    e.add_argument("--models", default=None, help="comma-separated; default = all default models")
    e.set_defaults(fn=do_evaluate)
    b = sub.add_parser("backtest")
    b.add_argument("--run", type=int, required=True)
    b.add_argument("--threshold", type=float, default=2.0)
    b.add_argument("--stake", type=float, default=1.0)
    b.set_defaults(fn=do_backtest)
    sub.add_parser("demo-report").set_defaults(fn=demo_report)
    c = sub.add_parser("collect-docs", help="collect official game documentation (robots.txt obeyed)")
    c.add_argument("urls", nargs="+")
    c.add_argument("--render", choices=["auto", "never", "always"], default="auto")
    c.add_argument("--no-follow", action="store_true", help="do not follow same-site rules/fairness links")
    c.add_argument("--max-pages", type=int, default=6)
    c.add_argument("--interval", type=float, default=5.0, help="min seconds between requests per host")
    c.set_defaults(fn=collect_docs)
    o = sub.add_parser("observe", help="record completed rounds from a public game page")
    o.add_argument("url", nargs="?", help="public game page; or use --profile")
    o.add_argument("--profile", choices=["spribe"], default=None, help="use the documented demo URL of a site profile")
    o.add_argument("--game", default="aviator")
    o.add_argument("--confirm-legal-age", action="store_true",
                   help="you confirm you are of legal age (the site shows an age check before its demo)")
    o.add_argument("--source", required=True, help="data source name (kind public_observation)")
    o.add_argument("--minutes", type=float, default=30)
    o.add_argument("--poll", type=float, default=2.0, help="seconds between DOM reads (no extra requests)")
    o.add_argument("--max-rounds", type=int, default=None)
    o.add_argument("--selector", default=None, help="CSS for history items if auto-detection picks the wrong list")
    o.add_argument("--oldest-first", action="store_true", help="history bar lists the oldest round first")
    o.add_argument("--interval", type=float, default=5.0)
    o.add_argument("--no-ws-decode", action="store_true",
                   help="ignore the game's WebSocket messages and read only the history bar")
    o.add_argument("--verify-fairness", action="store_true",
                   help="open each round's provably-fair record in the game (one request per round) and "
                        "re-derive the result from the revealed seeds; verified rounds are marked verified")
    o.add_argument("--log-raw-frames", action="store_true",
                   help="also store raw WebSocket frames (contain other players' names/bets; off by default)")
    o.set_defaults(fn=observe_cmd)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
