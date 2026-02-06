"""Tests for source quality and AI upscale detection."""

import pytest

from emby_dedupe.api.quality_compare import (
    SOURCE_QUALITY_TIERS,
    detect_ai_upscale,
    detect_source_quality,
)


class TestSourceQualityDetection:
    """Tests for source quality detection."""

    def test_detect_bluray_remux_patterns(self):
        """Test detection of BluRay REMUX patterns."""
        test_paths = [
            "/movies/Movie.2023.2160p.UHD.BluRay.REMUX.x265/movie.mkv",
            "/movies/Movie.BDREMUX.1080p.mkv",
            "/movies/Movie.BluRay.Remux.2160p.mkv",
            "/movies/Movie.Blu-ray.Remux.mkv",
            "/movies/Movie.BD.REMUX.mkv",
        ]

        expected_bonus = SOURCE_QUALITY_TIERS["bluray_remux"]["bonus"]

        for path in test_paths:
            result = detect_source_quality(path, None)
            assert result == expected_bonus, f"Failed for path: {path}"

    def test_detect_bluray_patterns(self):
        """Test detection of BluRay patterns (non-REMUX)."""
        test_paths = [
            "/movies/Movie.2023.BluRay.1080p.x264.mkv",
            "/movies/Movie.Blu-Ray.720p.mkv",
            "/movies/Movie.Blu-ray.2160p.mkv",
            "/movies/Movie.BRRip.1080p.mkv",
            "/movies/Movie.BDRip.720p.mkv",
            "/movies/Movie.BD.Rip.mkv",
        ]

        expected_bonus = SOURCE_QUALITY_TIERS["bluray"]["bonus"]

        for path in test_paths:
            result = detect_source_quality(path, None)
            assert result == expected_bonus, f"Failed for path: {path}"

    def test_detect_webdl_patterns(self):
        """Test detection of WEB-DL patterns."""
        test_paths = [
            "/movies/Movie.2023.WEB-DL.1080p.mkv",
            "/movies/Movie.WEBDL.2160p.mkv",
            "/movies/Movie.WEB.DL.1080p.mkv",
            "/movies/Movie.WEBRip.720p.mkv",
            "/movies/Movie.WEB-Rip.1080p.mkv",
            "/movies/Movie.WEB.Rip.mkv",
        ]

        expected_bonus = SOURCE_QUALITY_TIERS["webdl"]["bonus"]

        for path in test_paths:
            result = detect_source_quality(path, None)
            assert result == expected_bonus, f"Failed for path: {path}"

    def test_detect_hdtv_patterns(self):
        """Test detection of HDTV patterns."""
        test_paths = [
            "/movies/Movie.2023.HDTV.1080p.mkv",
            "/movies/Movie.DVB.720p.mkv",
            "/movies/Movie.PDTV.mkv",
        ]

        expected_bonus = SOURCE_QUALITY_TIERS["hdtv"]["bonus"]

        for path in test_paths:
            result = detect_source_quality(path, None)
            assert result == expected_bonus, f"Failed for path: {path}"

    def test_detect_source_from_path(self):
        """Test detection from Path field."""
        path = "/movies/Movie.BluRay.1080p.mkv"
        name = "Movie Title"

        result = detect_source_quality(path, name)
        assert result == SOURCE_QUALITY_TIERS["bluray"]["bonus"]

    def test_detect_source_from_name(self):
        """Test detection from Name field when path is None."""
        path = None
        name = "Movie Title BluRay 1080p"

        result = detect_source_quality(path, name)
        assert result == SOURCE_QUALITY_TIERS["bluray"]["bonus"]

    def test_source_quality_case_insensitive(self):
        """Test case-insensitive pattern matching."""
        test_cases = [
            "/movies/Movie.bluray.1080p.mkv",
            "/movies/Movie.BLURAY.1080p.mkv",
            "/movies/Movie.BluRay.1080p.mkv",
            "/movies/Movie.BLuRaY.1080p.mkv",
        ]

        expected_bonus = SOURCE_QUALITY_TIERS["bluray"]["bonus"]

        for path in test_cases:
            result = detect_source_quality(path, None)
            assert result == expected_bonus, f"Case sensitivity failed for: {path}"

    def test_unknown_source_penalty(self):
        """Test that unknown sources get the default penalty."""
        test_paths = [
            "/movies/Movie.2023.1080p.mkv",
            "/movies/Movie.x265.mkv",
            "/movies/Some.Random.Movie.mkv",
        ]

        expected_bonus = SOURCE_QUALITY_TIERS["unknown"]["bonus"]

        for path in test_paths:
            result = detect_source_quality(path, None)
            assert result == expected_bonus, f"Failed for path: {path}"

    def test_source_quality_priority_order(self):
        """Test that first match (highest priority) wins when multiple patterns present."""
        # Path contains both REMUX and BluRay - REMUX should win
        path = "/movies/Movie.BluRay.REMUX.1080p.mkv"

        result = detect_source_quality(path, None)
        assert result == SOURCE_QUALITY_TIERS["bluray_remux"]["bonus"]

    def test_source_quality_with_missing_fields(self):
        """Test handling of None/empty path and name."""
        # Both None
        result = detect_source_quality(None, None)
        assert result == SOURCE_QUALITY_TIERS["unknown"]["bonus"]

        # Empty strings
        result = detect_source_quality("", "")
        assert result == SOURCE_QUALITY_TIERS["unknown"]["bonus"]


class TestAIUpscaleDetection:
    """Tests for AI upscale detection."""

    def test_detect_ai_upscale_patterns(self):
        """Test detection of all AI upscale patterns."""
        test_paths = [
            "/movies/Movie.2023.4K.AI.UPSCALE.mkv",
            "/movies/Movie.AI-UPSCALE.2160p.mkv",
            "/movies/Movie.AI_UPSCALE.mkv",
            "/movies/Movie.Ai.Upscale.4K.mkv",
            "/movies/Movie.Ai-Upscale.mkv",
            "/movies/Movie.Ai_Upscale.mkv",
            "/movies/Movie.UPSCALED.2160p.mkv",
            "/movies/Movie.Upscaled.4K.mkv",
            "/movies/Movie.AI.Enhanced.mkv",
            "/movies/Movie.AI-Enhanced.2160p.mkv",
            "/movies/Movie.AI_Enhanced.mkv",
        ]

        for path in test_paths:
            result = detect_ai_upscale(path, None)
            assert result is True, f"Failed to detect AI upscale in: {path}"

    def test_ai_upscale_case_insensitive(self):
        """Test case-insensitive AI upscale detection."""
        test_cases = [
            "/movies/Movie.ai.upscale.mkv",
            "/movies/Movie.AI.UPSCALE.mkv",
            "/movies/Movie.Ai.Upscale.mkv",
            "/movies/Movie.aI.uPsCaLe.mkv",
        ]

        for path in test_cases:
            result = detect_ai_upscale(path, None)
            assert result is True, f"Case sensitivity failed for: {path}"

    def test_ai_upscale_from_path_and_name(self):
        """Test detection from both path and name fields."""
        # From path
        path = "/movies/Movie.AI.UPSCALE.4K.mkv"
        name = "Movie Title"
        result = detect_ai_upscale(path, name)
        assert result is True

        # From name only
        path = None
        name = "Movie Title AI.UPSCALE 4K"
        result = detect_ai_upscale(path, name)
        assert result is True

    def test_no_ai_upscale_false_positive(self):
        """Test that movies with 'AI' in title don't trigger false positive."""
        # Movie titled "AI: Artificial Intelligence" should not trigger
        test_paths = [
            "/movies/AI.Artificial.Intelligence.2001.BluRay.1080p.mkv",
            "/movies/A.I.2001.mkv",
            "/movies/Movie.With.AI.In.Title.BluRay.mkv",
        ]

        for path in test_paths:
            result = detect_ai_upscale(path, None)
            assert result is False, f"False positive for: {path}"

    def test_no_ai_upscale_detected(self):
        """Test that normal content is not detected as AI upscaled."""
        test_paths = [
            "/movies/Movie.2023.BluRay.1080p.mkv",
            "/movies/Movie.2160p.WEB-DL.mkv",
            "/movies/Movie.4K.REMUX.mkv",
        ]

        for path in test_paths:
            result = detect_ai_upscale(path, None)
            assert result is False, f"False positive for: {path}"

    def test_ai_upscale_with_missing_fields(self):
        """Test handling of None/empty path and name."""
        # Both None
        result = detect_ai_upscale(None, None)
        assert result is False

        # Empty strings
        result = detect_ai_upscale("", "")
        assert result is False
