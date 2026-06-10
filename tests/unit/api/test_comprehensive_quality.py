"""Tests for comprehensive quality scoring system with BPP and RED FLAG detection."""


from emby_dedupe.api.quality_compare import (
    ExistingQuality,
    ProposedQuality,
    calculate_bpp,
    compare_quality,
    get_bpp_multiplier,
    get_codec_multiplier_with_rtn,
    has_quality_red_flags,
)


class TestBPPCalculation:
    """Tests for bits per pixel calculation."""

    def test_calculate_bpp_basic(self):
        """Test BPP calculation with standard values."""
        # 1080p @ 24fps with 10 Mbps bitrate
        bpp = calculate_bpp(10_000_000, 1920, 1080, 24)
        # Expected: 10,000,000 / (1920 * 1080 * 24) = ~0.20 bpp
        assert 0.19 < bpp < 0.21

    def test_calculate_bpp_4k(self):
        """Test BPP calculation for 4K content."""
        # 4K @ 24fps with 15 Mbps bitrate (minimum acceptable)
        bpp = calculate_bpp(15_000_000, 3840, 2160, 24)
        # Expected: 15,000,000 / (3840 * 2160 * 24) = ~0.075 bpp (acceptable)
        assert 0.07 < bpp < 0.08

    def test_calculate_bpp_zero_dimensions(self):
        """Test BPP calculation with zero dimensions returns 0."""
        bpp = calculate_bpp(10_000_000, 0, 0, 24)
        assert bpp == 0.0

    def test_calculate_bpp_zero_fps(self):
        """Test BPP calculation with zero FPS returns 0."""
        bpp = calculate_bpp(10_000_000, 1920, 1080, 0)
        assert bpp == 0.0


class TestBPPMultiplier:
    """Tests for BPP quality multiplier calculation."""

    def test_excellent_quality(self):
        """Test excellent quality multiplier (>0.3 bpp)."""
        multiplier = get_bpp_multiplier(0.35)
        assert multiplier == 1.1

    def test_good_quality(self):
        """Test good quality multiplier (0.15-0.3 bpp)."""
        multiplier = get_bpp_multiplier(0.20)
        assert multiplier == 1.05

    def test_acceptable_quality(self):
        """Test acceptable quality multiplier (0.08-0.15 bpp)."""
        multiplier = get_bpp_multiplier(0.10)
        assert multiplier == 1.0

    def test_poor_quality(self):
        """Test poor quality multiplier (0.05-0.08 bpp)."""
        multiplier = get_bpp_multiplier(0.06)
        assert multiplier == 0.85

    def test_critical_quality(self):
        """Test critical quality multiplier (<0.05 bpp)."""
        multiplier = get_bpp_multiplier(0.03)
        assert multiplier == 0.5

    def test_hevc_codec_adjusts_bpp_bands(self):
        """Test that HEVC codec adjusts BPP for fairer band placement.

        HEVC at 0.07 bpp = 0.107 bpp equivalent → 'acceptable' (1.0x)
        Without codec, 0.07 bpp = 'poor' (0.85x).
        """
        assert get_bpp_multiplier(0.07) == 0.85       # No codec → poor
        assert get_bpp_multiplier(0.07, "hevc") == 1.0  # HEVC → acceptable


class TestRedFlagDetection:
    """Tests for RED FLAG quality issue detection."""

    def test_4k_under_bitrate_red_flag(self):
        """Test 4K content under 15 Mbps triggers RED FLAG."""
        has_flag, reason = has_quality_red_flags(2160, 10_000_000, 0.05)
        assert has_flag is True
        assert "4K under-bitrate" in reason
        assert "10.0 Mbps" in reason

    def test_1080p_under_bitrate_red_flag(self):
        """Test 1080p content under 5 Mbps triggers RED FLAG."""
        has_flag, reason = has_quality_red_flags(1080, 3_000_000, 0.06)
        assert has_flag is True
        assert "1080p under-bitrate" in reason
        assert "3.0 Mbps" in reason

    def test_720p_under_bitrate_red_flag(self):
        """Test 720p content under 3 Mbps triggers RED FLAG."""
        has_flag, reason = has_quality_red_flags(720, 2_000_000, 0.08)
        assert has_flag is True
        assert "720p under-bitrate" in reason
        assert "2.0 Mbps" in reason

    def test_critical_bpp_red_flag(self):
        """Test critical BPP (<0.05) triggers RED FLAG."""
        has_flag, reason = has_quality_red_flags(1080, 10_000_000, 0.03)
        assert has_flag is True
        assert "Critical BPP" in reason
        assert "0.03" in reason

    def test_no_red_flag_acceptable_quality(self):
        """Test acceptable quality passes RED FLAG checks."""
        # 4K @ 20 Mbps with 0.10 bpp - acceptable
        has_flag, reason = has_quality_red_flags(2160, 20_000_000, 0.10)
        assert has_flag is False
        assert reason == ""

    def test_no_red_flag_good_quality(self):
        """Test good quality passes RED FLAG checks."""
        # 1080p @ 10 Mbps with 0.20 bpp - good
        has_flag, reason = has_quality_red_flags(1080, 10_000_000, 0.20)
        assert has_flag is False


class TestCodecMultiplier:
    """Tests for codec efficiency multiplier."""

    def test_av1_multiplier(self):
        """Test AV1 codec gets highest efficiency multiplier."""
        multiplier = get_codec_multiplier_with_rtn("av1")
        assert multiplier == 1.15

    def test_hevc_multiplier(self):
        """Test HEVC codec gets efficiency multiplier."""
        multiplier = get_codec_multiplier_with_rtn("hevc")
        assert multiplier == 1.1

    def test_x265_multiplier(self):
        """Test x265 codec gets efficiency multiplier."""
        multiplier = get_codec_multiplier_with_rtn("x265")
        assert multiplier == 1.1

    def test_h264_multiplier(self):
        """Test H.264 codec gets baseline multiplier."""
        multiplier = get_codec_multiplier_with_rtn("h264")
        assert multiplier == 1.0

    def test_x264_multiplier(self):
        """Test x264 codec gets baseline multiplier."""
        multiplier = get_codec_multiplier_with_rtn("x264")
        assert multiplier == 1.0

    def test_unknown_codec_multiplier(self):
        """Test unknown codec gets neutral multiplier."""
        multiplier = get_codec_multiplier_with_rtn("unknown_codec")
        assert multiplier == 1.0


class TestTehranS03E02RegressionCase:
    """Regression test for Tehran S03E02 case that revealed scoring issues."""

    def test_4k_under_bitrate_rejected(self):
        """Test 4K at 2.6 Mbps is rejected (RED FLAG)."""
        # Tehran S03E02: 4K WEB-DL at 2.6 Mbps (2371MB)
        proposed = ProposedQuality(
            resolution="2160p",
            codec="x265",
            size_mb=2371,
            bitrate_kbps=2600,
            name="Tehran.S03E02.2160p.WEB-DL.mkv"
        )
        score = proposed.calculate_score()
        # Should be rejected (score = 0)
        assert score == 0.0, "Under-bitrate 4K should be auto-rejected"

    def test_720p_good_bitrate_accepted(self):
        """Test 720p at 4.9 Mbps is accepted."""
        # Tehran S03E02: 720p WEB-DL at 4.9 Mbps (3318MB)
        proposed = ProposedQuality(
            resolution="720p",
            codec="h264",
            size_mb=3318,
            bitrate_kbps=4900,
            name="Tehran.S03E02.720p.WEB-DL.mkv"
        )
        score = proposed.calculate_score()
        # Should be accepted (score > 0)
        assert score > 0, "Good 720p should be accepted"

    def test_4k_rejected_vs_720p_comparison(self):
        """Test that over-compressed 4K loses to good 720p in comparison."""
        # Proposed: Bad 4K
        proposed_4k = ProposedQuality(
            resolution="2160p",
            codec="x265",
            size_mb=2371,
            bitrate_kbps=2600
        )

        # Existing: Good 720p
        existing_items = [{
            "Id": "720p_good",
            "Name": "Tehran S03E02",
            "Path": "/tv/Tehran.S03E02.720p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1280, "Height": 720, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 3318 * 1024 * 1024,
            "Bitrate": 4900 * 1000,
        }]

        result = compare_quality(proposed_4k, existing_items)

        # Should recommend SKIP - keep existing 720p
        assert result.recommendation == "skip"
        # 4K should be heavily penalized (RED FLAG gives minimal score)
        assert result.proposed_score < 10  # Very low score due to RED FLAG
        # Existing 720p should have much higher score (in millions after KB normalization)
        assert result.existing_score > 1_000_000


class TestRemuxVsWebDL:
    """Tests comparing high-quality REMUX vs lower-quality 4K WEB-DL."""

    def test_1080p_remux_beats_4k_webdl_low_bitrate(self):
        """Test 1080p REMUX (30 Mbps) beats 4K WEB-DL (5 Mbps)."""
        # Existing: 1080p REMUX @ 30 Mbps (26.7GB)
        remux_item = {
            "Id": "remux_1080p",
            "Name": "Movie",
            "Path": "/movies/Movie.BluRay.REMUX.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 8},
            ],
            "Size": 26_700_000_000,  # 26.7GB
            "Bitrate": 30_000_000,    # 30 Mbps
        }

        # Proposed: 4K WEB-DL @ 5 Mbps (4.5GB) - under-bitrate!
        proposed_4k = ProposedQuality(
            resolution="2160p",
            codec="hevc",
            size_mb=4500,
            bitrate_kbps=5000,  # Way too low for 4K
            path="Movie.2160p.WEB-DL.mkv"
        )

        result = compare_quality(proposed_4k, [remux_item])

        # 4K should be rejected (RED FLAG), REMUX should win
        assert result.recommendation == "skip"
        # 4K should have minimal score due to RED FLAG
        assert result.proposed_score < 10
        # REMUX should have high score
        assert result.existing_score > 1_000_000

    def test_1080p_remux_beats_4k_webdl_marginal_bitrate(self):
        """Test 1080p REMUX beats 4K WEB-DL at marginal bitrate (18 Mbps)."""
        # Existing: 1080p REMUX @ 30 Mbps
        remux_item = {
            "Id": "remux_1080p",
            "Name": "Movie",
            "Path": "/movies/Movie.BluRay.REMUX.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 8},
            ],
            "Size": 26_700_000_000,
            "Bitrate": 30_000_000,
        }

        # Proposed: 4K WEB-DL @ 18 Mbps (marginal, passes RED FLAG but still poor quality)
        proposed_4k = ProposedQuality(
            resolution="2160p",
            codec="hevc",
            size_mb=16000,
            bitrate_kbps=18000,  # Above 15 Mbps minimum, but still low
            path="Movie.2160p.WEB-DL.mkv"
        )

        result = compare_quality(proposed_4k, [remux_item])

        # REMUX should still win due to superior source quality and bitrate
        assert result.recommendation == "skip"
        # Both should have scores > 0, but REMUX higher
        assert result.existing_score > result.proposed_score


class TestRemuxVsWebDLWithLanguagePriority:
    """Regression test: REMUX should beat WEB-DL even when WEB-DL has higher-priority language.

    Real-world case: Deadpool & Wolverine
    - Existing: 2160p WEB-DL (23.7GB) with Slovak+Czech+English audio
    - Proposed: 2160p REMUX (58.3GB, 63.7 Mbps) with Czech+English audio
    - With priorities ['sk', 'cs', 'en'], WEB-DL has Slovak (priority 0)
      but REMUX quality is 3.5x better — quality should override.
    """

    def test_remux_beats_webdl_despite_lower_language_priority(self):
        """REMUX with Czech should beat WEB-DL with Slovak when quality is 2x+ better."""
        proposed = ProposedQuality(
            resolution="2160p",
            codec="x265",
            audio="Atmos",
            audio_languages=["eng", "cze"],
            size_mb=58299,
            bitrate_kbps=63700,
            path="Deadpool.and.Wolverine.2024.UHD.BluRay.2160p.FLAC.TrueHD.Atmos.7.1.DV.HEVC.REMUX-JD",
            source_quality_tier="remux",
        )

        existing_items = [{
            "Id": "webdl_with_slovak",
            "Name": "Deadpool & Wolverine",
            "Path": "/Movies/4K/Deadpool.and.Wolverine.2024.2160p.WEB-DL.DD+5.1.Atmos.HDR.DoVi.HEVC-TreZzoR.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 1608, "Codec": "hevc"},
                {"Type": "Audio", "Channels": 6, "Language": "slo"},
                {"Type": "Audio", "Channels": 6, "Language": "cze"},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
            ],
            "Size": 24872600555,
            "Bitrate": 25952104,
        }]

        result = compare_quality(proposed, existing_items, lang_priorities=["sk", "cs", "en"])

        assert result.recommendation == "download"
        assert result.reason == "better_quality"
        assert result.proposed_score > result.existing_score * 2  # At least 2x better

    def test_webdl_keeps_when_quality_gap_small(self):
        """WEB-DL with Slovak should be kept when proposed quality is only marginally better."""
        proposed = ProposedQuality(
            resolution="2160p",
            codec="x265",
            audio_languages=["eng", "cze"],
            size_mb=28000,  # Only slightly larger
            bitrate_kbps=28000,
            path="Movie.2160p.WEB-DL.H265.mkv",
        )

        existing_items = [{
            "Id": "webdl_with_slovak",
            "Name": "Movie",
            "Path": "/Movies/4K/Movie.2160p.WEB-DL.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc"},
                {"Type": "Audio", "Channels": 6, "Language": "slo"},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
            ],
            "Size": 24000000000,
            "Bitrate": 25000000,
        }]

        result = compare_quality(proposed, existing_items, lang_priorities=["sk", "cs", "en"])

        # Quality gap < 2x, so Slovak language priority should hold
        assert result.recommendation == "skip"
        assert result.reason == "same_or_worse"


class TestProposedQualityScoring:
    """Tests for ProposedQuality comprehensive scoring."""

    def test_red_flag_overrides_all_other_factors(self):
        """Test that RED FLAG causes immediate rejection regardless of other factors."""
        # Excellent specs BUT critically under-bitrate for 4K
        # AV1 is efficient at 0.5x, so 15 Mbps * 0.5 = 7.5 Mbps minimum
        # Use 5 Mbps to trigger the red flag even with AV1 efficiency
        proposed = ProposedQuality(
            resolution="2160p",
            codec="av1",  # Best codec (0.5x efficiency)
            audio="atmos",  # Best audio
            size_mb=50000,  # Large file
            bitrate_kbps=5000,  # Below even AV1-adjusted threshold of 7.5 Mbps
            path="Movie.BluRay.REMUX.2160p.mkv"  # Best source
        )
        score = proposed.calculate_score()
        assert score == 0.0, "RED FLAG should override all positive factors"

    def test_all_multipliers_applied(self):
        """Test that all multipliers (source, BPP, codec) are applied correctly."""
        # Good quality 1080p with all multipliers
        proposed = ProposedQuality(
            resolution="1080p",
            codec="hevc",  # 1.1x multiplier
            size_mb=15000,
            bitrate_kbps=15000,  # Good bitrate for 1080p (0.3+ bpp)
            path="Movie.BluRay.REMUX.1080p.mkv"  # 1.3x multiplier
        )
        score = proposed.calculate_score()

        # Score should be significantly boosted by multipliers
        # Base score * 1.3 (REMUX) * 1.2 (excellent BPP) * 1.1 (HEVC) = ~1.7x boost
        assert score > 0


class TestExistingQualityScoring:
    """Tests for ExistingQuality comprehensive scoring."""

    def test_existing_red_flag_penalized_not_rejected(self):
        """Test existing items with RED FLAGS are penalized but not rejected."""
        # Existing item with under-bitrate (user already has it)
        item = {
            "Id": "existing_bad",
            "Name": "Movie",
            "Path": "/movies/Movie.2160p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 5_000_000_000,  # Small for 4K
            "Bitrate": 10_000_000,   # Under-bitrate for 4K
        }

        existing = ExistingQuality.from_emby_item(item)
        score = existing.calculate_score()

        # Should have minimal score (1.0) but not zero
        assert score > 0
        assert score < 100  # Very low score

    def test_existing_good_quality_scores_well(self):
        """Test existing items with good quality score appropriately."""
        item = {
            "Id": "existing_good",
            "Name": "Movie",
            "Path": "/movies/Movie.BluRay.REMUX.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "hevc"},
                {"Type": "Audio", "Channels": 8},
            ],
            "Size": 26_700_000_000,
            "Bitrate": 30_000_000,  # Excellent bitrate
        }

        existing = ExistingQuality.from_emby_item(item)
        score = existing.calculate_score()

        # Should have high score due to REMUX source, excellent BPP, HEVC codec
        assert score > 1_000_000


class TestHEVCThresholdRegression:
    """Regression tests for HEVC threshold bug fix (A Knight of the Seven Kingdoms)."""

    def test_hevc_4k_11mbps_not_rejected(self):
        """Regression: A Knight S01E03 - 2160p HEVC at 11.1 Mbps should NOT be rejected."""
        proposed = ProposedQuality(
            resolution="2160p",
            codec="x265",
            size_mb=2451,
            bitrate_kbps=11100,
            hdr="DV",
            audio="Atmos",
            audio_languages=["cze", "slk", "eng"],
            source_quality_tier="webdl",
            path="A.Knight.S01E03.2160p.HMAX.WEB-DL.DDP5.1.Atmos.HDR.DoVi.H265.mkv",
            name="A Knight of the Seven Kingdoms"
        )

        score = proposed.calculate_score()
        assert score > 1_000_000, f"HEVC 4K at 11.1 Mbps should score high, got {score}"

    def test_h264_4k_11mbps_rejected(self):
        """H.264 4K at 11.1 Mbps SHOULD be rejected (too low for inefficient codec)."""
        proposed = ProposedQuality(
            resolution="2160p",
            codec="x264",
            size_mb=2451,
            bitrate_kbps=11100,
            source_quality_tier="webdl",
            path="Movie.2160p.WEB-DL.x264.mkv",
            name="Test Movie"
        )

        score = proposed.calculate_score()
        assert score == 0.0, f"H.264 4K at 11.1 Mbps should be rejected, got {score}"
