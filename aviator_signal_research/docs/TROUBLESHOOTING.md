# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard shows "Cannot reach the API" | Backend not running, or wrong port | Start it with `uvicorn app.api.main:app --port 8000` from `backend/`. With a custom backend URL set `VITE_PROXY_TARGET` (dev) or `VITE_API_URL` (build) |
| Import returns 422 "No multiplier column found" | Column name not recognised | Rename the column to `multiplier` or one of the aliases in `docs/API.md` |
| Many rows `timezone_assumed_utc` | Naive timestamps without a timezone column | Pass `default_timezone` (e.g. `Europe/Berlin`) on import |
| Rows rejected as `backfill_before_stored_data` | File contains rounds older than the newest stored round of that source | Import older history into a **new** source (sequence numbers must stay stable for stored predictions) |
| Both rows of an id rejected as `conflicting_round` | Same round id with different multipliers | Resolve at the data source; the app cannot know which is correct |
| Verdict `needs_review` | > 20% rejected rows, conflicts or repeated blocks | Inspect the preview; fix the export before trusting results |
| Evaluation ends `INSUFFICIENT EVIDENCE` with "needs at least N rounds" | Not enough data for the protocol | Import more rounds, or lower `initial_train_size` / `block_size` (≥ 700 initial rounds are required because of the 500-round feature warm-up) |
| Evaluation `failed` | Exception during the run | The full traceback is stored in the run (`error`), shown on the Walk-forward page and in the report |
| "database is locked" | Concurrent SQLite writers during a long evaluation | Wait for the run, or use PostgreSQL (`AVR_DATABASE_URL=postgresql+psycopg://...`) |
| Signal says "Model trained N rounds ago" | More than 1,000 new rounds since the evaluation | Re-run the evaluation (documented retrain schedule) |
| Signal says "checksum mismatch" | Model artifact changed on disk | Re-run the evaluation; never edit `data/models/` by hand |
| Signal disabled, feed "STALE" | No new live round within `AVR_STALE_AFTER_SECONDS` | Check the collector; signals re-enable automatically when rounds arrive |
| Evaluation is slow | Random forest / HMM / ensemble on large data | Deselect models on the Walk-forward page, or increase `block_size` (fewer retrains) |
| `lightgbm` import error | Optional dependency missing | `pip install lightgbm` or ignore — it is skipped automatically |
| Frontend tests fail with jsdom errors | Node < 18 | Use Node 18+ (tested with 22) |
