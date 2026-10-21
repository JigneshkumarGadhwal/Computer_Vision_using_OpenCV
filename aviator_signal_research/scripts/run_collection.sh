#!/usr/bin/env bash
# Long, chunked observation run of the Spribe Aviator demo with per-round fairness verification.
# Usage: scripts/run_collection.sh [target_rounds] [max_hours] [chunk_minutes]
# Stops at the round target, the time limit, or after 3 consecutive chunks that recorded nothing.
set -u
cd "$(dirname "$0")/.."
TARGET=${1:-3000}; MAX_HOURS=${2:-24}; CHUNK=${3:-100}
SOURCE="spribe-demo-observed"
LOG="data/raw/observations/run_$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p data/raw/observations
START=$(date +%s); EMPTY=0
count() { python - <<PY
import sqlite3; c = sqlite3.connect("data/aviator_research.db")
r = c.execute("select count(*) from rounds r join data_sources s on s.id=r.source_id where s.name='$SOURCE'").fetchone()
print(r[0])
PY
}
echo "$(date -u +%FT%TZ) start target=$TARGET max_hours=$MAX_HOURS chunk=$CHUNK rounds=$(count)" | tee -a "$LOG"
while :; do
  N=$(count)
  if [ "$N" -ge "$TARGET" ]; then echo "$(date -u +%FT%TZ) target reached: $N" | tee -a "$LOG"; break; fi
  if [ $(( $(date +%s) - START )) -ge $(( MAX_HOURS * 3600 )) ]; then echo "$(date -u +%FT%TZ) time limit" | tee -a "$LOG"; break; fi
  python scripts/research_cli.py observe --profile spribe --confirm-legal-age --verify-fairness \
         --source "$SOURCE" --minutes "$CHUNK" > "$LOG.chunk" 2>&1
  M=$(count)
  echo "$(date -u +%FT%TZ) chunk done: +$((M - N)) rounds (total $M) $(grep -E '"stop_reason"' "$LOG.chunk" | tr -d ' ,')" | tee -a "$LOG"
  grep -E '"(requested|verified|mismatch|unavailable|confirmed|ws_only|dom_only)"' "$LOG.chunk" | tr -d ' \n' >> "$LOG"; echo >> "$LOG"
  if [ "$M" -eq "$N" ]; then EMPTY=$((EMPTY + 1)); else EMPTY=0; fi
  if [ "$EMPTY" -ge 3 ]; then echo "$(date -u +%FT%TZ) 3 empty chunks in a row: stopping" | tee -a "$LOG"; break; fi
  sleep 30
done
echo "$(date -u +%FT%TZ) finished: $(count) rounds" | tee -a "$LOG"
