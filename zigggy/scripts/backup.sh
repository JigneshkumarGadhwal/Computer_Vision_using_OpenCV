#!/usr/bin/env bash
# Online backup. SQLite: consistent copy via the sqlite backup API. PostgreSQL: pg_dump custom format.
# Keeps the newest $KEEP backups (default 14). Usage: ./scripts/backup.sh [out_dir]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
OUT=${1:-${ZIGGGY_DATA_DIR:-./data}/backups}
KEEP=${KEEP:-14}
mkdir -p "$OUT"
if [[ "${ZIGGGY_DATABASE_URL:-}" == postgresql* ]]; then
  URL=${ZIGGGY_DATABASE_URL/postgresql+asyncpg/postgresql}
  F="$OUT/zigggy-$(date -u +%Y%m%dT%H%M%SZ).dump"
  pg_dump --format=custom --file "$F" "$URL"
  echo "$F"
else
  .venv/bin/zigggy backup --out "$OUT"
fi
ls -1t "$OUT"/zigggy-* 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
