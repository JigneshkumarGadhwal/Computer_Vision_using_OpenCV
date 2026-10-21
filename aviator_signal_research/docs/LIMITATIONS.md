# Limitations and external dependencies

## What this project cannot know

* **No real Aviator data ships with the project.** All numbers in the bundled report come from clearly labelled
  synthetic data. Whether a real operator's rounds contain predictive information is unknown until you import an
  authorised export and run the evaluation.
* **The generation mechanism is not verified.** The commit–reveal / SHA-512 description and the ~97% RTP are
  secondhand (affiliate/explainer sites). The reference distribution `P(X≥m)=RTP/m` is a hypothesis, not a fact,
  and other "Aviator"-branded games may differ.
* **A statistical anomaly is not an edge.** Dependence tests in the History page can be significant without any
  model improving out-of-sample probabilities enough to matter, and a better Brier score need not survive the
  house edge.
* **A positive Brier skill is not a profitable strategy.** Break-even for a cash-out at M requires `p > 1/M`.
  Under the reference hypothesis `p = 0.97/M`, so a model needs about a 3% relative improvement in the hit
  probability just to break even, and must sustain it out-of-sample.

## Methodological limits

* Walk-forward features within a block are computed from the full round series. Their causality is enforced
  structurally and verified empirically (look-ahead audit), rather than by recomputing features from a truncated
  history at every round, which would be ~1000× slower.
* Missing rounds (gaps in identifiers) are reported but not filled; lag features then skip across the gap.
* Sample size matters: on the planted-signal synthetic data the HMM needs ~1,000+ training rounds before it
  reliably detects a strong regime effect. Weaker real effects need far more data.
* Multiple-comparison control covers the models and thresholds of one run. Re-running many evaluations on the
  same data and reporting the best one re-introduces selection bias; the registry keeps every run so this is
  visible.
* The backtest's actionability check (`decision_latency_s`) only uses gaps between consecutive round timestamps;
  it does not model betting windows, bet limits, server latency or partial cash-outs.
* Exact-multiplier models target `log(multiplier)`; heavy tails make raw-scale means unreliable.

## Components that need real data or external integration before production use

| Component | Status | What is needed |
|-----------|--------|----------------|
| Real historical data | **missing** | An authorised export (CSV/JSON) from the operator, or records you collected where permitted |
| Live feed | interface only (`POST /api/live/{source}/rounds`, `FeedAdapter`) | A documented, authorised API or your own permitted collector; no website connector is included |
| Fairness verification | commitment check only | Operator's documented seed→multiplier formula, verified, then `register_derivation()` |
| Platform payout rules | simplified (`S·(M−1)` / `−S`, optional cap/fee/tie rule) | The operator's documented settlement rules, limits and fees |
| RTP / reference distribution | assumed 0.97 | Operator documentation or certification report |
| PostgreSQL | supported via `AVR_DATABASE_URL` (immutability triggers included) | `pip install psycopg[binary]` and a server; Alembic migrations recommended for schema changes |
| Authentication | none | Add before exposing the API beyond localhost |

## Out of scope by design

No automated betting, no account connections, no stake escalation (martingale), no bypassing authentication or
access controls, and no attempt to obtain future seeds or private server state.
