# API reference

Base URL: `http://localhost:8000`. Interactive OpenAPI docs: `http://localhost:8000/docs`.
All responses are JSON unless noted. Errors use `{"detail": "..."}` with HTTP 404 (not found) or 422 (invalid
input / data).

## Health & configuration

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | DB ping, server time, running evaluations with progress |
| GET | `/api/config` | Pre-registered protocol, reference RTP, models, feature definitions, disclaimer |
| GET | `/api/docs/{methodology\|limitations\|api\|troubleshooting\|readme}` | Project documentation (text) |

## Data sources & import

| Method | Path | Description |
|---|---|---|
| GET | `/api/sources` | All sources with round counts, verified counts, period |
| GET | `/api/sources/{id}` | One source |
| GET | `/api/sources/{id}/rounds?limit&offset&newest_first` | Cleaned rounds |
| GET | `/api/sources/{id}/analysis?threshold=2` | Exploratory analysis + dependence tests + fairness audit |
| GET | `/api/sources/{id}/quality` | Import jobs and data-quality reports |
| POST | `/api/import/preview` | multipart: `file`, optional `source_name`, `default_timezone`. Validates, stores nothing |
| POST | `/api/import/commit` | multipart: `file`, `source_name`, `source_kind` (`user_upload`, `historical_export`, `authorized_api`, `manual`, `live_feed`), optional `default_timezone`, `platform`, `game_version`, `description` |
| POST | `/api/demo/load` | `{"generator": "independent"\|"regime", "n_rounds": 6000, "seed": 7}` → synthetic source (always flagged) |

Accepted file columns (case-insensitive aliases): `round_id` (round, id, round_number, game_id), `multiplier`
(crash, crash_point, result, odds, coefficient, x), `timestamp` (time, datetime, date, created_at; ISO-8601 or
epoch s/ms), `timezone`, `session_id`, `collected_at`, `verified`, `server_seed_hash`, `server_seed`,
`client_seeds`, `nonce`. JSON may be a list of objects, `{"rounds": [...]}`, or a plain list of multipliers.

## Live observation

| Method | Path | Description |
|---|---|---|
| POST | `/api/live/{source_name}/rounds` | `{"multiplier": "2.31x", "round_id"?, "timestamp"?, "session_id"?, "verified"?, "source_kind": "manual"\|"live_feed"\|"authorized_api"\|"public_observation"}` — a **completed** round. Resolves pending live predictions |
| GET | `/api/live/status` | Feed status for all sources (`data_mode`: live / delayed / historical / manual / synthetic; `stale`) |
| GET | `/api/live/status/{id}` | Feed status for one source |

## Evaluation

| Method | Path | Description |
|---|---|---|
| POST | `/api/evaluations` | `{"source_id", "thresholds"?, "primary_threshold"?, "dev_fraction"?, "initial_train_size"?, "block_size"?, "min_test_rounds"?, "models"?, "include_regression"?}` → 202 `{"run_id"}`; runs in background |
| GET | `/api/evaluations?source_id` | Runs (status, progress, conclusion) |
| GET | `/api/evaluations/{id}` | Full run: protocol, summary (per-threshold development & test metrics, calibration tables, per-period results, status and reasons), fingerprint, code version |
| GET | `/api/evaluations/{id}/results` | Validation-result rows (train / validation / test per model × threshold) |
| GET | `/api/evaluations/{id}/report` | Markdown report (text/plain) |
| GET | `/api/models?source_id&run_id&selected_only` | Model registry |

## Signals & predictions

| Method | Path | Description |
|---|---|---|
| GET | `/api/signals/{source_id}?threshold=2&persist=true` | Signal for the next round. Stores the model + baseline prediction immutably (idempotent per round). Fields: `status`, `status_reasons`, `probability_reach`, `probability_crash_before`, `baseline_probability_reach`, `historical_frequency`, `model_name`, `model_version`, `training_data_cutoff_*`, `data_mode`, `feed`, `out_of_sample`, `uncertainty`, `recent_live_performance`, `prediction_id`, `prediction_hash`, `warnings`, `disclaimer` |
| GET | `/api/predictions?source_id&mode&run_id&phase&model_name&threshold&limit` | Stored predictions with outcomes |
| GET | `/api/predictions/export.csv?...` | Same, as CSV |

## Backtests

| Method | Path | Description |
|---|---|---|
| POST | `/api/backtests` | `{"run_id", "threshold": 2.0, "stake": 1.0, "margins": [0,0.02,0.05,0.1], "tie_counts_as_win": true, "max_net_win": null, "fee_per_bet": 0, "decision_latency_s": null}` |
| GET | `/api/backtests?run_id` | Backtests (without bet logs) |
| GET | `/api/backtests/{id}?include_bets=false` | One backtest incl. audit |
| GET | `/api/backtests/{id}/bets.csv` | Every simulated bet |

## Reference documentation

| Method | Path | Description |
|---|---|---|
| POST | `/api/reference/collect` | `{"urls": [...], "render": "auto"\|"never"\|"always", "follow_doc_links": true, "max_pages": 6}` → `{documents, errors, context}`; errors carry `reason` (`network_blocked`, `robots_disallowed`, `access_challenge`, `fetch_failed`) |
| GET | `/api/reference` | Stored documentation records (summary facts, links, compact context) |

## System

| Method | Path | Description |
|---|---|---|
| GET | `/api/overview/{source_id}?threshold` | Overview tiles |
| GET | `/api/system/events?limit` | Import/evaluation/feed events |
| GET | `/api/system/integrity` | Ledger-wide check: no outcome recorded before its prediction, all feature cutoffs before targets |
