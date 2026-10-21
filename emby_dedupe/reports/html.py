"""
HTML report generation for the Emby Dedupe tool.
"""

import os
import tempfile
import time
from typing import Any

from tqdm import tqdm

from emby_dedupe.reports.common import calculate_report_statistics
from emby_dedupe.utils.logging import logger


def _validate_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filter decisions to only include valid ones with proper keep and delete items.

    Args:
        decisions: List of decision objects.

    Returns:
        List of valid decision objects.
    """
    valid_decisions = []

    for decision in decisions:
        # Regular validation for decisions
        if not decision.get("keep"):
            logger.debug("Skipping decision with no 'keep' item")
            continue
        if "id" not in decision.get("keep", {}):
            logger.debug(f"Skipping decision with no ID in keep item: {decision.get('keep')}")
            continue
        if not decision.get("delete"):
            logger.debug("Skipping decision with no items to delete")
            continue
        valid_decisions.append(decision)

    logger.debug(f"Found {len(valid_decisions)} valid decisions out of {len(decisions)} total")
    return valid_decisions


def _detect_language_priority_usage(valid_decisions: list[dict[str, Any]]) -> tuple[bool, bool, list | None]:
    """
    Detect if language prioritization was used and if it changed any decisions.

    Args:
        valid_decisions: List of valid decision objects.

    Returns:
        Tuple of (language_priorities_used, language_priorities_changed_selection, language_priorities_list).
    """
    language_priorities_used = False
    language_priorities_changed_selection = False
    language_priorities_list = None

    for decision in valid_decisions:
        if "keep" in decision and decision["keep"].get("selected_by_language_priority", False):
            language_priorities_used = True
            language_priorities_list = decision["keep"].get("language_priority_list", [])

            # Check if language priority changed the decision
            if decision["keep"].get("changed_by_language_priority", False):
                language_priorities_changed_selection = True
                break
        elif "keep" in decision and "language_priority_list" in decision["keep"] and decision["keep"]["language_priority_list"]:
            language_priorities_list = decision["keep"]["language_priority_list"]

    return language_priorities_used, language_priorities_changed_selection, language_priorities_list


def _ensure_quality_fields(quality_desc: dict[str, Any]) -> None:
    """
    Ensure quality description has proper audio and video fields with defaults.
    Modifies quality_desc in place.

    Args:
        quality_desc: Quality description dictionary to validate and populate.
    """
    if not quality_desc:
        return

    # Ensure video section exists
    if "video" not in quality_desc:
        quality_desc["video"] = {"codec": "unknown", "resolution": "unknown"}

    # Ensure audio section exists
    if "audio" not in quality_desc:
        quality_desc["audio"] = {"codec": "unknown", "channels": "unknown", "languages": ["unknown"]}
    elif "languages" not in quality_desc["audio"]:
        quality_desc["audio"]["languages"] = ["unknown"]
    else:
        languages = quality_desc["audio"].get("languages")
        if languages is None or callable(languages) or not isinstance(languages, (list, tuple, set)):
            quality_desc["audio"]["languages"] = ["unknown"]


def _create_language_priority_message(keep_item: dict[str, Any]) -> str:
    """
    Create a human-readable message about language priority selection.

    Args:
        keep_item: The item being kept.

    Returns:
        Language priority message string (empty if not applicable).
    """
    selected_by_language = keep_item.get('selected_by_language_priority', False)
    changed_by_language = keep_item.get('changed_by_language_priority', False)
    priority_language = keep_item.get('priority_language_used', None)
    language_priority_list = keep_item.get('language_priority_list', [])

    if selected_by_language and priority_language and changed_by_language:
        return f"This file was selected because it has '{priority_language}' audio track, overriding quality-based selection (priority languages: {', '.join(language_priority_list)})"
    elif selected_by_language and priority_language and not changed_by_language:
        return f"This file has '{priority_language}' audio track and also has the best quality (priorities: {', '.join(language_priority_list)})"
    elif language_priority_list:
        return f"Language priorities ({', '.join(language_priority_list)}) were considered but didn't change selection"

    return ""


def _process_delete_item(item: dict[str, Any], base_url: str, keep_serverid: str) -> dict[str, Any]:
    """
    Process a single delete item into template-friendly format.

    Args:
        item: Delete item to process.
        base_url: Emby server base URL.
        keep_serverid: Server ID from the kept item.

    Returns:
        Processed delete item dictionary.
    """
    deletion_status = item.get('deletion_result', {})
    status = deletion_status.get('status', 'not_attempted')

    # "Pending" is the fallback for a dry run (nothing was attempted). Every status the
    # run actually produced gets its own label — a guard refusal in particular must NOT
    # read as "Pending", which looks like "nothing happened yet" when the truth is
    # "deliberately refused to protect the keeper".
    status_class = "status-pending"
    status_text = "Pending"
    if status == "success":
        status_class = "status-success"
        status_text = "Deleted"
    elif status == "failed":
        status_class = "status-error"
        status_text = "Failed"
    elif status == "skipped_unsafe":
        status_class = "status-blocked"
        status_text = "Blocked by safety guard (both files kept)"
    elif status == "fold_safe_removed":
        status_class = "status-success"
        status_text = "Deleted (file-only, fold-safe)"
    elif status == "would_block":
        status_class = "status-blocked"
        status_text = "Would be blocked by safety guard (both files kept)"

    # Create URL for this item
    item_url = f"{base_url}/web/index.html#!/item?id={item['id']}&serverId={keep_serverid}"

    # Ensure audio and video fields are proper
    item_quality_desc = item.get("quality_description", {})
    _ensure_quality_fields(item_quality_desc)

    return {
        "id": item["id"],
        "name": item["name"],
        "image_url": item.get('image_url', f'{base_url}/web/assets/img/media.png'),
        "quality_description": item_quality_desc,
        "url": item_url,
        "status_class": status_class,
        "status_text": status_text,
        "error": deletion_status.get('error'),
        "is_episode": item.get("is_episode", False),
        "series_name": item.get("series_name", ""),
        "season_number": item.get("season_number", ""),
        "episode_number": item.get("episode_number", ""),
        "deletion_result": deletion_status,
        "provider_id": item.get("provider_id", "")
    }


def _item_path(item: dict[str, Any]) -> str:
    """The item's filesystem path for display, preferring the authoritative field.

    ``quality_description`` is built per-item from the details fetch and can come back
    empty (no codec, no size, no date, no path) when Emby returns a sparse record. Reading
    the path only from there then printed "unknown" for an item whose real path was known
    all along — the top-level ``path`` is the field the deletion guard actually uses.

    That mismatch is not cosmetic: on 2026-08-20 a report showing "unknown" was read as
    evidence that deletes had run with the guard bypassed, when the guard had in fact seen
    a valid path and evaluated normally. The report is the audit artifact, so it must show
    the same path the guard reasoned about.
    """
    return item.get("quality_description", {}).get("path") or item.get("path") or "unknown"


def _process_decision_group(decision: dict[str, Any], base_url: str) -> dict[str, Any]:
    """
    Process a single decision group into template-friendly format.

    Args:
        decision: Decision object containing keep and delete items.
        base_url: Emby server base URL.

    Returns:
        Processed group dictionary ready for template rendering.
    """
    keep_item = decision["keep"]
    delete_items = decision["delete"]

    # Create item URLs
    keep_url = f"{base_url}/web/index.html#!/item?id={keep_item['id']}&serverId={keep_item['serverid']}"

    # Find the newest and oldest items based on the date_added field
    all_items = [keep_item] + delete_items

    # Sort by date_added to find newest and oldest
    def date_sort_key(item: dict[str, Any]) -> str:
        return item['quality_description'].get('date_added', '0000-00-00')

    try:
        # Try to sort items by date. Safety net for potential None values or sorting errors.
        sorted_items = sorted(all_items, key=date_sort_key, reverse=True)
        newest_item = sorted_items[0] if sorted_items else keep_item
        oldest_item = sorted_items[-1] if len(sorted_items) > 1 else keep_item
    except (TypeError, ValueError) as e:
        logger.warning(f"Error sorting items by date: {e}")
        newest_item = keep_item
        oldest_item = keep_item

    # Process delete items
    processed_delete_items = []
    has_deleted_items = False

    for item in delete_items:
        processed_item = _process_delete_item(item, base_url, keep_item['serverid'])
        if processed_item["status_class"] == "status-success":  # Emby delete or fold-safe removal
            has_deleted_items = True
        processed_delete_items.append(processed_item)

    # Create language priority message
    language_priority_message = _create_language_priority_message(keep_item)
    # The grouped rows (reports/clusters) show it per file: "kept because it has cs audio".
    keep_item["language_priority_message"] = language_priority_message

    # Ensure keep_item's quality_description has proper audio and video fields
    keep_quality_desc = keep_item.get("quality_description", {})
    _ensure_quality_fields(keep_quality_desc)

    # Extract language priority info
    selected_by_language = keep_item.get('selected_by_language_priority', False)
    changed_by_language = keep_item.get('changed_by_language_priority', False)
    priority_language = keep_item.get('priority_language_used', None)

    return {
        "keep": keep_item,
        "keep_url": keep_url,
        "delete": processed_delete_items,
        "has_deleted_items": has_deleted_items,
        "newest_date_added": newest_item['quality_description'].get('date_added', 'unknown'),
        "newest_path": _item_path(newest_item),
        "newest_status": "✓ This file is being kept" if newest_item['id'] == keep_item['id'] else "⚠️ This newer file is being deleted!",
        "oldest_date_added": oldest_item['quality_description'].get('date_added', 'unknown'),
        "oldest_path": _item_path(oldest_item),
        "oldest_status": "✓ This file is being kept despite being older" if oldest_item['id'] == keep_item['id'] else "",
        "selected_by_language": selected_by_language,
        "changed_by_language_priority": changed_by_language,
        "priority_language": priority_language,
        "language_priority_message": language_priority_message
    }


def _prepare_template_data(base_url: str, stats: dict[str, Any], language_priorities_used: bool,
                           language_priorities_changed_selection: bool, language_priorities_list: Any,
                           metadata: dict[str, Any]) -> dict[str, Any]:
    """Prepare template data structure with stats and metadata."""
    excluded_ids = metadata.get("excluded_ids", [])

    return {
        # Server info
        "base_url": base_url,

        # Stats data
        "total_groups": stats["total_groups"],
        "total_items_to_keep": stats["total_items_to_keep"],
        "total_items_to_delete": stats["total_items_to_delete"],
        "deleted_items": stats["deleted_items"],
        "failed_deletions": stats["failed_deletions"],
        "skipped_deletions": stats["skipped_deletions"],
        "would_block": stats.get("would_block", 0),
        "would_delete": stats.get("would_delete", 0),
        "formatted_size_to_keep": stats["formatted_size_to_keep"],
        "formatted_size_to_delete": stats["formatted_size_to_delete"],
        "formatted_space_saved": stats["formatted_space_saved"],
        "deletion_attempted": stats["deletion_attempted"],
        "percentage_saved": f"{stats['percentage_saved']:.1f}",

        # Language priority info
        "language_priorities_used": language_priorities_used,
        "language_priorities_changed_selection": language_priorities_changed_selection,
        "language_priorities_list": language_priorities_list if isinstance(language_priorities_list, (list, tuple)) else [],
        "language_priorities_configured": list(metadata.get("language_priorities") or []),

        # Exclusion info
        "excluded_ids": excluded_ids,
        "has_excluded_ids": len(excluded_ids) > 0,
        "excluded_titles": metadata.get("excluded_titles", {}),
        "excluded_groups_count": metadata.get("excluded_groups_count", 0),
        "debug_metadata": metadata,

        # Groups data
        "duplicate_groups": []
    }


def _process_decisions_to_groups(valid_decisions: list[dict[str, Any]], base_url: str,
                                  template_data: dict[str, Any]) -> None:
    """Process decisions into template-friendly group format."""
    with tqdm(total=len(valid_decisions), desc="Preparing report data", unit="item") as progress_bar:
        for decision in valid_decisions:
            try:
                group_data = _process_decision_group(decision, base_url)
                template_data["duplicate_groups"].append(group_data)
                progress_bar.update(1)
            except Exception as e:
                logger.error(f"Error formatting decision group: {e}")
                continue


def _inline_group_posters(template_data: dict[str, Any]) -> None:
    """Replace every poster URL in the report with an embedded ``data:`` URI, in place.

    The scan builds image URLs with ``?api_key=<live key>`` so the browser can load
    them. Writing that into the report would put a working credential in a file that
    gets opened and shared, so the posters are fetched here (key sent as a header) and
    embedded instead. On failure the URL is kept but stripped of the key.
    """
    from emby_dedupe.reports.images import inline_images_in_place, share_inlined_images

    # Walks the entire context, not just duplicate_groups: the excluded-media section
    # renders posters too, and enumerating sections by hand let it leak the live key.
    inline_images_in_place(template_data)
    # Each distinct poster is then written once, as a CSS class, instead of once per card.
    template_data["poster_css"] = share_inlined_images(template_data)


def _log_rendering_error_details(template_data: dict[str, Any]) -> None:
    """Log detailed error information when template rendering fails."""
    logger.error("Template data structure:")
    logger.error(f"Number of duplicate groups: {len(template_data.get('duplicate_groups', []))}")

    # Debug the first group structure if available
    if not template_data.get('duplicate_groups'):
        return

    first_group = template_data['duplicate_groups'][0]
    logger.error(f"First group keep item structure: {type(first_group.get('keep'))}")

    if 'keep' not in first_group:
        return

    keep_item = first_group['keep']
    quality_desc = keep_item.get('quality_description', {})
    logger.error(f"Quality description type: {type(quality_desc)}")
    logger.error(f"Quality description keys: {list(quality_desc.keys()) if isinstance(quality_desc, dict) else 'Not a dict'}")

    if isinstance(quality_desc, dict) and 'video' in quality_desc:
        logger.error(f"Video section type: {type(quality_desc['video'])}")
        logger.error(f"Video section content: {quality_desc['video']}")


_CSS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "css", "report.css")


def _read_report_css() -> str:
    """The stylesheet, embedded in the report so the file stands alone.

    It used to be linked as ``report.css`` beside the report in the temp dir, a file every
    run (dedupe and cleanup) overwrote: an old report silently took the newest styles, and
    a moved or attached report lost its styling.
    """
    try:
        with open(_CSS_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        logger.error(f"Could not read report stylesheet {_CSS_PATH}: {e}")
        return ""


def _fmt_size(size_bytes: int) -> str:
    from emby_dedupe.reports.common import format_size
    return format_size(size_bytes)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _empty_verdict(excluded_groups: int) -> tuple[str, str]:
    if not excluded_groups:
        return "No duplicates found.", "Nothing to do."
    return "No duplicates found.", f"{_plural(excluded_groups, 'duplicate group')} skipped by your --exclude-ids."


def _dry_verdict(totals: dict[str, int], space: str) -> tuple[str, str]:
    would, blocked = totals["would_remove"], totals["would_block"]
    if not would and blocked:
        return "Nothing would be removed.", f"The safety guard would block all {_plural(blocked, 'delete')}."
    headline = f"Dry run: {_plural(would, 'file')} would be removed, {space} would be freed."
    if not blocked:
        return headline, ""
    return headline, f"{blocked} more would be blocked by the safety guard."


def _real_verdict(totals: dict[str, int], space: str) -> tuple[str, str]:
    removed, failed = totals["removed"], totals["failed"]
    headline = f"{_plural(removed, 'file')} removed, {space} freed."
    if failed:
        headline = f"{_plural(failed, 'delete')} failed. " + headline
    # The bar's legend already says how many the guard blocked and how many failed.
    return headline, ""


def _stays_on_disk(stats: dict[str, Any], dry: bool) -> int:
    """Bytes still on disk after the run: every keeper plus every copy that was not removed."""
    unremoved = stats["total_size_would_block"] if dry else stats["total_size_to_delete"] - stats["total_size_removed"]
    return int(stats["total_size_to_keep"] + unremoved)


def _copies(count: int) -> str:
    return "1 duplicate copy" if count == 1 else f"{count} duplicate copies"


def _reconcile_line(stats: dict[str, Any], totals: dict[str, int], dry: bool) -> str:
    """One sentence in one vocabulary: copies, groups, and where every copy went."""
    copies = _copies(stats["total_items_to_delete"])
    groups = _plural(stats["total_groups"], "group")
    if dry:
        parts = [f"{totals['would_remove']} would be removed",
                 f"{totals['would_block']} would be blocked by the guard"]
    else:
        parts = [f"{totals['removed']} removed", f"{totals['blocked']} blocked by the guard",
                 f"{totals['failed']} failed"]
    if totals["other"]:
        parts.append(f"{totals['other']} with an unknown status")
    kept = _fmt_size(stats["total_size_to_keep"])
    stays = _fmt_size(_stays_on_disk(stats, dry))
    return (f"{copies} in {groups}: {', '.join(parts)}. "
            f"{kept} of keepers stay on disk ({stays} with the copies that remain); "
            f"{stats['percentage_saved']:.1f}% of the pair space {'would be' if dry else 'was'} freed.")


def _bar_segments(totals: dict[str, int], dry: bool) -> list[dict[str, Any]]:
    """Outcome bar segments; widths come from the counts (``flex-grow``), no script needed."""
    if dry:
        spec = [("would_remove", "Would remove", "seg-would"), ("would_block", "Would be blocked", "seg-blocked")]
    else:
        spec = [("failed", "Failed", "seg-failed"), ("removed", "Removed", "seg-removed"),
                ("blocked", "Blocked by guard", "seg-blocked")]
    if totals["other"]:
        spec.append(("other", "Other status", "seg-other"))
    return [{"key": key, "label": label, "css": css, "count": totals[key]} for key, label, css in spec]


def _legend(segments: list[dict[str, Any]], clusters: dict[str, Any], excluded_groups: int,
            dry: bool) -> list[dict[str, Any]]:
    """The bar's legend doubles as the page navigation: one link per section, with its count."""
    legend = []
    if clusters["check"]:
        legend.append({"href": "#check", "label": "Check first" if dry else "Second look", "count": clusters["check_files"],
                       "css": "seg-check", "emphasis": True})
    legend += [{"href": f"#{s['key']}", "label": s["label"], "count": s["count"], "css": s["css"], "emphasis": False}
               for s in segments]
    legend.append({"href": "#excluded", "label": "Excluded", "count": excluded_groups, "css": "seg-excluded",
                   "emphasis": False})
    return legend


def _decide_block(clusters: dict[str, Any], dry: bool, run: dict[str, Any]) -> list[dict[str, str]]:
    """One copy-paste value per next action: exclude, run for real, read the twin."""
    blocked = clusters["sections"]["blocked"] + clusters["sections"]["would_block"]
    ids = sorted({pid for row in blocked for pid in row["exclude_ids"]})
    actions = []
    if ids:
        actions.append({"label": f"Keep every guarded pair for good ({_plural(len(ids), 'id')})",
                        "command": "--exclude-ids " + ",".join(ids)})
    if dry and clusters["totals"]["would_remove"]:
        argv = run.get("argv") or "the same command"
        actions.append({"label": "Run for real", "command": f"{argv} --doit"})
    return actions


def _verdict(stats: dict[str, Any], clusters: dict[str, Any], dry: bool, space: str,
             excluded_groups: int) -> tuple[str, str, str]:
    """Headline, subline and the reconciling sentence for the run."""
    totals = clusters["totals"]
    if clusters["total_files"] == 0:
        headline, subline = _empty_verdict(excluded_groups)
        return headline, subline, ""
    headline, subline = _dry_verdict(totals, space) if dry else _real_verdict(totals, space)
    return headline, subline, _reconcile_line(stats, totals, dry)


def _warning(dry: bool, clusters: dict[str, Any]) -> str:
    if dry or not clusters["deleted_files"]:
        return ""
    warning = "Files were deleted and cannot be restored."
    if clusters["check"]:
        warning += " Verify the kept copies in the second-look rows on disk now."
    return warning


def _language_line(template_data: dict[str, Any]) -> str:
    lang = template_data.get("language_priorities_list") or template_data.get("language_priorities_configured") or []
    decisions = template_data.get("language_decisions", 0)
    if not (lang and decisions):
        return ""
    return f"Language priority {', '.join(lang)} changed {_plural(decisions, 'decision')}."


def _stats_tiles(stats: dict[str, Any], totals: dict[str, int], dry: bool, space: str) -> list[dict[str, str]]:
    """The statistics row under the legend: counts, sizes and the space saved."""
    tiles = [
        {"label": "Duplicate groups", "value": str(stats["total_groups"])},
        {"label": "Copies found", "value": str(stats["total_items_to_delete"])},
        {"label": "Would remove" if dry else "Removed", "value": str(totals["would_remove" if dry else "removed"])},
        {"label": "Would block" if dry else "Blocked by guard", "value": str(totals["would_block" if dry else "blocked"])},
    ]
    if not dry:
        tiles.append({"label": "Failed", "value": str(totals["failed"])})
    tiles += [
        {"label": "Would be freed" if dry else "Freed", "value": space},
        {"label": "Keepers on disk", "value": stats["formatted_size_to_keep"]},
        {"label": "Space saved", "value": f"{stats['percentage_saved']:.1f}%"},
    ]
    return tiles


def _is_dry_run(metadata: dict[str, Any], stats: dict[str, Any]) -> bool:
    """True for a dry run.

    The CLI's ``--doit`` flag (``metadata["run"]["doit"]``) decides when it is known. A real
    run with nothing to delete has no deletion results, and guessing from them labelled it
    "Dry run". Reports built without the flag still guess from the deletion results.
    """
    doit = (metadata.get("run") or {}).get("doit")
    if doit is None:
        return not stats["deletion_attempted"]
    return not doit


def _build_run_summary(stats: dict[str, Any], clusters: dict[str, Any], template_data: dict[str, Any],
                       dry: bool) -> dict[str, Any]:
    """The one-sentence verdict, the outcome bar and the statistics at the top of the report.

    Counts are files (delete items), the same unit the sections use. A dry run is described
    as what --doit would do, including the deletes the safety guard will refuse.
    """
    totals = clusters["totals"]
    space = _fmt_size(stats["space_saved"])
    excluded_groups = template_data.get("excluded_groups_count", 0)
    headline, subline, reconcile = _verdict(stats, clusters, dry, space, excluded_groups)
    segments = _bar_segments(totals, dry)
    return {
        "sizes_text": {name: _fmt_size(size) for name, size in clusters["sizes"].items()},
        "stats_tiles": _stats_tiles(stats, totals, dry, space),
        "dry": dry, "headline": headline, "subline": subline, "reconcile": reconcile,
        "warning": _warning(dry, clusters),
        "risky": len(clusters["check"]), "risky_files": clusters["check_files"], "segments": segments,
        "aria": ", ".join(f"{s['count']} {s['label'].lower()}" for s in segments),
        "has_files": clusters["total_files"] > 0,
        "stays": _fmt_size(_stays_on_disk(stats, dry)),
        "legend": _legend(segments, clusters, excluded_groups, dry),
        "decide": _decide_block(clusters, dry, template_data.get("run") or {}),
        "language_line": _language_line(template_data),
        "excluded_groups": excluded_groups,
    }


def _run_metadata(metadata: dict[str, Any], dry: bool) -> dict[str, Any]:
    """Provenance for the header and footer; the CLI fills what it knows, the rest is derived."""
    from datetime import UTC, datetime

    from emby_dedupe import __version__

    run = dict(metadata.get("run") or {})
    now = datetime.now(UTC).replace(microsecond=0)
    run.setdefault("generated_at", now.isoformat())
    run.setdefault("run_id", str(int(now.timestamp())))
    run.setdefault("version", __version__)
    run.setdefault("host", "")
    run.setdefault("argv", "")
    run.setdefault("libraries", [])
    run["dry"] = dry
    run["generated_human"] = _human_time(run["generated_at"])
    run["mode"] = "Dry run · nothing deleted" if dry else "Real run (--doit)"
    run["mode_short"] = "Dry run" if dry else "Real run"
    return run


def _human_time(iso: str) -> str:
    """"11 Oct 2026 16:49" from an RFC 3339 stamp; the stamp itself when it does not parse."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).strftime("%-d %b %Y %H:%M")
    except ValueError:
        return iso


def _json_payload(template_data: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """The report's facts without markup or posters, for scripted reviews."""
    side_keys = ("id", "path", "short", "quality", "hdr", "audio", "langs", "runtime", "seconds", "size", "added",
                 "score", "score_summary")
    file_keys = ("episode", "status", "reason", "reason_code", "flags", "exclude_ids", "folders_differ", "size")
    row_keys = ("id", "name", "sub", "outcome", "reason_code", "flags", "risk", "count", "size", "exclude_ids",
                "blocked_runs", "keep_quality", "delete_quality", "langs_text", "score_text")
    clusters = template_data["clusters"]
    sections = []
    for name in clusters["sections"]:
        rows = []
        for row in clusters["sections"][name]:
            files = [{**{k: f[k] for k in file_keys},
                      "keep": {k: f["keep"][k] for k in side_keys},
                      "delete": {k: f["delete"][k] for k in side_keys}} for f in row["files"]]
            rows.append({**{k: row[k] for k in row_keys}, "files": files})
        sections.append({"outcome": name, "files": clusters["totals"][name], "bytes": clusters["sizes"][name], "rows": rows})
    return {
        "run": template_data["run"],
        "verdict": {"headline": template_data["run_summary"]["headline"], "subline": template_data["run_summary"]["subline"]},
        "stats": {k: stats[k] for k in ("total_groups", "total_items_to_delete", "deleted_items", "failed_deletions",
                                        "would_block", "total_size_to_keep", "total_size_to_delete",
                                        "total_size_removed", "total_size_would_block", "space_saved")},
        "sections": sections,
        "excluded": {"ids": template_data.get("excluded_ids", []),
                     "groups": [{"id": pid, "title": t.get("title", ""), "year": t.get("year", "")}
                                for pid, t in (template_data.get("excluded_titles") or {}).items()]},
    }


def _attach_json_twin(template_data: dict[str, Any], stats: dict[str, Any]) -> None:
    """Serialise the facts once: the page carries them in a data island and a .json twin."""
    import hashlib
    import json

    payload = json.dumps(_json_payload(template_data, stats), indent=1, sort_keys=True, ensure_ascii=False)
    template_data["report_json"] = payload
    # Inside <script> only "</" can end the island; "<\/" is the same JSON string.
    template_data["report_json_island"] = payload.replace("</", "<\\/")
    template_data["report_sha"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_report_context(base_url: str, decisions: list[dict[str, Any]],
                         metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the template needs, including the JSON twin (``report_json``).

    Args:
        base_url: The base URL of the Emby server.
        decisions: Decision objects containing items to keep and delete.
        metadata: Excluded ids, language priorities, run provenance (``run``) and the
            guard history (``history``, see reports/history).
    """
    stats = calculate_report_statistics(decisions)
    valid_decisions = _validate_decisions(decisions)
    language_priorities_used, language_priorities_changed_selection, language_priorities_list = _detect_language_priority_usage(valid_decisions)
    metadata = metadata or {}

    template_data = _prepare_template_data(
        base_url, stats, language_priorities_used,
        language_priorities_changed_selection, language_priorities_list, metadata
    )
    _process_decisions_to_groups(valid_decisions, base_url, template_data)
    template_data["language_decisions"] = sum(
        1 for d in valid_decisions if d["keep"].get("changed_by_language_priority"))

    # Embed the posters and drop the API key: the scan builds image URLs carrying
    # ?api_key=..., which would write a live credential into this file.
    _inline_group_posters(template_data)

    # Rows per title and outcome, after the posters: a row shows its keeper's poster class.
    from emby_dedupe.reports.clusters import build_clusters
    template_data["clusters"] = build_clusters(valid_decisions, base_url, metadata.get("history"))
    dry = _is_dry_run(metadata, stats)
    template_data["run"] = _run_metadata(metadata, dry=dry)
    template_data["run_summary"] = _build_run_summary(stats, template_data["clusters"], template_data, dry)
    template_data["report_css"] = _read_report_css()
    _attach_json_twin(template_data, stats)
    return template_data


def render_report(template_data: dict[str, Any]) -> str:
    """Render ``templates/report.html`` with a prepared context."""
    # jinja2 is a hard runtime dependency (pyproject) — import it plainly.
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=select_autoescape(['html', 'xml']))
    template = env.get_template('report.html')
    try:
        return template.render(**template_data)
    except Exception as e:
        logger.error(f"Template rendering error: {e}")
        _log_rendering_error_details(template_data)
        raise


def format_html_report(base_url: str, decisions: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str:
    """
    Formats decisions into a beautiful HTML report using Jinja2 templates.

    Args:
        base_url (str): The base URL of the Emby server.
        decisions (list): List of decision objects containing items to keep and delete.
        metadata (dict, optional): Additional metadata such as excluded IDs and language priorities.

    Returns:
        str: A fully formed HTML document as a string.
    """
    return render_report(build_report_context(base_url, decisions, metadata))


def generate_html_report(base_url: str, decisions: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str:
    """
    Generates an HTML report and saves it to a temporary file that can be opened in a browser.

    The facts the page shows are also written as a ``.json`` twin with the same stem
    (its SHA-256 is printed in the page footer), and the guard's refusals are counted
    into the dedupe history ("blocked in N runs").

    Args:
        base_url (str): The base URL of the Emby server.
        decisions (list): List of decision objects containing items to keep and delete.
        metadata (dict, optional): Additional metadata such as excluded IDs and language priorities.

    Returns:
        str: Path to the generated HTML file.
    """
    from emby_dedupe.reports.history import record_run

    metadata = dict(metadata or {})
    metadata.setdefault("history", record_run(decisions))

    # Reserve the twin's name first so the page can print it: the HTML takes the same stem.
    temp_dir = tempfile.gettempdir()
    fd, json_path = tempfile.mkstemp(prefix=f"emby_dedupe_report_{int(time.time())}_", suffix=".json", dir=temp_dir)
    os.close(fd)
    run = dict(metadata.get("run") or {})
    run.setdefault("json_name", os.path.basename(json_path))
    metadata["run"] = run

    context = build_report_context(base_url, decisions, metadata)
    html_content = render_report(context)

    temp_path = json_path[:-len(".json")] + ".html"
    with open(temp_path, "x", encoding="utf-8") as fh:
        fh.write(html_content)
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(context["report_json"])

    logger.info(f"HTML report generated at: {temp_path} (facts: {json_path})")
    return temp_path
