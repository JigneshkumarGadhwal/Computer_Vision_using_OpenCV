# ARGONA architecture & design decisions

## 1. System overview

```
             ┌────────────────────── argona serve (one process) ──────────────────────┐
 Polymarket ─┤ exchanges/  (adapters: public data | trading clients, capability registry) │
 Kalshi ─────┤      │                                                                    │
 SIM ────────┤      ▼                                                                    │
             │ market_data/ ── RIGO ──► SPREAD.assess ──► BRAM ──► ILSA ──► TESS ──► SPREAD.execute
 Coinbase ───┤                (books)   (microstructure)  (evidence) (fair value) (risk veto) (orders)
 Kraken ─────┤ research/ ────────────────────────────┘        │            │            │
 Open-Meteo ─┤                                     probability/ spread_engine/ risk/  execution/ ─► venue / paper sim
 Anthropic* ─┤                                                                          │
             │ portfolio/ (accounting) ◄──────────── fills / settlements ───────────────┘
             │ scheduler/ (jobs) · events (SSE + DB) · notifications · backtesting · api/ (REST, SSE, static UI)
             └───────────────────────────── SQLite / PostgreSQL ──────────────────────────────────┘
```
\* optional

**Why one process?** The prompt's target is "one technically capable individual". A single process with an in-process scheduler, one database and one container has the fewest moving parts. Restarts are handled by Docker or systemd. Concurrency comes from asyncio, and the workload is mostly I/O-bound polling. A database instance lock prevents two processes from trading the same account.

**Why polling, not WebSockets?** Polymarket's and Kalshi's streaming endpoints need authentication or per-market subscriptions. Polling the batch book endpoints every 45 s, with prioritised watchlists, is well within the public rate limits, simpler to make reliable, and adequate for the strategy horizon (hours to days). The adapter interface allows a streaming implementation to be added later.

## 2. Exchange layer

* The `MarketDataAdapter` (public) and `TradingAdapter` (credentialed) interfaces are separate. Only `ExecutionService` builds trading adapters (`ProviderRegistry.build_trading_adapter`). Agents never see credentials.
* **Kalshi:** discovery uses `GET /events?status=open&with_nested_markets=true`, which provides category, `mutually_exclusive` and settlement sources. Books come from `GET /markets/orderbooks?tickers=…`, with a fallback to `/markets/{ticker}/orderbook`. Parsing uses the fixed-point dollar fields of API 3.34 (`yes_bid_dollars`, `orderbook_fp.yes_dollars`, …) with a legacy-cents fallback. Multivariate combo markets are excluded. Orders use the v2 book-side endpoint `POST /portfolio/events/orders`: a YES bid at price *p*, or an ask, where buying NO at *p* equals selling YES at 1−*p*. Requests use RSA-PSS request signing. Field names were taken from Kalshi's official OpenAPI-generated SDK (`kalshi_python_sync` 3.34.0), because the public docs site was unreachable from the build environment.
* **Polymarket:** discovery uses Gamma `/markets` (active, closed=false, ordered by 24 h volume). Books use the CLOB `POST /books` batch with a fallback to `/book`. Only the YES token book is fetched, and NO is derived as the complement. Fee schedule comes from `feesEnabled` + `feeSchedule{rate, exponent}`. Trading is delegated to the official `polymarket-client` SDK (`AsyncSecureClient.place_limit_order`, `get_order`, `list_open_orders`, `get_balance_allowance`) as an optional dependency, because EIP-712 order signing should not be re-implemented. Polymarket has no client order id, so uncertain submissions are reconciled via the order id or open-order matching.
* **SIM:** a deterministic, persisted simulated venue. It has a GBM underlying (`SIMBTC`) and forecast markets with hidden true probabilities. Market-maker quotes follow a mean-reverting deviation from the truth. Everything it produces is flagged `is_simulated`, and it has no trading adapter, so it can never be traded live.
* **Capability registry:** `Capabilities` per venue (discover, orderbook, batch, streaming, place/cancel, fills, balances, positions, client order id, market orders, demo env, notes) is surfaced in Settings and Diagnostics.
* **HTTP client:** per-venue token-bucket rate limiting. GETs retry with exponential backoff and full jitter, and honour `Retry-After` on 429. Non-idempotent requests are **never** retried: a timeout or 5xx on order submission raises `SubmissionUncertain`, and the order becomes `UNKNOWN`.

## 3. Market discovery & eligibility

A market is eligible only if all of these hold:
* its category is enabled;
* its status is open;
* it has a resolution time in (now, now + max_days];
* it has non-empty resolution criteria (rules);
* the rules contain no ambiguity patterns ("at its sole discretion", "subjective", …) and carry no LLM flags;
* it has quotes;
* on Polymarket, it has a token id.

Rejected markets keep their flags, and the dashboard shows counts per reason. The watchlist for book refreshes is ordered as follows, capped at `ARGONA_MAX_BOOKS_PER_CYCLE`:
1. markets held or with working orders;
2. markets with an independent model;
3. linked markets;
4. 24 h volume.

## 4. Probability engine (ILSA)

Models return either an estimate (`q`, standard error `σ`, evidence quality, inputs, evidence with provenance) or an explicit **abstention with a reason**.

| Model | Applies to | Method | Evidence |
|---|---|---|---|
| `market_implied` (prior) | all | Mid, with σ = max(spread/2, *market prior σ*) + 0.03 if depth < $500 | order book |
| `crypto_lognormal` | crypto thresholds (Kalshi structured strikes; Polymarket text parsed: above/below/between/reach/dip) | Driftless lognormal: P(S_T>K)=N(d₂); ranges; reflection principle for touch markets. σ from 7-day hourly realised vol; uncertainty from a vol band ×0.8/×1.25 plus a settlement-index basis term | Coinbase spot (Kraken cross-check / fallback), candles |
| `weather_daily_high` | daily-max temperature (Kalshi KXHIGH* series with station mapping; Polymarket "Highest temperature in …" with bucket labels) | Normal error around the multi-model mean; s² = σ_lead² + model spread²; integer-rounding bucket semantics (between, ≥, ≤) | Open-Meteo (`best_match`, GFS, ECMWF IFS, ICON) |
| `external_forecast` | any market with a forecast | Uses the forecast's own p and σ | SIM forecaster, or operator file `data/forecasts.json`: `{"kalshi:TICKER": {"p":0.62,"sigma":0.05,"source":"…","as_of":"ISO","reliability":"high"}}` |
| `cross_venue_consensus` | operator-**confirmed** equivalent/complement links | Precision-weighted mids of linked markets | linked order books |
| `empirical_calibration` | categories with ≥ 40 resolved samples per price decile | Beta-binomial posterior of the resolution frequency given price (captures favourite–longshot bias), using prices observed ≥ 1 h before resolution | ARGONA's own resolution history |

**Combination:** a Gaussian Bayesian update. The market is the prior, each independent model is a measurement, and weaker evidence is down-weighted. The engine **abstains** when no independent model applies, so market-implied alone never trades. Confidence = (1 − σ_post/0.12) × evidence-quality weight × an agreement factor (×0.6 when independent models disagree by more than 2σ). Conflicting evidence is recorded. Examples: model disagreement, an estimate more than 25 points from the market, and spot sources diverging by more than 0.5%.

**Multi-outcome coherence:** when all open members of a mutually exclusive event are evaluated together, the estimates are renormalised to sum to 1. The weather model produces a coherent distribution by construction, since every bucket comes from the same normal.

**LLMs:** an LLM never produces probabilities or prices. The optional BRAM review reads only the market's rules text and returns structured flags (`json_schema` output, `claude-opus-5-5` by default with server-side refusal fallback). It is rate-limited and cached, and the flags can only make a market *ineligible*.

**Calibration tracking:** every estimate is stored with model version, inputs, evidence ids and snapshot id. The hourly job computes Brier, log loss and reliability for the ensemble against the market baseline, point in time, using the last estimate made at least 1 h before resolution.

## 5. Expected value & ranking

For buying a $1-payout contract (YES, or NO priced as its own contract) at executable VWAP *v*, with win probability π, per-contract fee *f* and buffer *b*:

* gross edge = π − best ask
* fee-adjusted edge = π − best − f
* **net EV = π − v − f − b** (execution-adjusted)
* return = net EV / (v + f)

A probability above the price is never enough: the candidate must clear net EV ≥ min edge, confidence, price band, spread, liquidity, staleness and time-to-resolution, and each failed condition is recorded as a reason. Exits compare the executable bid net of fees against the hold value π. Ranking score = net EV × confidence × liquidity factor × time factor × (1 + return).

**Arbitrage screen (SPREAD):**
* *Structural*: all YES (pays 1) or all NO (pays n−1) across mutually exclusive outcomes.
* *Cross-venue*: YES on one venue plus NO on a confirmed-equivalent market.

Both include fees and depth and are **never labelled risk-free**. Legs fill sequentially, exhaustiveness and resolution equivalence carry risk, and the flags say so. The screen reports candidates; it does not auto-execute multi-leg trades.

## 6. Sizing & risk (TESS)

Kelly is computed from the payoff: f* = (π − c)/(1 − c), with c = VWAP + fee per contract, and it applies equally to YES and NO contracts. Applied fraction = kelly_fraction × confidence × f* of current equity. Bankroll is current equity, so the system is never levered and never scales up after losses.

Hard checks on every order, paper or live:
* trading allowed (mode, pause, E-STOP);
* venue tradable in the current mode (no SIM in LIVE; live only on the verified venue);
* opportunity qualified;
* book fresh;
* no duplicate working order;
* daily loss and drawdown limits;
* max open orders;
* re-entry cooldown;
* no opposite position;
* exposure headroom: min of max position, per-market, per-event (correlated outcomes), per-category, total, and available cash.

Size then shrinks until the net EV *at that size* (VWAP through the book) still clears the threshold. Limit prices are tick-rounded. Every check and its detail is persisted (`risk_assessments`).

## 7. Execution & accounting

* **Write-ahead orders:** `PENDING_SUBMIT` → submit → `OPEN`, `PARTIALLY_FILLED`, `FILLED`, `CANCELED`, `EXPIRED`, `REJECTED` or `UNKNOWN`. Each order has a deterministic `client_order_id` with a unique constraint. TTL expiry cancels the remainder.
* **Live:** fills come only from the venue's fills endpoint and are applied idempotently by venue fill id. Reconciliation adopts the venue balance and flags position and open-order mismatches, which pause trading.
* **Paper simulator:**
  * Taker fills walk the *current* book up to the limit price.
  * Displayed depth is consumed once per snapshot.
  * Resting remainders fill only if a later book trades through the limit; there are no optimistic queue fills.
  * Fees follow the venue fee model, and slippage is recorded against the best price at decision time.
  * Paper has its own account (`paper`), separate from `live:<venue>`.
* **Accounting:** the ledger is persisted at micro-dollar precision (integer micro-units in the DB).
  * Cost basis includes entry fees. Realised P&L is booked on sells and settlements.
  * Unrealised P&L is marked at the executable bid (the conservative liquidation value).
  * Equity = cash + marked positions. Day P&L is measured from the UTC-midnight snapshot.

## 8. Persistence & audit

The tables are `markets`, `price_observations`, `orderbook_snapshots`, `evidence`, `probability_estimates`, `opportunities`, `agent_decisions`, `risk_assessments`, `orders`, `fills`, `positions`, `account_snapshots`, `settlements`, `events`, `settings_kv`, `config_history`, `backtest_runs`, `model_metrics`, `market_links`, `accounts` and `instance_lock`. Migrations are managed by Alembic (`backend/argona/migrations`) and verified on SQLite and PostgreSQL 16.

The trace endpoint `GET /api/orders/{id}/trace` reconstructs the whole chain: snapshot → estimate (model, version, evidence) → opportunity → risk checks → order → fills. The market and its settlement are linked by key. Secrets are never stored, and log and event text passes through a redaction filter.

## 9. Backtesting

The backtester replays ARGONA's own recorded snapshots, evidence and resolutions with strict point-in-time visibility:
* calibration tables are built only from markets resolved before *t*;
* there is no survivorship filter;
* fills and risk run through the same code paths as live trading.

Imported Polymarket price history (`argona import-history`) is labelled *price-only*, with the assumed spread and depth stored on every snapshot, and only prices strictly before resolution are imported. When no data exists the run says so and produces nothing. It never fabricates data.

## 10. Decisions log

| Decision | Alternatives | Why |
|---|---|---|
| Python/FastAPI + asyncio + SQLAlchemy 2 async; React/Vite/TS/Tailwind/Recharts | — | As specified; mature, typed |
| SQLite default (WAL), PostgreSQL optional | Postgres only | One-person install; Postgres verified with the full test suite |
| Money as integer micro-units | Decimal everywhere | Exact at rest on both DBs without Decimal/float mixing bugs in quant code |
| Precision-weighted market prior (σ floor 0.06, tunable) | Model-only estimates | Prediction markets are informative; avoids overconfident trading on model noise |
| Abstain without an independent model | LLM "opinions" | The prompt forbids treating LLM output as numerical fact |
| Kalshi v2 order endpoint; Polymarket via official SDK | Hand-rolled EIP-712 | Correctness and maintenance of signing |
| Fills from venue fills endpoint only (live) | Use the ack fill counts | Avoids double counting; idempotent by fill id |
| Instance lock in the DB | File lock | Works across containers/hosts sharing one DB |
| Dark-only UI | Light/dark | Product spec: trading-terminal aesthetic |

## 11. Risk register

| Risk | Mitigation |
|---|---|
| API schema drift (both venues migrated fields in 2025–26) | Adapters accept new and legacy fields; contract tests with fixtures; adapter errors surface as provider events and never trade on partial data |
| Jurisdiction / terms violations | Paper by default; live gated by a server switch, typed confirmation and verification; docs call out Polymarket's DE geoblock and Kalshi KYC |
| Model overconfidence | Market prior, quality weights, abstention, min net edge after fees, fractional Kelly, calibration tracking |
| Duplicate or orphaned orders | Write-ahead, deterministic client ids, no blind retries, reconciliation, instance lock |
| Stale data trading | Staleness checks at evaluation, risk and execution time; opportunities expire |
| Correlated exposure | Per-event and per-category caps; no opposite positions; re-entry cooldown |
| Operator error | Validated settings with an audit trail; E-STOP; LIVE never auto-resumes |
| Secret leakage | env-only secrets, redaction filter, loopback default, token auth, no secrets in DB/logs/notifications |
