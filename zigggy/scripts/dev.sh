#!/usr/bin/env bash
# Development: backend on :8000 (SIM venue + real providers), Vite dev server on :5173 with API proxy.
set -euo pipefail
cd "$(dirname "$0")/.."
export ZIGGGY_PROVIDERS=${ZIGGGY_PROVIDERS:-polymarket,kalshi,sim} ZIGGGY_LOG_JSON=false
.venv/bin/zigggy serve & BACK=$!
trap 'kill $BACK' EXIT
(cd frontend && npm run dev)
