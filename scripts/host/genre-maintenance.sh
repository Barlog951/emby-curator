#!/bin/bash
# Monthly genre maintenance, run by the emby-dedupe-genres unit on emby-gpu
# (installed as ~/emby-dedupe/scripts/genre-maintenance.sh, untracked there).
# Every step runs; a failed step is remembered and the script exits 1 after the audit,
# so the unit shows the failure. Output also goes to ~/genre-maintenance/<date>.log (kept a year).
set -uo pipefail

VENV=/home/barlog/emby-dedupe/.venv
BIN=$VENV/bin/emby-dedupe
WORKDIR=/home/barlog/emby-dedupe
LOG_DIR=/home/barlog/genre-maintenance
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name '*.log' -mtime +400 -delete
exec > >(tee -a "$LOG_DIR/$(date +%F).log") 2>&1

cd "$WORKDIR" || exit 1
failed=()
step() {  # step <label> <command...>
  local label=$1; shift
  echo "--- $label ---"
  if ! "$@"; then
    echo "!!! $label failed"
    failed+=("$label")
  fi
}

echo "=== Genre maintenance started: $(date -Iseconds) ==="
step "Step 1: Normalize variant genres" $BIN genres normalize --doit --all-libraries
step "Step 2: Fix missing genres (gaps only)" $BIN genres fix --doit --validate --all-libraries
# "Česko-slovenský" genre on Czech/Slovak films (add-only; ČSFD fallback via FlareSolverr).
step "Step 3: Czech/Slovak origin genre" env DEDUPE_FLARESOLVERR_URL=http://localhost:8191/v1 \
  $BIN genres origin --doit --all-libraries
step "Step 4: Audit + suggest unknown genres" $BIN genres audit --all-libraries --suggest
echo "=== Genre maintenance done: $(date -Iseconds) ==="

if [ ${#failed[@]} -gt 0 ]; then
  echo "Failed steps: ${failed[*]}"
  exit 1
fi
