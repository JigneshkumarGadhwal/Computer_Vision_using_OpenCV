"""ZigGgy command line: serve, migrate, status, run-cycle, backtest, import-history, backup, gen-token."""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _settings():
    from .config import get_settings

    return get_settings()


def cmd_serve(a) -> int:
    import uvicorn

    from .main import InsecureConfig, create_app

    s = _settings()
    try:
        app = create_app(s)
    except InsecureConfig as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=s.host, port=s.port, log_config=None, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                timeout_graceful_shutdown=25)
    return 0


def cmd_migrate(a) -> int:
    from .database.session import run_migrations

    s = _settings()
    run_migrations(s.resolved_database_url)
    print(f"migrations applied: {s.resolved_database_url.split('///')[-1] if 'sqlite' in s.resolved_database_url else 'postgresql'}")
    return 0


async def _with_core(fn):
    from .core import Core
    from .database.session import run_migrations

    s = _settings()
    run_migrations(s.resolved_database_url)
    core = Core(s)
    await core.start(run_scheduler=False)
    try:
        return await fn(core)
    finally:
        await core.stop()


def cmd_run_cycle(a) -> int:
    async def go(core):
        await core.rigo.discover()
        res = await core.orchestrator.run_cycle()
        await core.execution.monitor()
        await core.snapshot_job()
        return res

    print(json.dumps(asyncio.run(_with_core(go)), indent=2, default=str))
    return 0


def cmd_status(a) -> int:
    import httpx

    s = _settings()
    headers = {"Authorization": f"Bearer {s.api_token.get_secret_value()}"} if s.api_token else {}
    base = f"http://{'127.0.0.1' if s.host in ('0.0.0.0', '::') else s.host}:{s.port}"
    try:
        h = httpx.get(f"{base}/api/health", timeout=5).json()
        o = httpx.get(f"{base}/api/overview", headers=headers, timeout=10).json()
    except Exception as e:
        print(f"backend not reachable at {base}: {e}")
        return 1
    pf = o.get("portfolio") or {}
    print(f"health={h['status']} mode={o['mode']} paused={o['trading_paused']} estop={o['emergency_stop']}")
    print(f"equity={pf.get('equity')} cash={pf.get('cash')} open_positions={len(pf.get('positions', []))} open_orders={pf.get('open_orders')}")
    print(f"markets open={o['scanner']['open']} eligible={o['scanner']['eligible']} evaluated={o['scanner']['evaluated']} qualified24h={o['scanner']['qualified_24h']}")
    for p in o["providers"]:
        print(f"  provider {p['venue']}: connected={p['connected']} markets={p['markets']} last_sync={p['last_sync']} err={p['last_error']}")
    return 0


def cmd_backtest(a) -> int:
    from .backtesting.engine import BacktestEngine

    async def go(core):
        params = {"start": a.start, "end": a.end, "categories": a.categories or [], "starting_capital": a.capital, "step_s": a.step,
                  "include_simulated": not a.real_only, "risk": core.state.runtime.risk.model_dump()}
        rid = await BacktestEngine(core.db).run(params)
        from .database.models import BacktestRun

        async with core.db.session() as s:
            r = await s.get(BacktestRun, rid)
        return {"id": rid, "status": r.status, "metrics": r.metrics, "notes": r.notes}

    print(json.dumps(asyncio.run(_with_core(go)), indent=2, default=str))
    return 0


def cmd_import(a) -> int:
    from .backtesting.importer import import_polymarket_history

    async def go(core):
        s = core.settings
        return await import_polymarket_history(core.db, s.polymarket_gamma_url, s.polymarket_clob_url, n_markets=a.markets,
                                               half_spread=a.half_spread, depth=a.depth)

    print(json.dumps(asyncio.run(_with_core(go)), indent=2))
    return 0


def cmd_backup(a) -> int:
    s = _settings()
    url = s.resolved_database_url
    out_dir = Path(a.out or (s.data_dir / "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if url.startswith("sqlite"):
        src = url.split("///", 1)[1]
        dst = out_dir / f"zigggy-{stamp}.db"
        with sqlite3.connect(src) as c, sqlite3.connect(dst) as d:
            c.backup(d)  # online, consistent copy (safe while the app runs)
        print(str(dst))
        return 0
    print("PostgreSQL: use scripts/backup.sh (pg_dump)", file=sys.stderr)
    return 1


def cmd_restore(a) -> int:
    s = _settings()
    url = s.resolved_database_url
    if not url.startswith("sqlite"):
        print("PostgreSQL: use pg_restore / psql (see docs/OPERATIONS.md)", file=sys.stderr)
        return 1
    dst = Path(url.split("///", 1)[1])
    src = Path(a.file)
    if not src.exists():
        print(f"no such file {src}", file=sys.stderr)
        return 1
    if dst.exists():
        shutil.copy2(dst, dst.with_suffix(f".pre-restore-{datetime.utcnow():%Y%m%dT%H%M%SZ}.db"))
    for ext in ("-wal", "-shm"):
        Path(str(dst) + ext).unlink(missing_ok=True)
    shutil.copy2(src, dst)
    print(f"restored {src} -> {dst}. Start ZigGgy; it will reconcile on startup.")
    return 0


def cmd_gen_token(a) -> int:
    print(secrets.token_urlsafe(32))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="zigggy", description="ZigGgy prediction-market intelligence & trading system")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run API + dashboard + scheduler").set_defaults(fn=cmd_serve)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(fn=cmd_migrate)
    sub.add_parser("status", help="query a running instance").set_defaults(fn=cmd_status)
    sub.add_parser("run-cycle", help="one discovery + scan cycle without the server (respects mode)").set_defaults(fn=cmd_run_cycle)
    b = sub.add_parser("backtest", help="replay recorded data")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--categories", nargs="*")
    b.add_argument("--capital", type=float, default=10_000)
    b.add_argument("--step", type=int, default=300)
    b.add_argument("--real-only", action="store_true", help="exclude SIM venue data")
    b.set_defaults(fn=cmd_backtest)
    i = sub.add_parser("import-history", help="import resolved Polymarket markets + price history")
    i.add_argument("--markets", type=int, default=50)
    i.add_argument("--half-spread", type=float, default=0.01)
    i.add_argument("--depth", type=float, default=200)
    i.set_defaults(fn=cmd_import)
    bk = sub.add_parser("backup", help="online SQLite backup")
    bk.add_argument("--out")
    bk.set_defaults(fn=cmd_backup)
    rs = sub.add_parser("restore", help="restore SQLite backup (stop the service first)")
    rs.add_argument("file")
    rs.set_defaults(fn=cmd_restore)
    sub.add_parser("gen-token", help="generate a random API token").set_defaults(fn=cmd_gen_token)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
