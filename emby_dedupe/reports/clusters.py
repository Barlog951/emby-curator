"""Fold a dedupe run's decisions into report rows: one row per title and outcome.

A large run is hundreds of near-identical groups (149 Smallville episodes upgraded the
same way). The report summarises them as rows such as "Smallville · S03 · 22 files ·
480p HDTV → 1080p WEB-DL", while keeping every file's evidence inside the row.

Rules that keep the summary from hiding a wrong deletion:

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
from emby_dedupe.utils.constants import ISO_639_2_B_TO_T, LANGUAGE_NORMALIZATION_MAP

# Report section per deletion status. Anything unknown goes to "other" so no file is dropped.
OUTCOME_BY_STATUS = {
    "success": "removed",
    "fold_safe_removed": "removed",
    "failed": "failed",
    "skipped_unsafe": "blocked",
    "would_block": "would_block",
    "not_attempted": "would_remove",
    "fold_safe_would_remove": "would_remove",
}
# Statuses of a file-only removal over SSH, done or planned.
FILE_ONLY_STATUSES = frozenset({"fold_safe_removed", "fold_safe_would_remove"})
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
    "fold_safe_would_remove": "Would be deleted (file only, over SSH)",
}

RISK_TEXT = {
    "score_inversion": "kept copy scores lower",
    "language_loss": "deleted copy has a language the kept one lacks",
    "hdr_loss": "deleted copy has HDR the kept one lacks",
    "dup_suffix": "numbered file name like (2) and a different runtime",
    "cross_folder": "episodes in different folders",
    "name_lies": "file name claims a resolution the video does not have",
}
# The same facts worded for a pair that stays on disk.
NOTE_TEXT = {
    "score_inversion": "kept copy scores lower",
    "language_loss": "other copy has a language the kept one lacks",
    "hdr_loss": "other copy has HDR the kept one lacks",
    "dup_suffix": "numbered file name like (2) and a different runtime",
    "cross_folder": "episodes in different folders",
    "name_lies": "file name claims a resolution the video does not have",
}

# Short chip wording; the full sentence (RISK_TEXT / NOTE_TEXT) is the chip's tooltip.
CHIP_TEXT = {
    "score_inversion": "scores lower",
    "language_loss": "loses {langs}",
    "hdr_loss": "loses {hdr}",
    "dup_suffix": "numbered copy",
    "cross_folder": "different folders",
    "name_lies": "name overstates resolution",
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
    if video_range.startswith(("hdr10+", "hdr10plus")):
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
    squashed = res.lower().replace(" ", "")
    if hdr and ("dolbyvision" in squashed or hdr.lower().replace(" ", "") in squashed):
        hdr = ""
    src = _source_tier((item.get("quality_description") or {}).get("source_quality_multiplier"))
    return " ".join(x for x in (res, hdr, src) if x) or "unknown quality"


def _same_language(code: str) -> str:
    """One key per language for comparisons: "fre" and "fra" (B and T codes) both give "fra"."""
    return ISO_639_2_B_TO_T.get(code, code)


def _lost_languages(delete_langs: list[str], keep_langs: list[str]) -> list[str]:
    """The other copy's languages the kept copy has under no code, in the other copy's order."""
    kept = {_same_language(lang) for lang in keep_langs}
    return [lang for lang in delete_langs if _same_language(lang) not in kept]


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


# Guard messages name a rule by a stable token. A runtime message can embed the frame
# check's verdict ("frame-check unsure"), so the runtime token is matched first.
_REASON_CODES = (
    ("duration-mismatch", "runtime"),
    ("season folder", "season_folder"),
    ("merge-audio-first", "merge_audio"),
    ("frame-check different", "pictures_differ"),
    ("frame-check unavailable", "picture_check_missing"),
    ("frame-check unsure", "picture_check_unsure"),
    ("provider-id-conflict", "different_title"),
    ("runtime-unknown", "runtime_unknown"),
    ("duration-unknown", "runtime_unknown"),
    ("watched-copy", "watched_copy"),
)


def _reason_code(error: str | None) -> str:
    text = error or ""
    for token, code in _REASON_CODES:
        if token in text:
            return code
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
    if _lost_languages(_languages(item), _languages(keep)):
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


def _mark_odd_files(files: list[dict[str, Any]], role: str, field: str, name: str, common: str) -> None:
    """Give each file whose value differs from the row's its own value in ``vary``."""
    for f in files:
        if f[role][field] != common:
            f[role]["vary"][name] = f[role][field] or "none"


def _side_constants(files: list[dict[str, Any]], role: str, skip: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Facts shared by every file on one side; a file that differs gets them in ``vary``."""
    constant: list[tuple[str, str]] = []
    for field, name in _SHARED_FIELDS:
        if field in skip:
            continue
        common, n = Counter(f[role][field] for f in files).most_common(1)[0]
        # The majority value is the row's fact; a file that differs carries its own.
        if common:
            constant.append((_FACT_KIND[name], f"added {common}" if field == "added" else common))
        if n != len(files):
            _mark_odd_files(files, role, field, name, common)
    return constant


def _pair_runtimes_match(f: dict[str, Any]) -> bool:
    """The pair's runtimes are the same cut by the flag rule (within 5 s or 2%)."""
    k, d = f["keep"]["seconds"], f["delete"]["seconds"]
    if not k or not d:
        return f["keep"]["runtime"] == f["delete"]["runtime"]
    return abs(k - d) <= max(5, 0.02 * max(k, d))


def _common_facts(files: list[dict[str, Any]], folder: str, added: str) -> str:
    """Facts true of every file on both sides, said once under the pairs."""
    parts = []
    if all(_pair_runtimes_match(f) for f in files):
        runtimes = {f[role]["runtime"] for f in files for role in ("keep", "delete")}
        parts.append(f"runtime {next(iter(runtimes))} on every file" if len(runtimes) == 1
                     else "runtimes match in every pair")
    if added:
        parts.append(f"all added {added}")
    if folder:
        parts.append(f"folder {folder}")
    return " · ".join(parts)


def _side_lines(files: list[dict[str, Any]], row: dict[str, Any], dirs: dict[str, set[str]], one_folder: bool,
                added: str) -> list[dict[str, Any]]:
    """One line per side with its constant facts; one "Both" line when the sides read the same."""
    lines: list[dict[str, Any]] = []
    other_label = "Removed" if row["outcome"] in DELETING_OUTCOMES else "Other"
    for role, label in (("keep", "Kept"), ("delete", other_label)):
        constant = _side_constants(files, role, skip=("added",) if added else ())
        if not one_folder and len(dirs[role]) == 1:
            constant.append(("path", f"in {next(iter(dirs[role])) or '/'}"))
        if constant:
            lines.append(_fact_line(label, constant))
    if len(lines) == 2 and lines[0]["text"] == lines[1]["text"]:
        return [{**lines[0], "label": "Both"}]
    return lines


def _shared_facts(files: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, str]]:
    """Each side's constant facts once; what every file shares goes to ``row["common"]``."""
    dirs = {role: {f[role]["dir"] for f in files} for role in ("keep", "delete")}
    one_folder = len(dirs["keep"]) == 1 and dirs["keep"] == dirs["delete"]
    dates = {f[role]["added"] for f in files for role in ("keep", "delete")}
    added = next(iter(dates)) if len(dates) == 1 else ""
    lines = _side_lines(files, row, dirs, one_folder, added)
    row["common"] = _common_facts(files, (next(iter(dirs["keep"])) or "/") if one_folder else "", added)
    return lines + _guard_lines(row, files)


# What a removed file's size means, by the file's own outcome.
_SIZE_WORD = {"removed": "freed", "would_remove": "to free", "failed": "not freed"}
_VARY_PREFIX = {"audio": "audio ", "added": "added "}


def _file_extras(row: dict[str, Any], f: dict[str, Any], show_scores: bool) -> tuple[list[str], list[str]]:
    """What the kept and the other file of one pair need beyond their name and size."""
    keep, other = f["keep"], f["delete"]
    keep_extra = [_VARY_PREFIX.get(k, "") + v for k, v in keep["vary"].items()]
    other_extra = [_VARY_PREFIX.get(k, "") + v for k, v in other["vary"].items()]
    if not _pair_runtimes_match(f):
        other_extra.append(f"runtime {other['runtime'] or 'unknown'} (kept {keep['runtime'] or 'unknown'})")
    if show_scores or keep["score_note"] or other["score_note"]:
        keep_extra += [f"score {keep['score_text'] or '?'}", keep["score_note"]]
        other_extra += [f"score {other['score_text'] or '?'}", other["score_note"]]
    if row["per_file_status"]:
        other_extra.append(f["status_text"])
    if row["per_file_reason"]:
        other_extra.append(f["reason"])
    return keep_extra, other_extra


def _pair_lines(row: dict[str, Any]) -> None:
    """Per file: only what differs from the rest of the row or within its own pair."""
    show_scores = "score_inversion" in row["flags"]
    for f in row["files"]:
        keep_extra, other_extra = _file_extras(row, f, show_scores)
        f["keep_extra"] = " · ".join(part for part in keep_extra if part)
        f["other_extra"] = " · ".join(part for part in other_extra if part)
        f["size_word"] = _SIZE_WORD.get(f["outcome"], "held")


def _guard_lines(row: dict[str, Any], files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For a pair that stays: the next step, since the closed row already names the reason.

    The guard's own sentence is shown only where no next step exists (an unknown rule) or
    for a failed delete; the raw message stays in ``data-reason`` and the JSON twin.
    """
    guard = _guard_line(row, files)
    if not guard:
        return []
    if row["outcome"] in DELETING_OUTCOMES:
        return [_fact_line("Error", [("reason", guard)])]
    step = _next_step(row)
    if step:
        return [_fact_line("Next step", [("reason", step)])]
    return [_fact_line("Guard", [("reason", guard)])]


def _next_step(row: dict[str, Any]) -> str:
    """The one thing to do about a held pair, by guard rule."""
    if row["reason_code"] == "guard" and "co-located" in row["reason"]:
        return "Move one copy into its own folder, then run again."
    if row["reason_code"] == "runtime" and row.get("frame_code") in _UNPROVEN:
        return NEXT_STEP[row["frame_code"]]
    return NEXT_STEP.get(row["reason_code"], "")


# The guard's verdict in plain words; the raw message follows in brackets.
_GUARD_SENTENCE = {
    "pictures_differ": "the sampled frames don't match, so these are probably different episodes or cuts",
    "picture_check_missing": "the picture check didn't run for this pair, so nothing is proven either way",
    "picture_check_unsure": "the sampled frames neither clearly match nor clearly differ",
    "different_title": "the two copies carry ids of different titles",
    "runtime_unknown": "Emby hasn't read the runtime of one copy yet, so the same content can't be checked",
    "watched_copy": "someone watched the copy to delete, and the kept copy shares no id that would carry it over",
}

# What the reader can do about a held pair. Excluding is offered only where the copies
# are shown to differ; an unproven pair is re-checked, never hidden for good.
NEXT_STEP = {
    "runtime": "Compare both in Emby. Mislabelled: fix the numbering. A different cut or episode: exclude the pair.",
    "pictures_differ": "Compare both in Emby. Mislabelled: fix the numbering. Really different: exclude the pair.",
    "picture_check_missing": "Run again with the frame check. Don't exclude yet.",
    "picture_check_unsure": "Compare both in Emby before deciding. Don't exclude yet.",
    "season_folder": "Move the copy into the right season folder, then run again.",
    "merge_audio": "Check the audio language, tag or merge it, then run again.",
    "different_title": "Fix the wrong id in Emby, or exclude the pair.",
    "runtime_unknown": "Refresh both items' metadata in Emby, then run again.",
    "watched_copy": "Keep both, or move the watch history to the kept copy first.",
}

# The frame check's verdict as embedded in a guard message, weakest first: one unproven
# file makes the whole row unproven.
_FRAME_VERDICTS = (
    ("frame-check unavailable", "picture_check_missing"),
    ("frame-check unsure", "picture_check_unsure"),
    ("frame-check different", "pictures_differ"),
)
_UNPROVEN = frozenset({"picture_check_missing", "picture_check_unsure"})


def _frame_code(files: list[dict[str, Any]]) -> str:
    """The weakest frame-check verdict named in the row's guard messages, or ''."""
    text = " ".join(f["reason"] or "" for f in files)
    return next((code for token, code in _FRAME_VERDICTS if token in text), "")


# Reason codes whose pairs are shown to be different files, so excluding them is safe.
EXCLUDABLE_REASONS = frozenset({"runtime", "pictures_differ", "season_folder", "different_title"})


def _guard_line(row: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """Why the guard refused the row (or why deletes failed), once."""
    reasons = {f["reason"] for f in files if f["reason"]}
    if not reasons:
        return ""
    if row["reason_code"] == "runtime":
        return f"{_reason_text('runtime', files, '')}, beyond the guard's tolerance"
    if row["reason_code"] in _GUARD_SENTENCE:
        detail = next(iter(reasons)) if len(reasons) == 1 else "details per file below"
        return f"{_GUARD_SENTENCE[row['reason_code']]} ({detail})"
    if row["reason_code"] == "season_folder":
        return "the two copies sit in different season folders"
    if row["reason_code"] == "merge_audio":
        return ("deleting a copy would lose a language or the better picture: merge the audio into one file "
                "first (" + "; ".join(sorted(r.split(": ", 1)[-1] for r in reasons)) + ")")
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
        # The scorer multiplies a Dolby Vision profile 5 copy by 0.0001 so it never wins;
        # the near-zero score is deliberate and says so beside the number.
        "score_note": "Dolby Vision profile 5, scored down" if _video(item).get("is_dovi_p5") else "",
        "dovi_p5": bool(_video(item).get("is_dovi_p5")),
        "ai_upscale": bool(qd.get("is_ai_upscale")),
        "image_class": item.get("image_class", ""),
        "url": _emby_url(base_url, item, server_id),
        "vary": {},
    }


def _language_key(code: str) -> str:
    """The comparison key of a language code as the report displays it."""
    lower = str(code).lower()
    return _same_language(LANGUAGE_NORMALIZATION_MAP.get(lower, lower))


def _why_kept(keep: dict[str, Any], item: dict[str, Any]) -> str:
    """One short clause on why this copy won over ``item``, only when its language decided.

    Language priority picks per group; for one pair it decided only when the other copy
    lacks that language. The score clause compares this pair's own scores.
    """
    lang = keep.get("priority_language_used")
    if not keep.get("selected_by_language_priority") or not lang:
        return ""
    if _language_key(lang) in {_language_key(code) for code in _languages(item)}:
        return ""
    keep_score, item_score = _rating(keep), _rating(item)
    if keep_score is None or item_score is None or keep_score == item_score:
        return f"kept for its {lang} audio"
    if keep_score < item_score:
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
        "file_only": status in FILE_ONLY_STATUSES,
        "reason": result.get("error") or "",
        "reason_code": _reason_code(result.get("error")),
        "episode": _episode_label(keep),
        "season": keep.get("season_number") if isinstance(keep.get("season_number"), int) else 0,
        "episode_number": keep.get("episode_number") if isinstance(keep.get("episode_number"), int) else 0,
        "title": keep.get("name", ""),
        "keep": keep_side,
        "delete": delete_side,
        "folders_differ": keep_side["dir"] != delete_side["dir"],
        "lost_langs": _lost_languages(delete_side["langs_list"], keep_side["langs_list"]),
        "keep_note": _why_kept(keep, item),
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
    return f"{keep} (same)" if _same_langs(keep, delete) else f"{keep} / {delete}"


def _same_langs(keep: str, other: str) -> bool:
    """Language summaries with the same languages in any track order count as the same.

    A summary lists every distinct value, most common first ("cs eng / cs"); both sides
    must match variant for variant.
    """
    def variants(summary: str) -> list[list[str]]:
        return [sorted(part.split()) for part in summary.split(" / ")]
    return variants(keep) == variants(other)


def _row_id(row: dict[str, Any], index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-") or "row"
    season = row["season_folder"]
    where = f"-s{season:02d}" if isinstance(season, int) else ""
    return f"row-{row['outcome']}-{slug}{where}-{index}"


def _lost_hdr(files: list[dict[str, Any]]) -> str:
    """"Dolby Vision" when the other copy is DV and the kept one has some other HDR, else "HDR"."""
    for f in files:
        if f["delete"]["hdr"].startswith("DV") and f["keep"]["hdr"] not in ("SDR", "") and not f["keep"]["hdr"].startswith("DV"):
            return "Dolby Vision"
    return "HDR"


def _mark_repeated_keepers(files: list[dict[str, Any]]) -> None:
    """Group lines that share a kept file (two copies of one episode removed): the kept file
    and its links are shown once per group."""
    for before, f, after in zip([None, *files], files, [*files[1:], None], strict=False):
        f["same_keep"] = before is not None and before["keep"]["id"] == f["keep"]["id"]
        f["group_end"] = after is None or after["keep"]["id"] != f["keep"]["id"]


def _finish_row(row: dict[str, Any], index: int, history: dict[str, int]) -> dict[str, Any]:
    files = row["files"]
    files.sort(key=lambda f: (f["season"], f["episode_number"], f["keep"]["path"]))
    _mark_repeated_keepers(files)
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
    lost_hdr = _lost_hdr(files)
    row["chips"] = [{"text": CHIP_TEXT[f].format(langs=row["lang_loss"] or "a language", hdr=lost_hdr), "title": texts[f]}
                    for f in row["flags"]]
    reasons = list(dict.fromkeys(f["reason"] for f in files if f["reason"]))
    row["reason"] = _one_reason(reasons)
    row["reason_text"] = _reason_text(row["reason_code"], files, row["reason"])
    row["exclude_ids"] = sorted({pid for f in files for pid in f["exclude_ids"]})
    row["exclude_numeric"] = any(not pid.startswith("tt") for pid in row["exclude_ids"])
    # Excluding hides a pair for good; offer it only where the guard showed the copies differ.
    row["frame_code"] = _frame_code(files)
    row["excludable"] = row["outcome"] == "would_remove" or (
        row["reason_code"] in EXCLUDABLE_REASONS and row["frame_code"] not in _UNPROVEN)
    row["blocked_runs"] = max((history.get(f["pair_key"], 0) for f in files), default=0)
    row["shared"] = _shared_facts(files, row)
    row["per_file_status"] = len({f["status_text"] for f in files}) > 1
    row["per_file_reason"] = (row["reason_code"] not in ("runtime", "season_folder", "merge_audio")
                              and len({f["reason"] for f in files if f["reason"]}) > 1)
    row["diff_text"] = _diff_text(row, files)
    _pair_lines(row)
    return row


_SHORT_REASON = {
    "season_folder": "different season folders",
    "merge_audio": "merge audio first",
    "pictures_differ": "pictures differ",
    "picture_check_missing": "picture check didn't run",
    "picture_check_unsure": "pictures unclear",
    "different_title": "different titles",
    "runtime_unknown": "runtime unknown",
    "watched_copy": "watched copy",
}


def _short_reason(row: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """Three words for the closed row; the sentence lives in the shared block."""
    if row["outcome"] == "failed":
        return "delete failed"
    code = row["reason_code"]
    if code == "runtime":
        return _reason_text(code, files, row["reason"])
    if code == "guard":
        return "shared folder" if "co-located" in row["reason"] else "held by the guard"
    return _SHORT_REASON.get(code, "")


def _quality_diff(keep: str, other: str) -> str:
    """Only the words that differ: "1080p vs 1080i", not two full labels."""
    if keep == other:
        return "same format"
    k, o = keep.split(), other.split()
    only_k = [t for t in k if t not in o]
    only_o = [t for t in o if t not in k]
    if only_k and only_o:
        return f"{' '.join(only_k)} vs {' '.join(only_o)}"
    return f"{keep} vs {other}"


def _size_hint(files: list[dict[str, Any]]) -> str:
    """A size gap of 2x or more between the copies, which the format label alone hides."""
    ratios = [f["keep"]["size"] / f["delete"]["size"] for f in files if f["keep"]["size"] and f["delete"]["size"]]
    if not ratios:
        return ""
    ratio = median(ratios)
    if ratio >= 2:
        return f"kept copy {ratio:.0f}× larger"
    if ratio <= 0.5:
        return f"kept copy {1 / ratio:.0f}× smaller"
    return ""


def _audio_diff(keep: str, other: str) -> str:
    """The audio difference worth a closed-row mention, or ''.

    Summaries list every variant ("cs fre / cs"); when the first variants match, the
    difference is in another file, so both full summaries are shown. Same languages in a
    different order still differ in the first audio track.
    """
    if not _same_langs(keep, other):
        k, o = keep.split(" / ")[0], other.split(" / ")[0]
        return f"{k} vs {o}" if k != o else f"{keep} vs {other}"
    k_first, o_first = (keep.split() or [""])[0], (other.split() or [""])[0]
    return f"first audio track {k_first} vs {o_first}" if k_first != o_first else ""


def _runtime_part(files: list[dict[str, Any]], always: bool) -> str:
    """Runtime span of both sides with the usual cause; without ``always`` only a PAL speed-up."""
    pairs = [(f["keep"]["seconds"], f["delete"]["seconds"]) for f in files
             if f["keep"]["seconds"] and f["delete"]["seconds"]]
    if not pairs:
        return "runtime differs" if always else ""
    ratio = median(d / k for k, d in pairs)
    gap = median(abs(d - k) for k, d in pairs)
    relation = _runtime_relation(ratio, gap)
    if not always and relation != "PAL speed-up":
        return ""
    text = f"{_minute_span([k for k, _ in pairs])} vs {_minute_span([d for _, d in pairs])} min"
    return text if relation == "different cut" else f"{text} ({relation})"


def _diff_text(row: dict[str, Any], files: list[dict[str, Any]]) -> str:
    """What differs between the two copies, in as few words as possible.

    Uses each side's most common quality; a file that differs from its row is marked in
    the table. The runtime hint names only the unusual causes (PAL, half/double).
    """
    keep_q = row["keep_quality"].split(" / ")[0]
    other_q = row["delete_quality"].split(" / ")[0]
    quality = _quality_diff(keep_q, other_q)
    size = _size_hint(files) if quality == "same format" else ""
    parts = [("quality", f"{quality}, {size}" if size else quality)]
    audio = _audio_diff(row["keep_langs"], row["delete_langs"])
    if audio:
        parts.append(("audio", audio))
    runtime = _runtime_part(files, always=row["reason_code"] == "runtime")
    if runtime:
        parts.append(("runtime", runtime))
    if row["reason_code"] == "runtime":
        if row.get("frame_code"):
            parts.append(("reason", _SHORT_REASON[row["frame_code"]]))
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
    if code in _SHORT_REASON:
        return _SHORT_REASON[code]
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
