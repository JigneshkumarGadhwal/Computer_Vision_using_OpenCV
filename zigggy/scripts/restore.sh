#!/usr/bin/env bash
# Restore a backup. STOP ZigGgy FIRST (systemctl stop zigggy / docker compose stop zigggy).
# Usage: ./scripts/restore.sh <backup file>
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
F=${1:?usage: restore.sh <backup-file>}
if [[ "${ZIGGGY_DATABASE_URL:-}" == postgresql* ]]; then
  URL=${ZIGGGY_DATABASE_URL/postgresql+asyncpg/postgresql}
  pg_restore --clean --if-exists --no-owner --dbname "$URL" "$F"
else
  .venv/bin/zigggy restore "$F"
fi
.venv/bin/zigggy migrate
echo "Restored. Start ZigGgy; it reconciles on startup and never resumes LIVE automatically."
