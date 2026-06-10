"""
Tests for metadata module with focus on quality assessment and date handling
"""
from unittest.mock import patch

from emby_dedupe.api.metadata import (
    _build_tv_metadata,
    _extract_premiere_date,
    _format_file_size,
    _parse_iso_date,
    _resolve_date_added,
    _try_any_date_field,
    _try_fallback_date_fields,
    _try_filesystem_date,
    _try_parse_date_field,
    get_image_url,
    get_quality_description,
    rate_media_items,
)


class TestMetadata:
    """Tests for the metadata module."""

    def test_get_quality_description(self, sample_media_item):
        """Test getting quality description from a media item."""
        result = get_quality_description(sample_media_item)

        # Check structure
        assert "video" in result
        assert "audio" in result
        assert "size" in result

        # Check content
        assert result["video"]["codec"] == "h264"
        assert result["video"]["resolution"] == "1080p"
        assert result["audio"]["codec"] == "aac"
        assert result["audio"]["channels"] == 6
        assert result["size"] == 5000000000

    def test_get_quality_description_missing_streams(self):
        """Test getting quality description with missing streams."""
        item = {"Id": "12345", "Name": "Test Item"}  # No MediaStreams

        result = get_quality_description(item)

        assert result == {}

    def test_rate_media_items_empty(self):
        """Test rating empty media items list."""
        result = rate_media_items([])

        assert result == []

    def test_rate_media_items(self, sample_media_item):
        """Test rating media items."""
        # Create two items with different qualities
        items = [
            sample_media_item,  # 1080p, good quality
            {
                "Id": "67890",
                "Name": "Low Quality Item",
                "Path": "/movies/low_quality.mkv",
                "ServerId": "server1",
                "MediaStreams": [
                    {
                        "Type": "Video",
                        "Codec": "h264",
                        "Height": 720,
                        "Width": 1280,
                        "BitRate": 2000000,
                        "IsInterlaced": False,
                    },
                    {
                        "Type": "Audio",
                        "Codec": "aac",
                        "Channels": 2,
                        "BitRate": 128000,
                    }
                ],
                "Size": 2000000000,
                "Bitrate": 2100000
            }
        ]

        result = rate_media_items(items)

        # Should return 2 rated items
        assert len(result) == 2

        # First item should have higher rating
        assert result[0]["id"] == "12345"
        assert result[1]["id"] == "67890"
        assert result[0]["rating"] > result[1]["rating"]

        # Check that key fields are present
        for item in result:
            assert "id" in item
            assert "name" in item
            assert "rating" in item
            assert "quality_description" in item

    def test_rate_media_items_skip_missing_streams(self):
        """Test rating media items with missing streams."""
        items = [
            {"Id": "12345", "Name": "Missing Streams"}  # No MediaStreams
        ]

        result = rate_media_items(items)

        # Should skip items without MediaStreams
        assert result == []

    def test_get_image_url(self):
        """Test generating image URL."""
        # With image tags
        url = get_image_url("http://example.com", "item123", {"Primary": "tag123"}, "server1")
        assert "http://example.com/Items/item123/Images/Primary" in url
        assert "tag=tag123" in url

        # Without image tags
        url = get_image_url("http://example.com", "item123", {}, "server1")
        assert "http://example.com/web/assets/img/media.png" in url

        # With API key
        url = get_image_url("http://example.com", "item123", {"Primary": "tag123"}, "server1", "api_key123")
        assert "api_key=api_key123" in url

    def test_get_quality_description_with_date_fields(self):
        """Test quality description with various date formats."""
        # Test with DateCreated
        item = {
            "Id": "date1",
            "Name": "Test with DateCreated",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264"},
                {"Type": "Audio", "Codec": "aac"}
            ],
            "DateCreated": "2023-01-15T12:34:56.789Z"
        }

        quality = get_quality_description(item)
        assert "date_added" in quality
        assert "2023-01-15" in quality["date_added"]

        # Test with DateModified
        item = {
            "Id": "date2",
            "Name": "Test with DateModified",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264"},
                {"Type": "Audio", "Codec": "aac"}
            ],
            "DateModified": "2023-01-15T12:34:56.789Z"
        }

        quality = get_quality_description(item)
        assert "date_added" in quality
        assert "2023-01-15" in quality["date_added"]

        # Test with PremiereDate
        item = {
            "Id": "date3",
            "Name": "Test with PremiereDate",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264"},
                {"Type": "Audio", "Codec": "aac"}
            ],
            "PremiereDate": "2023-01-15T12:34:56.789Z"
        }

        quality = get_quality_description(item)
        assert "date_added" in quality
        assert "2023-01-15" in quality["date_added"]

        # Test with invalid date format
        item = {
            "Id": "date4",
            "Name": "Test with invalid date",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264"},
                {"Type": "Audio", "Codec": "aac"}
            ],
            "DateCreated": "not-a-date"
        }

        quality = get_quality_description(item)
        assert "date_added" in quality
        # Should handle invalid dates gracefully
        assert quality["date_added"] in ["unknown", "not-a-date"]

    def test_get_quality_description_with_tv_episode_metadata(self):
        """Test quality description with TV episode metadata."""
        item = {
            "Id": "episode1",
            "Name": "Test Episode",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264"},
                {"Type": "Audio", "Codec": "aac"}
            ],
            "SeriesName": "Test Series",
            # Emby API uses ParentIndexNumber for season, IndexNumber for episode
            "ParentIndexNumber": 1,
            "IndexNumber": 5
        }

        quality = get_quality_description(item)

        # Check TV-specific fields
        assert quality["is_episode"]
        assert quality["series_name"] == "Test Series"
        assert quality["season_number"] == 1
        assert quality["episode_number"] == 5
        assert quality["episode_info"] == "S1E5"

    def test_get_quality_description_with_audio_languages(self):
        """Test quality description with audio language information."""
        item = {
            "Id": "audio1",
            "Name": "Test with audio languages",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264"},
                {"Type": "Audio", "Codec": "aac", "Language": "eng"},
                {"Type": "Audio", "Codec": "aac", "Language": "spa"},
                {"Type": "Audio", "Codec": "aac"}  # No language specified
            ]
        }

        quality = get_quality_description(item)

        # Check audio languages
        assert "languages" in quality["audio"]
        assert "eng" in quality["audio"]["languages"]
        assert "spa" in quality["audio"]["languages"]
        assert len(quality["audio"]["languages"]) == 2  # Should only include languages that are specified

    def test_rate_media_items_with_date_priorities(self, sample_media_item):
        """Test rating media items with date considerations."""
        # Create item with newer date
        newer_item = sample_media_item.copy()
        newer_item.update({
            "Id": "newer123",
            "Name": "Newer Item",
            "DateCreated": "2023-01-15T12:34:56.789Z",  # Newer date using DateCreated
        })

        # Create item with older date but better quality
        older_better_item = sample_media_item.copy()
        older_better_item.update({
            "Id": "older456",
            "Name": "Older Better Item",
            "DateCreated": "2022-01-15T12:34:56.789Z",  # Older date
            "Size": 10000000000,  # Bigger size (better quality)
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Codec": "h265",  # Better codec
                    "Height": 2160,   # 4K
                    "Width": 3840,
                    "BitRate": 20000000,
                    "BitDepth": 10,   # Better bit depth
                }
            ] + older_better_item["MediaStreams"][1:]
        })

        # Rate the items - quality should take precedence over date
        with patch('emby_dedupe.api.metadata.logger'):  # Mock logger to prevent actual logging
            result = rate_media_items([newer_item, older_better_item])

            # Verify results exist
            assert len(result) == 2

            # Get items by ID
            older_item_result = next((item for item in result if item["id"] == "older456"), None)
            newer_item_result = next((item for item in result if item["id"] == "newer123"), None)

            # Make sure both items were found
            assert older_item_result is not None
            assert newer_item_result is not None

            # Check that date_added is included in quality description
            assert "date_added" in older_item_result["quality_description"]
            assert "date_added" in newer_item_result["quality_description"]

            # The 4K item should have a higher quality rating
            assert older_item_result["rating"] > newer_item_result["rating"]

    def test_rate_media_items_with_source_quality(self):
        """Test that source quality multipliers are applied during rating."""
        # Create two identical items except for source quality
        base_item = {
            "Id": "base123",
            "Name": "Base Movie",
            "Path": "/movies/Movie.1080p.mkv",  # Unknown source
            "ServerId": "server1",
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Codec": "h264",
                    "Height": 1080,
                    "Width": 1920,
                    "BitRate": 8000000,
                },
                {
                    "Type": "Audio",
                    "Codec": "aac",
                    "Channels": 6,
                    "BitRate": 384000,
                }
            ],
            "Size": 5000000000,
            "Bitrate": 8500000,
            "DateCreated": "2023-01-15T10:00:00Z"
        }
        bluray_item = {
            "Id": "bluray456",
            "Name": "BluRay Movie",
            "Path": "/movies/Movie.BluRay.1080p.mkv",  # BluRay source
            "ServerId": "server1",
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Codec": "h264",
                    "Height": 1080,
                    "Width": 1920,
                    "BitRate": 8000000,
                },
                {
                    "Type": "Audio",
                    "Codec": "aac",
                    "Channels": 6,
                    "BitRate": 384000,
                }
            ],
            "Size": 5000000000,
            "Bitrate": 8500000,
            "DateCreated": "2023-01-15T10:00:00Z"
        }

        result = rate_media_items([base_item, bluray_item])

        assert len(result) == 2

        # Get items by ID
        base_result = next((item for item in result if item["id"] == "base123"), None)
        bluray_result = next((item for item in result if item["id"] == "bluray456"), None)

        assert base_result is not None
        assert bluray_result is not None

        # BluRay should have higher rating due to 1.15x multiplier
        assert bluray_result["rating"] > base_result["rating"]

        # Check that source quality info is in quality description
        assert "source_quality_multiplier" in bluray_result["quality_description"]
        assert bluray_result["quality_description"]["source_quality_multiplier"] > 1.0

    def test_rate_media_items_with_ai_upscale(self):
        """Test that AI upscale penalty is applied during rating."""
        # Create two identical items except for AI upscale
        normal_item = {
            "Id": "normal123",
            "Name": "Normal Movie",
            "Path": "/movies/Movie.BluRay.2160p.mkv",  # Native 4K BluRay
            "ServerId": "server1",
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Codec": "hevc",
                    "Height": 2160,
                    "Width": 3840,
                    "BitRate": 20000000,
                },
                {
                    "Type": "Audio",
                    "Codec": "eac3",
                    "Channels": 8,
                    "BitRate": 768000,
                }
            ],
            "Size": 20000000000,
            "Bitrate": 25000000,
            "DateCreated": "2023-01-15T10:00:00Z"
        }
        ai_upscale_item = {
            "Id": "ai456",
            "Name": "AI Upscaled Movie",
            "Path": "/movies/Movie.AI.UPSCALE.2160p.mkv",  # AI upscaled 4K
            "ServerId": "server1",
            "MediaStreams": [
                {
                    "Type": "Video",
                    "Codec": "hevc",
                    "Height": 2160,
                    "Width": 3840,
                    "BitRate": 20000000,
                },
                {
                    "Type": "Audio",
                    "Codec": "eac3",
                    "Channels": 8,
                    "BitRate": 768000,
                }
            ],
            "Size": 20000000000,
            "Bitrate": 25000000,
            "DateCreated": "2023-01-15T10:00:00Z"
        }

        result = rate_media_items([normal_item, ai_upscale_item])

        assert len(result) == 2

        # Get items by ID
        normal_result = next((item for item in result if item["id"] == "normal123"), None)
        ai_result = next((item for item in result if item["id"] == "ai456"), None)

        assert normal_result is not None
        assert ai_result is not None

        # Normal should have higher rating due to AI upscale penalty (0.7x)
        assert normal_result["rating"] > ai_result["rating"]

        # Check that AI upscale info is in quality description
        assert "is_ai_upscale" in ai_result["quality_description"]
        assert ai_result["quality_description"]["is_ai_upscale"] is True
        assert normal_result["quality_description"]["is_ai_upscale"] is False


# ========== SAFETY NET TESTS FOR get_quality_description (Grade-F Function) ==========

class TestGetQualityDescriptionSafetyNet:
    """Safety net tests for get_quality_description (CC 100) to protect Phase 3 refactoring."""

    def test_get_quality_multiple_video_streams(self):
        """Test handling of multiple video streams (picks first)."""
        item = {
            "Id": "123",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840, "BitRate": 20000000},
                {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 8000000},
                {"Type": "Audio", "Codec": "aac", "Channels": 6, "BitRate": 384000},
            ],
            "Size": 10000000000,
        }

        result = get_quality_description(item)

        # Should use first video stream
        assert result["video"]["codec"] == "h265"
        assert "video" in result

    def test_get_quality_unusual_codecs(self):
        """Test handling of unusual/modern codecs."""
        item = {
            "Id": "123",
            "MediaStreams": [
                {"Type": "Video", "Codec": "av1", "Height": 2160, "Width": 3840, "BitRate": 15000000},
                {"Type": "Audio", "Codec": "opus", "Channels": 6, "BitRate": 256000, "Language": "en"},
            ],
            "Size": 8000000000,
        }

        result = get_quality_description(item)

        assert result["video"]["codec"] == "av1"
        assert result["audio"]["codec"] == "opus"

    def test_get_quality_zero_bitrate(self):
        """Test handling of zero/missing bitrate."""
        item = {
            "Id": "123",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 0},
                {"Type": "Audio", "Codec": "aac", "Channels": 2},
            ],
            "Size": 5000000000,
        }

        result = get_quality_description(item)

        # Should handle zero bitrate gracefully
        assert "video" in result
        assert result["video"]["bitrate"] == 0

    def test_get_quality_complex_date_parsing(self):
        """Test complex date field fallback logic."""
        item = {
            "Id": "123",
            "MediaStreams": [
                {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
            ],
            "Size": 5000000000,
            "ProductionYear": 2024,  # Fallback date field
        }

        result = get_quality_description(item)

        # Should include date_added from ProductionYear fallback
        assert "date_added" in result
        assert "2024" in result["date_added"]


class TestMetadataHelpers:
    """Tests for metadata helper functions extracted in Phase 3."""

    def test_format_file_size_zero(self):
        """Test formatting zero bytes."""
        assert _format_file_size(0) == "unknown"

    def test_format_file_size_bytes(self):
        """Test formatting bytes."""
        assert _format_file_size(500) == "500 bytes"

    def test_format_file_size_kb(self):
        """Test formatting kilobytes."""
        result = _format_file_size(2048)  # 2 KB
        assert "KB" in result
        assert "2.00" in result

    def test_format_file_size_mb(self):
        """Test formatting megabytes."""
        result = _format_file_size(5242880)  # 5 MB
        assert "MB" in result
        assert "5.00" in result

    def test_format_file_size_gb(self):
        """Test formatting gigabytes."""
        result = _format_file_size(5368709120)  # 5 GB
        assert "GB" in result
        assert "5.00" in result

    def test_parse_iso_date_with_time(self):
        """Test parsing ISO date with time."""
        result = _parse_iso_date("2024-01-15T14:30:45.123Z", include_time=True)
        assert result == "2024-01-15 14:30"

    def test_parse_iso_date_without_time(self):
        """Test parsing ISO date without time."""
        result = _parse_iso_date("2024-01-15T14:30:45.123Z", include_time=False)
        assert result == "2024-01-15"

    def test_parse_iso_date_invalid(self):
        """Test parsing invalid ISO date."""
        assert _parse_iso_date("not-a-date", include_time=True) is None
        assert _parse_iso_date("2024-01-15", include_time=True) is None  # No T separator

    def test_parse_iso_date_malformed(self):
        """Test parsing malformed ISO date."""
        assert _parse_iso_date("2024T14:30:45", include_time=True) is None  # Invalid date part

    def test_resolve_date_added_from_datecreated(self):
        """Test resolving date from DateCreated field."""
        item = {"DateCreated": "2024-01-15T14:30:45.123Z"}
        result = _resolve_date_added(item)
        assert "2024-01-15 14:30" in result

    def test_resolve_date_added_from_datemodified(self):
        """Test resolving date from DateModified when DateCreated missing."""
        item = {"DateModified": "2024-02-20T10:15:30.000Z"}
        result = _resolve_date_added(item)
        assert "2024-02-20 10:15" in result

    def test_resolve_date_added_from_production_year(self):
        """Test resolving date from ProductionYear."""
        item = {"ProductionYear": 2023}
        result = _resolve_date_added(item)
        assert "2023-01-01 (year only)" in result

    def test_resolve_date_added_unknown(self):
        """Test resolving date when no date fields available."""
        item = {"Id": "123", "Name": "Test"}
        result = _resolve_date_added(item)
        assert result == "unknown"

    @patch('os.path.exists')
    @patch('os.path.getmtime')
    def test_resolve_date_added_from_filesystem(self, mock_getmtime, mock_exists):
        """Test resolving date from filesystem modification time."""
        mock_exists.return_value = True
        mock_getmtime.return_value = 1704211200  # 2024-01-02 12:00:00 UTC

        item = {"Path": "/fake/path/movie.mkv"}
        result = _resolve_date_added(item)
        assert "file modified time" in result

    def test_extract_premiere_date_valid(self):
        """Test extracting premiere date with valid data."""
        item = {"PremiereDate": "2024-03-15T00:00:00.000Z"}
        result = _extract_premiere_date(item)
        assert result == "2024-03-15"

    def test_extract_premiere_date_missing(self):
        """Test extracting premiere date when field missing."""
        item = {"Id": "123"}
        result = _extract_premiere_date(item)
        assert result == "unknown"

    def test_extract_premiere_date_non_iso(self):
        """Test extracting premiere date with non-ISO format."""
        item = {"PremiereDate": "2024"}
        result = _extract_premiere_date(item)
        assert result == "2024"

    def test_build_tv_metadata_series(self):
        """Test building TV metadata for episode."""
        item = {
            "SeriesName": "Test Series",
            "ParentIndexNumber": 2,
            "IndexNumber": 5
        }
        quality_desc = {}

        _build_tv_metadata(item, quality_desc)

        assert quality_desc["is_episode"] is True
        assert quality_desc["series_name"] == "Test Series"
        assert quality_desc["season_number"] == 2
        assert quality_desc["episode_number"] == 5
        assert quality_desc["episode_info"] == "S2E5"

    def test_build_tv_metadata_movie(self):
        """Test building TV metadata for non-episode."""
        item = {"Name": "Movie"}
        quality_desc = {}

        _build_tv_metadata(item, quality_desc)

        assert quality_desc["is_episode"] is False

    def test_build_tv_metadata_incomplete(self):
        """Test building TV metadata with incomplete episode info."""
        item = {
            "SeriesName": "Test Series",
            "ParentIndexNumber": "unknown",
            "IndexNumber": 5
        }
        quality_desc = {}

        _build_tv_metadata(item, quality_desc)

        assert quality_desc["is_episode"] is True
        assert quality_desc["episode_info"] == "Unknown episode"

    def test_try_parse_date_field_success(self):
        """Test parsing date from a specific field."""
        item = {"DateCreated": "2024-01-15T14:30:45.123Z"}
        result = _try_parse_date_field(item, "DateCreated", include_time=True)
        assert result == "2024-01-15 14:30"

    def test_try_parse_date_field_missing(self):
        """Test parsing date from missing field."""
        item = {"OtherField": "value"}
        result = _try_parse_date_field(item, "DateCreated")
        assert result is None

    def test_try_parse_date_field_non_iso(self):
        """Test parsing non-ISO date."""
        item = {"DateCreated": "2024-01-15"}
        result = _try_parse_date_field(item, "DateCreated")
        assert result == "2024-01-15"

    def test_try_fallback_date_fields_production_year(self):
        """Test fallback to ProductionYear."""
        item = {"ProductionYear": 2023}
        result = _try_fallback_date_fields(item)
        assert "2023-01-01 (year only)" in result

    def test_try_fallback_date_fields_premiere_date(self):
        """Test fallback to PremiereDate."""
        item = {"PremiereDate": "2024-03-15T00:00:00.000Z"}
        result = _try_fallback_date_fields(item)
        assert result == "2024-03-15"

    def test_try_fallback_date_fields_none_found(self):
        """Test when no fallback fields available."""
        item = {"Name": "Test"}
        result = _try_fallback_date_fields(item)
        assert result is None

    @patch('os.path.exists')
    @patch('os.path.getmtime')
    def test_try_filesystem_date_success(self, mock_getmtime, mock_exists):
        """Test getting date from filesystem."""
        mock_exists.return_value = True
        mock_getmtime.return_value = 1704211200  # 2024-01-02 12:00:00 UTC

        item = {"Path": "/fake/path/movie.mkv"}
        result = _try_filesystem_date(item)
        assert result is not None
        assert "file modified time" in result

    def test_try_filesystem_date_no_path(self):
        """Test filesystem date when no path."""
        item = {"Name": "Test"}
        result = _try_filesystem_date(item)
        assert result is None

    def test_try_any_date_field_found(self):
        """Test finding alternative date field."""
        item = {"CustomDateField": "2024-01-15"}
        result = _try_any_date_field(item)
        assert "2024-01-15" in result
        assert "from CustomDateField" in result

    def test_try_any_date_field_none_found(self):
        """Test when no alternative date fields."""
        item = {"Name": "Test", "Size": 1000}
        result = _try_any_date_field(item)
        assert result is None
