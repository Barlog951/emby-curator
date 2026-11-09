#!/bin/bash
# Weekly: give every Movie/Series that still has no poster or no backdrop a frame-based one.
# Runs on emby-gpu; DEDUPE_EMBY_API_KEY comes from the unit's EnvironmentFile
# /run/emby-dedupe/poster-sweep.env; needs /home/barlog/.frame-venv.
# Both passes always run; the script exits non-zero when either pass reported errors.
# Output also goes to ~/poster-sweep/<date>.log (kept 90 days).
set -uo pipefail
BASE="http://localhost:8096/emby"; PY=/home/barlog/.frame-venv/bin/python; TOOL=/home/barlog/frame_poster.py
PASS_TIMEOUT=4h  # one hung ffmpeg must not block this and every later Sunday
export HF_HUB_OFFLINE=1  # the CLIP models are cached; no Hugging Face lookups on each run
LOG_DIR=/home/barlog/poster-sweep
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name '*.log' -mtime +90 -delete
exec > >(tee -a "$LOG_DIR/$(date +%F).log") 2>&1

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# The item list is several MB: pass it via a file, never argv (one argv string is capped at 128 KB).
if ! curl -sf -H @<(printf 'X-Emby-Token: %s\n' "$DEDUPE_EMBY_API_KEY") \
    "$BASE/Items?Recursive=true&IncludeItemTypes=Movie,Series&Fields=ImageTags,BackdropImageTags&Limit=100000" \
    -o "$WORK/items.json"; then
  echo "$(date '+%F %T') sweep: could not read the item list"
  exit 1
fi
python3 - "$WORK" <<'PY'
import json, os, sys
work = sys.argv[1]
items = json.load(open(os.path.join(work, "items.json")))["Items"]
open(os.path.join(work, "noposter.txt"), "w").write("\n".join(i["Id"] for i in items if "Primary" not in (i.get("ImageTags") or {})) + "\n")
open(os.path.join(work, "nobackdrop.txt"), "w").write("\n".join(i["Id"] for i in items if not i.get("BackdropImageTags")) + "\n")
PY
np=$(grep -c . "$WORK/noposter.txt" || true); nb=$(grep -c . "$WORK/nobackdrop.txt" || true)
echo "$(date '+%F %T') sweep: $np without poster, $nb without backdrop"
rc=0
if [ "$np" -gt 0 ]; then
  timeout "$PASS_TIMEOUT" $PY -u -W ignore "$TOOL" "$WORK/noposter.txt" --clip --apply || rc=1
fi
if [ "$nb" -gt 0 ]; then
  timeout "$PASS_TIMEOUT" $PY -u -W ignore "$TOOL" "$WORK/nobackdrop.txt" --clip --backdrop --apply || rc=1
fi
echo "$(date '+%F %T') sweep done (exit $rc)"
exit "$rc"
