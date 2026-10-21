# Final evaluation report

**Date:** 2026-10-21 · **Data:** SYNTHETIC ONLY. No real Aviator data were available to this project.
Detailed, auto-generated reports: [`evaluation_run_1.md`](evaluation_run_1.md) (independent) and
[`evaluation_run_2.md`](evaluation_run_2.md) (planted regime dependence). Reproduce with
`python scripts/research_cli.py demo-report` (backend source hash `ef62bacd59` in both runs).

## Bottom line

> **No predictive skill has been demonstrated on real Aviator data, because no real Aviator data were evaluated.**
> What *has* been demonstrated is that the evaluation framework behaves correctly in both directions. It reports
> **NO DEMONSTRATED EDGE** on independent rounds, and it detects a deliberately planted dependence. It also refuses
> to report a signal when a leakage bug is introduced.

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

Import an authorised historical export of ≥ 10,000 consecutive real rounds from one operator, then run
`evaluate`. Only that run can answer the actual research question. If the operator's documented provably-fair
mechanism is implemented correctly, the expected result is **NO DEMONSTRATED EDGE**.
