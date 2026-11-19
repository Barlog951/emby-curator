"""Regression tests for the 2026-09-27 dedupe data loss (Initial D / Count Duckula).

Paths are the real ones from that run's report. Initial D stores one folder per stage
(S01 … S06) but names every stage's files "S01Exx"; the season FOLDER was ignored, so
distinct episodes were grouped as duplicates and deleted.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from emby_dedupe.api import deduplication as dd
from emby_dedupe.api.deletion_guard import episode_identity_conflict, folder_season

ID = "/Movies/Serials/--- UKONCENE ---/Initial D (1998)"
MCLEOD = "/Movies/Serials/--- UKONCENE ---/McLeod's Daughters (2001)"
DUCKULA = "/Movies/Serials/--- UKONCENE ---/Count Duckula (1988)"

LOST_PAIRS = [  # (kept, deleted) — distinct episodes that were deleted
    (f"{ID}/S04/Initial D (1998) S01E10 - 1080p WEB-DL (3).mkv", f"{ID}/S05/Initial D (1998) S01E10 - 1080p WEB-DL (4).mkv"),
    (f"{ID}/S04/Initial D (1998) S01E24 - 1080p WEB-DL (2).mkv", f"{ID}/S01/Initial D (1998) S01E24 - 1080p WEB-DL.mkv"),
]
REAL_DUPLICATES = [  # (kept, deleted) — genuinely the same season-7 episode, must still dedupe
    (f"{MCLEOD}/S07/McLeod's Daughters (2001) S07E02 - 1080p WEB-DL CZ.mkv", f"{MCLEOD}/S01/7. Série/McLeod's Daughters (2001) S07E02 - 576p CZ.avi"),
    (f"{MCLEOD}/S07/McLeod's Daughters (2001) S07E15 - 1080p WEB-DL CZ.mkv", f"{MCLEOD}/S01/7. Série/McLeod's Daughters (2001) S07E15 - 576p CZ.avi"),
]
FLAT_VS_SEASON = (f"{DUCKULA}/Count Duckula (1988) S01E09 - 480p x264.mp4", f"{DUCKULA}/S01/Count Duckula S01E09 - 720p BluRay CZ.mkv")


@pytest.mark.parametrize(("path", "season"), [
    (f"{ID}/S05/Initial D (1998) S01E10 - 1080p WEB-DL (4).mkv", 5),
    (f"{MCLEOD}/S01/7. Série/McLeod's Daughters (2001) S07E02 - 576p CZ.avi", 7),  # nearest season folder wins
    ("/tv/Show/Season 2/Show 2x01.mkv", 2),
    ("/tv/Show/5. séria/a.mkv", 5),
    ("/tv/Show/Staffel 3/a.mkv", 3),
    ("/tv/Show/S05/extras/a.mkv", 5),
    ("/tv/Wednesday/Wednesday.S02.2160p/Wednesday.S02E06.mkv", 2),  # season-pack folder
    ("/tv/Wednesday/S02/Wednesday.S02E05-E08.2160p/Wednesday.S02E06.mkv", 2),
    (f"{DUCKULA}/Count Duckula (1988) S01E09 - 480p x264.mp4", None),  # flat
    ("/tv/The.Office.S01-S09.1080p/The.Office.S03E01.mkv", None),  # multi-season pack says nothing
    (None, None),
])
def test_folder_season(path, season):
    assert folder_season(path) == season


@pytest.mark.parametrize(("kept", "deleted"), LOST_PAIRS)
def test_distinct_stage_episodes_are_refused(kept, deleted):
    assert "different season folders" in episode_identity_conflict(kept, deleted)


@pytest.mark.parametrize(("kept", "deleted"), REAL_DUPLICATES)
def test_nested_same_season_copy_is_still_a_duplicate(kept, deleted):
    assert episode_identity_conflict(kept, deleted) is None


def test_flat_vs_season_folder_is_refused_and_folder_vs_filename_conflict_too():
    assert "flat folder" in episode_identity_conflict(*FLAT_VS_SEASON)
    both_in_s05 = (f"{ID}/S05/Initial D (1998) S01E10 - 1080p.mkv", f"{ID}/S05/Initial D (1998) S01E10 - 720p.mkv")
    assert "contradicts" in episode_identity_conflict(*both_in_s05)


def test_movies_and_same_folder_copies_are_not_judged():
    assert episode_identity_conflict("/m/Movie (2020)/Movie.mkv", "/m/Movie (2020)/Movie.1080p.mkv") is None
    assert episode_identity_conflict("/tv/Jackie/Jackie S01E10.mp4", "/tv/Jackie/Jackie S01E10 (2).mp4") is None


# --- layer 1: grouping keys ----------------------------------------------------

def test_series_keys_differ_across_season_folders():
    (kept, deleted), (m_kept, m_deleted) = LOST_PAIRS[0], REAL_DUPLICATES[0]
    base = {"series_name": "Initial D", "season_number": 1, "episode_number": 10}
    assert dd._create_series_key({**base, "path": kept}) != dd._create_series_key({**base, "path": deleted})
    mc = {"series_name": "McLeod's Daughters", "season_number": 7, "episode_number": 2}
    assert dd._create_series_key({**mc, "path": m_kept}) == dd._create_series_key({**mc, "path": m_deleted})


def test_path_grouping_never_pairs_different_stages():
    items = [{"Id": str(i), "SeriesName": "Initial D", "Path": p} for i, p in enumerate(LOST_PAIRS[0])]
    filtered, _ = dd._group_items_by_episode_path(items)
    assert len(filtered) == 1  # split apart: nothing left to deduplicate


# --- layer 2: delete-time backstop ----------------------------------------------

def test_real_deletion_refuses_without_calling_emby_or_marking_fold_safe(monkeypatch):
    calls: list = []
    monkeypatch.setattr(dd, "delete_item", lambda *a, **k: calls.append(a) or {"status": "success"})
    kept, deleted = LOST_PAIRS[0]
    item = {"id": "42", "name": "The 5 Consecutive Hairpins", "path": deleted}
    dd._execute_one_deletion(MagicMock(), "http://emby", item, kept, {"keep": {"path": kept}, "delete": [item]},
                             [kept, deleted], [deleted], "u", "p", "k", MagicMock())
    assert calls == []
    assert item["deletion_result"]["status"] == "skipped_unsafe"
    assert "fold_safe_candidate" not in item  # fold-safe must not delete it file-only either


def test_dry_run_warning_counts_the_refusal(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(dd.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))
    kept, deleted = LOST_PAIRS[1]
    decisions = [{"keep": {"path": kept}, "delete": [{"id": "7", "path": deleted}]}]
    dd._warn_unsafe_deletions(decisions, [kept, deleted], [deleted])
    assert any("would REFUSE" in w for w in warnings)
    assert "fold_safe_candidate" not in decisions[0]["delete"][0]


# --- runtime gate: a big duration gap means different content -------------------

MIN = 60 * 10_000_000  # Emby ticks per minute


@pytest.mark.parametrize(("keep", "delete"), [
    # Rust (140 min) that Emby filed under Runt's TMDb id (92 min) — two different films
    ({"path": "/Movies/HD/Runt (2024) - 1080p WEB-DL CZ/Runt (2024) - 1080p WEB-DL CZ.mkv", "runtime_ticks": int(92.5 * MIN)},
     {"id": "r", "path": "/Movies/4K/Rust (2024) - 2160p WEB-DL/Rust (2024) - 2160p WEB-DL.mkv", "runtime_ticks": int(139.9 * MIN)}),
    # Count Duckula S01E24 "(2)" in the same flat folder: 18.6 vs 22.5 min — another episode
    ({"path": f"{DUCKULA}/Count Duckula (1988) S01E24 - 480p x264.mp4", "runtime_ticks": int(22.54 * MIN)},
     {"id": "d", "path": f"{DUCKULA}/Count Duckula (1988) S01E24 - 480p x264 (2).mp4", "runtime_ticks": int(18.62 * MIN)}),
])
def test_runtime_mismatch_is_refused_at_delete_time(monkeypatch, keep, delete):
    calls: list = []
    monkeypatch.setattr(dd, "delete_item", lambda *a, **k: calls.append(a) or {"status": "success"})
    dd._execute_one_deletion(MagicMock(), "http://emby", delete, keep["path"], {"keep": keep, "delete": [delete]},
                             [keep["path"], delete["path"]], [delete["path"]], "u", "p", "k", MagicMock())
    assert calls == []
    assert delete["deletion_result"]["status"] == "skipped_unsafe"
    assert "duration-mismatch" in delete["deletion_result"]["error"]


def test_same_runtime_copy_is_still_deleted(monkeypatch):
    calls: list = []
    monkeypatch.setattr(dd, "delete_item", lambda *a, **k: calls.append(a) or {"status": "success"})
    keep = {"path": "/Movies/4K/Code 3 (2024) - 2160p/Code 3.mkv", "runtime_ticks": int(98.0 * MIN)}
    dup = {"id": "c", "path": "/Movies/HD/Code 3 (2025) - 2160p/Code 3.mkv", "runtime_ticks": int(98.2 * MIN)}
    dd._execute_one_deletion(MagicMock(), "http://emby", dup, keep["path"], {"keep": keep, "delete": [dup]},
                             [keep["path"], dup["path"]], [dup["path"]], "u", "p", "k", MagicMock())
    assert len(calls) == 1
    assert dup["deletion_result"] == {"status": "success"}


# --- language: never delete the best copy just because its language is untagged ---

def _episode(item_id, path, width, height, size, language, runtime_ticks=None, series="Count Duckula"):
    languages = language if isinstance(language, list) else [language]
    tracks = []
    for code in languages:
        audio = {"Type": "Audio", "Codec": "aac", "Channels": 2}
        if code is not None:
            audio["Language"] = code
        tracks.append(audio)
    item = {"Id": item_id, "Name": "All in a Fog", "SeriesName": series, "Path": path, "Size": size,
            "MediaStreams": [{"Type": "Video", "Codec": "h264", "Width": width, "Height": height}, *tracks]}
    if runtime_ticks is not None:
        item["RunTimeTicks"] = runtime_ticks
    return item


LANG_PRIO = ["sk", "cs", "eng"]  # what the CLI makes of --lang-prio "slo,sk,cze,ces,eng"
DUCKULA_BLURAY = "/tv/Duckula/S01/Duckula S01E09 - 720p BluRay CZ.mkv"
DUCKULA_X264 = "/tv/Duckula/S01/Duckula S01E09 - 480p x264.mp4"


def _duckula(bluray_language, runtime_ticks=int(22.5 * MIN)):
    return [_episode("1", DUCKULA_BLURAY, 1280, 720, 383_000_000, bluray_language, runtime_ticks),
            _episode("2", DUCKULA_X264, 640, 480, 159_000_000, "eng", runtime_ticks)]


def _assert_held(decision, keep_id, held_ids):
    """The best copy is the keeper and every other copy carries the untagged-best-copy hold,
    which is the reason the guard gives for it."""
    keep = decision["keep"]
    assert keep["id"] == keep_id
    assert sorted(d["id"] for d in decision["delete"]) == sorted(held_ids)
    for item in decision["delete"]:
        hold = item[dd.UNTAGGED_BEST_COPY_HOLD]
        assert hold.startswith(dd.MERGE_AUDIO_FIRST)
        assert "untagged audio" in hold
        assert dd._refusal_reason(item, keep) == hold


@pytest.mark.parametrize("bluray_language", [None, "und"])
def test_untagged_better_copy_is_not_deleted_for_a_tagged_worse_one(bluray_language):
    """Count Duckula: the 720p BluRay (Czech, but untagged) was deleted to keep a tagged
    English 480p. Now the BluRay is the keeper and the group is held, not deleted."""
    decision = dd.determine_items_to_delete(["1", "2"], _duckula(bluray_language), ["sk", "cs", "eng"])
    _assert_held(decision, "1", ["2"])
    assert decision["keep"]["selected_by_language_priority"] is False
    assert decision["keep"]["changed_by_language_priority"] is False


@pytest.mark.parametrize("bluray_language", [None, "und"])
def test_held_group_is_never_deleted_under_doit(monkeypatch, bluray_language):
    calls: list = []
    monkeypatch.setattr(dd, "delete_item", lambda *a, **k: calls.append(a) or {"status": "success"})
    decision = dd.determine_items_to_delete(["1", "2"], _duckula(bluray_language), ["sk", "cs", "eng"])
    keep, held = decision["keep"], decision["delete"][0]
    assert dd._different_content_reason(held, keep) is None  # the hold, not another guard, refuses it
    dd._execute_one_deletion(MagicMock(), "http://emby", held, keep["path"], decision,
                             [keep["path"], held["path"]], [held["path"]], "u", "p", "k", MagicMock())
    assert calls == []
    assert held["deletion_result"] == {"id": "2", "status": "skipped_unsafe", "error": held[dd.UNTAGGED_BEST_COPY_HOLD]}
    assert "fold_safe_candidate" not in held  # --fold-safe-delete must not remove it file-only


def test_held_group_shows_as_would_block_in_a_dry_run():
    decision = dd.determine_items_to_delete(["1", "2"], _duckula(None), ["sk", "cs", "eng"])
    keep = decision["keep"]
    dd._warn_unsafe_deletions([decision], [keep["path"], DUCKULA_X264], [DUCKULA_X264])
    dd._mark_items_as_not_attempted([decision], MagicMock())
    held = decision["delete"][0]
    assert held["deletion_result"]["status"] == "would_block"
    assert held["deletion_result"]["error"] == held[dd.UNTAGGED_BEST_COPY_HOLD]
    assert "fold_safe_candidate" not in held


def test_held_group_reaches_the_report_decisions(monkeypatch):
    """The held group used to vanish: an empty decision never reached the report."""
    items = _duckula(None)
    monkeypatch.setattr(dd, "fetch_items_details", lambda *a, **k: items)
    decisions, _ = dd.process_duplicate_groups(MagicMock(), "http://emby", [["1", "2"]], lang_priorities=["sk", "cs", "eng"])
    assert len(decisions) == 1
    _assert_held(decisions[0], "1", ["2"])


def test_held_group_is_a_merge_audio_row_that_is_never_excludable():
    from emby_dedupe.reports.clusters import EXCLUDABLE_REASONS, _reason_code
    decision = dd.determine_items_to_delete(["1", "2"], _duckula(None), ["sk", "cs", "eng"])
    code = _reason_code(decision["delete"][0][dd.UNTAGGED_BEST_COPY_HOLD])
    assert code == "merge_audio"
    assert code not in EXCLUDABLE_REASONS


def test_every_other_copy_of_a_held_group_is_held():
    """A third, worse copy in a language outside --lang-prio passes every other guard against
    the untagged keeper; the hold keeps it as well, as the whole group was kept before."""
    items = [*_duckula(None),
             _episode("3", "/tv/Duckula/S01/Duckula S01E09 - 360p.avi", 480, 360, 90_000_000, "ger", int(22.5 * MIN))]
    decision = dd.determine_items_to_delete(["1", "2", "3"], items, ["sk", "cs", "eng"])
    _assert_held(decision, "1", ["2", "3"])
    third = next(d for d in decision["delete"] if d["id"] == "3")
    assert dd._different_content_reason(third, decision["keep"]) is None
    assert dd.merge_audio_first_reason(third, decision["keep"]) is None  # only the hold refuses it


def test_untagged_best_copy_is_held_against_a_same_class_copy_with_every_language():
    """Same picture class, and the tagged copy carries every --lang-prio language: no merge
    reason fires for the untagged copy, so only the hold keeps it."""
    items = [_episode("1", "/tv/Show/S01/Show S01E01 - 1080p WEB-DL.mkv", 1920, 1080, 2_000_000_000, None, int(50 * MIN)),
             _episode("2", "/tv/Show/S01/Show S01E01 - 1080p HDTV.mkv", 1920, 1080, 1_900_000_000,
                      ["slo", "cze", "eng"], int(50 * MIN))]
    decision = dd.determine_items_to_delete(["1", "2"], items, LANG_PRIO)
    _assert_held(decision, "1", ["2"])
    assert dd.merge_audio_first_reason(decision["keep"], decision["delete"][0]) is None


def _true_detective(item_id, path, codec, width, height, video_bitrate, channels, language, size):
    audio = {"Type": "Audio", "Codec": "ac3", "BitRate": 384_000, "Channels": channels}
    if language:
        audio["Language"] = language
    return {"Id": item_id, "Name": "Night Finds You", "SeriesName": "True Detective", "Path": path, "Size": size,
            "RunTimeTicks": 34_610_000_000, "MediaStreams": [
                {"Type": "Video", "Codec": codec, "Width": width, "Height": height, "BitRate": video_bitrate}, audio]}


def test_true_detective_pack_with_untagged_czech_audio_is_held_and_reported():
    """True Detective S02E01: the 1080p WEB-DL "CZ" copy has one untagged audio track, the old
    720p copy a tagged Czech one, and language priority prefers the old copy. The pair was
    skipped without a report row; stream values are the real ones."""
    folder = "/Movies/Serials/--- PREBIEHAJUCE ---/True Detective/S02"
    items = [_true_detective("21582574", f"{folder}/True Detective S02E01 - 1080p WEB-DL CZ.mkv",
                             "hevc", 1920, 1080, 3_111_751, 2, None, 1_346_205_888),
             _true_detective("21430853", f"{folder}/True Detective S02E01 - 480p.mkv",
                             "h264", 1280, 720, 4_085_884, 6, "cze", 1_767_716_963)]
    decision = dd.determine_items_to_delete(["21430853", "21582574"], items, LANG_PRIO)
    _assert_held(decision, "21582574", ["21430853"])


def test_tagged_priority_language_still_decides_normally():
    items = [_episode("1", "/tv/Duckula/S01/Duckula S01E09 - 720p BluRay CZ.mkv", 1280, 720, 383_000_000, "cze"),
             _episode("2", "/tv/Duckula/S01/Duckula S01E09 - 480p x264.mp4", 640, 480, 159_000_000, "eng")]
    decision = dd.determine_items_to_delete(["1", "2"], items, ["sk", "cs", "eng"])
    assert decision["keep"]["id"] == "1"
    assert [d["id"] for d in decision["delete"]] == ["2"]
