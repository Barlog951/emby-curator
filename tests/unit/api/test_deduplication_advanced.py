"""
Advanced tests for deduplication functionality
"""
import pytest
import re
from unittest.mock import patch, Mock, MagicMock, call

from emby_dedupe.api.deduplication import (
    determine_items_to_delete,
    process_duplicate_groups,
    build_disjoint_set
)


class TestAdvancedDeduplication:
    """Advanced tests for deduplication functionality."""
    
    def test_determine_items_to_delete_basic(self, sample_media_item):
        """Test basic determination of items to delete."""
        # Create a list of items with varying quality
        items = [
            sample_media_item,  # Higher quality
            {
                "Id": "67890",
                "Name": "Test Item (Lower Quality)",
                "Path": "/movies/low_quality.mkv",
                "ServerId": "server1",
                "MediaStreams": [
                    {
                        "Type": "Video",
                        "Codec": "h264",
                        "Height": 720,
                        "Width": 1280,
                        "BitRate": 2000000,
                        "BitDepth": 8,
                        "IsInterlaced": False,
                        "DisplayTitle": "720p"
                    },
                    {
                        "Type": "Audio",
                        "Codec": "aac",
                        "Channels": 2,
                        "BitRate": 128000
                    }
                ],
                "Size": 2000000000,
                "Bitrate": 2500000
            }
        ]
        
        # Run determination
        decision = determine_items_to_delete(["12345", "67890"], items)
        
        # Verify decision
        assert "keep" in decision
        assert "delete" in decision
        
        # High quality item should be kept
        assert decision["keep"]["id"] == "12345"
        
        # Low quality item should be deleted
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "67890"
    
    def test_determine_items_to_delete_multiple(self):
        """Test determination with multiple items to delete."""
        # Create several items with varying quality
        items = [
            {
                "Id": "12345",  # Best quality
                "Name": "Test Item 4K",
                "Path": "/path/to/4K.mkv",
                "MediaStreams": [
                    {
                        "Type": "Video",
                        "Codec": "h265",
                        "Height": 2160,
                        "Width": 3840,
                        "BitRate": 20000000,
                        "BitDepth": 10
                    },
                    {
                        "Type": "Audio",
                        "Codec": "dts",
                        "Channels": 8,
                        "BitRate": 1500000
                    }
                ],
                "Size": 15000000000,
                "Bitrate": 25000000
            },
            {
                "Id": "67890",  # Medium quality
                "Name": "Test Item 1080p",
                "Path": "/path/to/1080p.mkv",
                "MediaStreams": [
                    {
                        "Type": "Video",
                        "Codec": "h264",
                        "Height": 1080,
                        "Width": 1920,
                        "BitRate": 8000000,
                        "BitDepth": 8
                    },
                    {
                        "Type": "Audio",
                        "Codec": "aac",
                        "Channels": 6,
                        "BitRate": 384000
                    }
                ],
                "Size": 8000000000,
                "Bitrate": 10000000
            },
            {
                "Id": "24680",  # Lowest quality
                "Name": "Test Item 720p",
                "Path": "/path/to/720p.mkv",
                "MediaStreams": [
                    {
                        "Type": "Video",
                        "Codec": "h264",
                        "Height": 720,
                        "Width": 1280,
                        "BitRate": 2000000,
                        "BitDepth": 8
                    },
                    {
                        "Type": "Audio",
                        "Codec": "aac",
                        "Channels": 2,
                        "BitRate": 128000
                    }
                ],
                "Size": 2000000000,
                "Bitrate": 2500000
            }
        ]
        
        # Run determination
        decision = determine_items_to_delete(["12345", "67890", "24680"], items)
        
        # Verify decision
        assert "keep" in decision
        assert "delete" in decision
        
        # Best quality item should be kept
        assert decision["keep"]["id"] == "12345"
        
        # All other items should be deleted
        assert len(decision["delete"]) == 2
        delete_ids = [item["id"] for item in decision["delete"]]
        assert "67890" in delete_ids
        assert "24680" in delete_ids
    
    def test_determine_items_to_delete_empty(self):
        """Test determination with empty items list."""
        decision = determine_items_to_delete(["12345", "67890"], [])
        
        # Should return empty decision
        assert decision == {"keep": {}, "delete": []}
    
    
    def test_determine_items_with_duplicate_paths(self):
        """Test handling of duplicate paths in determination."""
        # Items with the same path
        # Updated test: With our new implementation, items with identical paths are skipped 
        # to avoid marking identical files for deletion
        items = [
            {
                "Id": "12345",
                "Name": "Test Item 1",
                "Path": "/movies/same_path.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6}
                ],
                "Size": 5000000000
            },
            {
                "Id": "67890",
                "Name": "Test Item 2",
                "Path": "/movies/same_path.mkv",  # Same path as the first item
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6}
                ],
                "Size": 5000000000
            }
        ]
        
        # Run determination
        decision = determine_items_to_delete(["12345", "67890"], items)
        
        # Verify that we correctly return an empty decision when paths are identical
        # (indicating no true duplicates found)
        assert "keep" in decision
        assert "delete" in decision
        assert not decision["keep"]  # keep should be empty
        assert len(decision["delete"]) == 0  # delete should be empty
    
    def test_determine_items_with_language_priorities(self):
        """Test item determination with language priorities."""
        # Create items with different languages
        items = [
            {
                "Id": "12345",  # Higher quality but non-priority language
                "Name": "Test Item German",
                "Path": "/movies/german.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "ger"}
                ],
                "Size": 10000000000
            },
            {
                "Id": "67890",  # Lower quality but priority language
                "Name": "Test Item English",
                "Path": "/movies/english.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "eng"}
                ],
                "Size": 5000000000
            }
        ]
        
        # Set language priorities (English first)
        lang_priorities = ["eng", "fre", "spa"]
        
        # Run determination with language priorities
        decision = determine_items_to_delete(["12345", "67890"], items, lang_priorities)
        
        # Verify that the English item is kept despite lower quality
        assert decision["keep"]["id"] == "67890"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "12345"
        
        # Check that language priority information is included
        assert decision["keep"]["selected_by_language_priority"] == True
        assert decision["keep"]["changed_by_language_priority"] == True
        assert decision["keep"]["priority_language_used"] == "eng"
    
    def test_determine_items_with_episode_path_filtering(self):
        """Test filtering of TV episodes based on path pattern."""
        # Create items with different episode paths
        items = [
            {
                "Id": "12345",
                "Name": "Show S01E01",
                "Path": "/tv/Show/Season 1/S01E01.mkv",
                "SeriesName": "Show",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6}
                ],
                "Size": 3000000000
            },
            {
                "Id": "67890",
                "Name": "Show S01E02",  # Different episode
                "Path": "/tv/Show/Season 1/S01E02.mkv",
                "SeriesName": "Show",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6}
                ],
                "Size": 3000000000
            }
        ]
        
        # This should separate episodes and result in no true duplicates
        decision = determine_items_to_delete(["12345", "67890"], items)
        
        # Should return empty decision since they're different episodes
        assert "keep" in decision
        assert decision["keep"] == {}
        assert decision["delete"] == []
    
    @patch('emby_dedupe.api.deduplication.fetch_items_details')
    @patch('emby_dedupe.api.deduplication.get_image_url')
    @patch('emby_dedupe.api.deduplication.determine_items_to_delete')
    def test_process_duplicate_groups_basic(self, mock_determine, mock_get_image, mock_fetch):
        """Test the basic functionality of process_duplicate_groups."""
        # Set up mocks
        mock_client = MagicMock()
        base_url = "http://emby.server"
        api_key = "test_api_key"
        
        # Mock the items details fetch
        mock_fetch.return_value = [
            {"Id": "id1", "Name": "Item 1", "ImageTags": {"Primary": "tag1"}, "Path": "/path/1.mkv"},
            {"Id": "id2", "Name": "Item 2", "ImageTags": {"Primary": "tag2"}, "Path": "/path/2.mkv"}
        ]
        
        # Mock the determination function
        mock_determine.return_value = {
            "keep": {"id": "id1", "name": "Item 1", "serverid": "server1"},
            "delete": [{"id": "id2", "name": "Item 2", "serverid": "server1"}]
        }
        
        # Mock image URL generation
        mock_get_image.return_value = "http://emby.server/image/path"
        
        # Call the function with a single group
        result, exclusion_metadata = process_duplicate_groups(
            mock_client, 
            base_url, 
            [["id1", "id2"]]
        )
        
        # Verify the result
        assert len(result) == 1
        assert exclusion_metadata is not None
        assert "excluded_groups_count" in exclusion_metadata
        assert "excluded_titles" in exclusion_metadata
        decision = result[0]
        assert decision["keep"]["id"] == "id1"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "id2"
        
        # Verify image URLs were added
        assert "image_url" in decision["keep"]
        assert "image_url" in decision["delete"][0]
    
    
    def test_build_disjoint_set(self):
        """Test building disjoint sets from provider data."""
        # Set up test data with normal movies and TV episodes
        provider_data = {
            "imdb": {
                "tt1234": [
                    {"id": "movie1", "name": "Movie One"},
                    {"id": "movie2", "name": "Movie One (2023)"}
                ]
            },
            "tvdb": {
                "series1": [
                    {"id": "episode1", "name": "Show S01E01", "is_episode": True, "series_name": "Show", "season_number": 1, "episode_number": 1},
                    {"id": "episode2", "name": "Show S01E01 HD", "is_episode": True, "series_name": "Show", "season_number": 1, "episode_number": 1}
                ],
                "series2": [
                    {"id": "episode3", "name": "Show S01E02", "is_episode": True, "series_name": "Show", "season_number": 1, "episode_number": 2}
                ]
            }
        }
        
        # Mock the tqdm progress bar
        with patch('emby_dedupe.api.deduplication.tqdm') as mock_tqdm:
            mock_progress = MagicMock()
            mock_tqdm.return_value.__enter__.return_value = mock_progress
            
            # Build the disjoint set
            result = build_disjoint_set(provider_data)
            
            # Check that movies were grouped together
            assert result.find("movie1") == result.find("movie2")
            
            # Make sure all three episodes exist as keys
            assert "episode1" in result.parent
            assert "episode2" in result.parent
            assert "episode3" in result.parent