"""Merge audio first (Fuki 2026-10-13/14): dedupe keeps both copies when deleting one would lose a
language from --lang-prio or the better picture, so the two can be merged into one file.

The 2026-10-14 run removed nine English AHS S07 copies (Czech BluRay kept, English gone) and five
Regular Show S01 1080p English copies (720p Czech kept). Every pair below is from that run.
"""

from __future__ import annotations

from emby_dedupe.api import deduplication as dd
from emby_dedupe.reports import clusters

PRIO = ["slo", "sk", "cze", "ces", "eng"]          # scripts/run-dedupe.sh --lang-prio


def _item(item_id, langs, width, height, prio=None, **extra):
    item = {"id": item_id, "quality_description": {"audio": {"languages": langs}, "video": {"width": width, "height": height}}}
    if prio is not None:
        item["language_priority_list"] = prio
    item.update(extra)
    return item


def test_ahs_s07_english_copy_is_held():
    keep = _item("bluray-cz", ["cze"], 1920, 1080, PRIO)
    reason = dd.merge_audio_first_reason(_item("hdtv-en", ["eng"], 1280, 720), keep)
    assert reason is not None
    assert reason.startswith(dd.MERGE_AUDIO_FIRST)
    assert "has en audio" in reason


def test_regular_show_1080p_english_copy_is_held():
    keep = _item("720p-cz", ["bul", "cze", "eng", "hun", "rum", "rus"], 1280, 720, PRIO)
    reason = dd.merge_audio_first_reason(_item("1080p-en", ["eng"], 1920, 1080), keep)
    assert reason is not None
    assert "better picture (1080p vs 720p)" in reason


def test_extra_dubs_outside_lang_prio_never_hold():
    keep = _item("1080p", ["cze", "eng"], 1920, 1080, PRIO)
    assert dd.merge_audio_first_reason(_item("720p-pack", ["bul", "cze", "eng", "hun", "rus"], 1280, 720), keep) is None


def test_removals_that_lose_nothing_still_pass():
    # SAS Rogue Heroes S02: 1080p CZ+EN kept, 1080p EN deleted; Angel Heart: 1080p CZ+EN kept, 720p CZ deleted
    assert dd.merge_audio_first_reason(_item("web-en", ["eng"], 1920, 1080), _item("cz", ["cze", "eng"], 1920, 1080, PRIO)) is None
    assert dd.merge_audio_first_reason(_item("720p", ["cze"], 1280, 720), _item("1080p", ["cze", "eng"], 1920, 1080, PRIO)) is None


def test_untagged_audio_never_holds():
    keep = _item("cz", ["cze"], 1920, 1080, PRIO)
    for langs in (["und"], ["unknown"], [""], []):
        assert dd.merge_audio_first_reason(_item("x", langs, 1280, 720), keep) is None


def test_language_codes_are_normalised():
    keep = _item("cz", ["ces", "slo"], 1920, 1080, PRIO)
    assert dd.merge_audio_first_reason(_item("x", ["cze", "sk"], 1920, 1080), keep) is None


def test_without_lang_prio_only_the_picture_rule_applies():
    keep = _item("cz", ["cze"], 1920, 1080, prio=None)
    assert dd.merge_audio_first_reason(_item("en", ["eng"], 1280, 720), keep) is None
    assert "better picture (4K vs 1080p)" in dd.merge_audio_first_reason(_item("en4k", ["eng"], 3840, 2160), keep)


def test_cropped_widescreen_keeps_its_class():
    # a 2.39:1 1080p film is 1920x800; it must not count as 720p against a 1080p copy
    keep = _item("scope", ["cze"], 1920, 800, PRIO)
    assert dd.merge_audio_first_reason(_item("full", ["cze"], 1920, 1080), keep) is None
    assert dd._resolution_class(keep) == (3, "1080p")


def test_unknown_keeper_resolution_does_not_hold():
    assert dd.merge_audio_first_reason(_item("x", ["cze"], 1920, 1080), _item("k", ["cze"], 0, 0, PRIO)) is None


def test_content_conflict_wins_over_merge_reason():
    keep = _item("k", ["cze"], 1920, 1080, PRIO, provider_ids={"Imdb": "tt1"}, runtime_ticks=10)
    item = _item("d", ["eng"], 1280, 720, provider_ids={"Imdb": "tt2"}, runtime_ticks=10)
    assert "provider-id-conflict" in dd._refusal_reason(item, keep)


def test_delete_time_guard_refuses_and_never_marks_fold_safe():
    keep = _item("k", ["cze"], 1920, 1080, PRIO, runtime_ticks=26_000_000_000, path="/S/AHS/S07/a.mkv")
    item = _item("d", ["eng"], 1280, 720, runtime_ticks=26_000_000_000, path="/S/AHS/S07/b.mkv")
    reason = dd._refuse_different_content(item, keep)
    assert reason is not None
    assert reason.startswith(dd.MERGE_AUDIO_FIRST)
    assert item["deletion_result"]["status"] == "skipped_unsafe"
    assert "fold_safe_candidate" not in item


def test_report_names_the_merge_reason():
    reason = dd.merge_audio_first_reason(_item("en", ["eng"], 1280, 720), _item("cz", ["cze"], 1920, 1080, PRIO))
    assert clusters._reason_code(reason) == "merge_audio"
    assert clusters._reason_text("merge_audio", [], reason) == "merge audio first"


# --------------------------------------------------------------------------- #
# Real item shape (2026-10-14 dry run): the picture hold must see the Emby stream's Width/Height.
# The tests above build quality_description by hand; the dry run after the split-series merge still
# chose to delete a 4K HDR A Perfect Planet S01E05 (1080p CZ kept) and a 1080p Regular Show S01E07
# Remux, because metadata._extract_video_quality never carried the width/height.
# --------------------------------------------------------------------------- #
def _emby_item(item_id, langs, width, height, prio=None):
    from emby_dedupe.api.metadata import _extract_video_quality

    stream = {"Type": "Video", "Codec": "hevc", "Width": width, "Height": height, "DisplayTitle": f"{height}p"}
    item = {"id": item_id, "quality_description": {"audio": {"languages": langs}, "video": _extract_video_quality(stream)}}
    if prio is not None:
        item["language_priority_list"] = prio
    return item


def test_video_quality_carries_width_and_height():
    from emby_dedupe.api.metadata import _extract_video_quality

    vq = _extract_video_quality({"Codec": "hevc", "Width": 3840, "Height": 2160})
    assert (vq["width"], vq["height"]) == (3840, 2160)
    assert (_extract_video_quality(None)["width"], _extract_video_quality(None)["height"]) == (0, 0)


def test_a_perfect_planet_4k_copy_is_held_on_the_real_item_shape():
    keep = _emby_item("1080p-cz", ["cze", "eng"], 1920, 1080, PRIO)
    reason = dd.merge_audio_first_reason(_emby_item("4k-hdr-en", ["eng"], 3840, 2160), keep)
    assert reason is not None
    assert "better picture (4K vs 1080p)" in reason


def test_regular_show_remux_is_held_on_the_real_item_shape():
    keep = _emby_item("720p-cz", ["bul", "cze", "eng", "hun", "rum", "rus"], 1280, 720, PRIO)
    reason = dd.merge_audio_first_reason(_emby_item("1080p-remux-en", ["eng"], 1920, 1080), keep)
    assert reason is not None
    assert "better picture (1080p vs 720p)" in reason
