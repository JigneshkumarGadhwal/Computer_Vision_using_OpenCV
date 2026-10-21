# Aviator Crash-Game Signal Research Platform

A reproducible research system that answers one question honestly:

> **Do legitimately available Aviator round results contain information that predicts the next round better than
> a simple historical-frequency baseline, on rounds the model has never seen?**

It ingests and validates round data, runs a pre-registered chronological walk-forward evaluation of baseline,
statistical and machine-learning models, stores every prediction immutably *before* its outcome is known, converts
calibrated probabilities into transparent research signals (`INSUFFICIENT EVIDENCE` / `NO DEMONSTRATED EDGE` /
`RESEARCH SIGNAL`), and backtests hypothetical cash-out policies with an independent audit.

It does **not** place bets, connect to gambling accounts, scrape websites, or attempt to obtain seeds or server
state. It ships **no real Aviator data**; the bundled demo data are synthetic and labelled as such everywhere.

---

## Quick start (local)

Requirements: Python 3.11+ (tested 3.13), Node 18+ (tested 22).

```bash
cd aviator_signal_research

# 1. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend && uvicorn app.api.main:app --port 8000      # API + docs at http://localhost:8000/docs

# 2. Frontend (second terminal)
cd aviator_signal_research/frontend
npm ci
npm run dev                                              # dashboard at http://localhost:5173
```

In the dashboard: **Data import** → upload your CSV/JSON (or load a synthetic demo) → **Walk-forward** → start an
evaluation → inspect **Model comparison**, **Signals**, **Backtesting**.

### Docker

```bash
cd aviator_signal_research
cp .env.example .env          # optional
docker compose up --build     # dashboard http://localhost:8080, API http://localhost:8000
```

### Command line (reproducible runs)

```bash
python scripts/research_cli.py make-demo            # regenerate the synthetic demo CSVs
python scripts/research_cli.py import my_export.csv --source operator-x --kind historical_export --timezone Europe/Berlin
python scripts/research_cli.py evaluate --source operator-x
python scripts/research_cli.py backtest --run 1 --threshold 2
python scripts/research_cli.py demo-report          # full synthetic end-to-end run -> reports/
```

### Tests

```bash
cd backend && python -m pytest            # backend: ingestion, features, leakage, metrics, walk-forward,
                                          # evaluation, backtest, signals/live, API (~5 min)
cd frontend && npm test && npm run build  # dashboard states + typecheck + production build
```

---

## Project layout

```
aviator_signal_research/
├── backend/
│   ├── app/
│   │   ├── config.py            # env-based settings + pre-registered EvaluationProtocol
│   │   ├── db.py, models.py     # SQLAlchemy schema, immutability triggers
│   │   ├── ingestion/           # parsers, validation, import service (raw kept separately)
│   │   ├── analysis/            # EDA & dependence tests, fairness-metadata audit
│   │   ├── features.py          # causal feature definitions + look-ahead audit
│   │   ├── modeling/            # estimators, metrics, walk-forward, evaluation, regression
│   │   ├── signals.py           # next-round signal engine (immutable predictions)
│   │   ├── backtest.py          # event-driven backtest + independent audit
│   │   ├── live.py              # data-mode / feed-staleness, FeedAdapter interface
│   │   ├── reporting.py         # markdown report from stored runs
│   │   ├── synthetic.py         # generators with KNOWN properties
│   │   └── api/main.py          # FastAPI app
│   ├── tests/                   # pytest suite
│   ├── requirements*.txt, Dockerfile
├── frontend/                    # React + TypeScript + Recharts dashboard (10 screens)
├── data/demo/                   # SYNTHETIC demo datasets (not real results)
├── docs/                        # METHODOLOGY, LIMITATIONS, API, TROUBLESHOOTING, report template
├── reports/                     # generated evaluation reports
├── scripts/research_cli.py
├── docker-compose.yml, .env.example
```

## Key design decisions

| Decision | Why |
|---|---|
| Threshold probability (reach M?) is the primary target; exact multiplier is secondary | Cash-out decisions depend on P(X ≥ M); point error on a heavy-tailed variable is not decision-relevant |
| Expanding-frequency baseline; every model must beat it on a Holm-adjusted DM test | "Better than chance" is meaningless; "better than the obvious estimate" is the real bar |
| 70/30 chronological split, untouched final test, single pre-registered candidate per threshold | Prevents tuning on the test period and selection-by-luck |
| Commit-before-reveal ledger + DB triggers blocking UPDATE/DELETE on predictions | Proves predictions were made in advance |
| Look-ahead audit on every run; failure forces INSUFFICIENT EVIDENCE | A leakage bug otherwise looks like a breakthrough (see `test_leakage_bug_cannot_produce_a_signal`) |
| Calibration gate = Hosmer–Lemeshow + calibration gap, ECE reported only | Fixed ECE cut-offs reject well-calibrated models on ~1k-round samples |
| SQLite by default, PostgreSQL via `AVR_DATABASE_URL` | Zero-setup prototype; immutability triggers exist for both |
| No pull connector to any site | No documented, authorised Aviator API is known; data arrive by upload or authorised push |

See **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** for the full protocol and
**[docs/LIMITATIONS.md](docs/LIMITATIONS.md)** for limitations and the list of components that need real data or
external integration. API: **[docs/API.md](docs/API.md)**. Problems: **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.

## Results so far

Only synthetic data have been evaluated (no real data were available). See `reports/` and
**[reports/FINAL_REPORT.md](reports/FINAL_REPORT.md)**. In short: on independent synthetic rounds the system
reports **NO DEMONSTRATED EDGE**; on synthetic data with a deliberately planted regime dependence it detects the
signal. That validates the *framework*. It says nothing about real Aviator rounds.

## Responsible use

This is a research and statistics tool. Crash games are designed with a house edge; under the commonly quoted
(unverified) RTP of 97%, every fixed cash-out strategy loses about 3% of stakes in expectation. A model would have to
beat that edge, out of sample and consistently, before any decision value could exist. If gambling is causing
problems for you or someone you know, seek help from a local support service.
