# Final evaluation report

**Date:** 2026-10-22 · **Data:** (A) **3,000 real rounds observed on Spribe's public Aviator demo**, 2,933 of them
fairness-verified; (B) two labelled SYNTHETIC datasets used to validate the framework.
Detailed, auto-generated reports: [`evaluation_run_3.md`](evaluation_run_3.md) (real data),
[`evaluation_run_1.md`](evaluation_run_1.md) and [`evaluation_run_2.md`](evaluation_run_2.md) (synthetic).
Descriptive statistics and fairness audit of the real data: [`real_data_summary.json`](real_data_summary.json).

## Bottom line

> **No predictive improvement was demonstrated on real Aviator data.** On 900 unseen demo rounds the best
> pre-registered model was statistically indistinguishable from the historical-frequency baseline at 1.5x, 2x, 3x
> and 5x. All four thresholds got the status **NO DEMONSTRATED EDGE**, with Holm-adjusted DM p = 0.53 and bootstrap
> CIs for Brier skill that include 0. At 2x the model "called" the same side as the baseline on every round: 49.6%
> accuracy against a 50.4% hit rate.
>
> This is the expected result, and it has a mechanical explanation. Every one of the 2,933 rounds with revealed
> seeds was re-derived exactly from `SHA512(server seed + client seeds)`, with **0 mismatches**. The server seed is
> fixed and hash-committed before the round, so past results carry no information about the next one. The observed
> distribution matches `P(X ≥ m) = 0.97/m` (χ² p = 0.73, KS p = 0.76), and no dependence test is significant.
>
> The framework itself behaves correctly in both directions. It reports no edge on independent rounds, it detects
> a deliberately planted dependence in synthetic data, and it refuses to report a signal when a leakage bug is
> introduced (part B).

---

# Part A — Real data: Spribe Aviator demo (source `spribe-demo-observed`)

## Data

| Item | Value |
|---|---|
| Origin | `https://demo.spribe.io/launch/aviator` (linked from the official spribe.co/games/aviator page), observed with `observe --profile spribe --verify-fairness`; method in `docs/COLLECTION.md` |
| Rounds | **3,000**, 2026-10-21 17:56 UTC → 2026-10-22 14:07 UTC (`data/observed/spribe_demo_rounds.csv`) |
| Fairness-verified | **2,933 (97.8%)**: result re-derived from the revealed server seed + client seeds, **0 mismatches** |
| Not verified (67) | seq 0–60 (61 rounds collected before `--verify-fairness` existed); 3 rounds ≥ 1,000x (6,516.99x, 2,316.89x, 1,101.1x) read before the thousands-separator fix; 3 lookups the game answered as unavailable. Their multipliers still come from the game's WebSocket and were cross-checked against the on-screen history bar |
| Excluded | betting phases without a result (voided rounds, ≈ 1 in 15) and rounds seen only on the history bar during WebSocket reconnects (`dom_only`) |
| Data quality gate | passed (0 warning rows) |

**Collection gaps.** Within the period, rounds were missed during these gaps (> 3 min between consecutive stored
rounds). The rounds are adjacent in the stored sequence, so lag features skip across these points:

| From (UTC) | To (UTC) | Minutes | ≈ rounds missed | Cause |
|---|---|---|---|---|
| 10-21 18:01 | 18:20 | 19.4 | 53 | collector development (first test chunks) |
| 10-21 18:38 | 18:43 | 4.1 | 11 | collector restart between chunks |
| 10-21 18:47 | 18:56 | 9.2 | 25 | collector restart between chunks |
| 10-21 19:01 | 19:08 | 6.6 | 18 | container restart |
| 10-22 05:54 | 06:01 | 7.2 | 20 | demo server outage (game WebSocket reconnect loop) |
| 10-22 09:02 | 10:40 | 98.3 | 268 | container restart (environment down) |

Shorter restarts (≈ 02:59 and 04:04 UTC) cost under 3 minutes each. Because results are independent, the gaps
do not bias the frequency analysis. They only mean the 3,000 rounds are not one unbroken sequence.

## Distribution vs the documented mechanism

| Threshold m | Observed P(X ≥ m) | Expected 0.97/m | k / 3,000 | Binomial p (unadjusted) |
|---|---|---|---|---|
| instant crash (1.00x) | 4.23% | 3.96% | 127 | 0.43 |
| 1.5x | 65.23% | 64.67% | 1,957 | 0.53 |
| **2x** | **49.00%** | **48.50%** | 1,470 | 0.58 |
| 3x | 32.00% | 32.33% | 960 | 0.71 |
| 5x | 19.40% | 19.40% | 582 | 1.00 |
| 10x | 10.13% | 9.70% | 304 | 0.42 |
| 100x | 1.37% | 0.97% | 41 | 0.032 |
| 1,000x | 0.20% | 0.10% | 6 | 0.075 |

Binned goodness of fit χ² p = 0.73; KS test of the probability-integral transform p = 0.76. The ≥ 100x excess
(41 vs 29 expected) has p = 0.032 *before* correction for the 9 thresholds tested. After Holm it is p ≈ 0.29,
which is consistent with chance in a heavy tail. Median 1.94x; maximum 8,530.55x. Note: the instant-crash share
is 3.96%, not 3%, because results are floored to 2 decimals (METHODOLOGY §3).

**Dependence tests** (Holm-adjusted across 7 tests): runs test p = 0.09 (Holm 0.63); Ljung–Box on log-multiplier
p = 0.69 and on 2x hits p = 0.38; Spearman lag-1 p = 0.30; drift homogeneity p = 0.38; CUSUM p = 0.22; time of
day p = 0.91. **None significant.** Hit rate at 2x after 1, 2, 3, 4 and 5 consecutive misses was 46%, 49%, 48%,
50% and 54%, all with CIs covering 48.5%. There is no "due for a high one" effect.

## Walk-forward evaluation (run 3, pre-registered default protocol)

Development seq 0–2099 (2,100 rounds, walk-forward from 1,000, retrain every 250); final test seq 2100–2999
(**900 unseen rounds**, 2026-10-22 07:02 → 14:07 UTC). 12 models × 4 thresholds. Look-ahead audit **PASSED**.
Ledger: 67,200 predictions, 0 outcomes recorded before their prediction.

| Target | Selected model | Test Brier (model / baseline) | Brier skill [95% CI] | Holm DM p | AUC | Calibration | Status |
|---|---|---|---|---|---|---|---|
| ≥1.5x | logistic_l1 | 0.22347 / 0.22359 | +0.00050 [−0.00065, +0.00133] | 0.53 | 0.498 | failed (HL p 0.034) | NO DEMONSTRATED EDGE |
| **≥2x (primary)** | logistic_l1 | 0.25034 / 0.25038 | +0.00015 [−0.00150, +0.00175] | 0.53 | 0.493 | failed (HL p 0.044) | NO DEMONSTRATED EDGE |
| ≥3x | logistic_l1 | 0.22450 / 0.22463 | +0.00061 [−0.00100, +0.00187] | 0.53 | 0.513 | OK (HL p 0.26) | NO DEMONSTRATED EDGE |
| ≥5x | logistic_l1 | 0.16410 / 0.16424 | +0.00089 [−0.00084, +0.00207] | 0.53 | 0.514 | OK (HL p 0.59) | NO DEMONSTRATED EDGE |

In development, **no** non-baseline model beat the baseline at any threshold (every Holm DM p ≥ 0.93). The ML
models were worst: random forest and histogram GBM had negative Brier skill at every threshold (−0.4% to −1.7%),
i.e. they overfit
noise. LightGBM was negative except at 5x (+0.0001). The heavily regularised L1 logistic won selection because its
predictions barely moved away from the base rate, which is the best a model can do when there is nothing to learn.

**Accuracy (plain terms).** At p ≥ 0.5 the selected model's yes/no calls on the 900 test rounds were 66.3% correct
at 1.5x, 49.6% at 2x, 66.1% at 3x and 79.3% at 5x. Those are *exactly* the accuracies of always predicting the more
likely side, i.e. the model never made a call that differed from "always yes" or "always no". High accuracy at 5x
means only "most rounds do not reach 5x".

## Hypothetical backtest (fixed stake 1, final-test rounds, independent audit PASSED in all four)

| Threshold | Always-bet: hit rate | ROI | EV/bet 95% CI | Model EV gate | Baseline EV gate |
|---|---|---|---|---|---|
| 1.5x | 66.3% | −0.5% | −0.058 to +0.037 | 0 bets | 0 bets |
| 2x | 50.4% | +0.9% | −0.076 to +0.078 | 0 bets | 0 bets |
| 3x | 33.9% | +1.7% | −0.083 to +0.113 | 0 bets | 0 bets |
| 5x | 20.7% | +3.3% | −0.094 to +0.161 | 0 bets | 0 bets |

The small positive ROIs are luck in a 900-round window. Every CI includes the −3% house edge, which is the
long-run expectation of every fixed cash-out by construction. The EV gates placed **no** bets because no model
ever estimated `p·M > 1`, which is the correct behaviour.

## Exact multiplier (secondary)

No model beat the training median on absolute error of log(multiplier) on the test period (DM p ≥ 0.9997 for the
mean, ridge and gradient boosting). Test median absolute error was 0.80x for the median baseline vs 1.23–1.32x
for the models.

## Scope of the conclusion

* It applies to Spribe's **demo** server, observed 21–22 Oct 2026. Real-money operators run the same game
  software but may configure a different RTP. The verified mechanism (hash-committed seed → result) leaves no
  route for past results to predict future ones on any correctly operated server.
* 3,000 rounds (900 in the final test) rule out large effects, **not** small ones. The 95% CI for Brier skill at
  2x reaches +0.0018. A model that knew p = 0.50 instead of 0.485 on every round, just enough to break even at 2x,
  would gain only ≈ 0.0009, which this test cannot exclude. The case against any edge rests on the verified
  mechanism plus the absence of any dependence. A sample-size argument alone would not support it.
* Pre-round server-seed hashes were not recorded, so the commitment itself was not independently checked. The
  seed → result derivation was checked for 2,933 rounds.

---

# Part B — Framework validation on synthetic data (2026-10-21)

## Setup (identical for both runs)

| Item | Value |
|---|---|
| Rounds | 10,000 per dataset (`data/demo/*_SYNTHETIC_NOT_REAL.csv`) |
| Development / final test | seq 0–6999 (7,000) / seq 7000–9999 (3,000 unseen rounds) |
| Walk-forward | expanding window from 1,000 rounds (500 warm-up), retrain every 250 rounds |
| Models | 4 baselines + Markov, HMM, L2/L1 logistic, random forest, hist-GBM, LightGBM, ensemble |
| Baseline | expanding-window Laplace-smoothed frequency |
| Signal criteria | Brier skill > 0, Holm-adjusted DM p < 0.05, Hosmer–Lemeshow p ≥ 0.05, calibration gap ≤ 0.03, ≥ 500 test rounds |
| Look-ahead audit | PASSED in both runs (26 positions) |
| Ledger integrity | 672,000 predictions, 672,000 outcomes, 0 outcomes recorded before their prediction |

## Run 1 — independent rounds, P(X ≥ m) = 0.97/m (no signal exists)

| Target | Selected model | Test Brier (model / baseline) | Brier skill [95% CI] | Holm DM p | AUC | Status |
|---|---|---|---|---|---|---|
| ≥1.5x | logistic_l2 | 0.22976 / 0.22940 | −0.00161 [−0.00351, +0.00019] | 1.000 | 0.487 | NO DEMONSTRATED EDGE |
| **≥2x (primary)** | ensemble_mean | 0.25019 / 0.25008 | −0.00046 [−0.00206, +0.00115] | 1.000 | 0.488 | NO DEMONSTRATED EDGE |
| ≥3x | markov_conditional | 0.21908 / 0.21908 | +0.00002 [−0.00142, +0.00141] | 1.000 | 0.493 | NO DEMONSTRATED EDGE |
| ≥5x | hmm_regime | 0.15327 / 0.15330 | +0.00020 [−0.00152, +0.00200] | 1.000 | 0.513 | NO DEMONSTRATED EDGE |

Backtest at 2x (stake 1, 3,000 rounds, audit PASSED): always-bet ROI **−2.07%** (EV/bet CI −0.062 to +0.015),
model EV gate 133 bets, ROI −6.8%. This is consistent with the generator's −3% expected return.

The descriptive dependence tests flagged one test as significant after Holm correction: drift homogeneity,
p_holm = 0.035. That is a false positive on genuinely i.i.d. data. It shows in practice why a "significant anomaly" is
not an edge, and why only out-of-sample prediction counts.

## Run 2 — planted hidden-regime dependence (signal exists by construction)

| Target | Selected model | Test Brier (model / baseline) | Brier skill [95% CI] | Holm DM p | Calibration | Status |
|---|---|---|---|---|---|---|
| ≥1.5x | hmm_regime | 0.21376 / 0.22516 | +0.05064 [+0.03299, +0.07095] | <0.0001 | OK (HL p 0.91) | **RESEARCH SIGNAL** |
| **≥2x (primary)** | ensemble_mean | 0.24597 / 0.25006 | +0.01635 [+0.00773, +0.02488] | 0.0001 | **FAILED** (HL p 0.0075) | NO DEMONSTRATED EDGE |
| ≥3x | logistic_l2 | 0.21830 / 0.21968 | +0.00631 [−0.00068, +0.01282] | 0.0335 | OK (HL p 0.47) | **RESEARCH SIGNAL** (borderline: bootstrap CI includes 0) |
| ≥5x | logistic_l2 | 0.15579 / 0.15724 | +0.00922 [+0.00343, +0.01486] | 0.0016 | OK (HL p 0.62) | **RESEARCH SIGNAL** |

At 2x the model had real skill but failed the pre-registered calibration gate, so the framework withheld the signal
status. That is strict, and intended. Backtest at 2x: model EV gate 1,404 bets, ROI **+13.8%** (EV/bet CI +0.091
to +0.200), always-bet −0.07%. **This profit is an artefact of the generator.** Its hot regime has RTP 1.25, so the
average return is ≈ 1.0 rather than 0.97, and a model that tracks the regime profits by design. It says nothing about
any real game.

## Secondary task: exact multiplier

On both datasets no point model beat the training-median baseline on absolute error of log(multiplier). The DM
p-value was 1.0 for ridge, gradient boosting and the mean. Even where threshold probabilities are predictable
(run 2), the heavy-tailed point value is not usefully predictable.

## Tests actually executed (2026-10-21)

* Backend: `python -m pytest` → **56 passed** (17 min 55 s on a shared 4-core container). Covers ingestion and
  validation, feature causality, a planted-leak audit, metrics against sklearn/statsmodels, walk-forward vault and
  ledger, no false skill on independent data, planted-signal detection on 4 seeds, a leakage bug that cannot produce
  a signal, reproducibility, DB-level immutability, backtest maths and a tampered-settlement audit, signal lifecycle,
  stale feed, tampered artefact, and full API workflow.
* Frontend: `npm test` → **8 passed**. `tsc --noEmit` clean, `vite build` succeeds.
* Live app smoke test: uvicorn + vite + headless Chromium over all 10 screens at 1400 px and 390 px, using the two
  completed runs above. The first pass found mobile overflow from the run selector and an empty reliability diagram
  (Scatter animation), both now fixed. The final pass had no page errors, no API 4xx/5xx and no horizontal overflow
  (only a missing favicon 404). Screenshots are in `docs/screenshots/`.
* **Not executed:** Docker image builds. No Docker daemon was available; `docker compose config` validates.

## What would change the conclusion

Part A answers the research question for the demo server: no edge. A real-money operator's export of ≥ 10,000
consecutive rounds could be evaluated the same way (`import` → `evaluate`). Given the verified provably-fair
mechanism, the expected result there is also **NO DEMONSTRATED EDGE**. A different result would point to a
broken RNG implementation rather than a strategy.
