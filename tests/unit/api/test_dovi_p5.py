"""Tests for Dolby Vision Profile 5 ("green/pink") detection and dedupe penalty.

A DV Profile 5 file renders green/pink on non-DV playback paths, so when a clean
(non-defective) copy of the same item exists it must be kept even though the DV P5
file is usually LARGER. These tests pin that behaviour for a DV P5 episode and its
HDR10 conversion across every audio-language combination.
"""

from emby_dedupe.api.deduplication import determine_items_to_delete
from emby_dedupe.api.metadata import (
    _calculate_quality_rating,
    _extract_video_quality,
    _is_dovi_profile5,
)

# Example language-priority order (Slovak/Czech/English preference).
LANG_PRIO = ["slo", "sk", "cze", "ces", "eng"]


def _video_stream(*, p5: bool, langs=("cze", "eng")):
    """A 4K HEVC video stream, either DV Profile 5 (defective) or HDR10 (clean)."""
    if p5:
        return {
            "Type": "Video",
            "Codec": "hevc",
            "Width": 3840,
            "Height": 2160,
            "BitRate": 14000000,
            "BitDepth": 10,
            "IsInterlaced": False,
            "DisplayTitle": "4K Dolby Vision HEVC",
            "VideoRange": "DolbyVision",
            "ExtendedVideoType": "DolbyVision",
            "ExtendedVideoSubType": "DoviProfile50",
            "ExtendedVideoSubTypeDescription": "Profile 5.0",
        }
    return {
        "Type": "Video",
        "Codec": "hevc",
        "Width": 3840,
        "Height": 2160,
        "BitRate": 11000000,
        "BitDepth": 10,
        "IsInterlaced": False,
        "DisplayTitle": "4K HDR 10 HEVC",
        "VideoRange": "HDR 10",
        "ExtendedVideoType": "Hdr10",
        "ExtendedVideoSubType": "Hdr10",
        "ExtendedVideoSubTypeDescription": "HDR 10",
        "ColorSpace": "bt2020nc",
        "ColorTransfer": "smpte2084",
        "ColorPrimaries": "bt2020",
    }


def _audio_streams(langs):
    return [
        {"Type": "Audio", "Codec": "eac3", "Channels": 6, "BitRate": 768000, "Language": lang}
        for lang in langs
    ]


def _p5_item(langs=("cze", "eng")):
    """The defective DV P5 file — LARGER (~7.45 GB) and OLDER."""
    return {
        "Id": "1001",
        "Name": "Episode One",
        "SeriesName": "Example Show",
        "Type": "Episode",
        "ParentIndexNumber": 3,
        "IndexNumber": 1,
        "Path": "/media/TV/Example Show/Season 03/Example Show S03E01 - 2160p WEB-DL DoVi.mkv",
        "Size": 7_999_000_000,
        "Bitrate": 14000000,
        "DateCreated": "2025-08-07T05:27:00.0000000Z",
        "MediaStreams": [_video_stream(p5=True)] + _audio_streams(langs),
    }


def _hdr10_item(langs=("cze", "eng")):
    """The clean HDR10 fix — SMALLER (~5.75 GB) and NEWER."""
    return {
        "Id": "1002",
        "Name": "Episode One",
        "SeriesName": "Example Show",
        "Type": "Episode",
        "ParentIndexNumber": 3,
        "IndexNumber": 1,
        "Path": "/media/TV/Example Show/Season 03/Example Show S03E01 - HDR10.mkv",
        "Size": 6_170_000_000,
        "Bitrate": 11000000,
        "DateCreated": "2026-06-24T12:36:00.0000000Z",
        "MediaStreams": [_video_stream(p5=False)] + _audio_streams(langs),
    }


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def test_detects_dovi_profile5():
    assert _is_dovi_profile5(_video_stream(p5=True)) is True


def test_hdr10_is_not_flagged():
    assert _is_dovi_profile5(_video_stream(p5=False)) is False


def test_dovi_profile8_is_not_flagged():
    # P8 carries an HDR10 fallback -> no green/pink -> must NOT be flagged.
    p8 = {
        "Type": "Video",
        "VideoRange": "DolbyVision",
        "ExtendedVideoType": "DolbyVision",
        "ExtendedVideoSubType": "DoviProfile81",
        "ExtendedVideoSubTypeDescription": "Profile 8.1",
    }
    assert _is_dovi_profile5(p8) is False


def test_detection_fallback_without_subtype():
    # Older builds may omit ExtendedVideoSubType; fall back to range + description.
    stream = {
        "Type": "Video",
        "VideoRange": "DolbyVision",
        "ExtendedVideoSubTypeDescription": "Profile 5.0",
    }
    assert _is_dovi_profile5(stream) is True


def test_detection_handles_missing_stream():
    assert _is_dovi_profile5(None) is False


# --------------------------------------------------------------------------- #
# quality_description surfaces the flag (for the HTML badge)
# --------------------------------------------------------------------------- #
def test_extract_video_quality_exposes_dv_fields():
    vq = _extract_video_quality(_video_stream(p5=True))
    assert vq["is_dovi_p5"] is True
    assert vq["dv_profile"] == "DoviProfile50"
    assert vq["video_range"] == "DolbyVision"


def test_extract_video_quality_clean_file():
    vq = _extract_video_quality(_video_stream(p5=False))
    assert vq["is_dovi_p5"] is False
    assert vq["video_range"] == "HDR 10"


def test_extract_video_quality_defaults_include_dv_keys():
    vq = _extract_video_quality(None)
    assert vq["is_dovi_p5"] is False
    assert "dv_profile" in vq and "video_range" in vq


# --------------------------------------------------------------------------- #
# Rating penalty
# --------------------------------------------------------------------------- #
def test_p5_rated_below_clean_sibling_despite_being_larger():
    p5, hdr10 = _p5_item(), _hdr10_item()
    p5_video = p5["MediaStreams"][0]
    hdr10_video = hdr10["MediaStreams"][0]
    audio = p5["MediaStreams"][1]

    p5_rating = _calculate_quality_rating(p5, p5_video, audio)
    hdr10_rating = _calculate_quality_rating(hdr10, hdr10_video, audio)

    assert p5["Size"] > hdr10["Size"]          # the defective file is larger...
    assert p5_rating < hdr10_rating            # ...yet rates lower (penalty applied)


# --------------------------------------------------------------------------- #
# End-to-end: the DV P5 -> HDR10 dedupe decision, across language combinations.
#
# The HDR10 fix is a convert-in-place remux of the DV P5 source, so the two files
# always carry IDENTICAL audio tracks. We therefore vary the (shared) language set
# and assert the clean file is kept every time — the deciding factor is the DV P5
# quality penalty, never the file size.
# --------------------------------------------------------------------------- #
def _assert_keeps_clean(langs):
    result = determine_items_to_delete(
        ["1001", "1002"],
        [_p5_item(langs), _hdr10_item(langs)],
        lang_priorities=LANG_PRIO,
    )
    assert result["keep"]["id"] == "1002", f"kept wrong file for langs={langs}"
    assert [d["id"] for d in result["delete"]] == ["1001"]


def test_gold_both_have_priority_languages():
    """Both files carry cze+eng (the common dual-dub case)."""
    _assert_keeps_clean(("cze", "eng"))


def test_slovak_plus_english():
    _assert_keeps_clean(("slo", "eng"))


def test_english_only():
    _assert_keeps_clean(("eng",))


def test_czech_only():
    # 'cze' normalises to 'cs' (not in the priority list) -> both files are
    # no-priority -> decided purely on the DV P5 quality penalty.
    _assert_keeps_clean(("cze",))


def test_no_priority_language_at_all():
    _assert_keeps_clean(("ger",))


def test_decision_is_independent_of_no_lang_priorities():
    # Even with no language prioritisation at all, the clean file is kept.
    result = determine_items_to_delete(
        ["1001", "1002"], [_p5_item(), _hdr10_item()], lang_priorities=None
    )
    assert result["keep"]["id"] == "1002"
    assert [d["id"] for d in result["delete"]] == ["1001"]
