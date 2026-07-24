"""Cross-path tests for the unified quality-scoring model (emby_dedupe.api.scoring).

These pin the behaviours the scoring-unification refactor exists to guarantee:

* the dedupe rating (``metadata._calculate_quality_rating`` / ``rate_media_items``) and
  the check scoring (``quality_compare.ExistingQuality.calculate_score``) agree on the
  ORDERING of the same pair of files (they diverged before, ranking opposite);
* duration-aware bitrate estimation for TV episodes (no false starved-bitrate demotion);
* an unknown, un-estimable bitrate is treated as NEUTRAL, never "defective".
"""

from emby_dedupe.api.metadata import _calculate_quality_rating, rate_media_items
from emby_dedupe.api.quality_compare import (
    ExistingQuality,
    ProposedQuality,
    compare_quality,
)
from emby_dedupe.api.scoring import (
    bitrate_floor_mbps,
    codec_efficiency_ratio,
    compute_quality_breakdown,
    compute_quality_score,
    duration_minutes_from_ticks,
    estimate_bitrate_mbps_from_size,
    format_score_summary,
    hdr_bonus_from_string,
    hdr_marker_in_text,
)


# --------------------------------------------------------------------------- #
# Item builders (consumed identically by both scoring paths)
# --------------------------------------------------------------------------- #
def _4k_hevc_hdr10_webdl_25mbps():
    """4K HEVC HDR10 WEB-DL at 25 Mbps — the file the unify refactor must prefer."""
    return {
        "Id": "4k",
        "Name": "Movie",
        "Path": "/movies/Movie.2160p.WEB-DL.HEVC.mkv",
        "MediaStreams": [
            {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc",
             "BitRate": 25_000_000, "VideoRange": "HDR10"},
            {"Type": "Audio", "Channels": 6},
        ],
        "Size": 16_000_000_000,
        "Bitrate": 25_000_000,
    }


def _1080p_h264_remux_30mbps():
    """1080p H.264 BluRay REMUX at 30 Mbps — higher bitrate/source, lower resolution."""
    return {
        "Id": "1080",
        "Name": "Movie",
        "Path": "/movies/Movie.1080p.BluRay.REMUX.h264.mkv",
        "MediaStreams": [
            {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264",
             "BitRate": 30_000_000, "VideoRange": "SDR"},
            {"Type": "Audio", "Channels": 8},
        ],
        "Size": 30_000_000_000,
        "Bitrate": 30_000_000,
    }


def _4k_dovi_p5_item():
    """A defective 4K Dolby Vision Profile-5 file (larger, older)."""
    return {
        "Id": "p5",
        "Name": "Movie",
        "Path": "/movies/Movie.2160p.WEB-DL.DoVi.mkv",
        "MediaStreams": [
            {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc",
             "BitRate": 25_000_000, "VideoRange": "DolbyVision",
             "ExtendedVideoSubType": "DoviProfile50",
             "ExtendedVideoSubTypeDescription": "Profile 5.0"},
            {"Type": "Audio", "Channels": 6},
        ],
        "Size": 20_000_000_000,
        "Bitrate": 25_000_000,
    }


def _4k_hdr10_clean_item():
    """The clean 4K HDR10 fix of the same title (smaller)."""
    return {
        "Id": "clean",
        "Name": "Movie",
        "Path": "/movies/Movie.2160p.WEB-DL.HDR10.mkv",
        "MediaStreams": [
            {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc",
             "BitRate": 18_000_000, "VideoRange": "HDR10"},
            {"Type": "Audio", "Channels": 6},
        ],
        "Size": 14_000_000_000,
        "Bitrate": 18_000_000,
    }


def _dedupe_rating(item):
    """Score an item through the dedupe path."""
    video = next(s for s in item["MediaStreams"] if s["Type"] == "Video")
    audio = next((s for s in item["MediaStreams"] if s["Type"] == "Audio"), None)
    return _calculate_quality_rating(item, video, audio)


def _check_score(item):
    """Score an item through the check path."""
    return ExistingQuality.from_emby_item(item).calculate_score()


# --------------------------------------------------------------------------- #
# Cross-model ordering agreement
# --------------------------------------------------------------------------- #
class TestCrossModelOrdering:
    """The two scoring paths must agree on which of a pair of files is better."""

    def test_both_paths_prefer_4k_hevc_over_1080p_remux(self):
        """4K HEVC HDR10 WEB-DL 25 Mbps must beat 1080p H.264 REMUX 30 Mbps on BOTH paths.

        Before unification the dedupe path preferred the 4K (116 vs 71) while the check
        path preferred the 1080p (35.2M vs 41.1M) — opposite orderings.
        """
        four_k = _4k_hevc_hdr10_webdl_25mbps()
        hd = _1080p_h264_remux_30mbps()

        assert _dedupe_rating(four_k) > _dedupe_rating(hd)
        assert _check_score(four_k) > _check_score(hd)

        # And the dedupe pipeline entrypoint agrees.
        rated = {r["id"]: r["rating"] for r in rate_media_items([four_k, hd])}
        assert rated["4k"] > rated["1080"]

    def test_both_paths_prefer_clean_hdr10_over_dovi_p5(self):
        """A clean HDR10 copy must beat its defective DV Profile-5 sibling on BOTH paths."""
        p5 = _4k_dovi_p5_item()
        clean = _4k_hdr10_clean_item()

        assert p5["Size"] > clean["Size"]  # the defective file is larger...
        assert _dedupe_rating(clean) > _dedupe_rating(p5)  # ...yet the clean one wins
        assert _check_score(clean) > _check_score(p5)


# --------------------------------------------------------------------------- #
# Duration-aware bitrate estimation
# --------------------------------------------------------------------------- #
class TestDurationAwareBitrate:
    """A short 4K episode with a known runtime must not be falsely starved."""

    def _episode(self, *, runtime_ticks, bitrate=None):
        item = {
            "Id": "ep",
            "Name": "Episode",
            "Path": "/tv/Show/S01/Show S01E01 2160p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc",
                 "VideoRange": "HDR10"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 4_500_000_000,  # 4.5 GB
        }
        if runtime_ticks is not None:
            item["RunTimeTicks"] = runtime_ticks
        if bitrate is not None:
            item["Bitrate"] = bitrate
            item["MediaStreams"][0]["BitRate"] = bitrate
        return item

    def test_42min_4k_episode_estimated_bitrate_not_demoted(self):
        """42-min 4.5 GB 4K HEVC episode, Bitrate missing: estimate (~12.9 Mbps) is above
        the ~3.9 Mbps HEVC floor, so it must NOT be demoted as bitrate-starved."""
        # 42 minutes of runtime in Emby ticks.
        episode = self._episode(runtime_ticks=42 * 600_000_000)
        # Same item but with NO runtime → bitrate unknown & un-estimable → neutral.
        neutral = self._episode(runtime_ticks=None)
        # Same size but a runtime long enough that the estimate falls below the floor.
        starved = self._episode(runtime_ticks=200 * 600_000_000)

        episode_score = _check_score(episode)
        # Adequate estimate → full resolution credit, identical to the neutral case.
        assert episode_score == _check_score(neutral)
        # And strictly higher than the genuinely-starved (below-floor estimate) sibling.
        assert episode_score > _check_score(starved)


# --------------------------------------------------------------------------- #
# Neutral handling of a fully-unknown bitrate
# --------------------------------------------------------------------------- #
def test_unknown_bitrate_and_duration_is_neutral_not_defective():
    """Bitrate unknown AND size/duration unusable → neutral (full resolution credit),
    never treated as defective: the item still outranks a genuinely-starved sibling."""
    neutral_4k = ExistingQuality(
        id="neutral", name="x", width=3840, height=2160, codec="hevc",
        video_range="HDR10", audio_channels=6, size_bytes=0, bitrate=0,
    )
    starved_4k = ExistingQuality(
        id="starved", name="x", width=3840, height=2160, codec="hevc",
        video_range="HDR10", audio_channels=6, size_bytes=0,
        bitrate=1_500_000,  # 1.5 Mbps, below the ~3.9 Mbps HEVC 4K floor → demoted
    )
    assert neutral_4k.calculate_score() > starved_4k.calculate_score()


# --------------------------------------------------------------------------- #
# scoring.py primitives
# --------------------------------------------------------------------------- #
class TestScoringPrimitives:
    def test_hdr_bonus_from_string(self):
        assert hdr_bonus_from_string("DolbyVision") == 1.0
        assert hdr_bonus_from_string("HDR 10") == 1.0
        assert hdr_bonus_from_string("DV") == 1.0
        assert hdr_bonus_from_string("SDR") == 0.0
        assert hdr_bonus_from_string("") == 0.0
        assert hdr_bonus_from_string(None) == 0.0

    def test_hdr_marker_in_text(self):
        # explicit HDR markers in a filename → True
        assert hdr_marker_in_text("The Agency S02E07 - 2160p WEB-DL HDR CZ.mkv")
        assert hdr_marker_in_text("Movie (2026) - 2160p BluRay HDR10 CZ.mkv")
        assert hdr_marker_in_text("Movie - 2160p HDR10+ CZ.mkv")
        assert hdr_marker_in_text("Movie - 2160p Dolby Vision CZ.mkv")
        assert hdr_marker_in_text("Movie - 2160p DoVi CZ.mkv")
        assert hdr_marker_in_text("Movie - 1080p HLG.mkv")
        # no marker / SDR / false-positive traps → False
        assert not hdr_marker_in_text("The Agency S02E07 - 2160p WEB-DL CZ.mkv")
        assert not hdr_marker_in_text("Movie (2001) - DVDRip XviD.avi")  # bare DV excluded
        assert not hdr_marker_in_text("Shredder HDRdrive.mkv")           # HDR not word-bounded
        assert not hdr_marker_in_text("")
        assert not hdr_marker_in_text(None)

    def test_codec_efficiency_ratio(self):
        assert codec_efficiency_ratio("hevc") == 0.65
        assert codec_efficiency_ratio("x265") == 0.65
        assert codec_efficiency_ratio("av1") == 0.5
        assert codec_efficiency_ratio("h264") == 1.0
        assert codec_efficiency_ratio(None) == 1.0

    def test_bitrate_floor_mbps(self):
        assert bitrate_floor_mbps(2160) == 6.0
        assert bitrate_floor_mbps(1080) == 2.5
        assert bitrate_floor_mbps(720) == 1.2
        assert bitrate_floor_mbps(480) == 0.0

    def test_estimate_bitrate_mbps_from_size(self):
        # 4.5 GB over 42 minutes ≈ 12.9 Mbps (90% video).
        mbps = estimate_bitrate_mbps_from_size(4_500_000_000, 42)
        assert 12.0 < mbps < 13.5
        assert estimate_bitrate_mbps_from_size(0, 42) == 0.0
        assert estimate_bitrate_mbps_from_size(4_500_000_000, 0) == 0.0

    def test_duration_minutes_from_ticks(self):
        assert duration_minutes_from_ticks(42 * 600_000_000) == 42.0
        assert duration_minutes_from_ticks(None) is None
        assert duration_minutes_from_ticks(0) is None

    def test_compute_quality_score_neutral_beats_starved(self):
        common = dict(
            res_megapixels=8.29, height=2160, hdr_bonus=1.0, audio_channels=6,
            date_rating=0, codec="hevc", source_multiplier=1.0, ai_multiplier=1.0,
            is_dovi_p5=False,
        )
        neutral = compute_quality_score(video_bitrate_mbps=0.0, **common)  # unknown → neutral
        starved = compute_quality_score(video_bitrate_mbps=1.5, **common)  # below floor
        assert neutral > starved  # neutral keeps full resolution credit

    def test_compute_quality_score_dovi_p5_collapses(self):
        common = dict(
            res_megapixels=8.29, height=2160, hdr_bonus=1.0, video_bitrate_mbps=25.0,
            audio_channels=6, date_rating=0, codec="hevc", source_multiplier=1.0,
            ai_multiplier=1.0,
        )
        clean = compute_quality_score(is_dovi_p5=False, **common)
        p5 = compute_quality_score(is_dovi_p5=True, **common)
        assert p5 < clean
        assert p5 > 0  # positive penalty keeps ratio math intact


# --------------------------------------------------------------------------- #
# Per-factor score breakdown (explainability)
# --------------------------------------------------------------------------- #
class TestScoreBreakdown:
    """The breakdown must expose the spec keys and reconstruct the total exactly."""

    _INPUTS = dict(
        res_megapixels=8.29, height=2160, hdr_bonus=1.0, video_bitrate_mbps=25.0,
        audio_channels=6, date_rating=1_700_000_000, codec="hevc",
        source_multiplier=1.15, ai_multiplier=0.7, is_dovi_p5=False,
    )

    def test_breakdown_has_spec_keys(self):
        bd = compute_quality_breakdown(**self._INPUTS)
        assert set(bd) >= {
            "resolution_credit", "resolution_raw", "starved_demotion",
            "hdr", "bitrate", "audio", "date", "multipliers", "total",
        }
        assert set(bd["multipliers"]) == {"source", "ai", "dovi_p5"}
        assert bd["resolution_raw"] == 8.29
        assert bd["starved_demotion"] is False

    def test_breakdown_reconstructs_total(self):
        bd = compute_quality_breakdown(**self._INPUTS)
        base = bd["resolution_credit"] + bd["hdr"] + bd["bitrate"] + bd["audio"] + bd["date"]
        m = bd["multipliers"]
        assert bd["total"] == base * m["source"] * m["ai"] * m["dovi_p5"]

    def test_starved_demotion_flag_set(self):
        # 1.5 Mbps 4K HEVC is below the ~3.9 Mbps floor → demoted, flag True.
        starved = compute_quality_breakdown(
            **{**self._INPUTS, "video_bitrate_mbps": 1.5}
        )
        assert starved["starved_demotion"] is True
        assert starved["resolution_credit"] < starved["resolution_raw"] * 10  # credit reduced

    def test_compute_quality_score_equals_breakdown_total(self):
        assert compute_quality_score(**self._INPUTS) == compute_quality_breakdown(**self._INPUTS)["total"]

    def test_format_score_summary(self):
        summary = format_score_summary(compute_quality_breakdown(**self._INPUTS))
        assert summary.startswith("score ")
        assert "res " in summary and "HDR " in summary and "bitrate " in summary
        assert "×1.15 BluRay" in summary  # source multiplier + label
        assert "×0.70 AI-upscale" in summary

    def test_rated_items_carry_breakdown_and_summary(self):
        item = {
            "Id": "1", "Name": "Movie", "Path": "/movies/Movie.2160p.WEB-DL.HEVC.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc",
                 "BitRate": 25_000_000, "VideoRange": "HDR10"},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
            ],
            "Size": 16_000_000_000, "Bitrate": 25_000_000,
        }
        rated = rate_media_items([item])[0]
        # Top-level breakdown present, and its total equals the rating.
        assert rated["score_breakdown"]["total"] == rated["rating"]
        # One-line summary present top-level and mirrored into quality_description.
        assert rated["score_summary"].startswith("score ")
        assert rated["quality_description"]["score_summary"] == rated["score_summary"]

    def test_comparison_to_dict_includes_rounded_breakdowns(self):
        existing = {
            "Id": "e", "Name": "Movie", "Path": "/movies/Movie.1080p.BluRay.REMUX.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264",
                 "BitRate": 30_000_000, "VideoRange": "SDR"},
                {"Type": "Audio", "Channels": 8, "Language": "eng"},
            ],
            "Size": 30_000_000_000, "Bitrate": 30_000_000,
        }
        proposed = ProposedQuality(
            resolution="2160p", codec="hevc", hdr="HDR10", bitrate_kbps=25000,
            size_mb=16000, audio_languages=["eng"],
        )
        d = compare_quality(proposed, [existing]).to_dict()
        qc = d["quality_comparison"]
        assert qc["existing_breakdown"]["total"] > 0
        assert qc["proposed_breakdown"]["total"] > qc["existing_breakdown"]["total"]  # 4K wins
        # Rounded to 2 decimals for compact JSON.
        assert qc["existing_breakdown"]["resolution_credit"] == round(
            qc["existing_breakdown"]["resolution_credit"], 2
        )
        # starved_demotion stays a bool through rounding.
        assert isinstance(qc["existing_breakdown"]["starved_demotion"], bool)
        # ExistingQuality.to_dict also carries the breakdown.
        assert "score_breakdown" in d["existing"]

    def test_calculate_score_caches_breakdown(self):
        eq = ExistingQuality(
            id="1", name="x", width=3840, height=2160, codec="hevc",
            video_range="HDR10", audio_channels=6, bitrate=25_000_000,
        )
        assert eq._breakdown_cache is None  # not computed yet
        first = eq.calculate_score()
        assert eq._breakdown_cache is not None  # cached on first call
        assert eq.raw_score == first            # legacy slot populated
        assert eq.score_breakdown() is eq._breakdown_cache  # same object reused
        assert eq.calculate_score() == first

    def test_detection_runs_once_per_item_in_rate_media_items(self, mocker):
        """The double detect_source_quality/detect_ai_upscale pass is collapsed to one."""
        src = mocker.patch("emby_dedupe.api.metadata.detect_source_quality", return_value=1.0)
        ai = mocker.patch("emby_dedupe.api.metadata.detect_ai_upscale", return_value=False)
        item = {
            "Id": "1", "Name": "Movie", "Path": "/movies/Movie.2160p.WEB-DL.HEVC.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc",
                 "BitRate": 25_000_000, "VideoRange": "HDR10"},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
            ],
            "Size": 16_000_000_000, "Bitrate": 25_000_000,
        }
        rate_media_items([item])
        assert src.call_count == 1
        assert ai.call_count == 1
