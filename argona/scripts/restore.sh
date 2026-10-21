#!/usr/bin/env bash
# Restore a backup. STOP ARGONA FIRST (systemctl stop argona / docker compose stop argona).
# Usage: ./scripts/restore.sh <backup file>
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
F=${1:?usage: restore.sh <backup-file>}
if [[ "${ARGONA_DATABASE_URL:-}" == postgresql* ]]; then
  URL=${ARGONA_DATABASE_URL/postgresql+asyncpg/postgresql}
  pg_restore --clean --if-exists --no-owner --dbname "$URL" "$F"
else
  .venv/bin/argona restore "$F"
fi
.venv/bin/argona migrate
echo "Restored. Start ARGONA; it reconciles on startup and never resumes LIVE automatically."
