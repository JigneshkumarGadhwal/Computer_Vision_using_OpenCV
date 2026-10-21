# Evaluation report — run 1

> **SYNTHETIC DATA.** This run used generated demo data, not real Aviator results.

- **Data source:** SYNTHETIC demo file (independent) (kind: synthetic)
- **Rounds:** 10000 (warm-up 500)
- **Development period:** seq 0–6999 (7000 rounds)
- **Final test period:** seq 7000–9999 (3000 rounds; 2026-01-02T19:35:58.739000+00:00 → 2026-01-03T14:19:57.213000+00:00)
- **Data fingerprint:** `7e9a80efca3071d20eea995c58d128ba55b84bbc55427fe06ef3725cccd10251`
- **Code version:** `344b6ff+srcef62bacd59`
- **Started / finished:** 2026-10-21 01:37:51.407795 / 2026-10-21 02:10:07.641209
- **Status:** completed

## Conclusion

**NO DEMONSTRATED EDGE** — [SYNTHETIC DATA — not real Aviator results] No model demonstrated a statistically reliable out-of-sample improvement over the expanding-frequency baseline at any evaluated threshold. Primary target reaches_2x: selected model ensemble_mean, Brier skill -0.00046 (95% CI -0.00206 to +0.00115) over 3000 unseen rounds.

## Pre-registered protocol

- Thresholds: [1.5, 2.0, 3.0, 5.0] (primary 2.0)
- Development fraction: 0.7; initial train 1000; block (retrain schedule) 250; purge gap 0
- Signal criteria: Brier skill > 0.0, Holm-adjusted one-sided DM p < 0.05, Hosmer–Lemeshow p ≥ 0.05 and |mean-prediction gap| ≤ 0.03, ≥ 500 test rounds
- Baseline: expanding-window Laplace-smoothed historical frequency
- Random seed: 20260101

**Look-ahead audit:** PASSED (26 positions checked)

**Data quality gate:** passed (warning share 0.000%, verified share 0.0%)

## Target: reaches_1.5x (break-even probability 0.6667)

**Status: NO DEMONSTRATED EDGE** — Brier skill -0.00161 <= 0.0; Holm-adjusted DM p = 1.0000 >= 0.05; calibration OK (Hosmer-Lemeshow p = 0.1094, mean-prediction gap 0.0031; ECE 0.0272 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **logistic_l2**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.23074 | 0.22977 | 0.65214 | 0.492 | 0.0202 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.23088 | 0.22998 | 0.65260 | 0.511 | 0.0183 | -0.00089 | n/a |
| training_frequency | baseline | 0.23062 | 0.23001 | 0.65264 | 0.490 | 0.0268 | -0.00102 | n/a |
| reference_constant | baseline | 0.23068 | 0.22966 | 0.65190 | 0.500 | 0.0210 | +0.00048 | n/a |
| markov_conditional | statistical | 0.23057 | 0.23001 | 0.65266 | 0.496 | 0.0198 | -0.00106 | 1.0000 |
| hmm_regime | statistical | 0.23069 | 0.23029 | 0.65325 | 0.485 | 0.0292 | -0.00224 | 1.0000 |
| logistic_l2 | statistical | 0.22976 | 0.22991 | 0.65245 | 0.498 | 0.0250 | -0.00062 | 1.0000 |
| logistic_l1 | statistical | 0.23010 | 0.22993 | 0.65249 | 0.503 | 0.0200 | -0.00071 | 1.0000 |
| random_forest | ml | 0.22434 | 0.23126 | 0.65527 | 0.502 | 0.0336 | -0.00647 | 1.0000 |
| hist_gbm | ml | 0.23267 | 0.23106 | 0.65500 | 0.504 | 0.0332 | -0.00561 | 1.0000 |
| lightgbm | ml | 0.23048 | 0.23104 | 0.65484 | 0.510 | 0.0332 | -0.00553 | 1.0000 |
| ensemble_mean | ensemble | 0.23091 | 0.23001 | 0.65266 | 0.499 | 0.0210 | -0.00105 | 1.0000 |

### Final test (3000 unseen rounds; dev base rate 0.6427, test base rate 0.6437)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| logistic_l2 | 0.22976 | 0.65216 | 0.487 | 0.0272 | 0.109 | -0.0031 | 0.5806 | 0.0093 | 31 |
| expanding_frequency | 0.22940 | 0.65136 | 0.473 | 0.0214 | 0.491 | -0.0019 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.23017 | 0.65304 | 0.486 | 0.0356 | 0.019 | -0.0014 | 0.6344 | 0.0917 | 279 |
| reference_constant | 0.22937 | 0.65130 | 0.500 | 0.0217 | 0.582 | +0.0030 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **-0.00161** (95% block-bootstrap CI -0.00351 to +0.00019); DM p = 0.9683, Holm-adjusted across thresholds = 1.0000.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.6600 | +0.0173 | 0.22546 | 0.22479 |
| 7750–8499 | 750 | 0.6293 | -0.0133 | 0.23306 | 0.23345 |
| 8500–9249 | 750 | 0.6453 | +0.0027 | 0.22934 | 0.22892 |
| 9250–9999 | 750 | 0.6400 | -0.0027 | 0.23120 | 0.23043 |

## Target: reaches_2x (break-even probability 0.5000)

**Status: NO DEMONSTRATED EDGE** — Brier skill -0.00046 <= 0.0; Holm-adjusted DM p = 1.0000 >= 0.05; calibration OK (Hosmer-Lemeshow p = 0.3346, mean-prediction gap 0.0059; ECE 0.0274 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **ensemble_mean**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.24946 | 0.24986 | 0.69286 | 0.486 | 0.0186 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.24952 | 0.24994 | 0.69304 | 0.510 | 0.0183 | -0.00035 | n/a |
| training_frequency | baseline | 0.24935 | 0.25021 | 0.69359 | 0.488 | 0.0281 | -0.00143 | n/a |
| reference_constant | baseline | 0.24946 | 0.24962 | 0.69239 | 0.500 | 0.0188 | +0.00095 | n/a |
| markov_conditional | statistical | 0.24935 | 0.25030 | 0.69376 | 0.485 | 0.0240 | -0.00177 | 1.0000 |
| hmm_regime | statistical | 0.24946 | 0.25053 | 0.69422 | 0.483 | 0.0302 | -0.00268 | 1.0000 |
| logistic_l2 | statistical | 0.24817 | 0.25035 | 0.69388 | 0.509 | 0.0236 | -0.00198 | 1.0000 |
| logistic_l1 | statistical | 0.24859 | 0.25031 | 0.69377 | 0.490 | 0.0280 | -0.00179 | 1.0000 |
| random_forest | ml | 0.24604 | 0.25146 | 0.69628 | 0.506 | 0.0306 | -0.00643 | 1.0000 |
| hist_gbm | ml | 0.24946 | 0.25026 | 0.69369 | 0.509 | 0.0237 | -0.00163 | 1.0000 |
| lightgbm | ml | 0.25083 | 0.25066 | 0.69456 | 0.509 | 0.0309 | -0.00322 | 1.0000 |
| ensemble_mean | ensemble | 0.24890 | 0.24987 | 0.69290 | 0.507 | 0.0251 | -0.00006 | 1.0000 |

### Final test (3000 unseen rounds; dev base rate 0.4798, test base rate 0.4897)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| ensemble_mean | 0.25019 | 0.69354 | 0.488 | 0.0274 | 0.335 | -0.0059 | 0.4662 | 0.0422 | 133 |
| expanding_frequency | 0.25008 | 0.69331 | 0.470 | 0.0321 | 0.041 | -0.0111 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.25041 | 0.69397 | 0.502 | 0.0254 | 0.225 | -0.0014 | 0.5145 | 0.2907 | 830 |
| reference_constant | 0.24991 | 0.69298 | 0.500 | 0.0277 | 0.191 | -0.0047 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **-0.00046** (95% block-bootstrap CI -0.00206 to +0.00115); DM p = 0.7260, Holm-adjusted across thresholds = 1.0000.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.5107 | +0.0308 | 0.25110 | 0.25109 |
| 7750–8499 | 750 | 0.4773 | -0.0025 | 0.24971 | 0.24952 |
| 8500–9249 | 750 | 0.4893 | +0.0095 | 0.25015 | 0.25003 |
| 9250–9999 | 750 | 0.4813 | +0.0015 | 0.24982 | 0.24968 |

## Target: reaches_3x (break-even probability 0.3333)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.00002 > 0.0; Holm-adjusted DM p = 1.0000 >= 0.05; calibration OK (Hosmer-Lemeshow p = 0.3940, mean-prediction gap 0.0039; ECE 0.0239 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **markov_conditional**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.21664 | 0.21826 | 0.62830 | 0.481 | 0.0210 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.21692 | 0.21852 | 0.62890 | 0.501 | 0.0183 | -0.00121 | n/a |
| training_frequency | baseline | 0.21655 | 0.21846 | 0.62878 | 0.484 | 0.0216 | -0.00092 | n/a |
| reference_constant | baseline | 0.21659 | 0.21808 | 0.62788 | 0.500 | 0.0143 | +0.00082 | n/a |
| markov_conditional | statistical | 0.21646 | 0.21840 | 0.62864 | 0.493 | 0.0223 | -0.00064 | 1.0000 |
| hmm_regime | statistical | 0.21660 | 0.21857 | 0.62903 | 0.480 | 0.0255 | -0.00142 | 1.0000 |
| logistic_l2 | statistical | 0.21625 | 0.21883 | 0.62962 | 0.506 | 0.0194 | -0.00259 | 1.0000 |
| logistic_l1 | statistical | 0.21630 | 0.21864 | 0.62922 | 0.483 | 0.0241 | -0.00174 | 1.0000 |
| random_forest | ml | 0.21996 | 0.21885 | 0.62971 | 0.501 | 0.0269 | -0.00272 | 1.0000 |
| hist_gbm | ml | 0.21668 | 0.21973 | 0.63179 | 0.500 | 0.0306 | -0.00673 | 1.0000 |
| lightgbm | ml | 0.21642 | 0.21945 | 0.63107 | 0.501 | 0.0283 | -0.00547 | 1.0000 |
| ensemble_mean | ensemble | 0.21648 | 0.21858 | 0.62907 | 0.495 | 0.0260 | -0.00148 | 1.0000 |

### Final test (3000 unseen rounds; dev base rate 0.3213, test base rate 0.3240)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| markov_conditional | 0.21908 | 0.62997 | 0.493 | 0.0239 | 0.394 | -0.0039 | n/a | 0.0000 | 0 |
| expanding_frequency | 0.21908 | 0.62998 | 0.466 | 0.0295 | 0.005 | -0.0040 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.21941 | 0.63075 | 0.496 | 0.0271 | 0.092 | +0.0010 | 0.3255 | 0.3560 | 1063 |
| reference_constant | 0.21902 | 0.62985 | 0.500 | 0.0213 | 0.499 | -0.0007 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00002** (95% block-bootstrap CI -0.00142 to +0.00141); DM p = 0.4912, Holm-adjusted across thresholds = 1.0000.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.3573 | +0.0360 | 0.23106 | 0.23112 |
| 7750–8499 | 750 | 0.3000 | -0.0213 | 0.21034 | 0.21044 |
| 8500–9249 | 750 | 0.3213 | +0.0000 | 0.21851 | 0.21811 |
| 9250–9999 | 750 | 0.3173 | -0.0040 | 0.21640 | 0.21666 |

## Target: reaches_5x (break-even probability 0.2000)

**Status: NO DEMONSTRATED EDGE** — Brier skill +0.00020 > 0.0; Holm-adjusted DM p = 1.0000 >= 0.05; calibration OK (Hosmer-Lemeshow p = 0.7510, mean-prediction gap 0.0040; ECE 0.0149 reported only)

Selected model (lowest development-period Brier score among non-baseline models): **hmm_regime**

### Development walk-forward (validation)

| Model | Family | Train Brier (last fold, in-sample) | Validation Brier | Log loss | AUC | ECE | BSS vs baseline | DM p (Holm) |
|---|---|---|---|---|---|---|---|---|
| expanding_frequency | baseline | 0.15182 | 0.15434 | 0.48739 | 0.480 | 0.0148 | n/a | n/a |
| rolling_frequency_500 | baseline | 0.15196 | 0.15445 | 0.48772 | 0.504 | 0.0138 | -0.00068 | n/a |
| training_frequency | baseline | 0.15176 | 0.15443 | 0.48771 | 0.480 | 0.0193 | -0.00057 | n/a |
| reference_constant | baseline | 0.15181 | 0.15422 | 0.48699 | 0.500 | 0.0122 | +0.00078 | n/a |
| markov_conditional | statistical | 0.15173 | 0.15450 | 0.48794 | 0.480 | 0.0183 | -0.00099 | 1.0000 |
| hmm_regime | statistical | 0.15175 | 0.15445 | 0.48779 | 0.489 | 0.0158 | -0.00072 | 1.0000 |
| logistic_l2 | statistical | 0.15162 | 0.15447 | 0.48785 | 0.482 | 0.0172 | -0.00084 | 1.0000 |
| logistic_l1 | statistical | 0.15169 | 0.15453 | 0.48808 | 0.475 | 0.0203 | -0.00121 | 1.0000 |
| random_forest | ml | 0.15572 | 0.15497 | 0.48944 | 0.486 | 0.0247 | -0.00407 | 1.0000 |
| hist_gbm | ml | 0.15512 | 0.15530 | 0.49037 | 0.491 | 0.0264 | -0.00619 | 1.0000 |
| lightgbm | ml | 0.15305 | 0.15523 | 0.49043 | 0.489 | 0.0245 | -0.00575 | 1.0000 |
| ensemble_mean | ensemble | 0.15274 | 0.15457 | 0.48814 | 0.486 | 0.0204 | -0.00146 | 1.0000 |

### Final test (3000 unseen rounds; dev base rate 0.1905, test base rate 0.1890)

| Model | Brier | Log loss | AUC | ECE | HL p | Cal. gap | Precision@break-even | Recall@break-even | Calls |
|---|---|---|---|---|---|---|---|---|---|
| hmm_regime | 0.15327 | 0.48473 | 0.513 | 0.0149 | 0.751 | +0.0040 | 0.1882 | 0.2257 | 680 |
| expanding_frequency | 0.15330 | 0.48484 | 0.464 | 0.0245 | 0.010 | -0.0008 | n/a | 0.0000 | 0 |
| rolling_frequency_500 | 0.15378 | 0.48639 | 0.487 | 0.0259 | 0.013 | +0.0034 | 0.1879 | 0.3404 | 1027 |
| reference_constant | 0.15330 | 0.48485 | 0.500 | 0.0193 | 0.556 | +0.0050 | n/a | 0.0000 | 0 |

Brier skill vs baseline: **+0.00020** (95% block-bootstrap CI -0.00152 to +0.00200); DM p = 0.4147, Holm-adjusted across thresholds = 1.0000.

Performance by test sub-period:

| Seq range | n | Base rate | Shift vs dev | Brier model | Brier baseline |
|---|---|---|---|---|---|
| 7000–7749 | 750 | 0.2013 | +0.0108 | 0.16086 | 0.16100 |
| 7750–8499 | 750 | 0.1800 | -0.0105 | 0.14778 | 0.14768 |
| 8500–9249 | 750 | 0.1853 | -0.0052 | 0.15104 | 0.15101 |
| 9250–9999 | 750 | 0.1893 | -0.0012 | 0.15339 | 0.15351 |

## Secondary: exact multiplier (log scale)

Target is log(crash multiplier). Baseline is the training-window median. A lower point error does not imply profitable betting decisions.

**validation** (6000 rounds)

| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |
|---|---|---|---|---|
| train_mean_log | 0.7162 | 0.9674 | 1.2518 | 1.0000 |
| train_median_log | 0.6804 | 1.0181 | 0.7675 | 0.5000 |
| ridge | 0.7174 | 0.9775 | 1.2059 | 1.0000 |
| hgb_regressor | 0.7275 | 0.9776 | 1.2674 | 1.0000 |

**test** (3000 rounds)

| Model | MAE(log) | RMSE(log) | Median abs error (x) | DM p vs median |
|---|---|---|---|---|
| train_mean_log | 0.7106 | 0.9499 | 1.2819 | 1.0000 |
| train_median_log | 0.6757 | 0.9955 | 0.8000 | 0.5000 |
| ridge | 0.7101 | 0.9533 | 1.2217 | 1.0000 |
| hgb_regressor | 0.7127 | 0.9538 | 1.2383 | 1.0000 |

## Backtest 1

Config: `{'threshold': 2.0, 'stake': 1.0, 'margins': [0.0, 0.02, 0.05, 0.1], 'rules': {'tie_counts_as_win': True, 'max_net_win': None, 'fee_per_bet': 0.0}, 'decision_latency_s': None}`

| Policy | Bets | Hit rate | Net P&L | ROI | EV/bet 95% CI | Max drawdown |
|---|---|---|---|---|---|---|
| always_bet_fixed_threshold | 3000 | 0.4897 | -62.00 | -0.0207 | -0.0620 to +0.0153 | 120.00 |
| model_ev_gate | 133 | 0.4662 | -9.00 | -0.0677 | -0.0977 to +0.1282 | 13.00 |
| baseline_ev_gate | 0 | n/a | 0.00 | n/a | n/a to n/a | 0.00 |

Independent audit: **PASSED**

## Limitations

- Results apply only to this data source and period; other operators may use different software or rules.
- The reference distribution P(X≥m)=RTP/m and its RTP are unverified assumptions.
- A positive Brier skill is a statistical statement about probability estimates, not proof of a profitable strategy.
- Hypothetical backtests ignore limits, latency and rule details unless explicitly configured.
