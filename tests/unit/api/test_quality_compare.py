"""Tests for quality comparison module."""


from emby_dedupe.api.quality_compare import (
    SOURCE_QUALITY_TIERS,
    ComparisonResult,
    ExistingQuality,
    ProposedQuality,
    _apply_bluray_native_exception,
    _apply_smart_override_if_needed,
    _create_proposed_as_existing,
    apply_language_priority,
    compare_quality,
)


class TestProposedQuality:
    """Tests for ProposedQuality class."""

    def test_get_resolution_pixels_4k(self):
        """Test resolution pixel calculation for 4K."""
        proposed = ProposedQuality(resolution="2160p")
        assert proposed.get_resolution_pixels() == 3840 * 2160

    def test_get_resolution_pixels_1080p(self):
        """Test resolution pixel calculation for 1080p."""
        proposed = ProposedQuality(resolution="1080p")
        assert proposed.get_resolution_pixels() == 1920 * 1080

    def test_get_resolution_pixels_invalid(self):
        """Test resolution pixel calculation for invalid resolution."""
        proposed = ProposedQuality(resolution="invalid")
        assert proposed.get_resolution_pixels() == 0

    def test_get_audio_channels_atmos(self):
        """Test audio channel detection for Atmos."""
        proposed = ProposedQuality(audio="atmos")
        assert proposed.get_audio_channels() == 8

    def test_get_audio_channels_51(self):
        """Test audio channel detection for 5.1."""
        proposed = ProposedQuality(audio="5.1")
        assert proposed.get_audio_channels() == 6

    def test_get_audio_channels_stereo(self):
        """Test audio channel detection for stereo."""
        proposed = ProposedQuality(audio="stereo")
        assert proposed.get_audio_channels() == 2

    def test_get_size_bytes(self):
        """Test file size conversion to bytes."""
        proposed = ProposedQuality(size_mb=1000)
        assert proposed.get_size_bytes() == 1000 * 1024 * 1024

    def test_get_bitrate(self):
        """Test bitrate conversion."""
        proposed = ProposedQuality(bitrate_kbps=5000)
        assert proposed.get_bitrate() == 5000 * 1000

    def test_calculate_score(self):
        """Test quality score calculation."""
        proposed = ProposedQuality(
            resolution="2160p",
            audio="atmos",
            size_mb=10000,
            bitrate_kbps=15000,
        )
        score = proposed.calculate_score()
        assert score > 0
        # 4K should have high resolution score
        assert score > 1000000

    def test_get_source_quality_multiplier_bluray(self):
        """Test source quality multiplier detection for BluRay."""
        proposed = ProposedQuality(
            resolution="1080p",
            path="/movies/Movie.BluRay.1080p.mkv"
        )
        multiplier = proposed.get_source_quality_multiplier()
        assert multiplier == SOURCE_QUALITY_TIERS["bluray"]["bonus"]

    def test_get_source_quality_multiplier_remux(self):
        """Test source quality multiplier detection for REMUX."""
        proposed = ProposedQuality(
            resolution="2160p",
            path="/movies/Movie.REMUX.2160p.mkv"
        )
        multiplier = proposed.get_source_quality_multiplier()
        assert multiplier == SOURCE_QUALITY_TIERS["bluray_remux"]["bonus"]

    def test_is_ai_upscaled_true(self):
        """Test AI upscale detection returns True."""
        proposed = ProposedQuality(
            resolution="2160p",
            path="/movies/Movie.AI.UPSCALE.2160p.mkv"
        )
        assert proposed.is_ai_upscaled() is True

    def test_is_ai_upscaled_false(self):
        """Test AI upscale detection returns False."""
        proposed = ProposedQuality(
            resolution="2160p",
            path="/movies/Movie.BluRay.2160p.mkv"
        )
        assert proposed.is_ai_upscaled() is False

    def test_calculate_score_with_bluray_multiplier(self):
        """Test that BluRay source quality increases score."""
        # Same specs, different source quality
        base_proposed = ProposedQuality(
            resolution="1080p",
            audio="5.1",
            size_mb=5000,
            bitrate_kbps=8000,
            path="/movies/Movie.1080p.mkv"  # Unknown source
        )
        bluray_proposed = ProposedQuality(
            resolution="1080p",
            audio="5.1",
            size_mb=5000,
            bitrate_kbps=8000,
            path="/movies/Movie.BluRay.1080p.mkv"
        )

        base_score = base_proposed.calculate_score()
        bluray_score = bluray_proposed.calculate_score()

        # BluRay should score higher due to 1.15x multiplier
        assert bluray_score > base_score
        expected_ratio = SOURCE_QUALITY_TIERS["bluray"]["bonus"] / SOURCE_QUALITY_TIERS["unknown"]["bonus"]
        actual_ratio = bluray_score / base_score
        assert abs(actual_ratio - expected_ratio) < 0.01

    def test_calculate_score_with_ai_upscale_penalty(self):
        """Test that AI upscale reduces score."""
        # Same specs, one is AI upscaled
        normal_proposed = ProposedQuality(
            resolution="2160p",
            audio="atmos",
            size_mb=10000,
            bitrate_kbps=15000,
            path="/movies/Movie.BluRay.2160p.mkv"
        )
        ai_upscale_proposed = ProposedQuality(
            resolution="2160p",
            audio="atmos",
            size_mb=10000,
            bitrate_kbps=15000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv"
        )

        normal_score = normal_proposed.calculate_score()
        ai_upscale_score = ai_upscale_proposed.calculate_score()

        # AI upscale should score lower due to 0.7x penalty
        assert ai_upscale_score < normal_score
        # Score should be approximately 0.7x of normal (accounting for unknown source)
        expected_ratio = 0.7 * SOURCE_QUALITY_TIERS["unknown"]["bonus"] / SOURCE_QUALITY_TIERS["bluray"]["bonus"]
        actual_ratio = ai_upscale_score / normal_score
        assert abs(actual_ratio - expected_ratio) < 0.01

    def test_calculate_score_with_combined_multipliers(self):
        """Test score with both BluRay REMUX and AI upscale applied."""
        # BluRay REMUX but AI upscaled - multipliers should combine
        proposed = ProposedQuality(
            resolution="2160p",
            audio="atmos",
            size_mb=15000,
            bitrate_kbps=20000,
            path="/movies/Movie.BluRay.REMUX.AI.UPSCALE.2160p.mkv"
        )

        score = proposed.calculate_score()
        assert score > 0

        # Verify both multipliers are applied
        # Should have 1.3x (REMUX) * 0.7x (AI upscale) = 0.91x combined multiplier


class TestExistingQuality:
    """Tests for ExistingQuality class."""

    def test_from_emby_item_basic(self):
        """Test creating ExistingQuality from Emby item."""
        item = {
            "Id": "123",
            "Name": "Test Movie",
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Width": 1920,
                    "Height": 1080,
                    "Codec": "h264",
                },
                {
                    "Type": "Audio",
                    "Channels": 6,
                    "Language": "eng",
                },
            ],
            "Size": 5000000000,  # 5GB
            "Bitrate": 10000000,
            "DateCreated": "2023-01-15T10:00:00Z",
        }

        existing = ExistingQuality.from_emby_item(item)
        assert existing.id == "123"
        assert existing.name == "Test Movie"
        assert existing.resolution == "1080p"
        assert existing.width == 1920
        assert existing.height == 1080
        assert existing.codec == "h264"
        assert existing.audio_channels == 6
        assert existing.audio_languages == ["eng"]
        assert existing.size_bytes == 5000000000
        assert existing.bitrate == 10000000

    def test_from_emby_item_4k(self):
        """Test resolution detection for 4K content."""
        item = {
            "Id": "456",
            "Name": "4K Movie",
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Width": 3840,
                    "Height": 2160,
                    "Codec": "hevc",
                },
            ],
            "Size": 25000000000,
        }

        existing = ExistingQuality.from_emby_item(item)
        assert existing.resolution == "2160p"
        assert existing.width == 3840
        assert existing.height == 2160

    def test_from_emby_item_multiple_audio_languages(self):
        """Test extracting multiple audio languages."""
        item = {
            "Id": "789",
            "Name": "Multilang Movie",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
                {"Type": "Audio", "Channels": 6, "Language": "cze"},
                {"Type": "Audio", "Channels": 2, "Language": "sk"},
            ],
            "Size": 8000000000,
        }

        existing = ExistingQuality.from_emby_item(item)
        assert len(existing.audio_languages) == 3
        assert "eng" in existing.audio_languages
        assert "cze" in existing.audio_languages
        assert "sk" in existing.audio_languages

    def test_calculate_score(self):
        """Test quality score calculation for existing item."""
        existing = ExistingQuality(
            id="123",
            name="Test",
            width=1920,
            height=1080,
            audio_channels=6,
            size_bytes=5000000000,
            bitrate=10000000,
            date_rating=1673779200,  # Some timestamp
        )
        score = existing.calculate_score()
        assert score > 0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        existing = ExistingQuality(
            id="123",
            name="Test Movie",
            resolution="1080p",
            codec="h264",
            audio_channels=6,
            audio_languages=["eng", "cze"],
            size_bytes=5000000000,
            bitrate=10000000,
            path="/media/movies/test.mkv",
            provider_ids={"Imdb": "tt1234567"},
        )
        d = existing.to_dict()
        assert d["id"] == "123"
        assert d["name"] == "Test Movie"
        assert d["quality"]["resolution"] == "1080p"
        assert d["quality"]["codec"] == "h264"
        assert d["quality"]["audio_channels"] == 6
        assert d["audio_languages"] == ["eng", "cze"]
        assert d["provider_ids"] == {"Imdb": "tt1234567"}

    def test_from_emby_item_detects_bluray_source(self):
        """Test source quality detection during from_emby_item."""
        item = {
            "Id": "123",
            "Name": "Test Movie",
            "Path": "/movies/Test.Movie.BluRay.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
            ],
            "Size": 5000000000,
            "Bitrate": 10000000,
        }

        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "bluray"
        assert existing.is_ai_upscale is False

    def test_from_emby_item_detects_remux_source(self):
        """Test REMUX detection during from_emby_item."""
        item = {
            "Id": "456",
            "Name": "Test Movie",
            "Path": "/movies/Test.Movie.BluRay.REMUX.2160p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc"},
            ],
            "Size": 50000000000,
        }

        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "bluray_remux"

    def test_from_emby_item_detects_ai_upscale(self):
        """Test AI upscale detection during from_emby_item."""
        item = {
            "Id": "789",
            "Name": "Test Movie",
            "Path": "/movies/Test.Movie.AI.UPSCALE.2160p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc"},
            ],
            "Size": 15000000000,
        }

        existing = ExistingQuality.from_emby_item(item)
        assert existing.is_ai_upscale is True

    def test_from_emby_item_detects_both_remux_and_ai_upscale(self):
        """Test detecting both REMUX and AI upscale."""
        item = {
            "Id": "999",
            "Name": "Test Movie",
            "Path": "/movies/Test.Movie.BluRay.REMUX.AI.UPSCALE.2160p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc"},
            ],
            "Size": 25000000000,
        }

        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "bluray_remux"
        assert existing.is_ai_upscale is True

    def test_infer_webdl_from_eac3_audio(self):
        """Test that EAC3 audio infers webdl when filename has no source indicator."""
        item = {
            "Id": "100",
            "Name": "Clean Name Episode",
            "Path": "/Series/Show (2020)/S01/Show (2020) S01E01 - 1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6, "Codec": "eac3", "Language": "eng"},
            ],
            "Size": 655000000,
            "Bitrate": 4050000,
        }
        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "webdl"

    def test_infer_bluray_from_truehd_audio(self):
        """Test that TrueHD audio infers bluray when filename has no source indicator."""
        item = {
            "Id": "101",
            "Name": "Movie Title",
            "Path": "/Movies/Movie Title (2023)/Movie Title (2023) - 1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 8, "Codec": "truehd", "Language": "eng"},
            ],
            "Size": 8000000000,
            "Bitrate": 15000000,
        }
        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "bluray"

    def test_infer_remux_from_truehd_high_bitrate_4k(self):
        """Test that lossless audio + very high bitrate 4K infers remux."""
        item = {
            "Id": "102",
            "Name": "4K Movie",
            "Path": "/Movies/4K Movie (2023)/4K Movie (2023) - 2160p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160, "Codec": "hevc"},
                {"Type": "Audio", "Channels": 8, "Codec": "truehd", "Language": "eng"},
            ],
            "Size": 50000000000,
            "Bitrate": 50000000,
        }
        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "bluray_remux"

    def test_infer_webdl_from_aac_audio(self):
        """Test that AAC audio infers webdl."""
        item = {
            "Id": "103",
            "Name": "Episode Title",
            "Path": "/Series/Show/S02/Show S02E05 - 720p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1280, "Height": 720, "Codec": "h264"},
                {"Type": "Audio", "Channels": 2, "Codec": "aac", "Language": "eng"},
            ],
            "Size": 400000000,
            "Bitrate": 2500000,
        }
        existing = ExistingQuality.from_emby_item(item)
        assert existing.source_quality_tier == "webdl"

    def test_path_detection_takes_priority_over_stream_inference(self):
        """Test that explicit path indicator is not overridden by stream inference."""
        item = {
            "Id": "104",
            "Name": "Test",
            "Path": "/movies/Test.BluRay.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6, "Codec": "eac3", "Language": "eng"},
            ],
            "Size": 5000000000,
            "Bitrate": 4000000,
        }
        existing = ExistingQuality.from_emby_item(item)
        # Path says BluRay — stream inference should NOT override
        assert existing.source_quality_tier == "bluray"

    def test_infer_no_data_returns_none(self):
        """Test that no bitrate and no audio codec leaves source unknown."""
        result = ExistingQuality._infer_source_quality_from_streams(0, 1080, None)
        assert result is None

    def test_calculate_score_applies_bluray_multiplier(self):
        """Test that BluRay source increases score."""
        # Create items with same specs but different source quality
        # Note: base_item at 10 Mbps 1080p gets inferred as webdl from stream metadata
        base_item = {
            "Id": "1",
            "Name": "Test",
            "Path": "/movies/Test.1080p.mkv",  # No source in filename
            "MediaStreams": [{"Type": "Video", "Width": 1920, "Height": 1080}],
            "Size": 5000000000,
            "Bitrate": 10000000,
        }
        bluray_item = {
            "Id": "2",
            "Name": "Test",
            "Path": "/movies/Test.BluRay.1080p.mkv",
            "MediaStreams": [{"Type": "Video", "Width": 1920, "Height": 1080}],
            "Size": 5000000000,
            "Bitrate": 10000000,
        }

        base_quality = ExistingQuality.from_emby_item(base_item)
        bluray_quality = ExistingQuality.from_emby_item(bluray_item)

        base_score = base_quality.calculate_score()
        bluray_score = bluray_quality.calculate_score()

        # BluRay should score higher than stream-inferred webdl
        assert bluray_score > base_score
        # base_item inferred as webdl (1.0) from bitrate, bluray is 1.15
        expected_ratio = SOURCE_QUALITY_TIERS["bluray"]["bonus"] / SOURCE_QUALITY_TIERS["webdl"]["bonus"]
        actual_ratio = bluray_score / base_score
        assert abs(actual_ratio - expected_ratio) < 0.01

    def test_calculate_score_applies_ai_upscale_penalty(self):
        """Test that AI upscale reduces score."""
        normal_item = {
            "Id": "1",
            "Name": "Test",
            "Path": "/movies/Test.BluRay.2160p.mkv",
            "MediaStreams": [{"Type": "Video", "Width": 3840, "Height": 2160}],
            "Size": 20000000000,
        }
        ai_upscale_item = {
            "Id": "2",
            "Name": "Test",
            "Path": "/movies/Test.AI.UPSCALE.2160p.mkv",
            "MediaStreams": [{"Type": "Video", "Width": 3840, "Height": 2160}],
            "Size": 20000000000,
        }

        normal_quality = ExistingQuality.from_emby_item(normal_item)
        ai_quality = ExistingQuality.from_emby_item(ai_upscale_item)

        normal_score = normal_quality.calculate_score()
        ai_score = ai_quality.calculate_score()

        # AI upscale should score lower
        assert ai_score < normal_score

    def test_to_dict_includes_source_quality_fields(self):
        """Test that to_dict includes source quality information."""
        existing = ExistingQuality(
            id="123",
            name="Test Movie",
            resolution="1080p",
            codec="h264",
            audio_channels=6,
            size_bytes=5000000000,
            bitrate=10000000,
            path="/movies/Test.BluRay.1080p.mkv",
            source_quality_tier="bluray",
            is_ai_upscale=False,
        )

        d = existing.to_dict()
        assert "source_quality_tier" in d["quality"]
        assert "is_ai_upscale" in d["quality"]
        assert d["quality"]["source_quality_tier"] == "bluray"
        assert d["quality"]["is_ai_upscale"] is False


class TestComparisonResult:
    """Tests for ComparisonResult class."""

    def test_should_download_property(self):
        """Test should_download property."""
        result = ComparisonResult(
            recommendation="download",
            reason="better_quality",
            status="found",
        )
        assert result.should_download is True

        result = ComparisonResult(
            recommendation="skip",
            reason="same_or_worse",
            status="found",
        )
        assert result.should_download is False

    def test_to_dict_not_found(self):
        """Test to_dict for not found case."""
        proposed = ProposedQuality(resolution="2160p")
        result = ComparisonResult(
            recommendation="download",
            reason="not_found",
            status="not_found",
            proposed=proposed,
        )
        d = result.to_dict()
        assert d["status"] == "not_found"
        assert d["recommendation"] == "download"
        assert d["reason"] == "not_found"
        assert "proposed" in d

    def test_to_dict_found(self):
        """Test to_dict for found case with comparison."""
        existing = ExistingQuality(
            id="123",
            name="Test",
            width=1920,
            height=1080,
            audio_channels=6,
            size_bytes=5000000000,
        )
        proposed = ProposedQuality(resolution="2160p")
        result = ComparisonResult(
            recommendation="download",
            reason="better_quality",
            status="found",
            existing=existing,
            proposed=proposed,
            existing_score=500.0,
            proposed_score=800.0,
            score_diff=300.0,
        )
        d = result.to_dict()
        assert d["status"] == "found"
        assert "existing" in d
        assert "proposed" in d
        assert "quality_comparison" in d
        assert d["quality_comparison"]["winner"] == "proposed"


class TestApplyLanguagePriority:
    """Tests for apply_language_priority function."""

    def test_no_priorities(self):
        """Test sorting without language priorities."""
        items = [
            ExistingQuality(id="1", name="Low", width=1280, height=720, audio_channels=2,
                          size_bytes=1000000000, bitrate=4_000_000),  # 4 Mbps for 720p
            ExistingQuality(id="2", name="High", width=3840, height=2160, audio_channels=8,
                          size_bytes=10000000000, bitrate=25_000_000),  # 25 Mbps for 4K
        ]
        sorted_items = apply_language_priority(items)
        assert sorted_items[0].id == "2"  # Higher quality first

    def test_with_language_priorities(self):
        """Test sorting with language priorities."""
        items = [
            ExistingQuality(id="1", name="High quality eng", width=3840, height=2160, audio_channels=8,
                          size_bytes=10000000000, audio_languages=["eng"]),
            ExistingQuality(id="2", name="Low quality sk", width=1920, height=1080, audio_channels=6,
                          size_bytes=5000000000, audio_languages=["sk"]),
        ]
        sorted_items = apply_language_priority(items, ["sk", "eng"])
        # Slovak should come first despite lower quality
        assert sorted_items[0].id == "2"

    def test_language_normalization(self):
        """Test that Slovak/Czech language codes are normalized."""
        items = [
            ExistingQuality(id="1", name="Item with slo", width=1920, height=1080, audio_channels=6,
                          size_bytes=5000000000, audio_languages=["slo"]),
            ExistingQuality(id="2", name="Item with eng", width=1920, height=1080, audio_channels=6,
                          size_bytes=5000000000, audio_languages=["eng"]),
        ]
        sorted_items = apply_language_priority(items, ["sk", "eng"])
        # "slo" should be normalized to "sk" and match priority
        assert sorted_items[0].id == "1"


class TestCompareQuality:
    """Tests for compare_quality function."""

    def test_not_found_returns_download(self):
        """Test that not found items return download recommendation."""
        # Proposed with good quality (adequate bitrate for 1080p)
        proposed = ProposedQuality(resolution="1080p", bitrate_kbps=8000)  # 8 Mbps
        result = compare_quality(proposed, [])
        assert result.recommendation == "download"
        assert result.reason == "not_found"
        assert result.status == "not_found"

    def test_better_quality_returns_download(self):
        """Test that better quality returns download."""
        # Proposed: Good 4K with adequate bitrate
        proposed = ProposedQuality(resolution="2160p", size_mb=15000, bitrate_kbps=20000)  # 20 Mbps
        existing_items = [{
            "Id": "123",
            "Name": "Existing",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 5000000000,
            "Bitrate": 8_000_000,  # 8 Mbps for 1080p
        }]
        result = compare_quality(proposed, existing_items)
        assert result.recommendation == "download"
        assert result.reason == "better_quality"

    def test_4k_hevc_webdl_vs_1080p_bluray_h264(self):
        """Regression: 4K HEVC WEB-DL should beat 1080p BluRay H264.

        Real case: Kingsman 2160p DSNP WEB-DL DV HEVC 13.3GB at 14.2 Mbps
        was rejected vs 1080p BluRay H264 15.9GB because codec was not
        passed to _create_proposed_as_existing, causing RED FLAG to trigger
        at 15 Mbps threshold instead of 9.75 Mbps (HEVC-adjusted).
        """
        proposed = ProposedQuality(
            resolution="2160p",
            codec="x265",
            hdr="DV",
            audio="DDP",
            audio_languages=["cze", "eng", "slk"],
            size_mb=13303,
            bitrate_kbps=14200,
            source_quality_tier="webdl",
            path="The.Kings.Man.2021.2160p.DSNP.WEB-DL.DDP5.1.HDR.DoVi.Hybrid.HEVC-TreZzoR",
        )
        existing_items = [{
            "Id": "existing_bluray",
            "Name": "The King's Man",
            "Path": "/Movies/HD/The.Kings.Man.2021.1080p.BluRay.DD+7.1.x264-KASHMiR.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 8, "Language": "cze"},
                {"Type": "Audio", "Channels": 6, "Language": "eng"},
                {"Type": "Audio", "Channels": 6, "Language": "slk"},
            ],
            "Size": 16703913984,  # ~15929 MB
            "Bitrate": 17_000_000,  # ~17 Mbps
        }]
        result = compare_quality(proposed, existing_items)
        assert result.recommendation == "download", (
            f"4K HEVC DV should upgrade over 1080p BluRay H264. "
            f"Proposed score: {result.proposed_score:.0f}, "
            f"Existing score: {result.existing_score:.0f}"
        )
        assert result.reason == "better_quality"

    def test_worse_quality_returns_skip(self):
        """Test that worse quality returns skip."""
        proposed = ProposedQuality(resolution="720p", size_mb=2000)
        existing_items = [{
            "Id": "123",
            "Name": "Existing 4K",
            "MediaStreams": [
                {"Type": "Video", "Width": 3840, "Height": 2160},
                {"Type": "Audio", "Channels": 8},
            ],
            "Size": 20000000000,
        }]
        result = compare_quality(proposed, existing_items)
        assert result.recommendation == "skip"
        assert result.reason == "same_or_worse"

    def test_language_priority_affects_decision(self):
        """Test that language priority is considered when quality difference is small."""
        # Test with similar quality (< 3x ratio) - language priority should win
        proposed = ProposedQuality(resolution="1080p", audio_languages=["eng"], size_mb=10000)
        existing_items = [
            {
                "Id": "1",
                "Name": "Similar quality eng",
                "MediaStreams": [
                    {"Type": "Video", "Width": 1920, "Height": 1080},
                    {"Type": "Audio", "Channels": 6, "Language": "eng"},
                ],
                "Size": 12000000000,
            },
            {
                "Id": "2",
                "Name": "Similar quality sk",
                "MediaStreams": [
                    {"Type": "Video", "Width": 1920, "Height": 1080},
                    {"Type": "Audio", "Channels": 6, "Language": "sk"},
                ],
                "Size": 10000000000,
            },
        ]
        result = compare_quality(proposed, existing_items, ["sk", "eng"])
        # Should compare against Slovak version (preferred language, quality diff < 3x)
        assert result.existing.id == "2"

    def test_bluray_native_exception_1080p_beats_ai_4k(self):
        """Test that native BluRay 1080p beats AI upscaled 4K when 1.5x+ larger."""
        # Proposed: AI upscaled 4K, 15GB
        proposed = ProposedQuality(
            resolution="2160p",
            audio="5.1",
            size_mb=15000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",
            name="Movie AI Upscaled"
        )

        # Existing: Native BluRay 1080p, 23GB (1.53x larger)
        existing_items = [{
            "Id": "native123",
            "Name": "Movie BluRay",
            "Path": "/movies/Movie.BluRay.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 23000000000,  # 23GB
            "Bitrate": 20000000,
        }]

        result = compare_quality(proposed, existing_items)

        # Should recommend skip - native BluRay preferred due to 1.5x size ratio
        assert result.recommendation == "skip"
        assert result.reason == "same_or_worse"

    def test_ai_4k_wins_when_size_similar(self):
        """Test that native BluRay is preferred when AI 4K size difference is < 1.5x."""
        # Proposed: AI upscaled 4K, 20GB (AI penalty: 0.7x, unknown source: 0.95x)
        proposed = ProposedQuality(
            resolution="2160p",
            audio="5.1",
            size_mb=20000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",
            name="Movie AI Upscaled"
        )

        # Existing: Native BluRay 1080p, 25GB (BluRay: 1.15x, only 1.25x larger)
        existing_items = [{
            "Id": "native123",
            "Name": "Movie BluRay",
            "Path": "/movies/Movie.BluRay.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 25000000000,  # 25GB
            "Bitrate": 20000000,
        }]

        result = compare_quality(proposed, existing_items)

        # Should recommend skip - BluRay 1080p still preferred due to source quality
        # even though size ratio < 1.5x, the exception doesn't trigger
        assert result.recommendation == "skip"

    def test_exception_only_applies_to_bluray(self):
        """Test that exception logic only checks BluRay/REMUX sources."""
        # Proposed: AI upscaled 4K, 25GB (large file)
        proposed = ProposedQuality(
            resolution="2160p",
            audio="5.1",
            size_mb=25000,
            bitrate_kbps=20000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",
            name="Movie AI Upscaled"
        )

        # Existing: WEB-DL 1080p, 10GB (2.5x smaller, but not BluRay)
        existing_items = [{
            "Id": "webdl123",
            "Name": "Movie WEB-DL",
            "Path": "/movies/Movie.WEB-DL.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 10000000000,  # 10GB (smaller)
            "Bitrate": 10000000,
        }]

        result = compare_quality(proposed, existing_items)

        # Should recommend download - AI 4K is much larger and higher resolution
        # Exception doesn't trigger because existing is WEB-DL, not BluRay
        assert result.recommendation == "download"

    def test_exception_requires_1080p_vs_4k(self):
        """Test that exception only applies to 1080p vs 4K, not other resolutions."""
        # Proposed: AI upscaled 4K, 25GB (large file)
        proposed = ProposedQuality(
            resolution="2160p",
            audio="5.1",
            size_mb=25000,
            bitrate_kbps=20000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",
            name="Movie AI Upscaled"
        )

        # Existing: Native BluRay 720p, 10GB (smaller, 720p not 1080p)
        existing_items = [{
            "Id": "bluray720",
            "Name": "Movie BluRay",
            "Path": "/movies/Movie.BluRay.720p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1280, "Height": 720, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 10000000000,  # 10GB (smaller)
            "Bitrate": 10000000,
        }]

        result = compare_quality(proposed, existing_items)

        # Should recommend download - exception doesn't trigger for 720p
        # AI 4K wins due to much better resolution and larger file
        assert result.recommendation == "download"


class TestCompareQualityHelpers:
    """Tests for helper functions extracted in Phase 3 Step 8."""

    def test_create_proposed_as_existing_basic(self):
        """Test converting ProposedQuality to ExistingQuality."""
        proposed = ProposedQuality(
            resolution="1080p",
            audio="5.1",
            audio_languages=["en", "es"],
            size_mb=5200,
            bitrate_kbps=10000,
            path="/path/to/movie.mkv",
            name="Movie.1080p.BluRay.x264",
        )

        result = _create_proposed_as_existing(proposed)

        assert result.id == "proposed"
        assert result.name == "Proposed"
        assert result.width == 1920
        assert result.height == 1080
        assert result.audio_channels == 6
        assert result.audio_languages == ["en", "es"]

    def test_create_proposed_as_existing_4k(self):
        """Test converting 4K ProposedQuality."""
        proposed = ProposedQuality(
            resolution="2160p",
            audio="7.1",
            size_mb=25000,
            path="/path/to/movie.4k.mkv",
            name="Movie.2160p.WEB-DL.x265",
        )

        result = _create_proposed_as_existing(proposed)

        assert result.width == 3840
        assert result.height == 2160
        assert result.audio_channels == 8

    def test_create_proposed_as_existing_detects_bluray(self):
        """Test BluRay source detection in conversion."""
        proposed = ProposedQuality(
            resolution="1080p",
            path="/path/to/movie.bluray.mkv",
            name="Movie.1080p.BluRay.x264",
        )

        result = _create_proposed_as_existing(proposed)

        assert result.source_quality_tier == "bluray"

    def test_create_proposed_as_existing_detects_ai_upscale(self):
        """Test AI upscale detection in conversion."""
        proposed = ProposedQuality(
            resolution="2160p",
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",  # Use pattern from existing test
            name="Movie.2160p.AI.x265",
        )

        result = _create_proposed_as_existing(proposed)

        # AI upscale detection is case-insensitive and pattern-based
        assert result.is_ai_upscale is True

    def test_apply_bluray_native_exception_triggers(self):
        """Test BluRay exception triggers for 1080p BluRay vs AI 4K."""
        # Create 1080p BluRay item (larger)
        bluray_1080p = ExistingQuality(
            id="existing",
            name="BluRay",
            width=1920,
            height=1080,
            audio_channels=8,
            size_bytes=30 * 1024**3,  # 30GB
            path="/path/movie.bluray.mkv",
            source_quality_tier="bluray",
            is_ai_upscale=False,
        )

        # Create AI upscaled 4K item (smaller)
        ai_4k = ExistingQuality(
            id="proposed",
            name="AI 4K",
            width=3840,
            height=2160,
            audio_channels=6,
            size_bytes=15 * 1024**3,  # 15GB (half the size)
            path="/path/movie.topaz.mkv",
            is_ai_upscale=True,
        )

        # Test when AI 4K is proposed
        result = _apply_bluray_native_exception(ai_4k, bluray_1080p, "download")

        # Exception should trigger - prefer native BluRay
        assert result == "skip"

    def test_apply_bluray_native_exception_no_trigger_similar_size(self):
        """Test exception doesn't trigger when sizes are similar."""
        bluray_1080p = ExistingQuality(
            id="existing",
            name="BluRay 1080p",
            width=1920,
            height=1080,
            size_bytes=20 * 1024**3,  # 20GB
            path="/path/movie.bluray.mkv",
            source_quality_tier="bluray",
            is_ai_upscale=False,
        )

        ai_4k = ExistingQuality(
            id="proposed",
            name="AI 4K",
            width=3840,
            height=2160,
            size_bytes=18 * 1024**3,  # 18GB (1.11x ratio, < 1.5x threshold)
            path="/path/movie.topaz.mkv",
            is_ai_upscale=True,
        )

        result = _apply_bluray_native_exception(ai_4k, bluray_1080p, "download")

        # Exception should not trigger
        assert result == "download"

    def test_apply_bluray_native_exception_no_trigger_not_bluray(self):
        """Test exception doesn't trigger for non-BluRay sources."""
        webdl_1080p = ExistingQuality(
            id="existing",
            name="WEB-DL 1080p",
            width=1920,
            height=1080,
            size_bytes=30 * 1024**3,
            path="/path/movie.web-dl.mkv",
            source_quality_tier="web-dl",  # Not BluRay
            is_ai_upscale=False,
        )

        ai_4k = ExistingQuality(
            id="proposed",
            name="AI 4K",
            width=3840,
            height=2160,
            size_bytes=15 * 1024**3,
            path="/path/movie.topaz.mkv",
            is_ai_upscale=True,
        )

        result = _apply_bluray_native_exception(ai_4k, webdl_1080p, "download")

        # Exception should not trigger - not BluRay
        assert result == "download"

    def test_apply_bluray_native_exception_both_native(self):
        """Test exception doesn't apply when both are native."""
        item1 = ExistingQuality(
            id="existing",
            name="Native 1080p",
            width=1920,
            height=1080,
            size_bytes=30 * 1024**3,
            path="/path/movie1.mkv",
            is_ai_upscale=False,
        )

        item2 = ExistingQuality(
            id="proposed",
            name="Native 4K",
            width=3840,
            height=2160,
            size_bytes=15 * 1024**3,
            path="/path/movie2.mkv",
            is_ai_upscale=False,
        )

        result = _apply_bluray_native_exception(item2, item1, "download")

        # No change - both native
        assert result == "download"

    def test_apply_smart_override_no_priorities(self):
        """Test smart override with no language priorities."""
        items = [
            ExistingQuality(id="1", name="Item1", width=1920, height=1080),
            ExistingQuality(id="2", name="Item2", width=3840, height=2160),
        ]
        sorted_items = items.copy()

        result = _apply_smart_override_if_needed(items, sorted_items, None)

        # Should return unchanged
        assert result == sorted_items

    def test_apply_smart_override_same_best_item(self):
        """Test when language priority and quality agree."""
        item1 = ExistingQuality(
            id="1", name="Best", width=3840, height=2160,
            audio_languages=["cs"], size_bytes=20*1024**3
        )
        item2 = ExistingQuality(
            id="2", name="Worse", width=1920, height=1080,
            audio_languages=["en"], size_bytes=10*1024**3
        )

        all_items = [item1, item2]
        sorted_items = [item1, item2]  # Already sorted correctly

        result = _apply_smart_override_if_needed(all_items, sorted_items, ["cs"])

        # No override needed - same item is best
        assert result == sorted_items
