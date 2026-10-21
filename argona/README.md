# ▲ ARGONA — prediction-market intelligence & trading system

ARGONA is a self-hosted system that finds prediction markets, estimates fair probabilities, trades them on paper, and records why. It does six things:

- It **discovers** prediction markets on Polymarket and Kalshi.
- It **estimates fair probabilities** with transparent quantitative models: crypto lognormal and barrier pricing, multi-model weather forecasts, operator forecasts, cross-venue consensus, and empirical-Bayes calibration.
- It **prices executable edge** after fees, slippage and depth.
- It **sizes positions** with fractional Kelly under hard risk limits.
- It **trades them on paper** against the real order books, with fully simulated fills. Live trading is gated.
- It **records every decision** so each trade is auditable end to end.

Six cooperating agents run it continuously: RIGO (discovery), BRAM (evidence), ILSA (fair value), TESS (risk, with veto power), SPREAD (microstructure and execution) and an ORCHESTRATOR. A dark trading-terminal dashboard shows everything live.

> **Safety defaults.** ARGONA installs in **PAPER** mode with live trading **disabled server-side**. The API listens on **127.0.0.1** only. It never fabricates quotes, fills or profits. Paper results are simulated and are not real money.
>
> **Jurisdiction.** Polymarket's documentation lists **Germany** among countries where order placement is blocked. Kalshi trading needs an eligible, KYC-verified account. Reading public market data and paper trading do not place orders, so they are not affected. Check eligibility before you enable live trading anywhere.

## Quick start (local, about 5 minutes)

Requirements: Linux, macOS or WSL2; Python 3.11+; Node.js 20+; git.

```bash
git clone https://github.com/JigneshkumarGadhwal/Computer_Vision_using_OpenCV.git
cd Computer_Vision_using_OpenCV/argona
./scripts/install.sh          # venv, pinned deps, dashboard build, .env with a fresh API token, DB migration
./scripts/start.sh            # API + dashboard + scheduler in the foreground (Ctrl+C = safe shutdown)
```

Open **http://127.0.0.1:8000**. The token is printed as `ARGONA_API_TOKEN` in `.env`. The dashboard only asks for it when you connect from another machine.

**Try it without any network access to the venues:** set `ARGONA_PROVIDERS=polymarket,kalshi,sim` (or just `sim`) in `.env`. The SIM venue is a clearly labelled simulated market with simulated evidence feeds. It exercises the full lifecycle, from discovery through fills to settlement, within about an hour.

## Always-on (VPS or home server)

```bash
cp .env.example .env && sed -i "s|^ARGONA_API_TOKEN=.*|ARGONA_API_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')|" .env
docker compose up -d --build                    # restart: unless-stopped, healthcheck, data on a named volume
docker compose logs -f argona                   # structured JSON logs
```

You can also use systemd (`deployment/systemd/argona.service`). The VPS guide, HTTPS setup, backups and recovery are in **[docs/OPERATIONS.md](docs/OPERATIONS.md)**.

## Command reference

| Task | Command |
|---|---|
| Install / reinstall | `./scripts/install.sh` |
| Start (foreground) | `./scripts/start.sh` |
| Start / stop / restart (Docker) | `docker compose up -d` · `docker compose stop` · `docker compose restart argona` |
| Start / stop / restart (systemd) | `sudo systemctl start\|stop\|restart argona` |
| Health | `curl -s http://127.0.0.1:8000/api/health` · `.venv/bin/argona status` |
| Tail logs | `docker compose logs -f argona` · `journalctl -u argona -f` |
| Run tests | `./scripts/test.sh` |
| One cycle without the server | `.venv/bin/argona run-cycle` |
| Backtest recorded data | `.venv/bin/argona backtest --real-only` |
| Import real history (Polymarket) | `.venv/bin/argona import-history --markets 100` |
| Back up / restore | `./scripts/backup.sh` · `./scripts/restore.sh <file>` |
| Update | `./scripts/update.sh` |
| New API token | `.venv/bin/argona gen-token` |

## Layout

```
argona/
  backend/argona/      Python package (FastAPI app, agents, engines, adapters)
    agents/            RIGO, BRAM, ILSA, TESS, SPREAD, ORCHESTRATOR
    exchanges/         Polymarket, Kalshi, SIM adapters + capability registry
    market_data/       discovery persistence, eligibility, book cache, classification
    probability/       models, ensemble, calibration metrics
    spread_engine/     executable pricing, slippage, arbitrage screening, EV & ranking
    risk/              Kelly sizing, TESS risk engine
    portfolio/         accounting, snapshots, performance, ledger
    execution/         order lifecycle, paper simulator, reconciliation
    research/          evidence sources (Coinbase, Kraken, Open-Meteo, forecast file), optional LLM rules review
    scheduler/         non-overlapping jobs with backoff
    notifications/     Telegram, SMTP
    backtesting/       point-in-time replay engine, history importer
    api/               REST + SSE
    migrations/        Alembic migrations (shipped inside the package)
  frontend/            React + TypeScript + Vite + Tailwind + Recharts dashboard
  tests/               unit, integration (mocked exchange APIs), e2e (paper lifecycle)
  deployment/          systemd units, Caddy (HTTPS)
  scripts/             install, start, backup, restore, update, test
  docs/                OPERATIONS.md (full manual), ARCHITECTURE.md, READINESS.md
```

## Documentation

- **[docs/OPERATIONS.md](docs/OPERATIONS.md)**: prerequisites, installation, first launch, configuration reference, daily operation, VPS deployment, automation, troubleshooting, maintenance, costs.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: design, models, risk policy, data flow, decisions.
- **[docs/READINESS.md](docs/READINESS.md)**: what was verified, test results, known limitations.

## Disclaimer

This is research software. Nothing here is financial advice, and paper performance does not predict live performance. Expect losses, including total loss of deposited funds, if you enable live trading. You are responsible for complying with venue terms and the laws of your jurisdiction.
