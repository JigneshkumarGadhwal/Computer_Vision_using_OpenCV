# ZigGgy readiness report

Date: 2026-10-21. This report records what was built, what was actually executed and verified, and what remains unverified. Nothing here is a claim of profitability.

## 1. Verified in the build environment

| Area | Evidence |
|---|---|
| Backend installs and starts | Fresh `git clone` → `./scripts/install.sh` (31 s) → `./scripts/start.sh`. `/api/health` returned `ok`; `zigggy status` worked |
| Database and migrations | Alembic 0001 → 0002 applied on SQLite and **PostgreSQL 16**. `alembic check` reports no drift. Upgrading a live soak DB preserved all rows |
| Test suites | **94 backend tests pass on SQLite and on PostgreSQL**: 58 unit, 35 integration with mocked exchange APIs, 1 end-to-end paper lifecycle. **2 Playwright browser E2E tests pass**: the dashboard against a real backend, with controls checked against backend state, plus the token login gate and live stream on an authenticated server |
| Frontend | `tsc -b` clean; `vite build` succeeds; desktop (1440 px) and mobile (390 px) screenshots show no console errors and no horizontal overflow |
| Container | Image built (multi-stage, non-root `zigggy` user). Ran with token auth, healthcheck `healthy`, restart policy `unless-stopped`, named volume persistence across `docker stop/start`, graceful SIGTERM shutdown, migrations shipped in the package. The final image was built from the shipped Dockerfile and verified |
| Paper lifecycle | Automated: discovery → evidence → estimate → TESS approval → simulated fill → resolution → settlement, plus a full audit-chain check (`tests/e2e`). Live soak on the SIM venue: see §3 |
| Safety controls | Tested: pause, E-STOP (cancels orders, blocks trading, persists until reset), reset forces STOPPED, LIVE refused without the server switch, credentials, verification and confirmation; LIVE never resumed after restart; second instance forced READ-ONLY; idempotent `client_order_id`; uncertain submissions → `UNKNOWN` → reconciled, never resubmitted; settings validated and audited |
| Failure handling | Tested: 429 retry with `Retry-After`; GET backoff; POST never retried; proxy/DNS/connect errors become provider errors; one failing venue never blocks the others (verified live in this sandbox, where both real venues are blocked) |

## 2. Implemented but NOT verified against real services

| Item | Status | Why not verified | Activation step |
|---|---|---|---|
| Polymarket market data (Gamma + CLOB) | Implemented; contract-tested with fixtures shaped from the official `polymarket-client` SDK models | The build sandbox's egress policy blocks `*.polymarket.com` (proxy 403), and docs.polymarket.com does not resolve there | Run ZigGgy on a normal network; check *Diagnostics → Market-data connectivity* |
| Kalshi market data | Implemented to the official `kalshi_python_sync` 3.34.0 OpenAPI models (`orderbook_fp`, `*_dollars`, `/events?with_nested_markets`) with fallbacks | Same egress block | Same |
| Kalshi live trading (RSA-PSS signing, v2 orders, cancel, status, fills, balance, positions, reconciliation) | Implemented; signing verified cryptographically in tests; order body tested; lifecycle tested with a fake venue | No account or credentials; network blocked | Kalshi API key → `.env` → test on the **demo** environment (`ZIGGGY_KALSHI_API_URL=https://demo-api.kalshi.co/trade-api/v2`) → `ZIGGGY_LIVE_TRADING_ENABLED=true` → Settings → Live |
| Polymarket live trading | Implemented via the official SDK (optional extra) | No wallet; **order placement is geoblocked in Germany** per Polymarket's docs | Only where legally permitted: `pip install 'zigggy[polymarket-live]'`, set a dedicated wallet key |
| Evidence sources (Coinbase, Kraken, Open-Meteo) | Implemented with caching, cross-checks and fallbacks; parsing unit-tested | Egress blocked | Automatic on a normal network |
| Optional LLM rules review | Implemented (structured JSON output, rate cap, cache, refusal handling, failure isolation); tested with a mocked client | No API key used | `ZIGGGY_LLM_PROVIDER=anthropic`, `ZIGGGY_ANTHROPIC_API_KEY` |
| Telegram / email notifications | Implemented; filtering, prefixes, deduplication and failure isolation tested | No bot or SMTP credentials | Set in `.env`, then Settings → Send test notification |
| History importer (`zigggy import-history`) | Implemented; tested with mocked HTTP, including no post-resolution leakage | Egress blocked | Run on a normal network |

**Live trading status: NOT configured, NOT verified.** The application ships in PAPER mode, with `ZIGGGY_LIVE_TRADING_ENABLED=false`.

## 3. SIM soak (wall-clock run on the simulated venue)

A clean run of `zigggy serve` with `ZIGGGY_PROVIDERS=sim` and the scheduler on (scan every 15 s) ran from **01:42 to 02:04 UTC** on 2026-10-21, using real wall-clock time with no test hooks:

| Metric | Result |
|---|---|
| Scheduler jobs | 9 registered, **0 failures**; agents: **0 errors** |
| Orders | 8 submitted (paper), 8 filled (simulated), 0 rejected, 0 unknown |
| Resolutions | 2 SIM markets resolved at 02:02 and were settled automatically by the next settlement run (02:03:34) |
| Settlement correctness | NO ×320 (cost $211.68) → payout $320.00, **+$108.32**; YES ×179 (cost $102.73) → payout $0, **−$102.73** |
| Accounting identity | starting capital + realized P&L = cash + open cost basis = **$10,005.59** (exact) |
| Fees (simulated, Kalshi-style quadratic) | $27.97 |

An earlier soak on the pre-fix build also exercised a restart with open positions. It exposed two defects, both fixed and covered by regression tests: SIM state not persisted across restarts, and an infinite profit factor breaking JSON. The two settled trades above are a sample of two on simulated data. They demonstrate correct mechanics only, and say **nothing** about profitability.

## 4. Known limitations

1. **Real-venue connectivity is the main unverified dependency.** Field names come from the venues' official SDKs (October 2026 versions), and fallbacks exist for query-parameter differences. A schema change shows up as provider errors in Diagnostics. It never produces silent bad trades, because stale or missing books are rejected.
2. **Edge is genuinely scarce.** ZigGgy abstains on most markets: it only trades where an independent model applies, and the edge must survive fees and the market prior. Expect long stretches with zero qualified opportunities on efficient markets. That is the system working, not failing.
3. **Model scope.** The quantitative models cover crypto price thresholds, daily-high temperature markets, operator forecasts, confirmed cross-venue links and empirically calibrated categories. Politics, sports and most economics markets therefore rely on calibration history, which takes weeks of recorded resolutions to accumulate, or on operator forecasts.
4. **Weather station mapping is approximate** (Open-Meteo grid point vs. the exact ASOS station), so its evidence quality is "medium". Polymarket crypto markets are parsed from free text; unparseable markets abstain.
5. **Paper fills are taker-only against displayed depth.** There is no queue-position modelling for passive fills, which is conservative. Real latency and adverse selection are not modelled beyond the slippage buffer.
6. **Polling, not streaming.** The default is 45 s; this is adequate for multi-hour horizons and unsuitable for latency-sensitive strategies.
7. **Multi-leg arbitrage is reported, not auto-executed**, because of leg risk.
8. **Docker image build in this sandbox** needed the sandbox's egress proxy passed as build args (a property of this environment, not of the project). On a normal host, `docker compose up -d --build` needs no extra settings. The final image (after the rename) was built from the shipped Dockerfile and verified: healthy, non-root, auth enforced, trading on SIM.
9. **Backtests need recorded data.** History accrues while ZigGgy runs; imported Polymarket history is price-only, with assumed spread and depth (flagged in reports).
10. **Single process / single writer.** This is fine for one operator. Horizontal scaling is out of scope.
