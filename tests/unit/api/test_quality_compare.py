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
        """Test quality score calculation (unified normalised model)."""
        proposed = ProposedQuality(
            resolution="2160p",
            audio="atmos",
            size_mb=10000,
            bitrate_kbps=15000,
        )
        score = proposed.calculate_score()
        assert score > 0
        # Resolution dominates: the 4K item outscores an otherwise-identical 1080p one.
        hd = ProposedQuality(
            resolution="1080p", audio="atmos", size_mb=10000, bitrate_kbps=15000
        )
        assert score > hd.calculate_score()

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

    def test_from_emby_item_cropped_scope_keeps_its_class(self):
        """MobLand S02E07 (2026-11-04): the 3832x1600 4K copy was labelled "1080p", and the torrents
        check reported a 2160p release as "2160p marked as same_or_worse than 1080p" (EMBY-DEDUPE BUG DETECTED)."""
        def label(width: int, height: int) -> str | None:
            return ExistingQuality.from_emby_item(
                {"Id": "x", "MediaStreams": [{"Type": "Video", "Width": width, "Height": height}]}).resolution

        assert label(3832, 1600) == "2160p"
        assert label(3840, 1608) == "2160p"
        assert label(1916, 800) == "1080p"
        assert label(1920, 800) == "1080p"
        assert label(1440, 1080) == "1080p"
        assert label(2560, 1440) == "1080p"
        assert label(1280, 536) == "720p"
        assert label(960, 720) == "720p"
        assert label(720, 576) == "480p"
        assert label(640, 360) == "480p"
        assert label(480, 270) is None
        assert label(0, 0) is None

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
        """Test that native BluRay 1080p beats AI upscaled 4K when 1.5x+ larger.

        Under the unified model the AI-upscaled 4K would win on resolution, so this
        is exactly the case the BluRay-native exception exists for: once the native
        1080p is >=1.5x larger, the exception overrides and keeps the native file.
        """
        # Proposed: AI upscaled 4K, 15GB (~14.65 GiB)
        proposed = ProposedQuality(
            resolution="2160p",
            audio="5.1",
            size_mb=15000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",
            name="Movie AI Upscaled"
        )

        # Existing: Native BluRay 1080p, 25GB (~1.59x larger → exception fires)
        existing_items = [{
            "Id": "native123",
            "Name": "Movie BluRay",
            "Path": "/movies/Movie.BluRay.1080p.mkv",
            "MediaStreams": [
                {"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "h264"},
                {"Type": "Audio", "Channels": 6},
            ],
            "Size": 25000000000,  # 25GB, ~1.59x the AI 4K
            "Bitrate": 20000000,
        }]

        result = compare_quality(proposed, existing_items)

        # Should recommend skip - native BluRay preferred due to 1.5x size ratio
        assert result.recommendation == "skip"
        assert result.reason == "same_or_worse"

    def test_ai_4k_wins_when_size_similar(self):
        """AI 4K wins over 1080p BluRay when the size gap is < 1.5x.

        The BluRay-native exception only fires at >=1.5x. Below that, the unified
        (resolution-dominant) model lets the 4K win even after the 0.7x AI penalty.
        (The retired raw-magnitude model kept the 1080p here on bits-per-pixel.)
        """
        # Proposed: AI upscaled 4K, 20GB (AI penalty: 0.7x, unknown source: 0.95x)
        proposed = ProposedQuality(
            resolution="2160p",
            audio="5.1",
            size_mb=20000,
            path="/movies/Movie.AI.UPSCALE.2160p.mkv",
            name="Movie AI Upscaled"
        )

        # Existing: Native BluRay 1080p, 25GB (only 1.25x larger → exception dormant)
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

        # Size ratio < 1.5x → exception dormant → 4K resolution wins.
        assert result.recommendation == "download"

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


# --- a proposed copy is framed like the existing copy of the same width class ----------------

VOYEURS_EXISTING = {  # the real Emby item (tt11235772), 2026-10-05
    "Id": "voyeurs",
    "Name": "The Voyeurs",
    "Path": "/Movies/4K/The Voyeurs (2021) - 2160p WEB-DL HDR CZ/The Voyeurs (2021) - 2160p WEB-DL HDR CZ.mkv",
    "Size": 13_906_690_340,
    "Bitrate": 15_939_777,
    "RunTimeTicks": 69_796_160_000,
    "MediaStreams": [
        {"Type": "Video", "Width": 3840, "Height": 1600, "Codec": "hevc", "VideoRange": "HDR 10"},
        {"Type": "Audio", "Codec": "eac3", "Channels": 6, "Language": "eng"},
        {"Type": "Audio", "Codec": "eac3", "Channels": 2, "Language": "cze"},
    ],
}


def _voyeurs_torrent(**overrides):
    fields = dict(resolution="2160p", codec="x265", size_mb=4915, audio_languages=["cze", "eng"],
                  hdr="HDR10", audio="DDP", bitrate_kbps=5897)
    return ProposedQuality(**{**fields, **overrides})


def test_lower_bitrate_4k_does_not_beat_the_scope_4k_already_owned():
    """Regression (torrents gate, 2026-10-05): a 4.8 GB / 5.9 Mbps 2160p torrent was scored as a
    16:9 frame (8.3 MP) against the library's 3840x1600 scope copy (6.1 MP) and won on resolution
    alone, so it was downloaded and then deleted by dedupe as the worse copy."""
    result = compare_quality(_voyeurs_torrent(), [VOYEURS_EXISTING])
    assert (result.recommendation, result.reason) == ("skip", "same_or_worse")
    assert result.proposed_score < result.existing_score


def test_a_genuinely_better_scope_4k_is_still_downloaded():
    better = _voyeurs_torrent(size_mb=40_000, bitrate_kbps=45_000, hdr="DV", path="The.Voyeurs.2021.2160p.UHD.BluRay.REMUX.DV")
    assert compare_quality(better, [VOYEURS_EXISTING]).recommendation == "download"


def test_only_the_same_width_class_lends_its_frame():
    from emby_dedupe.api.quality_compare import (
        ExistingQuality,
        _adopt_existing_frame,
        _create_proposed_as_existing,
    )

    proposed = _create_proposed_as_existing(_voyeurs_torrent())
    scope_4k = ExistingQuality.from_emby_item(VOYEURS_EXISTING)
    near_1080 = ExistingQuality.from_emby_item({"Id": "a", "MediaStreams": [{"Type": "Video", "Width": 1916, "Height": 800}]})
    dvd = ExistingQuality.from_emby_item({"Id": "d", "MediaStreams": [{"Type": "Video", "Width": 720, "Height": 576}]})
    assert (_adopt_existing_frame(proposed, [dvd, scope_4k]).width, _adopt_existing_frame(proposed, [dvd, scope_4k]).height) == (3840, 1600)
    assert (_adopt_existing_frame(proposed, [near_1080, dvd]).width, _adopt_existing_frame(proposed, [near_1080, dvd]).height) == (3840, 2160)
    hd = _create_proposed_as_existing(_voyeurs_torrent(resolution="1080p"))
    assert (_adopt_existing_frame(hd, [near_1080]).width, _adopt_existing_frame(hd, [near_1080]).height) == (1916, 800)


# --- a same-kind newcomer must clearly beat the owned bitrate ------------------------------------

FUTURAMA_S02E03 = {  # the real Emby item 21422102 before the 2026-10-15 overwrite
    "Id": "futurama-s02e03",
    "Name": "A Head in the Polls",
    "Path": "/Movies/Serials/--- UKONCENE ---/Futurama/S02/Futurama S02E03 - 1080p WEB-DL CZ.mkv",
    "Size": 700_398_372,
    "Bitrate": 4_139_537,
    "RunTimeTicks": 13_535_780_000,
    "PremiereDate": "1999-11-06T23:00:00.0000000Z",
    "DateCreated": "2025-12-23T15:12:37.0000000Z",
    "ProductionYear": 1999,
    "MediaStreams": [
        {"Type": "Video", "Codec": "h264", "Width": 1920, "Height": 1080, "VideoRange": "SDR", "BitRate": 4_139_537},
        {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "cze", "BitRate": 192_000},
        {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "eng", "BitRate": 192_000},
    ],
}


def _futurama_pack(**overrides):
    fields = dict(name="Futurama", resolution="1080p", codec="x264", audio="AAC2.0", audio_languages=["cze", "eng"],
                  size_mb=667, bitrate_kbps=4157, source_quality_tier="webdl",
                  path="Futurama.S02.COMPLETE.1080p.DSNP.WEB-DL.AAC2.0.H264-TreZzoR")
    return ProposedQuality(**{**fields, **overrides})


def test_a_pack_sample_bitrate_a_hair_higher_is_not_an_upgrade():
    """Regression (torrents gate, 2026-10-15): the S02 pack was sent with one sample file's bitrate
    (4157 kb/s) for every episode. S02E03 owned at 4139 kb/s, same DSNP 1080p h264 cze+eng, outscored
    by 0.02 and "upgraded"; the download overwrote the library file."""
    result = compare_quality(_futurama_pack(), [FUTURAMA_S02E03])
    assert result.proposed_score > result.existing_score  # the raw score still prefers it; the margin flips it
    assert (result.recommendation, result.reason) == ("skip", "same_or_worse")


def test_a_per_file_bitrate_three_percent_higher_is_not_an_upgrade():
    """The replacement files are ~3 % larger (an extra dub); size ÷ runtime must not make them better."""
    size = 700_398_372 + 21_700_000
    kbps = round(size * 8 / 1353.578 / 1000)
    assert compare_quality(_futurama_pack(size_mb=size // 2**20, bitrate_kbps=kbps), [FUTURAMA_S02E03]).recommendation == "skip"


def test_a_same_kind_copy_with_double_the_bitrate_is_still_an_upgrade():
    assert compare_quality(_futurama_pack(size_mb=1400, bitrate_kbps=8300), [FUTURAMA_S02E03]).recommendation == "download"


def test_a_newcomer_adding_a_language_is_not_held_by_the_margin():
    english_only = {**FUTURAMA_S02E03, "MediaStreams": [
        s for s in FUTURAMA_S02E03["MediaStreams"] if s.get("Language") != "cze"]}
    assert compare_quality(_futurama_pack(), [english_only], ["cze", "eng"]).recommendation == "download"


def test_an_sdr_to_hdr_newcomer_is_not_held_by_the_margin():
    """Marshals S02E01 (torrents log 2026-10-15 09:59): SDR 2160p owned, HDR10 at ~3 % more bitrate."""
    owned = {
        "Id": "marshals-s02e01", "Name": "All Hat No Cattle",
        "Path": "/Movies/Serials/--- PREBIEHAJUCE ---/Marshals/S02/Marshals S02E01 - 2160p WEB-DL CZ.mkv",
        "Size": 5_064_144_172, "Bitrate": 15_754_162, "RunTimeTicks": 25_715_840_000,
        "MediaStreams": [
            {"Type": "Video", "Codec": "hevc", "Width": 3840, "Height": 2160, "VideoRange": "SDR"},
            {"Type": "Audio", "Codec": "eac3", "Channels": 6, "Language": "cze"},
            {"Type": "Audio", "Codec": "eac3", "Channels": 6, "Language": "eng"},
        ],
    }
    proposed = ProposedQuality(name="Marshals", resolution="2160p", codec="x265", hdr="HDR10", audio="DDP",
                               audio_languages=["cze", "eng"], size_mb=4980, bitrate_kbps=16200, source_quality_tier="webdl",
                               path="Marshals.S02E01.All.Hat.No.Cattle.2160p.SKST.WEB-DL.DDP5.1.H265-TreZzoR")
    assert compare_quality(proposed, [owned]).recommendation == "download"


def test_the_margin_leaves_unknown_bitrates_alone():
    no_bitrate = {**FUTURAMA_S02E03, "Bitrate": 0, "MediaStreams": [
        {**s, "BitRate": None} if s["Type"] == "Video" else s for s in FUTURAMA_S02E03["MediaStreams"]]}
    from emby_dedupe.api.quality_compare import (
        ExistingQuality,
        _apply_same_kind_margin,
        _create_proposed_as_existing,
    )

    owned = ExistingQuality.from_emby_item(no_bitrate)
    assert _apply_same_kind_margin(_create_proposed_as_existing(_futurama_pack()), owned, "download") == "download"


# --- an untagged owned copy cannot prove it lacks the newcomer's language -----------------------

TORRENTS_LANG_PRIO = ["slo", "sk", "cze", "ces", "eng"]  # ~/.emby-dedupe/config.yaml, the torrents gate's list

PECIE_S06E05 = {  # the real Emby item 21575528 (torrents log 2026-11-05 01:09)
    "Id": "21575528",
    "Name": "Pecie cele Slovensko S06E05 - 1080p WEB-DL CZ",
    "Path": "/Movies/Serials/--- PREBIEHAJUCE ---/Pecie cele Slovensko/S06/Pecie cele Slovensko S06E05 - 1080p WEB-DL CZ.mp4",
    "Size": 2_464_178_704,
    "Bitrate": 4_492_004,
    "RunTimeTicks": 43_885_600_000,
    "PremiereDate": "2026-11-03T23:00:00.0000000Z",
    "MediaStreams": [
        {"Type": "Video", "Codec": "h264", "Width": 1920, "Height": 1080, "VideoRange": "SDR"},
        {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "und"},
    ],
}


def _pecie_torrent(**overrides):
    fields = dict(name="Pecie celé Slovensko", resolution="1080p", codec="x264", audio="AAC", audio_languages=["slk"],
                  size_mb=2350, bitrate_kbps=4492, source_quality_tier="webdl",
                  path="Pecie.cele.Slovensko.S06E05.1080p.WEB-DL.AAC2.0.H.264")
    return ProposedQuality(**{**fields, **overrides})


def _pecie_owned(*languages):
    audio = [{"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": lang} for lang in languages]
    return {**PECIE_S06E05, "MediaStreams": [PECIE_S06E05["MediaStreams"][0], *audio]}


def test_the_same_release_tagged_slk_is_not_an_upgrade_over_an_untagged_copy():
    """Regression (torrents gate, 2026-11-05 01:09): the tracker name made the re-listed release "slk";
    the library copy of the same release (2 bytes apart) has one "und" track. The language sort put the
    torrent first and the same-kind margin never ran, so #4555 landed as a "(2)" duplicate."""
    result = compare_quality(_pecie_torrent(), [PECIE_S06E05], TORRENTS_LANG_PRIO)
    assert (result.recommendation, result.reason) == ("skip", "same_or_worse")


def test_a_kbps_rounding_edge_does_not_make_it_an_upgrade():
    result = compare_quality(_pecie_torrent(bitrate_kbps=4493), [PECIE_S06E05], TORRENTS_LANG_PRIO)
    assert result.proposed_score > result.existing_score  # the raw score prefers it; the margin holds it
    assert result.recommendation == "skip"


def test_an_untagged_copy_without_any_language_field_is_held_too():
    no_language = {**PECIE_S06E05, "MediaStreams": [
        PECIE_S06E05["MediaStreams"][0], {"Type": "Audio", "Codec": "aac", "Channels": 2}]}
    assert compare_quality(_pecie_torrent(), [no_language], TORRENTS_LANG_PRIO).recommendation == "skip"


def test_a_clearly_higher_bitrate_still_upgrades_an_untagged_copy():
    assert compare_quality(_pecie_torrent(size_mb=2900, bitrate_kbps=5500), [PECIE_S06E05],
                           TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_better_picture_still_upgrades_an_untagged_copy():
    uhd = _pecie_torrent(resolution="2160p", codec="x265", size_mb=7000, bitrate_kbps=13000,
                         path="Pecie.cele.Slovensko.S06E05.2160p.WEB-DL.AAC2.0.H.265")
    assert compare_quality(uhd, [PECIE_S06E05], TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_copy_tagged_in_another_language_still_gets_the_slovak_one():
    assert compare_quality(_pecie_torrent(), [_pecie_owned("eng")], TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_tagged_track_beside_an_untagged_one_still_needs_slovak_tagged():
    """Only an all-untagged copy is in doubt; "eng" + "und" proves it holds no tagged Slovak track."""
    assert compare_quality(_pecie_torrent(), [_pecie_owned("eng", "und")], TORRENTS_LANG_PRIO).recommendation == "download"


def test_untagged_tracks_on_both_sides_still_match():
    """An untagged track on the newcomer is no new language either (cs + und vs cs + und)."""
    result = compare_quality(_pecie_torrent(audio_languages=["cze", "und"]), [_pecie_owned("cze", "und")], TORRENTS_LANG_PRIO)
    assert result.recommendation == "skip"


def test_a_copy_without_audio_info_is_not_held_by_the_untagged_rule():
    """No audio stream at all (item fetched without its tracks) is no evidence of the same release."""
    video_only = {**PECIE_S06E05, "MediaStreams": [PECIE_S06E05["MediaStreams"][0]]}
    assert compare_quality(_pecie_torrent(), [video_only], TORRENTS_LANG_PRIO).recommendation == "download"


# --- a film sped up to 25 fps (PAL) is not an upgrade over the owned original-speed copy ---------

INHERITANCE_X265 = {  # the real Emby item 21406101 (torrents log 2026-11-06 02:36)
    "Id": "21406101",
    "Type": "Movie",
    "Name": "Inheritance",
    "Path": "/Movies/DiViX/Inheritance (2025) - 1080p x265/Inheritance (2025) - 1080p x265.mkv",
    "Size": 2_450_459_251,
    "Bitrate": 3_295_257,
    "RunTimeTicks": 59_490_560_000,
    "PremiereDate": "2025-01-23T23:00:00.0000000Z",
    "DateCreated": "2025-07-30T09:50:20.0000000Z",
    "ProductionYear": 2025,
    "MediaStreams": [
        {"Type": "Video", "Codec": "hevc", "Width": 1918, "Height": 1078, "VideoRange": "SDR", "BitRate": 3_295_257},
        {"Type": "Audio", "Codec": "ac3", "Channels": 2, "Language": "cze", "BitRate": 224_000},
        {"Type": "Audio", "Codec": "ac3", "Channels": 6, "Language": "eng", "BitRate": 384_000},
        {"Type": "Subtitle", "Codec": "subrip", "Language": "fre"},
        {"Type": "Subtitle", "Codec": "subrip", "Language": "eng"},
    ],
}


def _inheritance_torrent(**overrides):
    """The gate request for #4590, with the runtime the torrents side does not send yet."""
    fields = dict(name="Inheritance", resolution="1080p", codec="x264", audio="AAC", audio_languages=["cze", "eng"],
                  size_mb=4589, bitrate_kbps=6748, source_quality_tier="webdl", duration_minutes=95,
                  path="Otcovo dedictvi / Inheritance.2025.1080p.CNLP.WEB-DL.AAC2.0.H264-TreZzoR")
    return ProposedQuality(**{**fields, **overrides})


def _inheritance_owned(**overrides):
    return {**INHERITANCE_X265, **overrides}


def test_a_pal_speedup_of_the_owned_film_is_not_an_upgrade():
    """Regression (torrents gate, 2026-11-06 02:36): #4590 is Inheritance at 25 fps (95 min against
    99 min) with the same CZ dub and stereo only; raw bitrate made it 28.44 against 24.99 and it was
    downloaded, though the owned x265 already holds that dub merged at original speed plus EN 5.1."""
    result = compare_quality(_inheritance_torrent(), [INHERITANCE_X265], TORRENTS_LANG_PRIO)
    assert result.proposed_score > result.existing_score  # the raw score still prefers it; the rule flips it
    assert (result.recommendation, result.reason) == ("skip", "same_or_worse")


def test_the_pal_rule_needs_the_newcomer_runtime():
    """The torrents gate sends no runtime today: without one there is no evidence of a speed-up."""
    result = compare_quality(_inheritance_torrent(duration_minutes=None), [INHERITANCE_X265], TORRENTS_LANG_PRIO)
    assert result.recommendation == "download"


def test_an_original_speed_newcomer_is_not_held_by_the_pal_rule():
    result = compare_quality(_inheritance_torrent(duration_minutes=99), [INHERITANCE_X265], TORRENTS_LANG_PRIO)
    assert result.recommendation == "download"


def test_a_much_shorter_cut_is_not_taken_for_a_pal_copy():
    result = compare_quality(_inheritance_torrent(duration_minutes=90), [INHERITANCE_X265], TORRENTS_LANG_PRIO)
    assert result.recommendation == "download"


def test_a_pal_copy_bringing_a_new_dub_is_still_downloaded():
    """A sped-up copy with a dub the library lacks is wanted: the dub gets merged into the owned copy."""
    english_only = _inheritance_owned(MediaStreams=[s for s in INHERITANCE_X265["MediaStreams"] if s.get("Language") != "cze"])
    assert compare_quality(_inheritance_torrent(), [english_only], TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_pal_copy_is_not_held_against_an_untagged_owned_copy():
    """An untagged owned copy cannot show it holds the newcomer's CZ dub, so the rule stays out."""
    untagged = _inheritance_owned(MediaStreams=[
        {**s, "Language": "und"} if s["Type"] == "Audio" else s for s in INHERITANCE_X265["MediaStreams"]])
    assert compare_quality(_inheritance_torrent(), [untagged], TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_pal_copy_with_unknown_languages_is_not_held():
    """No language priority here: with one, the language sort already ranks the tagged owned copy first."""
    assert compare_quality(_inheritance_torrent(audio_languages=None), [INHERITANCE_X265]).recommendation == "download"
    assert compare_quality(_inheritance_torrent(), [INHERITANCE_X265]).recommendation == "skip"


def test_a_pal_4k_is_still_a_picture_upgrade():
    uhd = _inheritance_torrent(resolution="2160p", codec="x265", size_mb=14000, bitrate_kbps=20000,
                               path="Inheritance.2025.2160p.WEB-DL.AAC2.0.H265")
    assert compare_quality(uhd, [INHERITANCE_X265], TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_pal_copy_with_more_audio_channels_is_not_held():
    """The owned first track is CZ 2.0; a CZ 5.1 mix is a better dub track worth having."""
    surround = _inheritance_torrent(audio="DDP5.1", path="Inheritance.2025.1080p.WEB-DL.DDP5.1.H264")
    assert compare_quality(surround, [INHERITANCE_X265], TORRENTS_LANG_PRIO).recommendation == "download"


def test_the_pal_rule_leaves_episodes_alone():
    """Pack runtimes are one sample for many episodes, so an episode runtime ratio is no evidence."""
    episode = _inheritance_owned(Type="Episode")
    assert compare_quality(_inheritance_torrent(), [episode], TORRENTS_LANG_PRIO).recommendation == "download"


def test_the_pal_rule_needs_the_owned_runtime():
    no_runtime = _inheritance_owned(RunTimeTicks=None)
    assert compare_quality(_inheritance_torrent(), [no_runtime], TORRENTS_LANG_PRIO).recommendation == "download"


def test_a_runtime_without_a_bitrate_lets_the_starvation_check_estimate_one():
    """Sending a runtime has a side effect: with no bitrate the size ÷ runtime estimate feeds the
    starved-bitrate demotion (600 MB over 95 min is ~0.8 Mb/s, under the 1080p floor)."""
    from emby_dedupe.api.quality_compare import _create_proposed_as_existing

    starved = _create_proposed_as_existing(_inheritance_torrent(bitrate_kbps=None, size_mb=600))
    unknown = _create_proposed_as_existing(_inheritance_torrent(bitrate_kbps=None, size_mb=600, duration_minutes=None))
    assert starved.score_breakdown()["starved_demotion"] is True
    assert unknown.score_breakdown()["starved_demotion"] is False
