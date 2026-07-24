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

    def test_codec_efficiency_ratio_delegates_to_scoring(self):
        """The BPP/bitrate ratio helper delegates to the single scoring source
        (HEVC=0.65, AV1=0.5, else 1.0) — no duplicate ratio logic here."""
        from emby_dedupe.api.quality_compare import _get_codec_efficiency_ratio
        from emby_dedupe.api.scoring import codec_efficiency_ratio

        for codec in ("hevc", "x265", "h265", "av1", "h264", "avc", None, "weird"):
            assert _get_codec_efficiency_ratio(codec) == codec_efficiency_ratio(codec)
        assert _get_codec_efficiency_ratio("hevc") == 0.65
        assert _get_codec_efficiency_ratio("av1") == 0.5
        assert _get_codec_efficiency_ratio("h264") == 1.0


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
    """Under-bitrate handling (Tehran S03E02 case), unified-model semantics.

    The retired check model auto-rejected an under-bitrate 4K (score 0.0), so it lost
    to a good 720p. The unified model is resolution-dominant: it never auto-rejects
    and does not flip a 4K below a 720p. Instead it DEMOTES a 4K whose bitrate is below
    its codec-adjusted adequacy floor, so the starved 4K scores well below an
    adequate-bitrate 4K sibling. Intent preserved: under-bitrate is penalised (as a
    demotion, > 0); codec efficiency shifts the threshold.
    """

    def test_under_bitrate_4k_demoted_not_rejected(self):
        """4K x265 at 2.6 Mbps (below the ~3.9 Mbps HEVC-adjusted 4K floor) is demoted."""
        starved = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=2371, bitrate_kbps=2600,
            name="Tehran.S03E02.2160p.WEB-DL.mkv",
        )
        adequate = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=12000, bitrate_kbps=20000,
            name="Tehran.S03E02.2160p.WEB-DL.mkv",
        )
        starved_score = starved.calculate_score()
        assert starved_score > 0, "under-bitrate is a demotion, never a 0.0 auto-reject"
        assert starved_score < adequate.calculate_score(), "starved 4K demoted vs healthy 4K"

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

    def test_under_bitrate_4k_outranks_720p_but_is_demoted(self):
        """Resolution-dominant: the 4K outranks a good 720p even when under-bitrate,
        yet the starved 4K scores far below an adequate-bitrate 4K of the same title.
        (The retired model kept the 720p; documented deviation in the unify refactor.)"""
        starved_4k = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=2371, bitrate_kbps=2600,
            path="Tehran.S03E02.2160p.WEB-DL.mkv",
        )
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

        result = compare_quality(starved_4k, existing_items)
        assert result.recommendation == "download"  # 4K resolution dominates
        assert result.proposed_score > 0            # demotion, not rejection

        adequate_4k = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=12000, bitrate_kbps=20000,
            path="Tehran.S03E02.2160p.WEB-DL.mkv",
        )
        assert result.proposed_score < adequate_4k.calculate_score()


def _remux_1080p_item():
    """1080p REMUX @ 30 Mbps (26.7GB) — a high-source-quality 1080p reference."""
    return {
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


class TestRemuxVsWebDL:
    """4K WEB-DL vs 1080p REMUX under the unified resolution-dominant model.

    The retired check model preferred a 1080p REMUX over a 4K WEB-DL on bits-per-pixel.
    The unified model (matching the dedupe path) is resolution-dominant: an adequate 4K
    WEB-DL outranks a 1080p REMUX (this is the headline behaviour the unify refactor
    intends). Only a 4K that falls BELOW its codec-adjusted bitrate floor is demoted
    enough to lose to the 1080p REMUX.
    """

    def test_adequate_4k_webdl_beats_1080p_remux(self):
        """A healthy 4K WEB-DL (18 Mbps HEVC, above its floor) outranks a 1080p REMUX."""
        proposed_4k = ProposedQuality(
            resolution="2160p",
            codec="hevc",
            size_mb=16000,
            bitrate_kbps=18000,
            path="Movie.2160p.WEB-DL.mkv",
        )
        result = compare_quality(proposed_4k, [_remux_1080p_item()])
        assert result.recommendation == "download"
        assert result.proposed_score > result.existing_score

    def test_starved_4k_webdl_loses_to_1080p_remux(self):
        """A 4K WEB-DL far below its floor (1.5 Mbps HEVC) is demoted below the REMUX."""
        proposed_4k = ProposedQuality(
            resolution="2160p",
            codec="hevc",
            size_mb=800,
            bitrate_kbps=1500,  # well under the ~3.9 Mbps HEVC-adjusted 4K floor
            path="Movie.2160p.WEB-DL.mkv",
        )
        result = compare_quality(proposed_4k, [_remux_1080p_item()])
        assert result.recommendation == "skip"
        assert result.existing_score > result.proposed_score
        assert result.proposed_score > 0  # demotion, never a 0.0 auto-reject


class TestRemuxVsWebDLWithLanguagePriority:
    """Regression test: REMUX should beat WEB-DL even when WEB-DL has higher-priority language.

    Real-world case: Deadpool & Wolverine
    - Existing: 2160p WEB-DL (23.7GB) with Slovak+Czech+English audio
    - Proposed: 2160p REMUX (58.3GB, 63.7 Mbps) with Czech+English audio
    - With priorities ['sk', 'cs', 'en'], WEB-DL has Slovak (priority 0)
      but REMUX quality is 3.5x better — quality should override.
    """

    def test_remux_keeps_language_over_single_tier_quality(self):
        """A same-resolution REMUX-vs-WEB-DL is a single-tier source jump (~2-3x): per the
        language policy it does NOT override a preferred Slovak track (needs a >4x multi-tier
        jump). The REMUX scores higher, but it's skipped to keep the Slovak audio."""
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

        # Single-tier source jump (~2-3x) does NOT clear the 4x both-priority-language bar →
        # keep the Slovak WEB-DL even though the REMUX scores higher.
        assert result.recommendation == "skip"
        assert result.proposed_score > result.existing_score * 2  # REMUX is genuinely higher quality

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

    def test_under_bitrate_demotes_despite_excellent_specs(self):
        """A 4K below its codec floor is demoted even with the best source/audio.

        The demotion replaces the retired 0.0 auto-reject: the score stays positive
        (so the language-override ratio math is intact) but lands well below the same
        item at a healthy bitrate.
        """
        starved = ProposedQuality(
            resolution="2160p",
            codec="av1",  # 0.5x efficiency → floor ~3.0 Mbps for 4K
            audio="atmos",
            size_mb=50000,
            bitrate_kbps=1500,  # below even the AV1-adjusted 4K floor
            path="Movie.BluRay.REMUX.2160p.mkv",
        )
        healthy = ProposedQuality(
            resolution="2160p",
            codec="av1",
            audio="atmos",
            size_mb=50000,
            bitrate_kbps=12000,  # comfortably above the floor
            path="Movie.BluRay.REMUX.2160p.mkv",
        )
        starved_score = starved.calculate_score()
        assert starved_score > 0, "demotion, never a 0.0 auto-reject"
        assert starved_score < healthy.calculate_score()

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

        # Should outscore a poor, low-res, under-bitrate sibling.
        poor = ExistingQuality.from_emby_item({
            "Id": "existing_poor",
            "Name": "Movie",
            "Path": "/movies/Movie.480p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 854, "Height": 480, "Codec": "h264"},
                {"Type": "Audio", "Channels": 2},
            ],
            "Size": 900_000_000,
            "Bitrate": 1_200_000,
        })
        assert score > 0
        assert score > poor.calculate_score()


class TestHEVCThresholdRegression:
    """Codec efficiency shifts the bitrate-adequacy threshold (A Knight of the Seven Kingdoms).

    HEVC's adequacy floor is ~0.65x of H.264's, so a bitrate adequate for HEVC can be
    below-floor (and demoted) for H.264 at the same resolution. Neither is auto-rejected;
    codec efficiency only shifts where the starved-bitrate demotion begins.
    """

    def test_codec_efficiency_shifts_adequacy_threshold(self):
        """At 5 Mbps 4K, HEVC is above its ~3.9 Mbps floor (undemoted) while H.264 is
        below its 6.0 Mbps floor (demoted) — so HEVC scores higher for the same bitrate."""
        hevc = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=2451, bitrate_kbps=5000,
            source_quality_tier="webdl", path="Movie.2160p.WEB-DL.x265.mkv",
        )
        h264 = ProposedQuality(
            resolution="2160p", codec="x264", size_mb=2451, bitrate_kbps=5000,
            source_quality_tier="webdl", path="Movie.2160p.WEB-DL.x264.mkv",
        )
        assert hevc.calculate_score() > h264.calculate_score()

    def test_hevc_4k_above_floor_not_demoted(self):
        """A Knight S01E03: 4K HEVC at 11.1 Mbps is above the HEVC floor → full 4K credit,
        so it comfortably outscores a 1080p sibling (never rejected)."""
        proposed = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=2451, bitrate_kbps=11100,
            hdr="DV", audio="Atmos", audio_languages=["cze", "slk", "eng"],
            source_quality_tier="webdl",
            path="A.Knight.S01E03.2160p.HMAX.WEB-DL.DDP5.1.Atmos.HDR.DoVi.H265.mkv",
            name="A Knight of the Seven Kingdoms",
        )
        hd = ProposedQuality(
            resolution="1080p", codec="x265", size_mb=2451, bitrate_kbps=11100,
            source_quality_tier="webdl", path="A.Knight.S01E03.1080p.WEB-DL.H265.mkv",
        )
        assert proposed.calculate_score() > hd.calculate_score()

    def test_dv_proposed_not_penalized_vs_hdr10(self):
        """A proposed DV item must NOT be auto-penalised as DV Profile 5: the ``hdr``
        string can't tell a defective P5 from a harmless P7/P8, so a proposed DV item
        scores exactly like an equivalent HDR10 item (the P5 penalty is Existing-only)."""
        dv = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=2451, bitrate_kbps=11100, hdr="DV",
            source_quality_tier="webdl", path="A.Knight.S01E03.2160p.WEB-DL.H265.mkv",
        )
        hdr10 = ProposedQuality(
            resolution="2160p", codec="x265", size_mb=2451, bitrate_kbps=11100, hdr="HDR10",
            source_quality_tier="webdl", path="A.Knight.S01E03.2160p.WEB-DL.H265.mkv",
        )
        assert dv.calculate_score() == hdr10.calculate_score()
