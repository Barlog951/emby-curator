#!/bin/bash
# dv-worker.sh — CONTINUOUS Dolby-Vision P5 -> HDR10 drainer (GPU-priority-aware, hardened).
#
# Loops: scan -> ask dv-load how many slots are free (0 if Emby/Ollama need the GPU) ->
# take that many files off the pile -> convert them in parallel (niced + supervised) ->
# repeat. Drains the whole pile back-to-back with NO idle gaps, yields the GPU to Emby/Ollama
# the moment they need it, and exits when the pile is empty. The hourly dv-worker.timer just
# (re)starts it — to pick up newly-added media, or after a reboot / max-runtime cycle.
#
# Robustness:
#   * per-file `timeout` (DV_PERFILE) kills a hung/forever-paused conversion; dv-convert traps
#     SIGTERM and tears down its ffmpeg+supervisor cleanly (no orphans).
#   * a failed conversion is recorded via `dv-scan.py --fail`; after 3 fails the file is marked
#     'failed' and skipped, so one corrupt source can't block the queue.
#   * bounded lifetime (DV_MAX_RUN) so a wedged loop can't run forever; the timer restarts it.
# Every outcome is logged to journald (the service) AND to ~/dv-convert-history.jsonl.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${DV_LOG:-/tmp/dv-worker.log}"
ROOT="${DV_ROOT:-/Movies}"
MAXSLOTS="${DV_MAXSLOTS:-2}"
PERFILE="${DV_PERFILE:-14400}"     # 4h per conversion (allows long Emby-yield pauses)
MAX_RUN="${DV_MAX_RUN:-21600}"     # 6h max lifetime; the timer restarts us
IDLE_WAIT="${DV_IDLE_WAIT:-300}"   # recheck cadence while GPU is busy
ts() { date -u +%FT%TZ; }
log() { echo "$(ts) $*" | tee -a "$LOG"; }

START="$(date +%s)"
log "worker start (continuous: maxslots=$MAXSLOTS perfile=${PERFILE}s maxrun=${MAX_RUN}s)"

while true; do
  if [ $(( $(date +%s) - START )) -ge "$MAX_RUN" ]; then
    log "max runtime reached -> exit (timer will restart)"; break
  fi

  # incremental scan (fast; refresh pile, archive removed)
  python3 "$HERE/dv-scan.py" "$ROOT" >>"$LOG" 2>&1

  # how many conversions may start now (live GPU-pressure gate)
  SLOTS=$(DV_MAXSLOTS="$MAXSLOTS" python3 "$HERE/dv-load.py" 2>>"$LOG")
  SLOTS="${SLOTS:-0}"
  if [ "$SLOTS" -le 0 ]; then
    log "GPU in use by Emby/Ollama -> yield, recheck in ${IDLE_WAIT}s"
    sleep "$IDLE_WAIT"; continue
  fi

  # take SLOTS distinct files off the pile (only true Profile-5, 'failed' excluded)
  mapfile -t FILES < <(python3 "$HERE/dv-scan.py" --pile-paths 2>/dev/null | head -n "$SLOTS")
  if [ "${#FILES[@]}" -eq 0 ]; then
    log "pile empty -> drain complete, exit"; break
  fi

  # launch the batch: per-file timeout + niced + supervised; keep both files (--mark)
  declare -a PIDS=() PFILE=()
  for f in "${FILES[@]}"; do
    log "converting: $f"
    timeout "$PERFILE" nice -n 19 ionice -c3 \
      python3 "$HERE/dv-convert.py" "$f" --mark --supervise >>"$LOG" 2>&1 &
    PIDS+=("$!"); PFILE+=("$f")
    sleep 5
  done

  # wait; on failure (incl. timeout) record it so a bad file gets skipped after 3 tries
  for i in "${!PIDS[@]}"; do
    if wait "${PIDS[$i]}"; then
      log "ok: ${PFILE[$i]}"
    else
      rc=$?
      log "FAILED (rc=$rc): ${PFILE[$i]}"
      python3 "$HERE/dv-scan.py" --fail "${PFILE[$i]}" >>"$LOG" 2>&1
    fi
  done
done

log "worker exit"
