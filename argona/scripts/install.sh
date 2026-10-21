#!/usr/bin/env bash
# Local / VPS install without Docker. Idempotent. Usage: ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
command -v node >/dev/null || { echo "Node.js 20+ is required to build the dashboard"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
(cd frontend && npm ci --no-audit --no-fund && npm run build)
if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN=$(.venv/bin/argona gen-token)
  sed -i.bak "s|^ARGONA_API_TOKEN=.*|ARGONA_API_TOKEN=${TOKEN}|" .env && rm -f .env.bak
  chmod 600 .env
  echo "Created .env with a fresh API token (see ARGONA_API_TOKEN in .env)."
fi
mkdir -p data
.venv/bin/argona migrate
echo "Install complete. Start with: ./scripts/start.sh   (dashboard: http://127.0.0.1:8000)"
