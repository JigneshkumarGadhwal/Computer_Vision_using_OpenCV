#!/usr/bin/env bash
# Development: backend on :8000 (SIM venue + real providers), Vite dev server on :5173 with API proxy.
set -euo pipefail
cd "$(dirname "$0")/.."
export ARGONA_PROVIDERS=${ARGONA_PROVIDERS:-polymarket,kalshi,sim} ARGONA_LOG_JSON=false
.venv/bin/argona serve & BACK=$!
trap 'kill $BACK' EXIT
(cd frontend && npm run dev)
