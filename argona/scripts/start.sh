#!/usr/bin/env bash
# Foreground start (Ctrl+C = safe shutdown). For always-on operation use Docker or systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/argona serve
