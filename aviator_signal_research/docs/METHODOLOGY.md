# Prediction methodology

This document describes exactly what the system predicts, how it is evaluated, and why the evaluation is designed
the way it is. Every number shown in the dashboard is produced by the procedure below.

## 1. Targets

| # | Target | Type | Role |
|---|--------|------|------|
| 1 | Exact crash multiplier of the next round | continuous (modelled as `log(multiplier)`) | secondary research task |
| 2–5 | Next round reaches 1.5x / 2x / 3x / 5x | binary | primary evaluation tasks |
| 6 | Probability of reaching any configurable threshold M | binary | `AVR_THRESHOLDS`, API `thresholds` |
| 7 | Are the probabilities reliable enough to justify a research signal? | decision | signal status |

**Convention:** a cash-out at M succeeds iff `crash multiplier ≥ M` (configurable tie rule in the backtester).
Threshold prediction and exact-multiplier estimation are evaluated separately and never mixed: a low error on
`log(multiplier)` says nothing about whether a cash-out decision has positive expected value.

## 2. Data

* Raw files are stored byte-for-byte (`data/raw/`) and row-by-row (`raw_records`), separate from the cleaned
  `rounds` table.
* Validation rejects (never imputes) missing/invalid/out-of-range multipliers, missing identifiers, unparseable
  timestamps, duplicates, conflicting observations (same id, different multiplier — *both* rejected), conflicts
  with stored rounds and back-filled rounds. It warns on out-of-order timestamps, identifiers that disagree with
  time order, duplicate timestamps, assumed time zones, runs of identical values and repeated blocks.
* Each round gets a gap-free chronological `sequence` within its source. Missing round identifiers are reported
  but **not** filled; lag features treat the stored sequence as contiguous.
* Synthetic and real data can never share a source.

## 3. Generation mechanism (verified for Spribe's Aviator demo)

Primary sources (collected 2026-10-21, see `docs/COLLECTION.md`):

* spribe.co/games/aviator: *"Return to Player (RTP) 97%"*.
* In-game "What is Provably Fair": the operator generates a server seed; its SHA-256 hash is published before the
  round; at round start it is merged with the client seeds of the first 3 bettors, a SHA-512 hash is computed,
  "and from this hash — game result".
* In-game round detail / `roundFairnessResponse` messages: revealed server seed, client seeds, combined hash, the
  13-hex prefix and its decimal value, and the result.

The last step is not spelled out in the documentation. It was reconstructed and **verified exactly on 16 published
demo rounds** (`backend/tests/data/spribe_fairness_samples.json`, test `test_spribe_derivation_reproduces_real_published_rounds`):

```
H      = SHA512(server_seed + client_seed_1 + client_seed_2 + client_seed_3)     (16/16)
h      = int(H[:13], 16)                                  # first 52 bits          (16/16)
result = max(1.00, floor(97 · 2^52 / (2^52 − h)) / 100)                            (16/16)
```

(The published intermediate `number` equals `99 · 2^52/(2^52 − h)`; the result uses 97, matching the stated RTP.)

**Consequence.** If SHA-512 output is indistinguishable from uniform and the server seed is unknown before the
round, then `u = h/2^52 ~ U(0,1)` independently per round and

```
P(X ≥ m) = P(97/(1−u) ≥ 100·m) = 0.97 / m      for m ≥ 1.01
P(X = 1.00) = 1 − 0.97/1.01 ≈ 3.96%   (results are floored to 2 decimals, so everything below 1.01x shows as 1.00x)
```

which is exactly the reference distribution used throughout. Past rounds carry no information about the next one;
predicting a round would require the server seed, i.e. inverting its published SHA-256 commitment. This is why the
expected outcome of the walk-forward evaluation on real data is NO DEMONSTRATED EDGE, and why the evaluation is
still worth running: it tests empirically whether the implementation behaves as documented.

Caveats: verified on the **demo** server only (operators may configure a different RTP); in rounds with fewer than
3 early bettors the client seeds are fewer or absent (one sampled round had none), so the server seed alone decides
the result — still fixed in advance by its published hash, but commitment checks require recording the pre-round
hash, which the current collector does not yet do.

The fairness audit (`app/analysis/fairness.py`) re-derives every stored round that has revealed seeds
(`spribe_aviator_v1`) and reports mismatches; generic SHA-256/SHA-512 commitment checks run when a pre-round hash is
stored.

## 4. Features (all strictly causal)

For target round *t*, every feature uses rounds `0 … t−1` only (series are shifted by one round *before* any
rolling/expanding aggregation). The timestamp of round *t* itself is never used, because some sources record the
round *end* time, which encodes the flight duration and thus the multiplier.

Families: lagged log-multipliers (1–5), lagged threshold hits (1–3) for 1.5/2/3/5/10x, rolling threshold
frequencies (10/50/200 rounds), rolling mean/median/std of log-multiplier, low/high streak lengths, rounds since
the last ≥10x, hour-of-day of round *t−1*. Baselines additionally use the expanding (Laplace-smoothed) and
500-round rolling frequencies. Feature definitions and availability are stored in the `feature_definitions` table
and listed on the Config page.

**Look-ahead audit.** Every evaluation recomputes features on truncated copies of the data where round *t* is
replaced by an extreme value and all later rounds are removed; any change in row *t* fails the audit and forces
`INSUFFICIENT EVIDENCE`. A test plants a leaky feature and verifies that it (a) produces spectacular fake skill and
(b) is blocked by the audit.

## 5. Models

| Family | Models | Notes |
|--------|--------|-------|
| Baselines | expanding frequency (**primary baseline**), 500-round rolling frequency, training-window frequency, reference constant `RTP/M` | never eligible for selection |
| Statistical | 1st-order Markov conditional probability, 2-state Bernoulli HMM (Baum–Welch, causal forward filter), L2 logistic (C chosen by inner `TimeSeriesSplit` CV), L1 logistic | |
| ML | random forest, histogram GBM, LightGBM — each with Platt calibration fitted on the *latest* 20% of the training window | |
| Ensemble | mean of L2 logistic, HMM, histogram GBM | |
| Optional (off) | RBF SVM (O(n²), subsampled), MLP | not justified by data size / signal strength; enable explicitly |

## 6. Evaluation protocol (pre-registered; stored with every run)

1. **Chronological split.** First 70% of rounds = development; last 30% = final test, untouched by any choice.
2. **Walk-forward in development.** Expanding window from `initial_train_size` rounds (the first 500 are a
   feature warm-up), retrain every `block_size` rounds, optional purge gap. For each block the predictions are
   **committed to the immutable ledger before the outcomes are revealed** (`OutcomeVault` refuses to reveal
   uncommitted rounds or hand out unrevealed labels).
3. **Comparison.** One-sided Diebold–Mariano test on per-round Brier loss vs the expanding-frequency baseline
   (Newey–West variance, Harvey–Leybourne–Newbold correction), moving-block bootstrap CI for the Brier skill score.
   Holm–Bonferroni across all model × threshold comparisons.
4. **Selection.** Per threshold, the non-baseline model with the lowest development Brier score is the single
   candidate for the final test.
5. **Final test.** Walk-forward continues over the test period for the candidate and the baselines (training
   always ends before the predicted block). One DM test per threshold, Holm-adjusted across thresholds.
6. **Signal status per threshold.**
   * `INSUFFICIENT EVIDENCE` — fewer than `min_test_rounds` test rounds, failed look-ahead audit, failed
     data-quality gate (> 5% warning rows), stale feed, stale/missing/tampered model.
   * `RESEARCH SIGNAL` — Brier skill > 0 **and** Holm-adjusted DM p < 0.05 **and** calibrated on the test period
     (Hosmer–Lemeshow p ≥ 0.05 and |mean predicted − observed| ≤ 0.03).
   * `NO DEMONSTRATED EDGE` — otherwise.

*Design note:* ECE is reported but not used as a gate. Its noise floor for a perfectly calibrated model at ~1,000
rounds is ≈0.035, so a fixed ECE cut-off rejects good models on small samples. This choice was made while testing
the framework on synthetic data, before any real data was evaluated.

## 7. Metrics reported

Brier score, log loss, ROC-AUC, ECE, calibration slope/intercept, calibration-in-the-large, Hosmer–Lemeshow,
reliability table (equal-frequency buckets with Wilson CIs), precision/recall/confusion at p = 0.5 and at the
break-even probability 1/M, per-sub-period performance with base-rate shift, training (in-sample, last fold) vs
validation vs final-test performance, sample sizes and sequence/time ranges. Exact multiplier: MAE/RMSE on log
scale, median absolute error, pinball loss at q = 0.1/0.5/0.9 and interval coverage.

## 8. Signals, backtests and live use

* A signal is produced for the next sequence position from the final model (trained on all data at evaluation
  time), stored immutably with timestamp, model version, data cutoff and content hash, together with the baseline's
  prediction for later paired scoring. Asking twice for the same round returns the stored prediction.
* Outcomes are attached only from rounds ingested *after* the prediction was created.
* The backtester replays the stored final-test predictions round by round (decision → reveal → settle), compares a
  model EV gate (`p·M − 1 > margin`), a baseline EV gate and always-bet at a fixed threshold, and is independently
  re-computed from the raw `rounds` table. Stakes are fixed; martingale is not supported.
