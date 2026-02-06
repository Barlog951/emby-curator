"""
Tests for metadata module with focus on quality assessment and date handling
"""
import pytest
from unittest.mock import patch, Mock, MagicMock

from emby_dedupe.api.metadata import (
    get_quality_description,
    rate_media_items,
    get_image_url
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
            "SeasonNumber": 1,
            "IndexNumber": 5
        }
        
        quality = get_quality_description(item)
        
        # Check TV-specific fields
        assert quality["is_episode"] == True
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
    
