#!/usr/bin/env bash
# Online backup. SQLite: consistent copy via the sqlite backup API. PostgreSQL: pg_dump custom format.
# Keeps the newest $KEEP backups (default 14). Usage: ./scripts/backup.sh [out_dir]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
OUT=${1:-${ARGONA_DATA_DIR:-./data}/backups}
KEEP=${KEEP:-14}
mkdir -p "$OUT"
if [[ "${ARGONA_DATABASE_URL:-}" == postgresql* ]]; then
  URL=${ARGONA_DATABASE_URL/postgresql+asyncpg/postgresql}
  F="$OUT/argona-$(date -u +%Y%m%dT%H%M%SZ).dump"
  pg_dump --format=custom --file "$F" "$URL"
  echo "$F"
else
  .venv/bin/argona backup --out "$OUT"
fi
ls -1t "$OUT"/argona-* 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
