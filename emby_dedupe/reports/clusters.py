"""Fold a dedupe run's decisions into report rows: one row per title and outcome.

A large run is hundreds of near-identical groups (149 Smallville episodes upgraded the
same way). The report summarises them as rows such as "Smallville · S03 · 22 episodes ·
480p HDTV → 1080p WEB-DL", while keeping every file's evidence inside the row.

Rules that keep the summary from hiding a wrong deletion (design review, 2026-10-11):

- **Per file, not per group.** Each delete item is classified on its own; a group with one
  deleted and one blocked file contributes to both sections.
- **Never merged across** season folders, guard reasons or risk flags. A risky deletion
  (kept copy scores lower, deleted copy has a language the kept one lacks, a ``(n)`` file
  name, episodes in different folders) gets its own row, sorted first.
- **Counts are files.** Section totals reconcile with ``calculate_report_statistics``.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import Any

from emby_dedupe.api.deletion_guard import folder_season
from emby_dedupe.reports.common import DELETED_STATUSES
from emby_dedupe.utils.constants import LANGUAGE_NORMALIZATION_MAP

# Report section per deletion status. Anything unknown goes to "other" so no file is dropped.
OUTCOME_BY_STATUS = {
    "success": "removed",
    "fold_safe_removed": "removed",
    "failed": "failed",
    "skipped_unsafe": "blocked",
    "would_block": "would_block",
    "not_attempted": "would_remove",
}
SECTION_ORDER = ("failed", "removed", "would_remove", "blocked", "would_block", "other")

# Outcomes where a file is (or would be) deleted: risk flags matter only for these.
DELETING_OUTCOMES = frozenset({"removed", "would_remove", "failed"})

STATUS_TEXT = {
    "success": "Deleted",
    "fold_safe_removed": "Deleted (file only, over SSH)",
    "failed": "Failed",
    "skipped_unsafe": "Blocked by safety guard (both kept)",
    "would_block": "Would be blocked by safety guard",
    "not_attempted": "Would be deleted",
}

RISK_TEXT = {
    "score_inversion": "kept copy scores lower",
    "language_loss": "deleted copy has a language the kept one lacks",
    "dup_suffix": "'(n)' file name",
    "cross_folder": "episodes in different folders",
}

_UNKNOWN_LANGS = frozenset({"", "und", "unknown"})
_DUP_SUFFIX = re.compile(r" \(\d+\)\.[A-Za-z0-9]+$")
_TICKS_PER_SECOND = 10_000_000
_GB = 1024**3

# Source tier from the scoring multiplier, same thresholds as the report's badges.
_SOURCE_TIERS = ((1.3, "Remux"), (1.15, "BluRay"), (1.0, "WEB-DL"), (0.9, "HDTV"))


def _source_tier(mult: Any) -> str:
    if not isinstance(mult, (int, float)):
        return ""
    return next((label for floor, label in _SOURCE_TIERS if mult >= floor), "")


def _quality_label(item: dict[str, Any]) -> str:
    qd = item.get("quality_description") or {}
    res = ((qd.get("video") or {}).get("resolution") or "").strip()
    src = _source_tier(qd.get("source_quality_multiplier"))
    return " ".join(x for x in (res, src) if x) or "unknown quality"


def _languages(item: dict[str, Any]) -> list[str]:
    langs = ((item.get("quality_description") or {}).get("audio") or {}).get("languages") or []
    out: list[str] = []
    for lang in langs:
        code = str(lang).lower()
        if code in _UNKNOWN_LANGS:
            continue
        code = LANGUAGE_NORMALIZATION_MAP.get(code, code)
        if code not in out:
            out.append(code)
    return out


def _seconds(ticks: Any) -> int | None:
    if not isinstance(ticks, (int, float)) or ticks <= 0:
        return None
    return int(ticks / _TICKS_PER_SECOND)


def _runtime(ticks: Any) -> str:
    seconds = _seconds(ticks)
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _size(item: dict[str, Any]) -> int:
    try:
        return int((item.get("quality_description") or {}).get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _rating(item: dict[str, Any]) -> float | None:
    value = item.get("rating")
    return float(value) if isinstance(value, (int, float)) else None


def _reason_code(error: str | None) -> str:
    text = error or ""
    if "duration-mismatch" in text:
        return "runtime"
    if "season folder" in text:
        return "season_folder"
    return "guard" if text else ""


def _risk_flags(keep: dict[str, Any], item: dict[str, Any]) -> list[str]:
    flags = []
    keep_rating, item_rating = _rating(keep), _rating(item)
    if keep_rating is not None and item_rating is not None and keep_rating < item_rating:
        flags.append("score_inversion")
    if set(_languages(item)) - set(_languages(keep)):
        flags.append("language_loss")
    if any(_DUP_SUFFIX.search(p or "") for p in (keep.get("path"), item.get("path"))):
        flags.append("dup_suffix")
    if keep.get("is_episode") and os.path.dirname(keep.get("path") or "") != os.path.dirname(item.get("path") or ""):
        flags.append("cross_folder")
    return flags


def _shared_provider_ids(keep: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """Provider ids both copies carry: what --exclude-ids needs to stop pairing them."""
    def ids(d: dict[str, Any]) -> set[str]:
        return {str(v).lower() for v in (d.get("provider_ids") or {}).values() if v}
    return sorted(ids(keep) & ids(item))


def _episode_label(item: dict[str, Any]) -> str:
    season, episode = item.get("season_number"), item.get("episode_number")
    if isinstance(season, int) and isinstance(episode, int):
        return f"S{season:02d}E{episode:02d}"
    return ""


def _emby_url(base_url: str, item: dict[str, Any], server_id: str) -> str:
    return f"{base_url}/web/index.html#!/item?id={item.get('id', '')}&serverId={server_id}"


def _file_record(keep: dict[str, Any], item: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Everything the report needs to judge one deletion, without opening Emby."""
    result = item.get("deletion_result") or {}
    status = result.get("status") or "unknown"
    server_id = keep.get("serverid") or ""
    return {
        "status": status,
        "status_text": STATUS_TEXT.get(status, status),
        "outcome": OUTCOME_BY_STATUS.get(status, "other"),
        "file_only": status == "fold_safe_removed",
        "reason": result.get("error") or "",
        "reason_code": _reason_code(result.get("error")),
        "episode": _episode_label(keep),
        "title": keep.get("name", ""),
        "keep_path": keep.get("path") or "",
        "delete_path": item.get("path") or "",
        "keep_quality": _quality_label(keep),
        "delete_quality": _quality_label(item),
        "keep_langs": " ".join(_languages(keep)) or "untagged",
        "delete_langs": " ".join(_languages(item)) or "untagged",
        "keep_score": _rating(keep),
        "delete_score": _rating(item),
        "keep_runtime": _runtime(keep.get("runtime_ticks")),
        "delete_runtime": _runtime(item.get("runtime_ticks")),
        "keep_seconds": _seconds(keep.get("runtime_ticks")),
        "delete_seconds": _seconds(item.get("runtime_ticks")),
        "keep_note": keep.get("language_priority_message") or "",
        "exclude_ids": _shared_provider_ids(keep, item),
        "size": _size(item),
        "keep_url": _emby_url(base_url, keep, server_id),
        "delete_url": _emby_url(base_url, item, server_id),
        "flags": _risk_flags(keep, item),
    }


def _cluster_key(keep: dict[str, Any], rec: dict[str, Any]) -> tuple[Any, ...]:
    if keep.get("is_episode"):
        season = folder_season(keep.get("path"))
        title_key: tuple[Any, ...] = ("series", keep.get("series_name", ""), season)
    else:
        title_key = ("movie", keep.get("id", ""))
    return (rec["outcome"], rec["reason_code"], title_key, tuple(rec["flags"]))


def _summarise(values: list[str]) -> str:
    distinct = list(dict.fromkeys(values))
    return distinct[0] if len(distinct) == 1 else f"{distinct[0]} +{len(distinct) - 1} more (mixed)"


def _new_row(keep: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    is_episode = bool(keep.get("is_episode"))
    return {
        "outcome": rec["outcome"],
        "name": (keep.get("series_name") or keep.get("name", "")) if is_episode else keep.get("name", ""),
        "is_episode": is_episode,
        "season_folder": folder_season(keep.get("path")) if is_episode else None,
        "reason_code": rec["reason_code"],
        "flags": rec["flags"],
        "risk": rec["outcome"] in DELETING_OUTCOMES and bool(rec["flags"]),
        "image_class": keep.get("image_class", ""),
        "image_url": keep.get("image_url", ""),
        "files": [],
    }


def _finish_row(row: dict[str, Any]) -> dict[str, Any]:
    files = row["files"]
    episodes = sorted({f["episode"] for f in files if f["episode"]})
    if row["is_episode"]:
        season = row["season_folder"]
        where = f"S{season:02d}" if isinstance(season, int) else "no season folder"
        row["sub"] = f"{where} · {episodes[0]}" if len(files) == 1 and episodes else f"{where} · {len(files)} files"
    else:
        row["sub"] = "Movie" if len(files) == 1 else f"Movie · {len(files)} files"
    row["count"] = len(files)
    row["size"] = sum(f["size"] for f in files)
    row["size_gb"] = f"{row['size'] / _GB:.2f} GB"
    row["file_only"] = sum(1 for f in files if f["file_only"])
    row["keep_quality"] = _summarise([f["keep_quality"] for f in files])
    row["delete_quality"] = _summarise([f["delete_quality"] for f in files])
    row["keep_langs"] = _summarise([f["keep_langs"] for f in files])
    row["delete_langs"] = _summarise([f["delete_langs"] for f in files])
    row["risk_text"] = [RISK_TEXT[f] for f in row["flags"] if f in RISK_TEXT]
    reasons = list(dict.fromkeys(f["reason"] for f in files if f["reason"]))
    row["reason"] = _one_reason(reasons)
    row["reason_text"] = _reason_text(row["reason_code"], files, row["reason"])
    row["exclude_ids"] = ",".join(sorted({pid for f in files for pid in f["exclude_ids"]}))
    return row


def _one_reason(reasons: list[str]) -> str:
    if not reasons:
        return ""
    if len(reasons) == 1:
        return reasons[0]
    return f"{len(reasons)} different reasons"


def _minute_span(values: list[int]) -> str:
    minutes = sorted({round(v / 60) for v in values})
    return f"{minutes[0]}" if minutes[-1] - minutes[0] <= 1 else f"{minutes[0]}–{minutes[-1]}"


def _reason_text(code: str, files: list[dict[str, Any]], reason: str) -> str:
    """Short chip text for a guarded row, e.g. "Runtime differs · 52 vs 58 min"."""
    if code == "runtime":
        pairs = [(f["keep_seconds"], f["delete_seconds"]) for f in files
                 if f["keep_seconds"] and f["delete_seconds"]]
        if pairs:
            return (f"Runtime differs · {_minute_span([k for k, _ in pairs])} vs "
                    f"{_minute_span([d for _, d in pairs])} min")
        return "Runtime differs"
    if code == "season_folder":
        return "Different season folders"
    return reason[:80] if reason else ""


def build_clusters(decisions: list[dict[str, Any]], base_url: str) -> dict[str, Any]:
    """Group every delete item of ``decisions`` into report rows by outcome.

    Returns ``{"sections": {outcome: [row, ...]}, "totals": {outcome: files},
    "total_files": n, "deleted_files": n}``. Rows carry their files' full evidence.
    """
    rows: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
    for decision in decisions:
        keep = decision.get("keep") or {}
        for item in decision.get("delete") or []:
            rec = _file_record(keep, item, base_url)
            key = _cluster_key(keep, rec)
            row = rows.get(key)
            if row is None:
                row = rows[key] = _new_row(keep, rec)
            row["files"].append(rec)

    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTION_ORDER}
    for row in rows.values():
        sections[row["outcome"]].append(_finish_row(row))
    for name in sections:
        sections[name].sort(key=lambda r: (not r["risk"], -r["count"], r["name"]))

    totals = {name: sum(r["count"] for r in rs) for name, rs in sections.items()}
    deleted = sum(1 for rs in sections.values() for r in rs for f in r["files"]
                  if f["status"] in DELETED_STATUSES)
    return {"sections": sections, "totals": totals,
            "total_files": sum(totals.values()), "deleted_files": deleted}
