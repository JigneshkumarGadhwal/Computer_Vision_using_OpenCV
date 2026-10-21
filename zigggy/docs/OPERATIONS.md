# ZigGgy operations manual

This manual covers installing, running, automating, troubleshooting and maintaining ZigGgy. Every command below matches a file in this repository. Paths are relative to the `zigggy/` directory unless they are absolute.

Contents: [A Prerequisites](#a-prerequisites) · [B Installation](#b-installation-from-scratch) · [C First launch](#c-first-launch) · [D Configuration](#d-configuration-reference) · [E Daily operation](#e-daily-operation) · [F VPS](#f-vps-installation) · [G Automation](#g-automation--continuous-operation) · [H Troubleshooting](#h-troubleshooting) · [I Maintenance](#i-upgrades-and-maintenance) · [J Costs](#j-cost-and-resource-requirements) · [K Commands](#k-operating-command-reference) · [Live trading](#live-trading-activation-real-money)

---

## A. Prerequisites

| Item | Requirement |
|---|---|
| OS | Linux (Ubuntu 22.04/24.04, Debian 12 tested paths), macOS 13+, Windows via **WSL2** |
| Python | 3.11 – 3.13 (developed on 3.13) |
| Node.js | 20+ (only to build the dashboard; developed on 22) |
| Optional | Docker 24+ with Compose v2 (recommended for always-on), PostgreSQL 16 (optional), Caddy (HTTPS) |
| Resources | 1 vCPU, 1 GB RAM, 5 GB disk is enough for SQLite + 60-day history; 2 GB RAM recommended |
| Network | Outbound HTTPS to the venues and evidence sources listed below |

Outbound hosts ZigGgy contacts:

| Host | Purpose | Auth | Cost |
|---|---|---|---|
| `gamma-api.polymarket.com`, `clob.polymarket.com` | Polymarket markets, order books, price history | none (read) | free |
| `api.elections.kalshi.com` | Kalshi markets and order books (and trading if configured) | none for read; RSA key for trading | free (trading fees apply) |
| `api.exchange.coinbase.com`, `api.kraken.com` | crypto spot + realised volatility evidence | none | free |
| `api.open-meteo.com` | multi-model temperature forecasts | none | free for non-commercial use (check Open-Meteo terms for commercial use) |
| `api.telegram.org` / your SMTP server | notifications (optional) | bot token / SMTP login | free |
| `api.anthropic.com` | optional LLM rules review | API key | paid per token |

Free vs. paid: everything required to run paper trading on real market data is free. Optional costs: a VPS, LLM usage, and (live only) exchange fees and capital. See [J](#j-cost-and-resource-requirements).

---

## B. Installation from scratch

### B1. Scripted (recommended)

```bash
git clone https://github.com/JigneshkumarGadhwal/Computer_Vision_using_OpenCV.git
cd Computer_Vision_using_OpenCV/zigggy
./scripts/install.sh
```

`install.sh` does the following:
1. checks Python ≥ 3.11 and Node;
2. creates `.venv`;
3. installs the pinned backend dependencies (`requirements.lock`) and ZigGgy itself (editable);
4. builds the dashboard into `frontend/dist`;
5. creates `.env` from `.env.example` with a fresh random `ZIGGGY_API_TOKEN` (`chmod 600`);
6. creates `data/` and runs the database migrations.

### B2. Manual (same result)

```bash
cd Computer_Vision_using_OpenCV/zigggy
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock          # pinned backend deps
.venv/bin/pip install --no-deps -e .                 # ZigGgy package + `zigggy` CLI
(cd frontend && npm ci && npm run build)             # dashboard -> frontend/dist
cp .env.example .env && chmod 600 .env
.venv/bin/zigggy gen-token                           # paste into ZIGGGY_API_TOKEN in .env
.venv/bin/zigggy migrate                             # creates/updates the database schema
```

### B3. Tests

```bash
./scripts/test.sh                                    # backend pytest + dashboard type-check/build
.venv/bin/python -m pytest -q                        # backend only
ZIGGGY_TEST_DATABASE_URL=postgresql+asyncpg://user@host:5432/zigggy_test .venv/bin/python -m pytest -q   # also on PostgreSQL (DESTROYS that DB's schema)
E2E=1 ./scripts/test.sh                              # + browser E2E (boots its own SIM backends; needs Chromium)
```

### B4. Start

```bash
./scripts/start.sh            # foreground; Ctrl+C performs a safe shutdown
```

---

## C. First launch

1. **Open the dashboard** at http://127.0.0.1:8000 and enter the token from `.env` (`ZIGGGY_API_TOKEN`) once per browser. From another machine, use an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 you@server`) or the HTTPS proxy ([F6](#f6-https-and-authentication)).
2. **Backend health:** `curl -s http://127.0.0.1:8000/api/health` returns `{"status":"ok","db":true,"scheduler_running":true,...}`. Then open **Diagnostics** and confirm every scheduler job shows `ok`.
3. **Confirm market data is live:** in **Diagnostics → Market-data connectivity**, Polymarket and Kalshi should show `connected=yes`, a market count and a recent *Last sync*. In the **Scanner**, the *Fresh* column shows book age in seconds (red above the staleness limit). Real venues have no SIM badge. Anything simulated carries a violet **SIM** badge.
4. **Agent status:** **Agents** shows each agent's last run, decisions, API calls, errors and recent findings. The **Overview** shows a compact version.
5. **First paper cycle:** the scheduler runs a scan cycle every `ZIGGGY_SCAN_INTERVAL_S` (default 45 s), and you can force one from **Diagnostics → scan_cycle → Run now**. Expect most markets to show *abst.* (abstained). That is correct: ZigGgy only trades where an independent model applies (crypto price thresholds, daily-high temperature markets, operator forecasts, confirmed cross-venue links, or calibrated categories), and the edge must survive fees.
6. **Inspect the first simulated order:** go to **Portfolio → Order history → Trace**. The trace shows the full chain: book snapshot → probability estimate (model, version, inputs, evidence with source URLs and timestamps) → opportunity (gross/fee/net edge) → TESS risk checks and Kelly sizing → order → simulated fills.
7. **Activity log and ledger:** the Overview log shows timestamped backend events (`DISCOVER`, `PROB`, `EDGE`, `RISK ✓/✗`, `ORDER`, `FILL`, `RESOLVED`, `P&L`). **Portfolio → Trade ledger** lists every fill and settlement with cash impact.

> **No venue access?** Add `sim` to `ZIGGGY_PROVIDERS` to watch the full lifecycle on the clearly labelled simulated venue. SIM markets expire 15–90 minutes after creation, so settlements appear within about an hour.

---

## D. Configuration reference

There are two layers:

* **Static settings** live in `.env` / environment variables, read at startup. They cover secrets, providers, hosts and intervals. Restart the service after changing them.
* **Runtime settings** are changed in the dashboard (**Settings**) or via `PUT /api/settings`, persisted in the database, and audited in `config_history`. They cover risk limits, thresholds, categories and notification preferences, and take effect on the next cycle.

### D1. Static settings (`.env`)

| Variable | Default | Notes / safe range |
|---|---|---|
| `ZIGGGY_HOST` | `127.0.0.1` | Non-loopback **requires** `ZIGGGY_API_TOKEN`, or the server refuses to start |
| `ZIGGGY_PORT` | `8000` | |
| `ZIGGGY_API_TOKEN` | *(empty)* | Bearer token for the API/dashboard. Generate with `zigggy gen-token`. Empty means loopback clients only |
| `ZIGGGY_LOG_LEVEL` / `ZIGGGY_LOG_JSON` | `INFO` / `true` | JSON logs with secret redaction |
| `ZIGGGY_DATA_DIR` | `./data` | SQLite DB, SIM state, backups |
| `ZIGGGY_DATABASE_URL` | SQLite in data dir | e.g. `postgresql+asyncpg://zigggy:pw@host:5432/zigggy` |
| `ZIGGGY_PROVIDERS` | `polymarket,kalshi` | Any of `polymarket`, `kalshi`, `sim` |
| `ZIGGGY_POLYMARKET_MAX_MARKETS` / `ZIGGGY_KALSHI_MAX_MARKETS` | 1500 / 3000 | Discovery caps per run |
| `ZIGGGY_KALSHI_SERIES` | *(all)* | Restrict Kalshi discovery, e.g. `KXHIGHNY,KXHIGHCHI,KXBTCD` |
| `ZIGGGY_MAX_BOOKS_PER_CYCLE` | 60 | Order books fetched per scan cycle (rate-limit friendly; prioritises held, modelled, then liquid markets) |
| `ZIGGGY_DISCOVERY_INTERVAL_S` | 600 | 300–3600 |
| `ZIGGGY_SCAN_INTERVAL_S` | 45 | 15–300; lower means more API calls |
| `ZIGGGY_ORDER_MONITOR_INTERVAL_S` | 10 | Fill/expiry checks |
| `ZIGGGY_SETTLEMENT_INTERVAL_S` | 180 | Resolution tracking |
| `ZIGGGY_SNAPSHOT_INTERVAL_S` | 60 | Equity-curve resolution |
| `ZIGGGY_PAPER_STARTING_CAPITAL` | 10000 | Used when the paper account is first created |
| `ZIGGGY_START_MODE` | `PAPER` | First-run mode, `PAPER` or `STOPPED`. LIVE is never a start mode |
| `ZIGGGY_LIVE_TRADING_ENABLED` | `false` | Server-side master switch for real-money trading |
| `ZIGGGY_LIVE_RESUME_ON_RESTART` | `false` | Even if true, LIVE resumes only after successful verification and reconciliation |
| `ZIGGGY_KALSHI_KEY_ID` / `ZIGGGY_KALSHI_PRIVATE_KEY_PATH` | — | Kalshi API key and PEM file (trading only) |
| `ZIGGGY_KALSHI_API_URL` | production | Set `https://demo-api.kalshi.co/trade-api/v2` for Kalshi's demo/sandbox |
| `ZIGGGY_POLYMARKET_PRIVATE_KEY` / `ZIGGGY_POLYMARKET_WALLET` | — | Dedicated trading wallet. Requires `pip install 'zigggy[polymarket-live]'` |
| `ZIGGGY_LLM_PROVIDER` | `none` | `anthropic` enables BRAM's rules review (flags only, never prices) |
| `ZIGGGY_ANTHROPIC_API_KEY` / `ZIGGGY_LLM_MODEL` | — / `claude-opus-5-5` | `claude-haiku-5-5` is about 40× cheaper |
| `ZIGGGY_LLM_MAX_REVIEWS_PER_HOUR` | 20 | Hard cost cap; reviews are cached per rules text |
| `ZIGGGY_TELEGRAM_BOT_TOKEN` / `ZIGGGY_TELEGRAM_CHAT_ID` | — | Telegram channel |
| `ZIGGGY_SMTP_*` | — | Email channel (STARTTLS by default) |

### D2. Runtime settings (dashboard → Settings)

| Setting | Default | Meaning |
|---|---|---|
| Categories | all | crypto, weather, economics, commodities, politics, sports, other |
| Min net edge | 0.03 | Required $/contract **after** fees, slippage and buffer. Safe range 0.02–0.10 |
| Min confidence | 0.35 | Ensemble confidence 0–1 |
| Fractional Kelly | 0.25 | Quarter-Kelly. Never above 0.5 for live trading |
| Max position ($) | 250 | Per order |
| Max daily loss ($) | 300 | Blocks new entries until the next UTC day once realised + unrealised day P&L falls below −limit |
| Max drawdown | 15% | From peak equity; blocks new entries |
| Max per-market / per-event / per-category / total exposure | 5% / 8% / 25% / 60% of equity | Capital at risk (cost basis + reserved orders) |
| Max open orders | 20 | |
| Min liquidity | $150 within 5¢ of best | |
| Max spread | 0.10 | |
| Max data staleness | 180 s | Older books are rejected and opportunities expired |
| Min hours / max days to resolution | 0.25 h / 45 d | |
| Price band | 0.03 – 0.97 | Avoids extreme prices where fees and tails dominate |
| Slippage buffer | 0.005 | Added cost assumption per contract |
| Order TTL | 300 s | Unfilled remainder is cancelled |
| Re-entry cooldown | 600 s | No new entry in a market within this window |
| Market prior σ | 0.06 | How much the ensemble defers to the market price. Lower means more conservative |
| Exit on edge reversal / min exit edge | on / 0.02 | Sell when the bid beats the expected settlement value by this margin |
| Allow market orders | off | Limit orders only by default |
| Emergency stop cancels working orders | on | |
| Notifications | warnings and above, orders, fills, risk, provider | |

---

## E. Daily operation

* **Start / stop:** use `./scripts/start.sh` (foreground), Docker (`docker compose up -d` / `docker compose stop`) or systemd (`sudo systemctl start|stop zigggy`). Stopping is always safe: the scheduler drains, state is persisted, and paper orders resume on restart.
* **Pause new trades:** use the header's **Pause trades** button. Monitoring, fills of existing orders and exits continue. **Resume trades** undoes it.
* **Stop scanning:** **Stop scan** halts the evaluation cycle. Discovery, monitoring and settlement continue.
* **Emergency stop:** the header's **■ E-STOP** (with confirmation) blocks all new orders and cancels working orders. It persists across restarts until you press **Reset**, which sets the mode to **STOPPED**. You must then choose PAPER or LIVE deliberately. Without the dashboard: `curl -XPOST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"reason":"manual"}' http://127.0.0.1:8000/api/control/emergency-stop`.
* **Change risk settings:** go to **Settings → Risk limits → Save**. The change is validated server-side (out-of-range values are rejected) and recorded in *Configuration history*.
* **Review opportunities:** **Spread Engine** lists qualified and rejected opportunities with gross edge, fee, net EV, confidence and the reason. The **Scanner** shows fair value and net edge per market. Click a row for evidence, estimates and depth.
* **Orders and positions:** **Portfolio** shows positions (marked at best bid), working orders (Cancel or Trace), order history, the trade ledger, exposure by category and performance windows (24h/7d/30d/all).
* **Monitor P&L:** the Overview tiles show day net P&L (realised plus unrealised since 00:00 UTC) and realised P&L. Unrealised P&L is marked at the executable bid, so it is conservative. Every paper figure is simulated.
* **Read agent decisions:** each agent card in **Agents** has a *Decision trail*. Common ILSA reasons: *no gross edge*, *edge consumed by fees*, *net EV < min*. Common TESS vetoes: *exposure_headroom*, *daily_loss_limit*, *reentry_cooldown*, *no_duplicate_order*.
* **Stale data:** a red *Fresh* value in the Scanner, a `stale_data` flag on opportunities, or *Diagnostics → Last sync* older than a few minutes. Stale books are never traded.
* **Normal operation checklist:** `/api/health` returns `ok`; every Diagnostics job shows `ok` with a recent *Last OK*; providers show `connected`; the activity log shows a `CYCLE` entry at least every few minutes; there are no `critical` entries under *Recent warnings*.

---

## F. VPS installation

ZigGgy is a single process with SQLite, so a small VPS is enough (1–2 vCPU, 2 GB RAM, 20 GB disk). Choose a provider and region where you may legally use the venues you intend to use.

### F1. Provision and secure SSH

```bash
# on your machine
ssh-keygen -t ed25519 -f ~/.ssh/zigggy
ssh-copy-id -i ~/.ssh/zigggy.pub root@SERVER_IP
# on the server
adduser --disabled-password --gecos "" zigggy && usermod -aG sudo zigggy
mkdir -p /home/zigggy/.ssh && cp ~/.ssh/authorized_keys /home/zigggy/.ssh/ && chown -R zigggy:zigggy /home/zigggy/.ssh
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
apt update && apt -y upgrade && apt -y install ufw fail2ban unattended-upgrades git
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw --force enable    # 80/443 only if you use HTTPS
timedatectl set-timezone UTC
```

### F2. Option 1: Docker (recommended)

```bash
curl -fsSL https://get.docker.com | sh && usermod -aG docker zigggy   # re-login afterwards
su - zigggy
git clone https://github.com/JigneshkumarGadhwal/Computer_Vision_using_OpenCV.git && cd Computer_Vision_using_OpenCV/zigggy
cp .env.example .env && chmod 600 .env
sed -i "s|^ZIGGGY_API_TOKEN=.*|ZIGGGY_API_TOKEN=$(openssl rand -base64 32 | tr -d '=+/')|" .env
mkdir -p secrets && chmod 700 secrets          # exchange key files go here (mounted read-only at /secrets)
docker compose up -d --build
docker compose ps && curl -s http://127.0.0.1:8000/api/health
```

Persistent storage is the named volume `zigggy-data` (`/data` in the container). The restart policy is `unless-stopped`, and a healthcheck runs every 30 s. Logs rotate at 20 MB × 5 files.

PostgreSQL (optional): set `ZIGGGY_DATABASE_URL=postgresql+asyncpg://zigggy:<POSTGRES_PASSWORD>@postgres:5432/zigggy` and `POSTGRES_PASSWORD` in `.env`, then run `docker compose --profile postgres up -d`.

### F3. Option 2: systemd (no Docker)

```bash
sudo apt -y install python3 python3-venv nodejs npm      # Node ≥ 20 needed: use NodeSource if your distro ships older
sudo mkdir -p /opt/zigggy && sudo chown zigggy:zigggy /opt/zigggy
git clone https://github.com/JigneshkumarGadhwal/Computer_Vision_using_OpenCV.git /tmp/zigggy-src
cp -r /tmp/zigggy-src/zigggy/. /opt/zigggy/ && cd /opt/zigggy
./scripts/install.sh
sudo cp deployment/systemd/zigggy.service deployment/systemd/zigggy-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zigggy zigggy-backup.timer
systemctl status zigggy && curl -s http://127.0.0.1:8000/api/health
```

The unit runs `zigggy migrate` before every start, restarts on failure (`Restart=always`, 5 s) and is sandboxed (`ProtectSystem=strict`, write access only to `/opt/zigggy/data`).

### F4. Configure environment variables

Edit `.env` (Docker: in the repo directory; systemd: `/opt/zigggy/.env`), then restart (`docker compose up -d` / `sudo systemctl restart zigggy`). Never put secrets in the repository or the database. `.env`, `*.pem` and `data/` are git-ignored.

### F5. Logs

```bash
docker compose logs -f --tail=200 zigggy        # Docker
journalctl -u zigggy -f                          # systemd
journalctl -u zigggy --since "1 hour ago" | grep -E '"level": "(WARNING|ERROR)"'
```

### F6. HTTPS and authentication

The dashboard API is protected by `ZIGGGY_API_TOKEN` (bearer). Never expose port 8000 directly; publish it through HTTPS.

* **Docker + Caddy:** set `ZIGGGY_DOMAIN=zigggy.example.com` in `.env`, point the domain's DNS A record at the server, then run `docker compose --profile https up -d`. Caddy obtains and renews Let's Encrypt certificates automatically (`deployment/caddy/Caddyfile`).
* **systemd + host Caddy:** install Caddy (`apt install caddy`), copy `deployment/caddy/Caddyfile.host` to `/etc/caddy/Caddyfile`, set your domain, and run `systemctl reload caddy`.
* **No domain:** use an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 zigggy@SERVER_IP`.

### F7. Verify startup after a reboot

```bash
sudo reboot
# after reconnecting:
curl -s http://127.0.0.1:8000/api/health        # status ok, scheduler_running true
docker compose ps   # or: systemctl is-active zigggy
```

The activity log shows `ZigGgy started (mode ...)`. If the previous mode was LIVE, the log shows that LIVE was **not** resumed and the mode is STOPPED, unless explicitly configured otherwise ([G](#g-automation--continuous-operation)).

### F8. Disk and resources

```bash
df -h /; du -sh /var/lib/docker/volumes/*zigggy-data* 2>/dev/null || du -sh /opt/zigggy/data
docker stats --no-stream zigggy 2>/dev/null || systemctl status zigggy | grep -E 'Memory|CPU'
```

The maintenance job prunes price observations and book snapshots older than 60 days and evidence older than 120 days. Expect roughly 50–300 MB per month at default intervals with about 60 books per cycle.

### F9. Backups and recovery

* **Automatic (systemd):** `zigggy-backup.timer` runs daily at 03:17 UTC and keeps the newest 14 copies in `data/backups/`.
* **Docker:** `docker compose exec zigggy zigggy backup --out /data/backups`. Add a host cron job such as `17 3 * * * cd ~/Computer_Vision_using_OpenCV/zigggy && docker compose exec -T zigggy zigggy backup --out /data/backups`.
* **Manual:** `./scripts/backup.sh` uses SQLite's online backup API (safe while running) or `pg_dump` for PostgreSQL.
* **Copy off-site:** `rsync -av data/backups/ backup-host:zigggy/` (or object storage).
* **Restore:** stop the service, run `./scripts/restore.sh data/backups/zigggy-YYYYmmddTHHMMSSZ.db`, then start it. Docker: `docker compose stop zigggy && docker compose run --rm zigggy zigggy restore /data/backups/<file> && docker compose start zigggy`. On startup ZigGgy recovers interrupted orders, never resumes LIVE silently, and reconciles live accounts before trading.

---

## G. Automation (continuous operation)

* **Background workers:** the scheduler runs inside the `zigggy serve` process. Docker (`restart: unless-stopped`) or systemd (`Restart=always`) keeps that process alive across crashes and reboots, so no terminal, and no Claude session, needs to stay open.
* **Schedules** (from `.env`):

| Job | Default | Does |
|---|---|---|
| `discovery` | 600 s | RIGO lists open markets per venue, persists them, flags ineligible ones, and emits provider disconnect/recover events |
| `scan_cycle` | 45 s | ORCHESTRATOR runs RIGO (books) → SPREAD (microstructure) → BRAM (evidence) → ILSA (estimates and EV) → TESS (risk) → SPREAD (execution). It runs in STOPPED mode too, but places no orders then |
| `order_monitor` | 10 s | Paper re-matching, TTL expiry, live order status and fill sync |
| `settlement` | 180 s | Resolution tracking and settlement of held markets |
| `reconcile_live` | 60 s | LIVE only: balance, positions and open orders against the venue. A mismatch pauses new trades and raises a critical alert |
| `portfolio_snapshot` | 60 s | Equity curve, daily P&L reset, peak equity |
| `health` | 30 s | Instance-lock heartbeat and provider staleness alerts |
| `model_metrics` | 3600 s | Point-in-time calibration (Brier, log loss, reliability) and calibration tables |
| `maintenance` | 6 h | Pruning and cross-venue link proposals |

* **Overlap and failure:** a job never overlaps itself. On failure it backs off exponentially (×2 per failure, capped at 30 min, ±20% jitter) and recovers automatically. Failures at the 1st, 5th and 20th consecutive attempt raise an alert.
* **Scheduler health:** check the **Diagnostics** table, `curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/health/details | python3 -m json.tool`, or `zigggy status`.
* **Alerts:** configure Telegram (create a bot with @BotFather, send it a message, read your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`) or SMTP in `.env`, restart, then press **Settings → Send test notification**. Every message is prefixed `[PAPER]`, `[LIVE]` or `[SYSTEM]`.
* **Crash recovery:** Docker or systemd restarts the process. On startup, orders written ahead as `PENDING_SUBMIT` become `OPEN` (paper) or `UNKNOWN` (live). Live `UNKNOWN` orders are looked up at the venue by venue id or client order id before anything else happens. If an order isn't found after a 90 s grace period, it is marked `REJECTED`. It is never resubmitted.
* **Safe restart:** `docker compose restart zigggy` or `sudo systemctl restart zigggy`. SIGTERM drains the scheduler (up to 20 s) and persists state.
* **Duplicate-order prevention after recovery:**
  * Each opportunity maps to a deterministic `client_order_id` (`ZIG-<opportunity id>`) protected by a database unique constraint.
  * Submissions are written ahead of the network call.
  * Timeouts never trigger an automatic resubmission.
  * A database **instance lock** puts any second ZigGgy process on the same database into READ-ONLY mode, so two instances can never both trade.

---

## H. Troubleshooting

| Symptom | Diagnose | Likely cause | Fix |
|---|---|---|---|
| App will not start | `journalctl -u zigggy -n 50` / `docker compose logs zigggy` | Non-loopback host without token (`Refusing to serve…`); port in use; bad `.env` value | Set `ZIGGGY_API_TOKEN` or bind `127.0.0.1`; `ss -ltnp \| grep 8000`; fix the value named in the validation error |
| Dependencies fail to install | `python3 --version`; `node --version`; pip output | Python < 3.11, Node < 20, no compiler for a wheel, proxy | Install the supported versions; `pip install --upgrade pip`; set `HTTPS_PROXY` |
| Migration errors | `.venv/bin/zigggy migrate` | DB unreachable or corrupted; partial manual edits | Check `ZIGGGY_DATABASE_URL`; restore the latest backup ([F9](#f9-backups-and-recovery)) |
| Frontend cannot reach backend | Browser dev tools → network; `curl /api/health` | Backend down; dev server proxy pointing elsewhere; 401 | Start the backend; `ZIGGGY_API=http://127.0.0.1:8000 npm run dev`; enter the token |
| Dashboard says "Frontend not built" | `ls frontend/dist/index.html` | Dashboard not built | `cd frontend && npm ci && npm run build` |
| Provider authentication fails | Diagnostics → Execution / `POST /api/control/verify/kalshi` | Wrong key id, PEM path or permissions; clock skew (Kalshi signatures are timestamped) | Check the key path inside the container (`/secrets/...`); `timedatectl` (enable NTP); regenerate the key |
| Rate limits exceeded | Diagnostics → 429s; `rate_limited` events | Intervals too short; too many books per cycle | Raise `ZIGGGY_SCAN_INTERVAL_S`; lower `ZIGGGY_MAX_BOOKS_PER_CYCLE`; lower the `*_RPS` settings |
| No markets discovered | Diagnostics → provider `connected=no`, last error | Outbound HTTPS blocked; DNS; venue outage | `curl -I https://gamma-api.polymarket.com/markets?limit=1`; check firewall/proxy; wait for recovery (automatic) |
| No opportunities qualify | Spread Engine → rejected reasons; Scanner *abst.* | Normal: markets are mostly efficient and fees are real; no independent model applies | Expected behaviour. Enable modelled categories (crypto, weather), add forecasts (`data/forecasts.json`), confirm cross-venue links. Do **not** lower thresholds blindly |
| Orders remain pending | Portfolio → working orders; Trace | Paper: limit not crossed by later books (no optimistic fills). Live: venue resting order | Wait for the TTL (auto-cancel) or cancel manually |
| Trading blocked by risk controls | TESS decision trail; `risk_limit_breached` events | Daily loss, drawdown, exposure caps, cooldown | Wait for the next UTC day / reduce exposure; adjust limits deliberately |
| Dashboard data is stale | Header `○ stream`; Diagnostics jobs | Proxy buffering SSE; backend paused/crashed | Use the provided Caddy config (`flush_interval -1`); restart the backend |
| Scheduler stops running | Diagnostics → `backoff ×N`; `scheduler_error` events | Persistent failure (DB, network) | Read *last error*; fix the cause; jobs recover automatically, or press **Run now** |
| VPS restarts unexpectedly | `last -x \| head`; `journalctl -k \| grep -i oom` | OOM, provider maintenance | Add swap/RAM; ZigGgy auto-starts and never resumes LIVE silently |
| Disk space exhausted | `df -h`; `du -sh data/*` | Old backups, logs | Delete old backups; `KEEP=7 ./scripts/backup.sh`; `docker system prune`; lower retention (maintenance job) |
| Notification delivery fails | Settings → *last error*; `notification_failed` | Wrong bot token or chat id; SMTP auth/port | Re-check the `.env` values; press **Send test notification** |
| Second instance shows READ-ONLY | Diagnostics → Backend | Another process holds the DB instance lock | Stop the other instance; the lock is taken over 90 s after its last heartbeat |

---

## I. Upgrades and maintenance

* **Update:** run `./scripts/update.sh`. It backs up, `git pull --ff-only`, reinstalls pinned deps, rebuilds the dashboard, migrates, and restarts systemd if active. Docker: `git pull && docker compose up -d --build`.
* **Migrate the database:** `.venv/bin/zigggy migrate` (idempotent; runs automatically at every start).
* **Back up / restore:** see [F9](#f9-backups-and-recovery).
* **Rotate API credentials:**
  1. Create a new key at the venue with trading-only permissions and no withdrawals.
  2. Update `.env` and the PEM file in `secrets/`.
  3. Restart.
  4. Press **Settings → Verify**.
  5. Revoke the old key.
  6. To rotate the dashboard token: `zigggy gen-token`, update `.env`, restart.
* **Update dependencies:** `uv pip compile pyproject.toml --extra postgres --extra llm -o requirements.lock` (and `requirements-dev.lock` with `--extra dev`), then run `./scripts/test.sh` before deploying. Frontend: bump versions in `frontend/package.json`, `npm install`, `npm run build`, and run the Playwright test.
* **Rerun tests:** `./scripts/test.sh`.
* **Roll back a failed update:** `git log --oneline` → `git checkout <previous-commit>` → `./scripts/restore.sh data/backups/<pre-update backup>` (only if a migration changed the schema) → `./scripts/update.sh` or `docker compose up -d --build`.
* **Disable live trading safely:**
  1. In the dashboard, **Settings → Stop trading** (cancels working orders) or **E-STOP**.
  2. Set `ZIGGGY_LIVE_TRADING_ENABLED=false` and restart.
  3. Revoke the exchange API key if you won't use it again.

---

## J. Cost and resource requirements

| Item | Required? | Cost | Source |
|---|---|---|---|
| Local machine | — | €0 (uses about 100–150 MB RAM, low CPU) | measured in development |
| VPS (1–2 vCPU, 2 GB) | optional | typically about €4–12 / US$5–12 per month | **estimate**; check provider pricing |
| Market data (Polymarket, Kalshi, Coinbase, Kraken, Open-Meteo) | yes | €0 within public rate limits | public APIs (Open-Meteo: free for non-commercial use) |
| Exchange fees (live only) | live only | Kalshi: modelled as `ceil(0.07 × C × P × (1−P))` dollars per order (series multiplier applied). Polymarket: `rate × (P(1−P))^exponent` per share on fee-enabled markets, 0 otherwise | implemented from the official SDK models; **verify the current schedules** before trading |
| LLM rules review | optional | `claude-opus-5-5` at $4 / $20 per M input/output tokens: about $0.01 per review; the cap of 20/h bounds the worst case to about $0.20/h. `claude-haiku-5-5` ($0.10 / $0.50) is about 40× cheaper | Anthropic pricing table (Oct 2026); reviews are cached per market |
| Notifications (Telegram / SMTP) | optional | €0 (Telegram); your mail provider | — |
| PostgreSQL / monitoring | optional | €0 self-hosted; managed DBs extra | — |
| Trading capital | live only | your risk capital; can be lost entirely | — |

---

## K. Operating command reference

```bash
./scripts/install.sh                         # install / reinstall
./scripts/start.sh                           # start (foreground)
docker compose up -d --build                 # start (Docker, always-on)
docker compose stop | restart zigggy         # stop / restart
sudo systemctl start|stop|restart zigggy     # systemd
curl -s http://127.0.0.1:8000/api/health     # health (public)
.venv/bin/zigggy status                      # mode, equity, providers (uses token from .env)
docker compose logs -f zigggy                # tail logs (Docker)
journalctl -u zigggy -f                      # tail logs (systemd)
./scripts/test.sh                            # run tests
.venv/bin/zigggy run-cycle                   # one discovery+scan cycle without the server
.venv/bin/zigggy backtest [--start ISO] [--end ISO] [--real-only]
.venv/bin/zigggy import-history --markets 100
./scripts/backup.sh                          # back up
./scripts/restore.sh <file>                  # restore (service stopped)
./scripts/update.sh                          # update
.venv/bin/zigggy gen-token                   # new API token
```

API (bearer token): `GET /api/overview`, `/api/markets`, `/api/opportunities`, `/api/portfolio`, `/api/orders`, `/api/orders/{id}/trace`, `/api/agents`, `/api/health/details`, `/api/events`, `GET /api/stream` (SSE). Controls: `POST /api/control/{pause,scanning,mode,emergency-stop,emergency-reset,run-cycle,run-job/{name},verify/{venue}}`, `PUT /api/settings`. Interactive docs: `/api/docs`.

---

## Live trading activation (real money)

> Read this whole section first. Live trading can lose all deposited funds, and paper results do not transfer to live trading.

1. **Eligibility:** confirm you may legally trade on the venue from your location and residence.
   * Polymarket's official geoblock documentation lists **Germany** among fully blocked countries for order placement. Check `https://polymarket.com/api/geoblock` from your server.
   * Kalshi requires an eligible, KYC-verified account.
2. **Credentials (trading-only, no withdrawal permission):**
   * Kalshi: create an API key in the account settings and save the private key as `secrets/kalshi.pem` (`chmod 600`). Set `ZIGGGY_KALSHI_KEY_ID`, and set `ZIGGGY_KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi.pem` (Docker: `/secrets/kalshi.pem`).
   * Test first against the Kalshi demo environment by setting `ZIGGGY_KALSHI_API_URL=https://demo-api.kalshi.co/trade-api/v2`.
   * Polymarket: use a dedicated wallet, run `pip install 'zigggy[polymarket-live]'`, and set `ZIGGGY_POLYMARKET_PRIVATE_KEY`.
3. **Set conservative limits:** small max position, small max daily loss, Kelly ≤ 0.25.
4. **Enable the server switch:** set `ZIGGGY_LIVE_TRADING_ENABLED=true` and restart.
5. **Activate:** go to **Settings → Live mode…**, choose the venue and type `ENABLE LIVE TRADING`. ZigGgy then verifies connectivity (balance call), reconciles balance, positions and open orders, and only then switches. Every live event and notification is tagged `[LIVE]`.
6. **Continuous safety:** reconciliation runs every 60 s; a mismatch pauses new trades. Daily loss and drawdown limits block new entries. E-STOP cancels working orders. After any restart LIVE is **not** resumed unless `ZIGGGY_LIVE_RESUME_ON_RESTART=true` **and** verification and reconciliation succeed.

Status in this delivery: live execution is implemented and unit/integration-tested against mocked APIs and a fake venue. It has **not** been verified against a real exchange account. See `docs/READINESS.md`.
