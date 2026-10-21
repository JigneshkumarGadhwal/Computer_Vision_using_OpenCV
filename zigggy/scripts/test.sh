#!/usr/bin/env bash
# Backend tests (+ optional PostgreSQL run) and frontend type-check/build.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pip install -q -r requirements-dev.lock
.venv/bin/python -m pytest -q
if [ -n "${ZIGGGY_TEST_DATABASE_URL:-}" ]; then .venv/bin/python -m pytest -q; fi
(cd frontend && npx tsc -b && npx vite build >/dev/null && echo "frontend OK")
# Browser E2E (boots its own SIM backends). Needs Chromium: npx playwright install chromium, or set PLAYWRIGHT_CHROMIUM_PATH.
if [ "${E2E:-0}" = "1" ]; then (cd frontend && npx playwright test --reporter=line); fi
