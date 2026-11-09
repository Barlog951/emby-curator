#!/bin/bash
# Weekly ČSFD pass, run by the emby-csfd-weekly unit on emby-gpu:
#   1. fill   — overview / genres / year / rating / poster / cast for anything still missing them,
#               honouring the hand-made map in ~/csfd-map.tsv ("<emby_id><TAB><csfd_url>" lines)
#   2. people — ČSFD portraits for actors without a photo (real photos only, never placeholders);
#               the page cache makes re-runs cheap: only new actors hit ČSFD.
# Both steps always run; the script exits with the worse of their exit codes, so a failed
# step marks the unit failed. Output also goes to ~/csfd-weekly/<date>.log (kept 90 days).
# Secrets (Emby API key, TypeSafe key) come from the unit's EnvironmentFile /run/emby-dedupe/csfd.env.
set -uo pipefail
export DEDUPE_FLARESOLVERR_URL=http://localhost:8191/v1
LOG_DIR=/home/barlog/csfd-weekly
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name '*.log' -mtime +90 -delete
exec > >(tee -a "$LOG_DIR/$(date +%F).log") 2>&1

# AI match fallback: review-only. Suggestions land in ~/csfd-ai-review.tsv as ready --map lines.
AI_ARGS=()
if [ -n "${DEDUPE_TYPESAFE_API_KEY:-}" ]; then
  AI_ARGS=(--ai-match --ai-review-file /home/barlog/csfd-ai-review.tsv)
fi
CLI=/home/barlog/emby-dedupe/.venv/bin/emby-curator
MAP=/home/barlog/csfd-map.tsv; [ -f "$MAP" ] || : > "$MAP"

echo "$(date '+%F %T') csfd fill"
$CLI csfd fill --all-libraries --doit --map "$MAP" --report /home/barlog/csfd-weekly-fill.tsv "${AI_ARGS[@]}"
fill_rc=$?
echo "$(date '+%F %T') csfd fill exit $fill_rc"

echo "$(date '+%F %T') csfd people"
# most-used actors first; the limit drains the long tail over successive weeks instead of one 15-hour run
$CLI csfd people --all-libraries --min-refs "${CSFD_PEOPLE_MIN_REFS:-1}" --limit "${CSFD_PEOPLE_LIMIT:-1500}" \
  --doit --report /home/barlog/csfd-weekly-people.tsv
people_rc=$?
echo "$(date '+%F %T') csfd people exit $people_rc"

rc=$(( fill_rc > people_rc ? fill_rc : people_rc ))
echo "$(date '+%F %T') csfd weekly done (exit $rc)"
exit "$rc"
