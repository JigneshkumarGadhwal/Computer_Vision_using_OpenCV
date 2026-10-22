# Setup and usage guide (your own PC)

This guide takes you from a fresh Windows, macOS or Linux machine to:

1. the dashboard running with the 3,000 real Spribe demo rounds loaded,
2. your own evaluation and backtest,
3. optional collection of new rounds.

Allow about 20 minutes, most of it spent on installs.

> **What this tool is and isn't.** It *measures* whether past rounds predict the next one. On the collected data
> they do not (see `reports/FINAL_REPORT.md`). It does not place bets and must not be used as a betting system.

---

## Step 0 — Install the prerequisites (once)

| Tool | Version | Windows | macOS | Linux (Ubuntu/Debian) |
|---|---|---|---|---|
| Git | any recent | <https://git-scm.com/download/win> | `xcode-select --install` | `sudo apt install git` |
| Python | **3.12 or 3.13** (3.11 minimum) | <https://www.python.org/downloads/>, tick **"Add python.exe to PATH"** | `brew install python@3.13` | `sudo apt install python3.12 python3.12-venv` |
| Node.js | **22 LTS** (18 minimum) | <https://nodejs.org/> (LTS installer) | `brew install node@22` | <https://nodejs.org/en/download> (nvm or NodeSource) |

Check in a new terminal (Windows: **PowerShell**):

```bash
git --version
python --version      # Windows may need:  py --version
node --version
npm --version
```

## Step 1 — Get the code

```bash
git clone https://github.com/JigneshkumarGadhwal/Computer_Vision_using_OpenCV.git
cd Computer_Vision_using_OpenCV
git checkout claude/aviator-prediction-system-0zdx26     # or main, once merged
cd aviator_signal_research
```

All further commands run from this `aviator_signal_research` folder unless stated otherwise.

## Step 2 — Backend (Python) environment

**Windows (PowerShell)**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If you get "running scripts is disabled": run once
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# then activate again.
python -m pip install --upgrade pip
pip install -r backend/requirements-dev.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements-dev.txt
```

Your prompt now starts with `(.venv)`. **Activate it again in every new terminal** before running Python commands.

Optional: run the test suite (5–20 minutes depending on your CPU). It proves the installation works:

```bash
cd backend
python -m pytest -q
cd ..
```

## Step 3 — Load the real observed data

The database is not in Git. It is created on first use at `data/aviator_research.db`. Import the committed rounds:

```bash
python scripts/research_cli.py import data/observed/spribe_demo_rounds.csv --source spribe-demo --kind public_observation --timezone UTC
```

Expected output: `"n_accepted": 3000` (or the number of rows in the file), `"n_rejected": 0`. The warning
*"… round identifiers are missing …"* is normal. Spribe's round ids come from a counter shared across the platform,
so the gaps are not missing rounds.

## Step 4 — Run the evaluation and a backtest

```bash
python scripts/research_cli.py evaluate --source spribe-demo
```

This takes about 4–10 minutes. The last lines print the run id and the conclusion, e.g. `run 1: completed`. A
markdown report is written to `reports/evaluation_run_<id>.md`.

```bash
python scripts/research_cli.py backtest --run 1 --threshold 2
```

This replays the stored out-of-sample predictions as hypothetical fixed-stake bets at 2x. It compares betting on
every round, the model's gate and the baseline's gate, then audits the result independently. No real money is
involved.

## Step 5 — Start the dashboard

You need **two terminals**.

**Terminal 1: API** (with the virtual environment active)

```bash
cd backend
uvicorn app.api.main:app --port 8000
```

Leave it running. Check <http://localhost:8000/api/health>. The interactive API docs are at
<http://localhost:8000/docs>.

**Terminal 2: dashboard**

```bash
cd frontend
npm ci          # first time only
npm run dev
```

Open **<http://localhost:5173>**.

To stop either one, press `Ctrl+C` in its terminal.

## Step 6 — Using the dashboard (screen by screen)

| Screen | What to do there | What to look at |
|---|---|---|
| **Overview** | Pick the data source `spribe-demo` and the run | Signal status per threshold, data quality, feed freshness |
| **Data import** | Upload more CSV/JSON files (preview first, then commit); load the synthetic demos; collect documentation pages | Accepted/rejected rows and why |
| **History & distribution** | Explore the rounds | Observed vs expected `0.97/m` frequencies; dependence tests (runs, Ljung–Box, streaks, drift, time of day) with Holm-corrected p-values |
| **Walk-forward** | Start a new evaluation from the UI | Progress, dev/test split, look-ahead audit result |
| **Model comparison** | Compare every model against the baseline | Brier skill with 95% CI, DM p-value, AUC, calibration (reliability diagram) |
| **Signals** | Request the signal for the next round | Status: `INSUFFICIENT EVIDENCE` / `NO DEMONSTRATED EDGE` / `RESEARCH SIGNAL`, with the reason |
| **Backtesting** | Run a backtest for a run and threshold | ROI and EV per bet with CI; audit pass/fail |
| **Model registry** | Inspect stored model versions | Training window, hashes, metrics |
| **System health** | Check integrity | Ledger: no outcome stored before its prediction; immutability triggers |
| **Config & docs** | Read the protocol | Thresholds, feature definitions, methodology |

**How to read a result.** A threshold only shows `RESEARCH SIGNAL` if the model beats the frequency baseline on
unseen rounds with statistical significance *and* is calibrated. Even then, profit needs `p > 1/M`, e.g. more
than 50% at 2x, consistently, which the house edge (−3%) works against.

## Step 7 (optional) — Collect new rounds yourself

Collection opens Spribe's public **demo** game in a headless browser and reads the round results the game
shows every player. It requires a one-time browser download:

```bash
python -m playwright install chromium
```

Then:

```bash
python scripts/research_cli.py observe --profile spribe --confirm-legal-age --verify-fairness --source spribe-demo-live --minutes 60
```

* `--confirm-legal-age`: you confirm that you are 18+. The demo page asks the same question.
* `--verify-fairness`: re-computes every result from the server seed Spribe reveals after the round. Any
  mismatch is reported.
* It stops by itself after `--minutes`, after `--max-rounds N`, after 5 minutes without a new round, or when the
  site shows a CAPTCHA or blocks access. It never tries to get past those.
* Expect about 2.5–3 rounds per minute. Keep the PC awake (disable sleep), because a sleeping laptop silently stops
  collection.
* Export what you collected:

  ```bash
  python scripts/research_cli.py export-rounds --source spribe-demo-live --out data/observed/my_rounds.csv
  ```

* Keep new observations in their **own source name**. Do not mix demo rounds with data from a real-money operator.

## Alternative — Docker (no Python/Node install)

Install Docker Desktop, then:

```bash
cd aviator_signal_research
docker compose up --build
```

The dashboard is at <http://localhost:8080> and the API at <http://localhost:8000>. Import the CSV through
**Data import** in the dashboard. (Docker builds were validated with `docker compose config` only; no Docker
daemon was available in the development environment.)

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found (Windows) | Use `py` instead, or reinstall Python with "Add to PATH" ticked |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| `pip install` fails building numpy/pandas/lightgbm | Your Python is too new or too old. Use 3.12 or 3.13, recreate `.venv` |
| LightGBM error on macOS (`libomp`) | `brew install libomp` |
| Dashboard loads but shows network errors | The API isn't running. Start Terminal 1 (port 8000) first |
| Port 8000 or 5173 already in use | `uvicorn app.api.main:app --port 8001`, then start the dashboard with `VITE_PROXY_TARGET=http://127.0.0.1:8001 npm run dev` (PowerShell: `$env:VITE_PROXY_TARGET="http://127.0.0.1:8001"; npm run dev`) |
| `database is locked` | Only one long job (evaluate/observe) at a time; close other runs |
| `observe` stops with `network_blocked` / `access_challenge` | A network or site restriction; it is intentional that the tool stops. Try later or another network you are allowed to use |
| Want to start over | Stop everything and delete `data/aviator_research.db` (re-import afterwards) |

More: `docs/TROUBLESHOOTING.md`. Method: `docs/METHODOLOGY.md`. Limits: `docs/LIMITATIONS.md`.
