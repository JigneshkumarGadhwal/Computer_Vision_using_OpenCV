# Limitations and external dependencies

## What this project cannot know

* **Real data are demo-server observations, not an operator export.** 3,000 rounds were observed on Spribe's
  public demo (`data/observed/spribe_demo_rounds.csv`, source `spribe-demo-observed`) with collection gaps
  (container restarts, one demo-server outage; see `docs/COLLECTION.md`). They answer the research question for
  the demo server only; a real-money operator may configure a different RTP.
* **The generation mechanism is verified for the demo server only.** The 97% RTP is stated on spribe.co, and the
  seed→multiplier formula was reconstructed and reproduced exactly on 16 published demo rounds (METHODOLOGY §3).
  Real-money operators may configure a different RTP, and pre-round server-seed commitments are not yet recorded,
  so "the server seed was fixed before the round" is documented but not independently checked here.
  Note the instant-crash share: results are floored to 2 decimals, so P(1.00x) = 1 − 0.97/1.01 ≈ 3.96%, not 3%. Other
  "Aviator"-branded games may use different software.
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
| Real historical data | **3,000 observed demo rounds** (≥96% fairness-verified) | An authorised operator export of ≥10,000 consecutive rounds for a real-money conclusion |
| Live feed | `POST /api/live/{source}/rounds`, `FeedAdapter`, and the `observe` collector for public pages | The demo host `demo.spribe.io` (domain `spribe.io`) must be allowed in the environment's network policy; check the site's terms; demo-page rounds are not operator rounds |
| Official documentation | `collect-docs` collector implemented and tested on fixtures | Same network access; extracted RTP/fairness facts must be confirmed by a human |
| Fairness verification | `spribe_aviator_v1` derivation verified (16/16 demo rounds); `observe --verify-fairness` re-derives every observed round | Record the pre-round "next server seed SHA256" to check commitments; verify on a real-money operator |
| Platform payout rules | simplified (`S·(M−1)` / `−S`, optional cap/fee/tie rule) | The operator's documented settlement rules, limits and fees |
| RTP / reference distribution | RTP 97% stated on spribe.co; `P(X≥m)=0.97/m` follows from the verified formula (demo server) | Operators may configure a different RTP |
| PostgreSQL | supported via `AVR_DATABASE_URL` (immutability triggers included) | `pip install psycopg[binary]` and a server; Alembic migrations recommended for schema changes |
| Authentication | none | Add before exposing the API beyond localhost |

## Out of scope by design

No automated betting, no account connections, no stake escalation (martingale), no bypassing authentication or
access controls, and no attempt to obtain future seeds or private server state.
