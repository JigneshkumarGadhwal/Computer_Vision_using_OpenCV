# Evaluation report — run 2

> **SYNTHETIC DATA.** This run used generated demo data, not real Aviator results.

- **Data source:** SYNTHETIC demo file (regime) (kind: synthetic)
- **Rounds:** 10000 (warm-up 500)
- **Development period:** seq 0–6999 (7000 rounds)
- **Final test period:** seq 7000–9999 (3000 rounds; 2026-01-02T20:58:13.820000+00:00 → 2026-01-03T15:53:48.021000+00:00)
- **Data fingerprint:** `4f6f53210f32f190f57bd8149d8a57af15bee32e35c8c73ec052f11015d8c0a9`
- **Code version:** `049dec7+srcef62bacd59`
- **Started / finished:** 2026-10-21 02:10:53.256494 / 2026-10-21 02:29:24.564756
- **Status:** completed

## Conclusion

**RESEARCH SIGNAL** — [SYNTHETIC DATA — not real Aviator results] Out-of-sample predictive improvement over the expanding-frequency baseline was demonstrated at threshold(s) 1.5x, 3x, 5x under the pre-registered criteria. Primary target reaches_2x: selected model ensemble_mean, Brier skill +0.01635 (95% CI +0.00773 to +0.02488) over 3000 unseen rounds.

## Pre-registered protocol

- Thresholds: [1.5, 2.0, 3.0, 5.0] (primary 2.0)
- Development fraction: 0.7; initial train 1000; block (retrain schedule) 250; purge gap 0
- Signal criteria: Brier skill > 0.0, Holm-adjusted one-sided DM p < 0.05, Hosmer–Lemeshow p ≥ 0.05 and |mean-prediction gap| ≤ 0.03, ≥ 500 test rounds
- Baseline: expanding-window Laplace-smoothed historical frequency
- Random seed: 20260101

**Look-ahead audit:** PASSED (26 positions checked)

**Data quality gate:** passed (warning share 0.180%, verified share 0.0%)

## Target: reaches_1.5x (break-even probability 0.6667)

**Status: RESEARCH SIGNAL** — Brier skill +0.05064 > 0.0; Holm-adjusted DM p = 0.0000 < 0.05; calibration OK (Hosmer-Lemeshow p = 0.9110, mean-prediction gap 0.0011; ECE 0.0156 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **hmm_regime**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.22058 | 0.22144 | 0.63496 | 0.498 | 0.0182 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.22108 | 0.22209 | 0.63649 | 0.514 | 0.0330 | -0.00294 | n/a |
| training_frequency | baseline | 0.22020 | 0.22201 | 0.63629 | 0.459 | 0.0456 | -0.00258 | n/a |
| reference_constant | baseline | 0.22087 | 0.22169 | 0.63548 | 0.500 | 0.0268 | -0.00113 | n/a |
| markov_conditional | statistical | 0.21683 | 0.21889 | 0.62931 | 0.535 | 0.0344 | +0.01154 | 0.0016 |
| hmm_regime | statistical | 0.20770 | 0.20937 | 0.60689 | 0.639 | 0.0092 | +0.05453 | 0.0000 |
| logistic_l2 | statistical | 0.20766 | 0.21323 | 0.61576 | 0.617 | 0.0164 | +0.03709 | 0.0000 |
| logistic_l1 | statistical | 0.20876 | 0.21331 | 0.61611 | 0.619 | 0.0341 | +0.03673 | 0.0000 |
| random_forest | ml | 0.19477 | 0.21079 | 0.61052 | 0.633 | 0.0165 | +0.04810 | 0.0000 |
| hist_gbm | ml | 0.19248 | 0.21637 | 0.62362 | 0.598 | 0.0268 | +0.02291 | 0.0105 |
| lightgbm | ml | 0.18927 | 0.21679 | 0.62473 | 0.597 | 0.0355 | +0.02102 | 0.0349 |
| ensemble_mean | ensemble | 0.20087 | 0.21060 | 0.60983 | 0.635 | 0.0192 | +0.04897 | 0.0000 |

### Final test (3000 unseen rounds; dev base rate 0.6698, test base rate 0.6583)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| hmm_regime | 0.21376 | 0.61666 | 0.633 | 0.0156 | 0.911 | -0.0011 | 0.7659 | 0.5301 | 1367 |
| expanding_frequency | 0.22516 | 0.64265 | 0.480 | 0.0202 | 0.722 | +0.0131 | 0.6583 | 1.0000 | 3000 |
| rolling_frequency_500 | 0.22491 | 0.64197 | 0.531 | 0.0278 | 0.013 | +0.0055 | 0.6810 | 0.4324 | 1254 |
| reference_constant | 0.22507 | 0.64243 | 0.500 | 0.0717 | 0.000 | -0.0117 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.05064** (95% block-bootstrap CI +0.03299 to +0.07095); DM p = 0.0000, Holm-adjusted across thresholds = 0.0000.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.6133 | -0.0565 | 0.22909 | 0.24067 |
| 7750–8499 | 750 | 0.6773 | +0.0075 | 0.20377 | 0.21864 |
| 8500–9249 | 750 | 0.6867 | +0.0168 | 0.21264 | 0.21543 |
| 9250–9999 | 750 | 0.6560 | -0.0138 | 0.20953 | 0.22591 |

## Target: reaches_2x (break-even probability 0.5000)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.01635 > 0.0; Holm-adjusted DM p = 0.0001 < 0.05; calibration FAILED (Hosmer-Lemeshow p = 0.0075, mean-prediction gap 0.0045; ECE 0.0315 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **ensemble_mean**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.25024 | 0.25016 | 0.69347 | 0.492 | 0.0142 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.25122 | 0.25106 | 0.69529 | 0.507 | 0.0305 | -0.00359 | n/a |
| training_frequency | baseline | 0.24997 | 0.25048 | 0.69412 | 0.473 | 0.0367 | -0.00129 | n/a |
| reference_constant | baseline | 0.25038 | 0.25031 | 0.69378 | 0.500 | 0.0217 | -0.00062 | n/a |
| markov_conditional | statistical | 0.24879 | 0.24955 | 0.69225 | 0.517 | 0.0304 | +0.00243 | 0.5217 |
| hmm_regime | statistical | 0.24448 | 0.24540 | 0.68389 | 0.577 | 0.0128 | +0.01903 | 0.0001 |
| logistic_l2 | statistical | 0.24186 | 0.24471 | 0.68246 | 0.583 | 0.0118 | +0.02181 | 0.0000 |
| logistic_l1 | statistical | 0.24228 | 0.24529 | 0.68366 | 0.580 | 0.0271 | +0.01948 | 0.0000 |
| random_forest | ml | 0.23169 | 0.24453 | 0.68221 | 0.588 | 0.0272 | +0.02250 | 0.0000 |
| hist_gbm | ml | 0.23119 | 0.24745 | 0.68813 | 0.563 | 0.0191 | +0.01082 | 0.0756 |
| lightgbm | ml | 0.22876 | 0.24718 | 0.68761 | 0.567 | 0.0264 | +0.01191 | 0.0462 |
| ensemble_mean | ensemble | 0.23822 | 0.24419 | 0.68143 | 0.589 | 0.0137 | +0.02389 | 0.0000 |

### Final test (3000 unseen rounds; dev base rate 0.5030, test base rate 0.4997)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| ensemble_mean | 0.24597 | 0.68510 | 0.575 | 0.0315 | 0.007 | -0.0045 | 0.5691 | 0.5330 | 1404 |
| expanding_frequency | 0.25006 | 0.69327 | 0.483 | 0.0245 | 0.467 | +0.0043 | 0.4997 | 1.0000 | 3000 |
| rolling_frequency_500 | 0.25033 | 0.69383 | 0.519 | 0.0281 | 0.136 | +0.0017 | 0.5146 | 0.5404 | 1574 |
| reference_constant | 0.25021 | 0.69358 | 0.500 | 0.0517 | 0.000 | -0.0147 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.01635** (95% block-bootstrap CI +0.00773 to +0.02488); DM p = 0.0000, Holm-adjusted across thresholds = 0.0001.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.4507 | -0.0523 | 0.24597 | 0.25042 |
| 7750–8499 | 750 | 0.5280 | +0.0250 | 0.24522 | 0.24990 |
| 8500–9249 | 750 | 0.5080 | +0.0050 | 0.24867 | 0.24998 |
| 9250–9999 | 750 | 0.5120 | +0.0090 | 0.24403 | 0.24994 |

## Target: reaches_3x (break-even probability 0.3333)

**Status: RESEARCH SIGNAL** — Brier skill +0.00631 > 0.0; Holm-adjusted DM p = 0.0335 < 0.05; calibration OK (Hosmer-Lemeshow p = 0.4690, mean-prediction gap 0.0023; ECE 0.0209 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l2**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.22244 | 0.22073 | 0.63345 | 0.510 | 0.0195 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.22300 | 0.22102 | 0.63408 | 0.509 | 0.0265 | -0.00129 | n/a |
| training_frequency | baseline | 0.22215 | 0.22078 | 0.63357 | 0.492 | 0.0190 | -0.00023 | n/a |
| reference_constant | baseline | 0.22225 | 0.22044 | 0.63280 | 0.500 | 0.0207 | +0.00133 | n/a |
| markov_conditional | statistical | 0.22192 | 0.22059 | 0.63312 | 0.511 | 0.0224 | +0.00065 | 1.0000 |
| hmm_regime | statistical | 0.22036 | 0.21907 | 0.62975 | 0.548 | 0.0221 | +0.00753 | 0.0621 |
| logistic_l2 | statistical | 0.21802 | 0.21830 | 0.62794 | 0.562 | 0.0188 | +0.01104 | 0.0029 |
| logistic_l1 | statistical | 0.21849 | 0.21853 | 0.62853 | 0.559 | 0.0222 | +0.00998 | 0.0006 |
| random_forest | ml | 0.21470 | 0.21916 | 0.62994 | 0.551 | 0.0221 | +0.00713 | 0.1156 |
| hist_gbm | ml | 0.21224 | 0.22097 | 0.63390 | 0.527 | 0.0243 | -0.00109 | 1.0000 |
| lightgbm | ml | 0.21552 | 0.22105 | 0.63419 | 0.528 | 0.0294 | -0.00146 | 1.0000 |
| ensemble_mean | ensemble | 0.21633 | 0.21854 | 0.62851 | 0.558 | 0.0157 | +0.00991 | 0.0002 |

### Final test (3000 unseen rounds; dev base rate 0.3280, test base rate 0.3257)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l2 | 0.21830 | 0.62818 | 0.551 | 0.0209 | 0.469 | +0.0023 | 0.3641 | 0.5087 | 1365 |
| expanding_frequency | 0.21968 | 0.63123 | 0.477 | 0.0227 | 0.456 | +0.0058 | 0.3103 | 0.0553 | 174 |
| rolling_frequency_500 | 0.22013 | 0.63227 | 0.514 | 0.0279 | 0.096 | -0.0033 | 0.3376 | 0.4299 | 1244 |
| reference_constant | 0.21961 | 0.63108 | 0.500 | 0.0357 | 0.005 | -0.0023 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00631** (95% block-bootstrap CI -0.00068 to +0.01282); DM p = 0.0335, Holm-adjusted across thresholds = 0.0335.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.2813 | -0.0467 | 0.20386 | 0.20471 |
| 7750–8499 | 750 | 0.3573 | +0.0293 | 0.22910 | 0.23042 |
| 8500–9249 | 750 | 0.3267 | -0.0013 | 0.21935 | 0.22000 |
| 9250–9999 | 750 | 0.3373 | +0.0093 | 0.22087 | 0.22360 |

## Target: reaches_5x (break-even probability 0.2000)

**Status: RESEARCH SIGNAL** — Brier skill +0.00922 > 0.0; Holm-adjusted DM p = 0.0016 < 0.05; calibration OK (Hosmer-Lemeshow p = 0.6206, mean-prediction gap 0.0031; ECE 0.0175 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l2**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.16188 | 0.15983 | 0.50010 | 0.501 | 0.0157 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.16228 | 0.16003 | 0.50063 | 0.501 | 0.0228 | -0.00124 | n/a |
| training_frequency | baseline | 0.16172 | 0.15985 | 0.50018 | 0.481 | 0.0199 | -0.00015 | n/a |
| reference_constant | baseline | 0.16180 | 0.15973 | 0.49980 | 0.500 | 0.0154 | +0.00061 | n/a |
| markov_conditional | statistical | 0.16164 | 0.15986 | 0.50016 | 0.503 | 0.0218 | -0.00017 | 1.0000 |
| hmm_regime | statistical | 0.16112 | 0.15933 | 0.49855 | 0.540 | 0.0121 | +0.00312 | 0.5217 |
| logistic_l2 | statistical | 0.15967 | 0.15890 | 0.49723 | 0.554 | 0.0152 | +0.00581 | 0.1462 |
| logistic_l1 | statistical | 0.16000 | 0.15892 | 0.49738 | 0.551 | 0.0154 | +0.00566 | 0.0132 |
| random_forest | ml | 0.15676 | 0.15953 | 0.49927 | 0.536 | 0.0168 | +0.00186 | 1.0000 |
| hist_gbm | ml | 0.15708 | 0.16125 | 0.50373 | 0.518 | 0.0280 | -0.00891 | 1.0000 |
| lightgbm | ml | 0.15687 | 0.16114 | 0.50396 | 0.512 | 0.0323 | -0.00823 | 1.0000 |
| ensemble_mean | ensemble | 0.15904 | 0.15915 | 0.49801 | 0.546 | 0.0150 | +0.00422 | 0.1156 |

### Final test (3000 unseen rounds; dev base rate 0.1995, test base rate 0.1953)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l2 | 0.15579 | 0.48950 | 0.570 | 0.0175 | 0.621 | +0.0031 | 0.2274 | 0.5375 | 1385 |
| expanding_frequency | 0.15724 | 0.49404 | 0.471 | 0.0193 | 0.397 | +0.0051 | 0.1856 | 0.5222 | 1649 |
| rolling_frequency_500 | 0.15738 | 0.49461 | 0.518 | 0.0217 | 0.292 | -0.0030 | 0.2086 | 0.4539 | 1275 |
| reference_constant | 0.15718 | 0.49387 | 0.500 | 0.0280 | 0.021 | -0.0013 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00922** (95% block-bootstrap CI +0.00343 to +0.01486); DM p = 0.0008, Holm-adjusted across thresholds = 0.0016.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.1600 | -0.0395 | 0.13477 | 0.13607 |
| 7750–8499 | 750 | 0.2080 | +0.0085 | 0.16365 | 0.16484 |
| 8500–9249 | 750 | 0.2093 | +0.0098 | 0.16492 | 0.16562 |
| 9250–9999 | 750 | 0.2040 | +0.0045 | 0.15980 | 0.16241 |

## Secondary: exact multiplier (log scale)

Target is log(crash multiplier). Baseline is the training-window median. A lower point error does not imply profitable betting decisions.

**validation** (6000 rounds)

| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |
|---|---|---|---|---|
| train_mean_log | 0.7370 | 0.9920 | 1.3448 | 1.0000 |
| train_median_log | 0.6960 | 1.0302 | 0.9000 | 0.5000 |
| ridge | 0.7315 | 0.9912 | 1.2987 | 1.0000 |
| hgb_regressor | 0.7349 | 0.9927 | 1.3221 | 1.0000 |

**test** (3000 rounds)

| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |
|---|---|---|---|---|
| train_mean_log | 0.7323 | 0.9816 | 1.3272 | 1.0000 |
| train_median_log | 0.6930 | 1.0172 | 0.9000 | 0.5000 |
| ridge | 0.7273 | 0.9694 | 1.3034 | 1.0000 |
| hgb_regressor | 0.7260 | 0.9709 | 1.2774 | 1.0000 |

## Backtest 2

Config: `{'threshold': 2.0, 'stake': 1.0, 'margins': [0.0, 0.02, 0.05, 0.1], 'rules': {'tie_counts_as_win': True, 'max_net_win': None, 'fee_per_bet': 0.0}, 'decision_latency_s': None}`

| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |
|---|---|---|---|---|---|---|
| always_bet_fixed_threshold | 3000 | 0.4997 | -2.00 | -0.0007 | -0.0527 to +0.0520 | 101.00 |
| model_ev_gate | 1404 | 0.5691 | 194.00 | +0.1382 | +0.0912 to +0.1995 | 17.00 |
| baseline_ev_gate | 3000 | 0.4997 | -2.00 | -0.0007 | -0.0527 to +0.0520 | 101.00 |

Independent audit: **PASSED**

## Limitations

- Results apply only to this data source and period; other operators may use different software or rules.
- The reference distribution P(X≥m)=RTP/m and its RTP are unverified assumptions.
- A positive Brier skill is a statistical statement about probability estimates, not proof of a profitable strategy.
- Hypothetical backtests ignore limits, latency and rule details unless explicitly configured.
