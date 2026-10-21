# Evaluation report template

The system writes a filled-in version of this report automatically for every run
(`reports/evaluation_run_<id>.md`, or `GET /api/evaluations/<id>/report`). Use this template when reporting
results elsewhere (thesis, review). **Every number must come from a stored run; cite the run id and data
fingerprint.**

---

## 1. Identification
- Run id / date:
- Data source (name, kind, platform, game version):
- Real or synthetic:
- Data fingerprint (SHA-256 of round ids + multipliers):
- Code version:

## 2. Data
- Rounds stored / verified:
- Period covered (first → last timestamp):
- Import verdicts, rejected rows and reasons:
- Known gaps / missing rounds:
- Data-source limitations (collection method, delays, authorisation):

## 3. Pre-registered protocol
- Targets and thresholds (primary threshold):
- Development / final-test split (sequence and time ranges):
- Walk-forward: initial window, retrain schedule, purge gap:
- Baseline:
- Signal criteria (skill, test, multiple-comparison correction, calibration, minimum sample):
- Any deviation from the protocol and why (if none, state "none"):

## 4. Integrity checks
- Look-ahead audit result:
- Data-quality gate:
- Prediction-ledger integrity (predictions before outcomes):
- Backtest audit:

## 5. Results per threshold
For each threshold:
- Selected model and selection rule:
- Development: train vs validation Brier for all models; Holm-adjusted p-values:
- Final test (n rounds): Brier, log loss, AUC, ECE, Hosmer–Lemeshow p, calibration gap — model vs each baseline:
- Brier skill vs baseline with 95% CI; DM p (raw and Holm-adjusted):
- Precision / recall / confusion at the break-even probability 1/M:
- Performance by sub-period and under base-rate shift:
- Status and reasons:

## 6. Exact multiplier (secondary)
- MAE/RMSE on log scale, pinball losses and coverage vs training-median baseline:
- Note that point accuracy does not imply profitable decisions.

## 7. Hypothetical decision analysis (optional)
- Stake, cash-out target, payout rules used:
- Policies compared (always-bet, model EV gate, baseline EV gate):
- Bets, hit rate, net P&L, ROI, EV per bet with CI, maximum drawdown, losing streaks:
- Sensitivity to EV margin and threshold:
- Statement that this is a hypothetical simulation and not evidence of a profitable strategy.

## 8. Conclusion
- Was out-of-sample predictive improvement over the baseline demonstrated? (yes / no / insufficient evidence)
- Strength of evidence and main caveats:
- What additional data or verification would change the conclusion:
