"""Fold-safe deletion — remove a duplicate the safety guard refused, without data loss.

The safety guard (:mod:`emby_dedupe.api.deletion_guard`) refuses any Emby
``DELETE /Items/{id}`` that would fold-delete a folder holding the keeper. That is the
right call for the Emby API — but it leaves the co-located duplicate on disk, needing
manual cleanup (e.g. two quality versions of one title dropped into a single
``/Movies/Dokumenty/<title>/`` folder).

This module removes exactly those refused duplicates the ONLY way that cannot destroy the
keeper: delete the single **file** on the media host over SSH (never the directory, never
via the Emby API). Emby drops the now-missing item on its next library scan. Every removal
re-verifies on the host itself — the delete path is a real file, the keeper is a real file,
and they are different paths — so a stale plan can never rm the wrong thing.

Runs from wherever the dedupe runs (Emby reports the host's canonical ``/Movies/...`` path,
which is also the real path on the media host — no path mapping needed). The SSH runner is
injectable so the planning/command logic is unit-testable without touching a real host.
"""
import logging
import os
import shlex
import subprocess

logger = logging.getLogger(__name__)

# Marker key set on a delete item by the deletion code when the guard refuses it (in both
# --doit and dry-run). Reading it here reuses the guard's ACTUAL decision, so this module
# never re-derives "was it refused?" and can never diverge from what the run did.
GUARD_REFUSED_KEY = "fold_safe_candidate"

# SSH target of the media host, as ``user@host``. Deliberately has NO default: this module
# runs ``rm`` on whatever host it is pointed at, so the target must always be stated
# explicitly (``--fold-safe-host`` / ``DEDUPE_FOLD_SAFE_HOST``) rather than inherited from
# someone else's environment.
DEFAULT_SSH_HOST = os.environ.get("DEDUPE_FOLD_SAFE_HOST", "")

# Same-content sanity gate (added after the 2026-07-09 incident: 19 DISTINCT episodes
# mis-named "S01E01 (2)".."(20)" were grouped as duplicates of one episode and auto-rm'd —
# guard-refusal says "co-located", it does NOT say "same content").
#
# Two gates, each independently forcing a plan to ``needs_review`` (never auto-rm):
#
# 1. Member count: more than this many planned rm's in ONE decision group is not a
#    duplicate cleanup, it is a mis-grouped set (a title legitimately has 2-4 quality
#    versions; the incident had 19). The whole group goes to review.
FOLD_SAFE_MAX_GROUP_DELETES = 5
# 2. Duration identity: the same content in a different encode has the SAME runtime to
#    within seconds, while a wrongly-grouped different movie/cut does not. (File SIZE is
#    deliberately NOT used: a 2160p keeper next to a 1080p delete differs 4-5x in size
#    while being the same content, and the incident's distinct episodes had SIMILAR sizes
#    — size separates neither class.) Unknown duration on either side also goes to
#    review: rm-ing a file over SSH is unrecoverable, so "can't verify" means "don't".
FOLD_SAFE_DURATION_TOLERANCE = 0.02   # 2% relative divergence
FOLD_SAFE_DURATION_FLOOR_SECONDS = 15  # ignore sub-15s absolute differences

_TICKS_PER_SECOND = 10_000_000  # Emby RunTimeTicks resolution


def _duration_review_reason(keeper_ticks, delete_ticks) -> str | None:
    """Return a needs-review reason when durations are unknown or diverge, else None."""
    if not keeper_ticks or not delete_ticks:
        return "duration-unknown (cannot verify same content)"
    keeper_s = keeper_ticks / _TICKS_PER_SECOND
    delete_s = delete_ticks / _TICKS_PER_SECOND
    diff = abs(keeper_s - delete_s)
    if diff > max(FOLD_SAFE_DURATION_TOLERANCE * max(keeper_s, delete_s),
                  FOLD_SAFE_DURATION_FLOOR_SECONDS):
        return f"duration-mismatch (keeper {keeper_s:.0f}s vs delete {delete_s:.0f}s)"
    return None


def plan_fold_safe_deletes(decisions: list) -> list:
    """Collect the guard-refused duplicates that can be safely file-deleted.

    Guard-refusal is necessary but NOT sufficient for an auto-rm: every plan also passes
    a same-content sanity gate (duration identity + group-size cap, see the constants
    above). Plans failing the gate are still returned, marked ``status="needs_review"``
    with a ``review_reason`` — :func:`execute_fold_safe_deletes` refuses to rm them.

    Args:
        decisions: the dedupe decision groups, after the deletion pass has tagged each
            guard-refused item with :data:`GUARD_REFUSED_KEY`.

    Returns:
        A list of plan dicts ``{"id", "group", "keeper", "delete", "reason", "status",
        "review_reason"}`` — one per refused duplicate whose keeper is a *different*,
        non-empty path; ``status`` is ``"ok"`` or ``"needs_review"``. Pure: no host
        access, no filesystem access.
    """
    plans = []
    for decision in decisions or []:
        group_plans = _plans_for_decision(decision)
        _flag_oversized_group(group_plans)
        plans.extend(group_plans)
    return plans


def _plans_for_decision(decision: dict) -> list[dict]:
    """Build the plan dicts for ONE decision group (duration gate applied per item)."""
    title = decision.get("title") or decision.get("name") or ""
    keeper_ticks = (decision.get("keep") or {}).get("runtime_ticks")
    group_plans = []
    for item in decision.get("delete", []) or []:
        cand = item.get(GUARD_REFUSED_KEY)
        if not cand:
            continue
        keeper = cand.get("keeper")
        delete = cand.get("delete") or item.get("path")
        if not (keeper and delete and keeper != delete):
            continue
        review_reason = _duration_review_reason(keeper_ticks, item.get("runtime_ticks"))
        group_plans.append(
            {
                "id": item.get("id"),
                "group": title,
                "keeper": keeper,
                "delete": delete,
                "reason": cand.get("reason", ""),
                "status": "needs_review" if review_reason else "ok",
                "review_reason": review_reason or "",
            }
        )
    return group_plans


def _flag_oversized_group(group_plans: list[dict]) -> None:
    """Send an implausibly large group ENTIRELY to review, in place.

    A group with more than :data:`FOLD_SAFE_MAX_GROUP_DELETES` planned deletes is a
    mis-grouping, not a duplicate cleanup — so every plan in it is held, including any
    that passed the duration gate.
    """
    if len(group_plans) <= FOLD_SAFE_MAX_GROUP_DELETES:
        return
    suspect = f"group-too-large ({len(group_plans)} planned deletes in one group)"
    for plan in group_plans:
        plan["status"] = "needs_review"
        plan["review_reason"] = (
            f"{plan['review_reason']}; {suspect}" if plan["review_reason"] else suspect
        )


def _build_remote_script(keeper: str, delete: str, doit: bool) -> str:
    """Build the remote shell script that verifies then (optionally) removes one file.

    Verification and removal live in ONE script so there is no gap between the check and
    the ``rm`` (no TOCTOU). Refuses unless the delete path is a regular file, the keeper is
    a regular file, and the two resolve to different files. Same-stem sidecars (``.nfo`` and
    subtitle files, including language-infixed ones like ``name.en.srt``) are removed too
    when present. Never uses ``rm -r`` and never names a directory.
    """
    d = shlex.quote(delete)
    k = shlex.quote(keeper)
    # After removing the media file, sweep same-stem sidecars (metadata + subtitles) so the
    # server drops them on rescan instead of leaving orphans. `for s in "$dd"/*` uses pathname
    # expansion (space-safe); the quoted "$stem" in each case-pattern is matched literally, so
    # special characters in the name are handled correctly and only THIS file's sidecars match.
    sidecar_sweep = (
        'dd=$(dirname -- "$d"); stem=$(basename -- "$d"); stem="${stem%.*}"; '
        'for s in "$dd"/*; do [ -f "$s" ] || continue; bs=$(basename -- "$s"); case "$bs" in '
        '"$stem".nfo|"$stem".srt|"$stem".ass|"$stem".ssa|"$stem".sub|"$stem".idx|"$stem".vtt) rm -- "$s" ;; '
        '"$stem".*.srt|"$stem".*.ass|"$stem".*.ssa|"$stem".*.sub|"$stem".*.idx|"$stem".*.vtt) rm -- "$s" ;; '
        'esac; done'
    )
    action = f'rm -- "$d"; {sidecar_sweep}; echo OK' if doit \
        else 'echo "WOULD-RM $d"'
    return (
        "set -u; "
        f"d={d}; k={k}; "
        '[ -f "$d" ] || { echo "SKIP delete-not-a-regular-file"; exit 10; }; '
        '[ -f "$k" ] || { echo "SKIP keeper-missing"; exit 11; }; '
        '[ "$(readlink -f "$d")" != "$(readlink -f "$k")" ] || { echo "SKIP same-file"; exit 12; }; '
        f"{action}"
    )


def _ssh_runner(ssh_host: str, remote_script: str) -> tuple[int, str, str]:
    """Run ``remote_script`` on ``ssh_host`` via SSH. Returns (rc, stdout, stderr).

    ``BatchMode=yes`` fails fast instead of hanging on an auth/host-key prompt.
    """
    proc = subprocess.run(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new", ssh_host, remote_script,
        ],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def execute_fold_safe_deletes(
    plans: list, ssh_host: str = DEFAULT_SSH_HOST, doit: bool = False, runner=_ssh_runner
) -> list:
    """Verify and (if ``doit``) file-delete each planned duplicate on the media host.

    Args:
        plans: output of :func:`plan_fold_safe_deletes`.
        ssh_host: ``user@host`` of the media host.
        doit: when True, actually ``rm`` the file; when False, only verify + report what
            would be removed (dry-run — nothing on the host is touched).
        runner: callable ``(ssh_host, remote_script) -> (rc, stdout, stderr)`` — injected in
            tests. Defaults to a real SSH call.

    Returns:
        A list of result dicts ``{**plan, "status", "detail"}`` where status is one of
        ``removed`` / ``would_remove`` / ``skipped`` / ``failed`` / ``needs_review``.
    """
    results = []
    for plan in plans:
        if plan.get("status") == "needs_review":
            # Failed the same-content sanity gate in planning — never auto-rm; the user
            # must resolve these manually.
            logger.warning(
                "fold-safe delete: NEEDS REVIEW (not removed) %r — %s",
                plan["delete"], plan.get("review_reason", ""),
            )
            results.append(
                {**plan, "status": "needs_review", "detail": plan.get("review_reason", "")}
            )
            continue
        script = _build_remote_script(plan["keeper"], plan["delete"], doit)
        try:
            rc, out, err = runner(ssh_host, script)
        except Exception as e:  # noqa: BLE001 — one bad host call must not abort the rest
            logger.error("fold-safe delete: SSH call failed for %r: %s", plan["delete"], e)
            results.append({**plan, "status": "failed", "detail": str(e)})
            continue

        if rc == 0 and out == "OK":
            logger.info("fold-safe delete: removed %r (keeper kept: %r)", plan["delete"], plan["keeper"])
            results.append({**plan, "status": "removed", "detail": ""})
        elif rc == 0 and out.startswith("WOULD-RM"):
            results.append({**plan, "status": "would_remove", "detail": ""})
        elif rc in (10, 11, 12):
            logger.warning("fold-safe delete: skipped %r — %s", plan["delete"], out)
            results.append({**plan, "status": "skipped", "detail": out})
        else:
            logger.error(
                "fold-safe delete: FAILED %r rc=%s out=%r err=%r", plan["delete"], rc, out, err
            )
            results.append({**plan, "status": "failed", "detail": err or out})
    return results
