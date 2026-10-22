# Evaluation report — run 3

- **Data source:** spribe-demo-observed (kind: public_observation)
- **Rounds:** 3000 (warm-up 500)
- **Development period:** seq 0–2099 (2100 rounds)
- **Final test period:** seq 2100–2999 (900 rounds; 2026-10-22T07:02:11.539000+00:00 → 2026-10-22T14:07:31.139000+00:00)
- **Data fingerprint:** `79ec14e598be7ef71d8a48ab7cf5dc3741406d6b5dfa28c8545d35666e75666d`
- **Code version:** `36bb0f4+src678b641a80`
- **Started / finished:** 2026-10-22 14:07:54.649111 / 2026-10-22 14:09:01.042303
- **Status:** completed

## Conclusion

**NO DEMONSTRATED EDGE** — No model demonstrated a statistically reliable out-of-sample improvement over the expanding-frequency baseline at any evaluated threshold. Primary target reaches_2x: selected model logistic_l1, Brier skill +0.00015 (95% CI -0.00150 to +0.00175) over 900 unseen rounds.

## Pre-registered protocol

- Thresholds: [1.5, 2.0, 3.0, 5.0] (primary 2.0)
- Development fraction: 0.7; initial train 1000; block (retrain schedule) 250; purge gap 0
- Signal criteria: Brier skill > 0.0, Holm-adjusted one-sided DM p < 0.05, Hosmer–Lemeshow p ≥ 0.05 and |mean-prediction gap| ≤ 0.03, ≥ 500 test rounds
- Baseline: expanding-window Laplace-smoothed historical frequency
- Random seed: 20260101

**Look-ahead audit:** PASSED (26 positions checked)

**Data quality gate:** passed (warning share 0.000%, verified share 97.8%)

## Target: reaches_1.5x (break-even probability 0.6667)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.00050 > 0.0; Holm-adjusted DM p = 0.5322 >= 0.05; calibration FAILED (Hosmer-Lemeshow p = 0.0342, mean-prediction gap 0.0111; ECE 0.0611 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l1**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.22775 | 0.22612 | 0.64463 | 0.469 | 0.0406 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.22795 | 0.22632 | 0.64508 | 0.456 | 0.0404 | -0.00089 | n/a |
| training_frequency | baseline | 0.22750 | 0.22596 | 0.64429 | 0.501 | 0.0231 | +0.00069 | n/a |
| reference_constant | baseline | 0.22751 | 0.22591 | 0.64417 | 0.500 | 0.0207 | +0.00092 | n/a |
| markov_conditional | statistical | 0.22722 | 0.22638 | 0.64516 | 0.508 | 0.0292 | -0.00114 | 1.0000 |
| hmm_regime | statistical | 0.22752 | 0.22676 | 0.64599 | 0.486 | 0.0434 | -0.00284 | 1.0000 |
| logistic_l2 | statistical | 0.22706 | 0.22605 | 0.64447 | 0.506 | 0.0419 | +0.00030 | 1.0000 |
| logistic_l1 | statistical | 0.22750 | 0.22597 | 0.64430 | 0.497 | 0.0186 | +0.00066 | 0.9323 |
| random_forest | ml | 0.22298 | 0.22993 | 0.65234 | 0.510 | 0.0578 | -0.01686 | 1.0000 |
| hist_gbm | ml | 0.21779 | 0.22862 | 0.64971 | 0.497 | 0.0422 | -0.01104 | 1.0000 |
| lightgbm | ml | 0.22334 | 0.22864 | 0.64993 | 0.487 | 0.0704 | -0.01116 | 1.0000 |
| ensemble_mean | ensemble | 0.22395 | 0.22648 | 0.64538 | 0.501 | 0.0284 | -0.00161 | 1.0000 |

### Final test (900 unseen rounds; dev base rate 0.6555, test base rate 0.6633)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l1 | 0.22347 | 0.63913 | 0.498 | 0.0611 | 0.034 | -0.0111 | n/a | 0.0000 | 0 |
| expanding_frequency | 0.22359 | 0.63938 | 0.449 | 0.0439 | 0.373 | -0.0126 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.22374 | 0.63973 | 0.457 | 0.0556 | 0.022 | -0.0007 | 0.6032 | 0.2546 | 252 |
| reference_constant | 0.22360 | 0.63941 | 0.500 | 0.0273 | 0.885 | -0.0167 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00050** (95% block-bootstrap CI -0.00065 to +0.00133); DM p = 0.1479, Holm-adjusted across thresholds = 0.5322.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 2100–2324 | 225 | 0.6889 | +0.0334 | 0.21602 | 0.21597 |
| 2325–2549 | 225 | 0.6489 | -0.0066 | 0.22784 | 0.22793 |
| 2550–2774 | 225 | 0.6489 | -0.0066 | 0.22774 | 0.22792 |
| 2775–2999 | 225 | 0.6667 | +0.0112 | 0.22231 | 0.22252 |

## Target: reaches_2x (break-even probability 0.5000)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.00015 > 0.0; Holm-adjusted DM p = 0.5322 >= 0.05; calibration FAILED (Hosmer-Lemeshow p = 0.0442, mean-prediction gap 0.0189; ECE 0.0604 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l1**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.24991 | 0.24996 | 0.69307 | 0.475 | 0.0340 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.25016 | 0.25029 | 0.69373 | 0.477 | 0.0435 | -0.00132 | n/a |
| training_frequency | baseline | 0.24968 | 0.24998 | 0.69310 | 0.481 | 0.0381 | -0.00005 | n/a |
| reference_constant | baseline | 0.24968 | 0.24979 | 0.69272 | 0.500 | 0.0339 | +0.00069 | n/a |
| markov_conditional | statistical | 0.24922 | 0.25043 | 0.69402 | 0.512 | 0.0416 | -0.00188 | 1.0000 |
| hmm_regime | statistical | 0.24964 | 0.25071 | 0.69458 | 0.490 | 0.0333 | -0.00301 | 1.0000 |
| logistic_l2 | statistical | 0.24911 | 0.25023 | 0.69362 | 0.500 | 0.0339 | -0.00108 | 1.0000 |
| logistic_l1 | statistical | 0.24964 | 0.24998 | 0.69310 | 0.482 | 0.0418 | -0.00005 | 1.0000 |
| random_forest | ml | 0.26388 | 0.25126 | 0.69571 | 0.496 | 0.0529 | -0.00519 | 1.0000 |
| hist_gbm | ml | 0.25061 | 0.25253 | 0.69830 | 0.493 | 0.0477 | -0.01027 | 1.0000 |
| lightgbm | ml | 0.26368 | 0.25254 | 0.69828 | 0.487 | 0.0570 | -0.01032 | 1.0000 |
| ensemble_mean | ensemble | 0.24971 | 0.25064 | 0.69443 | 0.490 | 0.0386 | -0.00270 | 1.0000 |

### Final test (900 unseen rounds; dev base rate 0.4855, test base rate 0.5044)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l1 | 0.25034 | 0.69383 | 0.493 | 0.0604 | 0.044 | -0.0189 | n/a | 0.0000 | 0 |
| expanding_frequency | 0.25038 | 0.69390 | 0.503 | 0.0253 | 0.880 | -0.0205 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.25013 | 0.69342 | 0.501 | 0.0396 | 0.586 | -0.0043 | 0.5136 | 0.4978 | 440 |
| reference_constant | 0.25036 | 0.69386 | 0.500 | 0.0416 | 0.529 | -0.0194 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00015** (95% block-bootstrap CI -0.00150 to +0.00175); DM p = 0.4083, Holm-adjusted across thresholds = 0.5322.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 2100–2324 | 225 | 0.4889 | +0.0034 | 0.24985 | 0.25001 |
| 2325–2549 | 225 | 0.4667 | -0.0188 | 0.24923 | 0.24928 |
| 2550–2774 | 225 | 0.5200 | +0.0345 | 0.25106 | 0.25097 |
| 2775–2999 | 225 | 0.5422 | +0.0568 | 0.25122 | 0.25125 |

## Target: reaches_3x (break-even probability 0.3333)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.00061 > 0.0; Holm-adjusted DM p = 0.5322 >= 0.05; calibration OK (Hosmer-Lemeshow p = 0.2597, mean-prediction gap 0.0228; ECE 0.0501 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l1**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.21742 | 0.21507 | 0.62160 | 0.457 | 0.0465 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.21769 | 0.21550 | 0.62258 | 0.455 | 0.0499 | -0.00197 | n/a |
| training_frequency | baseline | 0.21712 | 0.21511 | 0.62168 | 0.478 | 0.0302 | -0.00015 | n/a |
| reference_constant | baseline | 0.21714 | 0.21504 | 0.62152 | 0.500 | 0.0257 | +0.00015 | n/a |
| markov_conditional | statistical | 0.21682 | 0.21550 | 0.62254 | 0.509 | 0.0353 | -0.00199 | 1.0000 |
| hmm_regime | statistical | 0.21712 | 0.21556 | 0.62270 | 0.467 | 0.0361 | -0.00225 | 1.0000 |
| logistic_l2 | statistical | 0.21620 | 0.21508 | 0.62161 | 0.495 | 0.0228 | -0.00002 | 1.0000 |
| logistic_l1 | statistical | 0.21712 | 0.21503 | 0.62150 | 0.480 | 0.0288 | +0.00021 | 1.0000 |
| random_forest | ml | 0.21229 | 0.21590 | 0.62370 | 0.525 | 0.0577 | -0.00385 | 1.0000 |
| hist_gbm | ml | 0.21198 | 0.21677 | 0.62543 | 0.511 | 0.0569 | -0.00789 | 1.0000 |
| lightgbm | ml | 0.20787 | 0.21667 | 0.62551 | 0.510 | 0.0456 | -0.00743 | 1.0000 |
| ensemble_mean | ensemble | 0.21503 | 0.21514 | 0.62173 | 0.503 | 0.0382 | -0.00029 | 1.0000 |

### Final test (900 unseen rounds; dev base rate 0.3127, test base rate 0.3389)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l1 | 0.22450 | 0.64133 | 0.513 | 0.0501 | 0.260 | -0.0228 | n/a | 0.0000 | 0 |
| expanding_frequency | 0.22463 | 0.64165 | 0.510 | 0.0495 | 0.148 | -0.0256 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.22421 | 0.64070 | 0.513 | 0.0292 | 0.858 | -0.0171 | 0.3662 | 0.3770 | 314 |
| reference_constant | 0.22429 | 0.64084 | 0.500 | 0.0364 | 0.675 | -0.0156 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00061** (95% block-bootstrap CI -0.00100 to +0.00187); DM p = 0.1597, Holm-adjusted across thresholds = 0.5322.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 2100–2324 | 225 | 0.3067 | -0.0061 | 0.21270 | 0.21274 |
| 2325–2549 | 225 | 0.3156 | +0.0028 | 0.21601 | 0.21608 |
| 2550–2774 | 225 | 0.3644 | +0.0517 | 0.23404 | 0.23425 |
| 2775–2999 | 225 | 0.3689 | +0.0562 | 0.23524 | 0.23546 |

## Target: reaches_5x (break-even probability 0.2000)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.00089 > 0.0; Holm-adjusted DM p = 0.5322 >= 0.05; calibration OK (Hosmer-Lemeshow p = 0.5870, mean-prediction gap 0.0127; ECE 0.0358 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l1**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.16038 | 0.15687 | 0.49332 | 0.467 | 0.0332 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.16040 | 0.15699 | 0.49367 | 0.473 | 0.0321 | -0.00079 | n/a |
| training_frequency | baseline | 0.16000 | 0.15676 | 0.49296 | 0.478 | 0.0285 | +0.00068 | n/a |
| reference_constant | baseline | 0.16004 | 0.15670 | 0.49275 | 0.500 | 0.0255 | +0.00110 | n/a |
| markov_conditional | statistical | 0.15982 | 0.15705 | 0.49374 | 0.492 | 0.0270 | -0.00112 | 1.0000 |
| hmm_regime | statistical | 0.16000 | 0.15724 | 0.49443 | 0.470 | 0.0264 | -0.00234 | 1.0000 |
| logistic_l2 | statistical | 0.15981 | 0.15679 | 0.49305 | 0.486 | 0.0250 | +0.00052 | 1.0000 |
| logistic_l1 | statistical | 0.16000 | 0.15673 | 0.49285 | 0.486 | 0.0249 | +0.00089 | 1.0000 |
| random_forest | ml | 0.15616 | 0.15762 | 0.49565 | 0.521 | 0.0277 | -0.00478 | 1.0000 |
| hist_gbm | ml | 0.15854 | 0.15780 | 0.49636 | 0.481 | 0.0440 | -0.00592 | 1.0000 |
| lightgbm | ml | 0.15978 | 0.15686 | 0.49327 | 0.523 | 0.0369 | +0.00008 | 1.0000 |
| ensemble_mean | ensemble | 0.15943 | 0.15712 | 0.49410 | 0.464 | 0.0303 | -0.00160 | 1.0000 |

### Final test (900 unseen rounds; dev base rate 0.1945, test base rate 0.2067)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l1 | 0.16410 | 0.50995 | 0.514 | 0.0358 | 0.587 | -0.0127 | n/a | 0.0000 | 0 |
| expanding_frequency | 0.16424 | 0.51042 | 0.491 | 0.0317 | 0.419 | -0.0170 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.16409 | 0.50998 | 0.509 | 0.0284 | 0.828 | -0.0136 | 0.2096 | 0.3280 | 291 |
| reference_constant | 0.16412 | 0.51001 | 0.500 | 0.0268 | 0.609 | -0.0127 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00089** (95% block-bootstrap CI -0.00084 to +0.00207); DM p = 0.1331, Holm-adjusted across thresholds = 0.5322.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 2100–2324 | 225 | 0.1867 | -0.0079 | 0.15188 | 0.15190 |
| 2325–2549 | 225 | 0.1867 | -0.0079 | 0.15188 | 0.15189 |
| 2550–2774 | 225 | 0.2133 | +0.0188 | 0.16829 | 0.16845 |
| 2775–2999 | 225 | 0.2400 | +0.0455 | 0.18433 | 0.18473 |

## Secondary: exact multiplier (log scale)

Target is log(crash multiplier). Baseline is the training-window median. A lower point error does not imply profitable betting decisions.

**validation** (1100 rounds)

| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |
|---|---|---|---|---|
| train_mean_log | 0.7337 | 1.0287 | 1.3257 | 1.0000 |
| train_median_log | 0.6862 | 1.0779 | 0.7725 | 0.5000 |
| ridge | 0.8059 | 1.1088 | 1.5052 | 1.0000 |
| hgb_regressor | 0.7680 | 1.0716 | 1.4219 | 1.0000 |

**test** (900 rounds)

| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |
|---|---|---|---|---|
| train_mean_log | 0.7584 | 1.0243 | 1.3221 | 0.9997 |
| train_median_log | 0.7226 | 1.0854 | 0.7950 | 0.5000 |
| ridge | 0.7593 | 1.0430 | 1.2250 | 0.9998 |
| hgb_regressor | 0.7714 | 1.0431 | 1.3049 | 0.9999 |

## Backtest 3

Config: `{'threshold': 2.0, 'stake': 1.0, 'margins': [0.0, 0.02, 0.05, 0.1], 'rules': {'tie_counts_as_win': True, 'max_net_win': None, 'fee_per_bet': 0.0}, 'decision_latency_s': None}`

| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |
|---|---|---|---|---|---|---|
| always_bet_fixed_threshold | 900 | 0.5044 | 8.00 | +0.0089 | -0.0756 to +0.0778 | 34.00 |
| model_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |
| baseline_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |

Independent audit: **PASSED**

## Backtest 4

Config: `{'threshold': 1.5, 'stake': 1.0, 'margins': [0.0, 0.02, 0.05, 0.1], 'rules': {'tie_counts_as_win': True, 'max_net_win': None, 'fee_per_bet': 0.0}, 'decision_latency_s': None}`

| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |
|---|---|---|---|---|---|---|
| always_bet_fixed_threshold | 900 | 0.6633 | -4.50 | -0.0050 | -0.0583 to +0.0367 | 17.50 |
| model_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |
| baseline_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |

Independent audit: **PASSED**

## Backtest 5

Config: `{'threshold': 3.0, 'stake': 1.0, 'margins': [0.0, 0.02, 0.05, 0.1], 'rules': {'tie_counts_as_win': True, 'max_net_win': None, 'fee_per_bet': 0.0}, 'decision_latency_s': None}`

| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |
|---|---|---|---|---|---|---|
| always_bet_fixed_threshold | 900 | 0.3389 | 15.00 | +0.0167 | -0.0833 to +0.1133 | 50.00 |
| model_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |
| baseline_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |

Independent audit: **PASSED**

## Backtest 6

Config: `{'threshold': 5.0, 'stake': 1.0, 'margins': [0.0, 0.02, 0.05, 0.1], 'rules': {'tie_counts_as_win': True, 'max_net_win': None, 'fee_per_bet': 0.0}, 'decision_latency_s': None}`

| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |
|---|---|---|---|---|---|---|
| always_bet_fixed_threshold | 900 | 0.2067 | 30.00 | +0.0333 | -0.0944 to +0.1612 | 52.00 |
| model_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |
| baseline_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |

Independent audit: **PASSED**

## Limitations

- Results apply only to this data source and period; other operators may use different software or rules.
- The reference distribution P(X≥m)=RTP/m and its RTP are unverified assumptions.
- A positive Brier skill is a statistical statement about probability estimates, not proof of a profitable strategy.
- Hypothetical backtests ignore limits, latency and rule details unless explicitly configured.
