"""Fold a dedupe run's decisions into report rows: one row per title and outcome.

A large run is hundreds of near-identical groups (149 Smallville episodes upgraded the
same way). The report summarises them as rows such as "Smallville · S03 · 22 files ·
480p HDTV → 1080p WEB-DL", while keeping every file's evidence inside the row.

Rules that keep the summary from hiding a wrong deletion (design reviews, 2026-10-11):

- **Per file, not per group.** Each delete item is classified on its own; a group with one
  deleted and one blocked file contributes to both sections.
- **Never merged across** season folders, guard reasons or risk flags. A risky deletion
  (kept copy scores lower, deleted copy has a language or HDR the kept one lacks, a
  ``(n)`` file name, episodes in different folders) gets its own row, sorted first.
- **Flags show on every outcome.** On a deletion they are "Check"; on a guard refusal they
  are "Note" (the keep-both evidence the reader is asked about).
- **Summaries list every distinct value** ("1080i HDTV ×7 · 480p HDTV ×1"), never
  "+N more (mixed)": that hid 480p copies named "1080p HDTV.mkv".
- **Counts are files.** Section totals reconcile with ``calculate_report_statistics``.
"""
from __future__ import annotations

import os
import re
from collections import Counter, OrderedDict
from statistics import median
from typing import Any

from emby_dedupe.api.deletion_guard import folder_season
from emby_dedupe.reports.common import DELETED_STATUSES, format_size
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
# "other" first: an unknown status is a bug signal, not a footnote.
SECTION_ORDER = ("other", "failed", "removed", "would_remove", "blocked", "would_block")

# Outcomes where a file is (or would be) deleted: flags are risks there, notes elsewhere.
DELETING_OUTCOMES = frozenset({"removed", "would_remove", "failed"})
BLOCKED_STATUSES = frozenset({"skipped_unsafe", "would_block"})

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
    "hdr_loss": "deleted copy has HDR the kept one lacks",
    "dup_suffix": "'(n)' file name",
    "cross_folder": "episodes in different folders",
    "name_lies": "file name claims a resolution the video does not have",
}
# The same facts worded for a pair that stays on disk.
NOTE_TEXT = {
    "score_inversion": "kept copy scores lower",
    "language_loss": "other copy has a language the kept one lacks",
    "hdr_loss": "other copy has HDR the kept one lacks",
    "dup_suffix": "'(n)' file name",
    "cross_folder": "episodes in different folders",
    "name_lies": "file name claims a resolution the video does not have",
}

# Short chip wording; the full sentence (RISK_TEXT / NOTE_TEXT) is the chip's tooltip.
CHIP_TEXT = {
    "score_inversion": "scores lower",
    "language_loss": "loses {langs}",
    "hdr_loss": "loses HDR",
    "dup_suffix": "'(n)' name",
    "cross_folder": "different folders",
    "name_lies": "name lies",
}

_UNKNOWN_LANGS = frozenset({"", "und", "unknown"})
# Tags that are not languages but turn up in the audio track list.
_LANG_DISPLAY = {"org": "orig"}
_DUP_SUFFIX = re.compile(r" \(\d+\)\.[A-Za-z0-9]+$")
_RES_TOKEN = re.compile(r"\b(\d{3,4})[pi]\b|\b(4k)\b", re.IGNORECASE)
_TICKS_PER_SECOND = 10_000_000
_GB = 1024**3

# Source tier from the scoring multiplier, same thresholds as the report's badges.
_SOURCE_TIERS = ((1.3, "Remux"), (1.15, "BluRay"), (1.0, "WEB-DL"), (0.9, "HDTV"))
_CHANNEL_LAYOUTS = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}


def _source_tier(mult: Any) -> str:
    if not isinstance(mult, (int, float)):
        return ""
    return next((label for floor, label in _SOURCE_TIERS if mult >= floor), "")


def _video(item: dict[str, Any]) -> dict[str, Any]:
    return (item.get("quality_description") or {}).get("video") or {}


def _hdr_tag(item: dict[str, Any]) -> str:
    """"DV", "DV P5", "HDR10+", "HDR" or "" from the stream fields Emby reports."""
    video = _video(item)
    profile = str(video.get("dv_profile") or "").lower()
    video_range = str(video.get("video_range") or "").replace(" ", "").lower()
    resolution = str(video.get("resolution") or "").lower()
    if "dovi" in profile or "dolby" in video_range or "dolby vision" in resolution:
        return "DV P5" if video.get("is_dovi_p5") else "DV"
    if video_range.startswith("hdr10+") or video_range.startswith("hdr10plus"):
        return "HDR10+"
    if video_range.startswith("hdr") or video_range == "hlg":
        return video_range.upper()
    return ""


def _hdr_rank(item: dict[str, Any]) -> int:
    tag = _hdr_tag(item)
    if tag.startswith("DV"):
        return 2
    return 1 if tag else 0


def _quality_label(item: dict[str, Any]) -> str:
    """Resolution and codec as Emby names them, plus HDR/DV and the source tier."""
    res = str(_video(item).get("resolution") or "").strip()
    hdr = _hdr_tag(item)
    if hdr and ("dolby vision" in res.lower() or hdr.lower() in res.lower()):
        hdr = ""
    src = _source_tier((item.get("quality_description") or {}).get("source_quality_multiplier"))
    return " ".join(x for x in (res, hdr, src) if x) or "unknown quality"


def _languages(item: dict[str, Any]) -> list[str]:
    langs = ((item.get("quality_description") or {}).get("audio") or {}).get("languages") or []
    out: list[str] = []
    for lang in langs:
        code = str(lang).lower()
        if code in _UNKNOWN_LANGS:
            continue
        code = _LANG_DISPLAY.get(code, LANGUAGE_NORMALIZATION_MAP.get(code, code))
        if code not in out:
            out.append(code)
    return out


def _audio_text(item: dict[str, Any]) -> str:
    """"ac3 5.1" — codec and channel layout, the part the language list leaves out."""
    audio = (item.get("quality_description") or {}).get("audio") or {}
    codec = str(audio.get("codec") or "").lower()
    channels = audio.get("channels")
    layout = _CHANNEL_LAYOUTS.get(channels, str(channels)) if isinstance(channels, int) else ""
    text = " ".join(x for x in (codec, layout) if x and x != "unknown")
    return text or "unknown"


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


def _claimed_resolution(path: str) -> int | None:
    """The height a file name advertises ("… - 1080p HDTV.mkv" → 1080), if any."""
    match = _RES_TOKEN.search(os.path.basename(path or ""))
    if not match:
        return None
    return 2160 if match.group(2) else int(match.group(1))


def _actual_resolution(item: dict[str, Any]) -> int | None:
    match = _RES_TOKEN.search(str(_video(item).get("resolution") or ""))
    if not match:
        return None
    return 2160 if match.group(2) else int(match.group(1))


def _name_lies(item: dict[str, Any]) -> bool:
    """A "1080p HDTV.mkv" that is really 480p: the file name misled the last review."""
    claimed, actual = _claimed_resolution(item.get("path") or ""), _actual_resolution(item)
    return claimed is not None and actual is not None and claimed > actual


def _same_runtime(keep: dict[str, Any], item: dict[str, Any]) -> bool:
    """Both runtimes known and within 5 s or 2% of each other."""
    a, b = _seconds(keep.get("runtime_ticks")), _seconds(item.get("runtime_ticks"))
    if a is None or b is None:
        return False
    return abs(a - b) <= max(5, 0.02 * max(a, b))


def _risk_flags(keep: dict[str, Any], item: dict[str, Any]) -> list[str]:
    flags = []
    keep_rating, item_rating = _rating(keep), _rating(item)
    if keep_rating is not None and item_rating is not None and keep_rating < item_rating:
        flags.append("score_inversion")
    if set(_languages(item)) - set(_languages(keep)):
        flags.append("language_loss")
    if _hdr_rank(item) > _hdr_rank(keep):
        flags.append("hdr_loss")
    if any(_DUP_SUFFIX.search(p or "") for p in (keep.get("path"), item.get("path"))) and not _same_runtime(keep, item):
        # A "(2)" name hid a different episode once (Initial D). Two copies with the same
        # runtime are the same episode; the name alone is then no reason to doubt.
        flags.append("dup_suffix")
    if keep.get("is_episode") and os.path.dirname(keep.get("path") or "") != os.path.dirname(item.get("path") or ""):
        flags.append("cross_folder")
    if _name_lies(keep) or _name_lies(item):
        flags.append("name_lies")
    return flags


def _shared_provider_ids(keep: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """Provider ids both copies carry, the IMDb ones first.

    ``--exclude-ids`` matches a bare number against TMDB *and* TVDB, so a numeric id can
    silently exclude an unrelated film. The hint therefore offers the IMDb id alone when
    there is one; the numbers are a fallback for titles without IMDb ids.
    """
    def ids(d: dict[str, Any]) -> set[str]:
        return {str(v).lower() for v in (d.get("provider_ids") or {}).values() if v}
    shared = sorted(ids(keep) & ids(item))
    imdb = [i for i in shared if i.startswith("tt")]
    return imdb or shared


def _episode_label(item: dict[str, Any]) -> str:
    season, episode = item.get("season_number"), item.get("episode_number")
    if isinstance(season, int) and isinstance(episode, int):
        return f"S{season:02d}E{episode:02d}"
    return ""


def _emby_url(base_url: str, item: dict[str, Any], server_id: str) -> str:
    return f"{base_url}/web/index.html#!/item?id={item.get('id', '')}&serverId={server_id}"


_EPISODE_CODE = re.compile(r"S\d{1,2}E\d{1,3}", re.IGNORECASE)


def _short_name(base: str, is_episode: bool) -> str:
    """The part of an episode's file name after "Show S12E01 -": what differs between copies."""
    if is_episode:
        match = _EPISODE_CODE.search(base)
        if match:
            rest = base[match.end():].lstrip(" -–_.")
            if rest and "." in rest and rest.rsplit(".", 1)[0]:
                return rest
    return base


# Facts that are usually the same for every file in a row: said once, above the table,
# unless they vary, in which case the odd file carries its own value.
_SHARED_FIELDS = (("quality", "video"), ("audio_langs", "audio"), ("added", "added"))
# Each fact kind has one colour in the report (templates/report.html, .fact-*).
_FACT_KIND = {"video": "quality", "audio": "audio", "added": "date"}


def _fact_line(label: str, parts: list[tuple[str, str]]) -> dict[str, Any]:
    """One labelled line of the shared block, each part tagged with its fact kind."""
    parts = [(kind, text) for kind, text in parts if text]
    return {"label": label, "text": " · ".join(text for _, text in parts),
            "parts": [{"kind": kind, "text": text} for kind, text in parts]}


def _side_constants(files: list[dict[str, Any]], role: str) -> list[tuple[str, str]]:
    """Facts shared by every file on one side; a file that differs gets them in ``vary``."""
    constant: list[tuple[str, str]] = []
    for field, name in _SHARED_FIELDS:
        counts = Counter(f[role][field] for f in files)
        common, n = counts.most_common(1)[0]
        # The majority value is the row's fact; a file that differs carries its own.
        if common:
            constant.append((_FACT_KIND[name], f"added {common}" if field == "added" else common))
        if n == len(files):
            continue
        for f in files:
            if f[role][field] != common:
                f[role]["vary"][name] = f[role][field] or "none"
    return constant


def _shared_facts(files: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, str]]:
    """Fold constant facts out of the files; mark the files whose value differs."""
    dirs = {role: {f[role]["dir"] for f in files} for role in ("keep", "delete")}
    one_folder = len(dirs["keep"]) == 1 and dirs["keep"] == dirs["delete"]
    lines: list[dict[str, Any]] = []
    if one_folder:
        lines.append(_fact_line("Folder", [("path", next(iter(dirs["keep"])) or "/")]))
    other_label = "Removed" if row["outcome"] in DELETING_OUTCOMES else "Other"
    for role, label in (("keep", "Kept"), ("delete", other_label)):
        constant = _side_constants(files, role)
        if not one_folder and len(dirs[role]) == 1:
            constant.append(("path", f"in {next(iter(dirs[role])) or '/'}"))
        if constant:
            lines.append(_fact_line(label, constant))
    # The pill and the section already name the status; only the guard's reason is new here.
    guard = _guard_line(row, files)
    if guard:
        label = "Error" if row["outcome"] in DELETING_OUTCOMES else "Guard"
        lines.append(_fact_line(label, [("reason", guard)]))
    return lines


def _guard_line(row: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """Why the guard refused the row (or why deletes failed), once."""
    reasons = {f["reason"] for f in files if f["reason"]}
    if not reasons:
        return ""
    if row["reason_code"] == "runtime":
        return f"{_reason_text('runtime', files, '')}, beyond the guard's tolerance"
    if row["reason_code"] == "season_folder":
        return "the two copies sit in different season folders"
    if len(reasons) == 1:
        reason = next(iter(reasons))
        if "co-located" in reason:
            return "deleting either copy would make Emby delete the whole folder, including the kept copy"
        return reason
    return f"{len(reasons)} different reasons, one per file below"


def _side(item: dict[str, Any], base_url: str, server_id: str) -> dict[str, Any]:
    """One copy's evidence: everything the old per-group card showed, as plain values."""
    qd = item.get("quality_description") or {}
    path = item.get("path") or qd.get("path") or ""
    langs = _languages(item)
    score = _rating(item)
    return {
        "id": str(item.get("id") or ""),
        "path": path,
        "dir": os.path.dirname(path),
        "base": os.path.basename(path),
        "quality": _quality_label(item),
        "hdr": _hdr_tag(item) or "SDR",
        "audio": _audio_text(item),
        "langs_list": langs,
        "langs": " ".join(langs) or "untagged",
        "audio_langs": f"{_audio_text(item)} {' '.join(langs) or 'untagged'}",
        "short": _short_name(os.path.basename(path), bool(item.get("is_episode"))),
        "runtime": _runtime(item.get("runtime_ticks")),
        "seconds": _seconds(item.get("runtime_ticks")),
        "size": _size(item),
        "size_text": format_size(_size(item)),
        "added": str(qd.get("date_added") or "")[:10],
        "score": score,
        "score_text": f"{score:.1f}" if score is not None else "",
        "score_summary": str(qd.get("score_summary") or ""),
        "dovi_p5": bool(_video(item).get("is_dovi_p5")),
        "ai_upscale": bool(qd.get("is_ai_upscale")),
        "image_class": item.get("image_class", ""),
        "url": _emby_url(base_url, item, server_id),
        "vary": {},
    }


def _why_kept(keep: dict[str, Any]) -> str:
    """One short clause on why this copy won, only when language priority had a say."""
    lang = keep.get("priority_language_used")
    if not keep.get("selected_by_language_priority") or not lang:
        return ""
    if keep.get("changed_by_language_priority"):
        return f"kept for its {lang} audio, over a copy with the better score"
    return f"kept for its {lang} audio; also the better score"


def _file_record(keep: dict[str, Any], item: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Everything the report needs to judge one deletion, without opening Emby."""
    result = item.get("deletion_result") or {}
    status = result.get("status") or "unknown"
    server_id = keep.get("serverid") or ""
    keep_side, delete_side = _side(keep, base_url, server_id), _side(item, base_url, server_id)
    outcome = OUTCOME_BY_STATUS.get(status, "other")
    flags = _risk_flags(keep, item)
    return {
        "status": status,
        "status_text": STATUS_TEXT.get(status, status),
        "outcome": outcome,
        "file_only": status == "fold_safe_removed",
        "reason": result.get("error") or "",
        "reason_code": _reason_code(result.get("error")),
        "episode": _episode_label(keep),
        "season": keep.get("season_number") if isinstance(keep.get("season_number"), int) else 0,
        "episode_number": keep.get("episode_number") if isinstance(keep.get("episode_number"), int) else 0,
        "title": keep.get("name", ""),
        "keep": keep_side,
        "delete": delete_side,
        "folders_differ": keep_side["dir"] != delete_side["dir"],
        "lost_langs": [lang for lang in delete_side["langs_list"] if lang not in keep_side["langs_list"]],
        "keep_note": _why_kept(keep),
        "exclude_ids": _shared_provider_ids(keep, item),
        "pair_key": f"{keep_side['path']}|{delete_side['path']}",
        "size": delete_side["size"],
        "flags": flags,
        "flag_text": ", ".join((RISK_TEXT if outcome in DELETING_OUTCOMES else NOTE_TEXT)[f] for f in flags),
    }


def _cluster_key(keep: dict[str, Any], rec: dict[str, Any]) -> tuple[Any, ...]:
    if keep.get("is_episode"):
        season = folder_season(keep.get("path"))
        title_key: tuple[Any, ...] = ("series", keep.get("series_name", ""), season)
    else:
        title_key = ("movie", keep.get("id", ""))
    # A deletion is split by its risk flags (each risky row stands alone); a refused pair
    # stays with its season and carries the flags as notes.
    flags = tuple(rec["flags"]) if rec["outcome"] in DELETING_OUTCOMES else ()
    return (rec["outcome"], rec["reason_code"], title_key, flags)


def _summarise(values: list[str]) -> str:
    """Every distinct value, most common first: "1080i H264 HDTV / 480p H264 HDTV"."""
    counts = Counter(values)
    return " / ".join(value for value, _ in counts.most_common())


def _poster_class(keep: dict[str, Any]) -> str:
    """The series poster when the scan fetched one, else the keeper's own."""
    series = keep.get("series_poster") or {}
    return series.get("image_class") or keep.get("image_class", "")


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
        "image_class": _poster_class(keep),
        "image_url": keep.get("image_url", ""),
        "files": [],
    }


def _score_text(files: list[dict[str, Any]]) -> str:
    """The narrowest kept-vs-other margin in the row: the pair to doubt first."""
    pairs = [(f["keep"]["score"], f["delete"]["score"]) for f in files
             if f["keep"]["score"] is not None and f["delete"]["score"] is not None]
    if not pairs:
        return ""
    keep_score, delete_score = min(pairs, key=lambda p: p[0] - p[1])
    return f"{keep_score:.1f} vs {delete_score:.1f}"


def _lang_text(files: list[dict[str, Any]]) -> str:
    keep = _summarise([f["keep"]["langs"] for f in files])
    delete = _summarise([f["delete"]["langs"] for f in files])
    return f"{keep} (same)" if keep == delete else f"{keep} / {delete}"


def _row_id(row: dict[str, Any], index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-") or "row"
    season = row["season_folder"]
    where = f"-s{season:02d}" if isinstance(season, int) else ""
    return f"row-{row['outcome']}-{slug}{where}-{index}"


def _finish_row(row: dict[str, Any], index: int, history: dict[str, int]) -> dict[str, Any]:
    files = row["files"]
    files.sort(key=lambda f: (f["season"], f["episode_number"], f["keep"]["path"]))
    episodes = [f["episode"] for f in files if f["episode"]]
    if row["is_episode"]:
        season = row["season_folder"]
        where = f"S{season:02d}" if isinstance(season, int) else "no season folder"
        row["sub"] = episodes[0] if len(files) == 1 and episodes else f"{where} · {len(files)} files"
    else:
        row["sub"] = "Movie" if len(files) == 1 else f"Movie · {len(files)} files"
    row["id"] = _row_id(row, index)
    row["count"] = len(files)
    row["size"] = sum(f["size"] for f in files)
    row["size_text"] = format_size(row["size"])
    row["file_only"] = sum(1 for f in files if f["file_only"])
    row["keep_quality"] = _summarise([f["keep"]["quality"] for f in files])
    row["delete_quality"] = _summarise([f["delete"]["quality"] for f in files])
    row["keep_langs"] = _summarise([f["keep"]["langs"] for f in files])
    row["delete_langs"] = _summarise([f["delete"]["langs"] for f in files])
    row["langs_text"] = _lang_text(files)
    lost = sorted({lang for f in files for lang in f["lost_langs"]})
    row["lang_loss"] = " ".join(lost)
    row["score_text"] = _score_text(files)
    # Flags of a refused pair are notes; a deletion's flags make the row a "Check" row.
    row["flags"] = sorted({flag for f in files for flag in f["flags"]}, key=list(RISK_TEXT).index)
    texts = RISK_TEXT if row["risk"] else NOTE_TEXT
    row["risk_text"] = [texts[f] for f in row["flags"]]
    row["chips"] = [{"text": CHIP_TEXT[f].format(langs=row["lang_loss"] or "a language"), "title": texts[f]}
                    for f in row["flags"]]
    reasons = list(dict.fromkeys(f["reason"] for f in files if f["reason"]))
    row["reason"] = _one_reason(reasons)
    row["reason_text"] = _reason_text(row["reason_code"], files, row["reason"])
    row["exclude_ids"] = sorted({pid for f in files for pid in f["exclude_ids"]})
    row["exclude_numeric"] = any(not pid.startswith("tt") for pid in row["exclude_ids"])
    row["blocked_runs"] = max((history.get(f["pair_key"], 0) for f in files), default=0)
    row["shared"] = _shared_facts(files, row)
    row["per_file_status"] = len({f["status_text"] for f in files}) > 1
    row["per_file_reason"] = (row["reason_code"] not in ("runtime", "season_folder")
                              and len({f["reason"] for f in files if f["reason"]}) > 1)
    row["diff_text"] = _diff_text(row, files)
    return row


def _short_reason(row: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """Three words for the closed row; the sentence lives in the shared block."""
    if row["outcome"] == "failed":
        return "delete failed"
    code = row["reason_code"]
    if code == "runtime":
        return _reason_text(code, files, row["reason"])
    if code == "season_folder":
        return "different season folders"
    if code == "guard":
        return "guard: shared folder" if "co-located" in row["reason"] else "guard refused"
    return ""


def _quality_diff(keep: str, other: str) -> str:
    """Only the words that differ: "1080p vs 1080i", not two full labels."""
    if keep == other:
        return "same quality"
    k, o = keep.split(), other.split()
    only_k = [t for t in k if t not in o]
    only_o = [t for t in o if t not in k]
    if only_k and only_o:
        return f"{' '.join(only_k)} vs {' '.join(only_o)}"
    return f"{keep} vs {other}"


def _diff_text(row: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """What differs between the two copies, in as few words as possible.

    Uses each side's most common quality; a file that differs from its row is marked in
    the table. The runtime hint names only the unusual causes (PAL, half/double).
    """
    keep_q = row["keep_quality"].split(" / ")[0]
    other_q = row["delete_quality"].split(" / ")[0]
    parts = [("quality", _quality_diff(keep_q, other_q))]
    if row["keep_langs"] != row["delete_langs"]:
        parts.append(("audio", f"{row['keep_langs'].split(' / ')[0]} vs {row['delete_langs'].split(' / ')[0]}"))
    if row["reason_code"] == "runtime":
        pairs = [(f["keep"]["seconds"], f["delete"]["seconds"]) for f in files
                 if f["keep"]["seconds"] and f["delete"]["seconds"]]
        if pairs:
            ratio = median(d / k for k, d in pairs)
            gap = median(abs(d - k) for k, d in pairs)
            relation = _runtime_relation(ratio, gap)
            text = f"{_minute_span([k for k, _ in pairs])} vs {_minute_span([d for _, d in pairs])} min"
            parts.append(("runtime", text if relation == "different cut" else f"{text} ({relation})"))
        else:
            parts.append(("runtime", "runtime differs"))
    else:
        short = _short_reason(row, files)
        if short:
            parts.append(("reason", short))
    row["diff_parts"] = [{"kind": kind, "text": text} for kind, text in parts]
    return " · ".join(text for _, text in parts)


def _one_reason(reasons: list[str]) -> str:
    if not reasons:
        return ""
    if len(reasons) == 1:
        return reasons[0]
    return f"{len(reasons)} different reasons"


def _minute_span(values: list[int]) -> str:
    minutes = sorted({round(v / 60) for v in values})
    return f"{minutes[0]}" if minutes[-1] - minutes[0] <= 1 else f"{minutes[0]}–{minutes[-1]}"


def _runtime_relation(ratio: float, gap: float) -> str:
    """Name the usual causes of a runtime gap so the reader does not re-derive them."""
    if 1.035 <= ratio <= 1.05 or 0.952 <= ratio <= 0.966:
        return "PAL speed-up"
    if 1.85 <= ratio <= 2.15 or 0.46 <= ratio <= 0.54:
        return "half or double episode"
    if 45 <= gap <= 180:
        return "extras or recap"
    return "different cut"


def _reason_text(code: str, files: list[dict[str, Any]], reason: str) -> str:
    """Short reason for a guarded row, e.g. "runtime 52 vs 58 min (×1.12, different cut)"."""
    if code == "runtime":
        pairs = [(f["keep"]["seconds"], f["delete"]["seconds"]) for f in files
                 if f["keep"]["seconds"] and f["delete"]["seconds"]]
        if pairs:
            ratio = median(d / k for k, d in pairs)
            gap = median(abs(d - k) for k, d in pairs)
            return (f"runtime {_minute_span([k for k, _ in pairs])} vs "
                    f"{_minute_span([d for _, d in pairs])} min "
                    f"(×{ratio:.2f}, {_runtime_relation(ratio, gap)})")
        return "runtime differs"
    if code == "season_folder":
        return "different season folders"
    return reason[:80] if reason else ""


def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    # Risky rows first; then the biggest deletions, or the most files for a refusal.
    weight = -row["size"] if row["outcome"] in DELETING_OUTCOMES else -row["count"]
    return (not row["risk"], weight, row["name"], row["season_folder"] or 0)


def build_clusters(decisions: list[dict[str, Any]], base_url: str,
                   history: dict[str, int] | None = None) -> dict[str, Any]:
    """Group every delete item of ``decisions`` into report rows by outcome.

    ``history`` maps ``keep_path|delete_path`` to the number of runs the guard refused
    that pair (reports/history), shown as "blocked in N runs".

    Returns ``{"sections": {outcome: [row, ...]}, "check": [risky rows], "totals":
    {outcome: files}, "sizes": {outcome: bytes}, "total_files": n, "deleted_files": n}``.
    Rows carry their files' full evidence.
    """
    history = history or {}
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
    for index, row in enumerate(rows.values(), start=1):
        sections[row["outcome"]].append(_finish_row(row, index, history))
    for name in sections:
        sections[name].sort(key=_row_sort_key)

    check = [r for name in SECTION_ORDER for r in sections[name] if r["risk"]]
    totals = {name: sum(r["count"] for r in rs) for name, rs in sections.items()}
    sizes = {name: sum(r["size"] for r in rs) for name, rs in sections.items()}
    deleted = sum(1 for rs in sections.values() for r in rs for f in r["files"]
                  if f["status"] in DELETED_STATUSES)
    return {"sections": sections, "check": check, "totals": totals, "sizes": sizes,
            "total_files": sum(totals.values()), "deleted_files": deleted,
            "check_files": sum(r["count"] for r in check)}
