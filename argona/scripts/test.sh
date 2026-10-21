#!/usr/bin/env bash
# Backend tests (+ optional PostgreSQL run) and frontend type-check/build.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pip install -q -r requirements-dev.lock
.venv/bin/python -m pytest -q
if [ -n "${ARGONA_TEST_DATABASE_URL:-}" ]; then .venv/bin/python -m pytest -q; fi
(cd frontend && npx tsc -b && npx vite build >/dev/null && echo "frontend OK")
