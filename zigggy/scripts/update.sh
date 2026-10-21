#!/usr/bin/env bash
# Update in place with automatic pre-update backup. Roll back with: git checkout <previous-tag> && ./scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/backup.sh
git pull --ff-only
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
(cd frontend && npm ci --no-audit --no-fund && npm run build)
.venv/bin/zigggy migrate
if systemctl is-active --quiet zigggy 2>/dev/null; then sudo systemctl restart zigggy; fi
echo "Updated. Verify: curl -s http://127.0.0.1:8000/api/health"
