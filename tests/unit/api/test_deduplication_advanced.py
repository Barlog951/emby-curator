"""
Advanced tests for deduplication functionality
"""
from unittest.mock import MagicMock, patch

from emby_dedupe.api.deduplication import (
    build_disjoint_set,
    determine_items_to_delete,
    process_duplicate_groups,
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
        assert decision["keep"]["selected_by_language_priority"]
        assert decision["keep"]["changed_by_language_priority"]
        assert decision["keep"]["priority_language_used"] == "eng"

    # Smart Language Priority Tests
    # These tests validate the enhanced language priority logic that prevents
    # small single-language movies from being kept over much larger multi-language movies
    # when there's a significant quality difference.

    def test_smart_language_priority_quality_override(self):
        """Test smart language priority - quality override for single vs multi-language."""
        # Create items: small single-language high-priority vs large multi-language lower-priority
        items = [
            {
                "Id": "single_lang_sk",  # Single SK language, smaller, higher priority
                "Name": "Movie Slovak Only",
                "Path": "/movies/movie_sk.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 3000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "slo"}
                ],
                "Size": 2000000000,  # 2GB - much smaller
                "Bitrate": 3500000
            },
            {
                "Id": "multi_lang_cz_en",  # Multi-language CZ/EN, larger, lower priority
                "Name": "Movie Czech English",
                "Path": "/movies/movie_cz_en.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840, "BitRate": 15000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "cze"},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "eng"}
                ],
                "Size": 50000000000,  # 50GB - much larger
                "Bitrate": 18000000
            }
        ]

        # Set language priorities (Slovak first, Czech second)
        lang_priorities = ["sk", "cs", "eng"]  # Using normalized language codes

        # Run determination with language priorities
        decision = determine_items_to_delete(["single_lang_sk", "multi_lang_cz_en"], items, lang_priorities)

        # Should choose the multi-language, higher-quality item despite language priority
        # because single-language item vs multi-language item with significant quality difference
        assert decision["keep"]["id"] == "multi_lang_cz_en"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "single_lang_sk"

    def test_smart_language_priority_normal_behavior(self):
        """Test smart language priority - quality wins when both have priority langs and quality is 2x+ better."""
        # Create items where both have multiple languages but quality gap is massive (4K vs 1080p)
        items = [
            {
                "Id": "multi_lang_cz_en",  # Multi-language CZ/EN, much higher quality (4K)
                "Name": "Movie Czech English",
                "Path": "/movies/movie_cz_en.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840, "BitRate": 15000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "cze"},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "eng"}
                ],
                "Size": 50000000000,
                "Bitrate": 18000000
            },
            {
                "Id": "multi_lang_sk_en",  # Multi-language SK/EN, much lower quality (1080p)
                "Name": "Movie Slovak English",
                "Path": "/movies/movie_sk_en.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 8000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "slo"},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "eng"}
                ],
                "Size": 25000000000,
                "Bitrate": 9000000
            }
        ]

        # Set language priorities (Slovak first, Czech second)
        lang_priorities = ["sk", "cs", "eng"]  # Using normalized language codes

        # Run determination with language priorities
        decision = determine_items_to_delete(["multi_lang_cz_en", "multi_lang_sk_en"], items, lang_priorities)

        # Quality override: 4K CZ/EN is 2x+ better than 1080p SK/EN, so quality wins
        assert decision["keep"]["id"] == "multi_lang_cz_en"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "multi_lang_sk_en"

    def test_smart_language_priority_small_quality_gap(self):
        """Test language priority wins when both have priority langs but quality gap is small (< 2x)."""
        # Both items at same resolution, similar quality - language should win
        items = [
            {
                "Id": "cz_1080p",  # Czech, slightly better quality
                "Name": "Movie Czech",
                "Path": "/movies/movie_cz_1080p.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 10000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "cze"},
                    {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "eng"}
                ],
                "Size": 8000000000,
                "Bitrate": 10000000
            },
            {
                "Id": "sk_1080p",  # Slovak, slightly lower quality but higher language priority
                "Name": "Movie Slovak",
                "Path": "/movies/movie_sk_1080p.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 8000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "slo"},
                    {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "eng"}
                ],
                "Size": 6000000000,
                "Bitrate": 8000000
            }
        ]

        lang_priorities = ["sk", "cs", "eng"]

        decision = determine_items_to_delete(["cz_1080p", "sk_1080p"], items, lang_priorities)

        # Quality gap is < 2x, so language priority (Slovak) wins
        assert decision["keep"]["id"] == "sk_1080p"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "cz_1080p"

    def test_smart_language_priority_insufficient_quality_difference(self):
        """Test smart language priority - no override when quality difference is insufficient."""
        # Create items with single vs multi-language but insufficient quality difference
        items = [
            {
                "Id": "single_lang_sk",  # Single SK language, higher priority
                "Name": "Movie Slovak Only",
                "Path": "/movies/movie_sk.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 8000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "slo"}
                ],
                "Size": 25000000000,
                "Bitrate": 9000000
            },
            {
                "Id": "multi_lang_cz_en",  # Multi-language, only slightly better quality
                "Name": "Movie Czech English",
                "Path": "/movies/movie_cz_en.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 10000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "cze"},
                    {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "eng"}
                ],
                "Size": 30000000000,  # Only slightly larger
                "Bitrate": 11000000
            }
        ]

        # Set language priorities (Slovak first, Czech second)
        lang_priorities = ["sk", "cs", "eng"]  # Using normalized language codes

        # Run determination with language priorities
        decision = determine_items_to_delete(["single_lang_sk", "multi_lang_cz_en"], items, lang_priorities)

        # Should use normal language priority since quality difference isn't significant (< 1.5x)
        assert decision["keep"]["id"] == "single_lang_sk"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "multi_lang_cz_en"
        assert decision["keep"]["selected_by_language_priority"]

    def test_smart_language_priority_reverse_scenario(self):
        """Test smart language priority - quality wins when gap is massive (4K vs 1080p)."""
        # 4K English-only vs 1080p Slovak/Czech — quality gap is >3x
        items = [
            {
                "Id": "single_lang_high_quality",  # Single language but highest quality
                "Name": "Movie English Ultra",
                "Path": "/movies/movie_en_ultra.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840, "BitRate": 25000000},
                    {"Type": "Audio", "Codec": "truehd", "Channels": 8, "Language": "eng"}
                ],
                "Size": 80000000000,  # Very large
                "Bitrate": 30000000
            },
            {
                "Id": "multi_lang_lower_quality",  # Multi-language but much lower quality
                "Name": "Movie Slovak Czech",
                "Path": "/movies/movie_sk_cz.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 5000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "slo"},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "cze"}
                ],
                "Size": 15000000000,
                "Bitrate": 7000000
            }
        ]

        # Set language priorities (Slovak first)
        lang_priorities = ["sk", "cs", "eng"]

        decision = determine_items_to_delete(["single_lang_high_quality", "multi_lang_lower_quality"], items, lang_priorities)

        # Quality override: 4K English is 2x+ better than 1080p Slovak/Czech
        assert decision["keep"]["id"] == "single_lang_high_quality"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "multi_lang_lower_quality"

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

    def test_language_normalization_slovak_czech_variants(self):
        """Test that Slovak/Czech language variants (slo/sk, cze/ces/cs) are treated with same priority."""
        # Create items with different Slovak/Czech language variants
        items = [
            {
                "Id": "slovak_slo",  # Uses "slo" language code
                "Name": "Movie Slovak SLO",
                "Path": "/movies/movie_slovak_slo.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 1080, "Width": 1920, "BitRate": 10000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "slo"}  # Slovak ISO 639-2
                ],
                "Size": 40000000000,  # 40GB
                "Bitrate": 10000000
            },
            {
                "Id": "slovak_sk", # Uses "sk" language code
                "Name": "Movie Slovak SK",
                "Path": "/movies/movie_slovak_sk.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 1080, "Width": 1920, "BitRate": 12000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "sk"}  # Slovak ISO 639-1
                ],
                "Size": 45000000000,  # 45GB - slightly better quality
                "Bitrate": 12000000
            },
            {
                "Id": "czech_cze", # Uses "cze" language code
                "Name": "Movie Czech CZE",
                "Path": "/movies/movie_czech_cze.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 1080, "Width": 1920, "BitRate": 11000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "cze"}  # Czech ISO 639-2
                ],
                "Size": 42000000000,  # 42GB
                "Bitrate": 11000000
            },
            {
                "Id": "czech_ces", # Uses "ces" language code
                "Name": "Movie Czech CES",
                "Path": "/movies/movie_czech_ces.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 1080, "Width": 1920, "BitRate": 9000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "ces"}  # Czech ISO 639-2 alternate
                ],
                "Size": 35000000000,  # 35GB - lower quality
                "Bitrate": 9000000
            },
            {
                "Id": "english", # English item - should be lowest priority
                "Name": "Movie English",
                "Path": "/movies/movie_english.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840, "BitRate": 20000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "eng"}  # English
                ],
                "Size": 60000000000,  # 60GB - highest quality but English
                "Bitrate": 20000000
            }
        ]

        # Language priorities: Slovak first (slo/sk should be treated as same), Czech second (cze/ces should be same), English last
        lang_priorities = ["sk", "cs", "eng"]  # Using normalized forms

        # Run determination with language priorities
        decision = determine_items_to_delete(["slovak_slo", "slovak_sk", "czech_cze", "czech_ces", "english"], items, lang_priorities)

        # Should choose the best Slovak item (slovak_sk has slightly better quality)
        # since all Slovak variants should be treated with same priority, quality should be the tiebreaker
        assert decision["keep"]["id"] == "slovak_sk"
        assert len(decision["delete"]) == 4

        # Verify that language priority was applied
        kept_item = decision["keep"]
        assert kept_item.get("selected_by_language_priority")
        assert kept_item.get("priority_language_used") == "sk"  # Should be normalized to "sk"

        # Make sure the deleted items include both Slovak and Czech variants and English
        deleted_ids = [item["id"] for item in decision["delete"]]
        assert "slovak_slo" in deleted_ids  # Other Slovak variant
        assert "czech_cze" in deleted_ids   # Czech variants
        assert "czech_ces" in deleted_ids
        assert "english" in deleted_ids     # English (lowest priority)

    def test_language_normalization_priority_vs_quality_balance(self):
        """Test that quality wins when gap is massive, even with language normalization."""
        items = [
            {
                "Id": "czech_ces_high_quality",  # Czech CES variant, very high quality (4K 70GB)
                "Name": "Movie Czech CES HQ",
                "Path": "/movies/movie_czech_ces_hq.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840, "BitRate": 25000000},
                    {"Type": "Audio", "Codec": "dts", "Channels": 8, "Language": "ces"}
                ],
                "Size": 70000000000,
                "Bitrate": 25000000
            },
            {
                "Id": "slovak_slo_lower_quality",  # Slovak SLO variant, much lower quality (720p 15GB)
                "Name": "Movie Slovak SLO",
                "Path": "/movies/movie_slovak_slo.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 720, "Width": 1280, "BitRate": 5000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 2, "Language": "slo"}
                ],
                "Size": 15000000000,
                "Bitrate": 5000000
            }
        ]

        lang_priorities = ["sk", "cs"]

        decision = determine_items_to_delete(["czech_ces_high_quality", "slovak_slo_lower_quality"], items, lang_priorities)

        # Quality override: 4K Czech (70GB) is 2x+ better than 720p Slovak (15GB)
        assert decision["keep"]["id"] == "czech_ces_high_quality"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "slovak_slo_lower_quality"

    def test_language_normalization_priority_wins_at_similar_quality(self):
        """Test that Slovak wins over Czech when quality gap is small (normalization works)."""
        items = [
            {
                "Id": "czech_ces",
                "Name": "Movie Czech",
                "Path": "/movies/movie_czech.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 10000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "ces"}
                ],
                "Size": 8000000000,
                "Bitrate": 10000000
            },
            {
                "Id": "slovak_slo",
                "Name": "Movie Slovak",
                "Path": "/movies/movie_slovak.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920, "BitRate": 8000000},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6, "Language": "slo"}
                ],
                "Size": 6000000000,
                "Bitrate": 8000000
            }
        ]

        lang_priorities = ["sk", "cs"]

        decision = determine_items_to_delete(["czech_ces", "slovak_slo"], items, lang_priorities)

        # Quality gap < 2x, so Slovak language priority wins
        assert decision["keep"]["id"] == "slovak_slo"
        assert len(decision["delete"]) == 1
        assert decision["delete"][0]["id"] == "czech_ces"
        assert decision["keep"]["selected_by_language_priority"]

    def test_tv_episode_path_parsing_uses_filename_not_folder(self):
        """Test that episode parsing uses filename, not folder names.

        This test verifies the fix for a bug where folder names like "S02" or
        "Wednesday.S02E05-E08" were incorrectly matched instead of the actual
        episode number from the filename.

        Example paths that should all be parsed as S02E06:
        - /Wednesday/S02/Wednesday.S02E05-E08.../Wednesday.S02E06.mkv
        - /Wednesday/Wednesday.S02.2160p.../Wednesday.S02E06.mkv
        - /Wednesday/Wednesday.S02.1080p.../Wednesday.S02E6.mkv (E6 = E06)
        """
        items = [
            {
                "Id": "20200212",
                "Name": "Woe Thyself",
                "SeriesName": "Wednesday",
                # Folder contains S02E05-E08 but filename is S02E06
                "Path": "/Movies/Serials/Wednesday/S02/Wednesday.S02E05-E08.2160p/Wednesday.S02E06.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840},
                    {"Type": "Audio", "Codec": "eac3", "Channels": 6}
                ],
                "Size": 8000000000
            },
            {
                "Id": "20224286",
                "Name": "Woe Thyself",
                "SeriesName": "Wednesday",
                "Path": "/Movies/Serials/Wednesday/Wednesday.S02.2160p/Wednesday.S02E06.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h265", "Height": 2160, "Width": 3840},
                    {"Type": "Audio", "Codec": "eac3", "Channels": 6}
                ],
                "Size": 8000000000
            },
            {
                "Id": "20254909",
                "Name": "Woe Thyself",
                "SeriesName": "Wednesday",
                # Note: S02E6 (without leading zero) should be normalized to match S02E06
                "Path": "/Movies/Serials/Wednesday/Wednesday.S02.1080p/Wednesday.S02E6.mkv",
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 6}
                ],
                "Size": 4000000000
            }
        ]

        # Run determination
        decision = determine_items_to_delete(
            ["20200212", "20224286", "20254909"],
            items
        )

        # All 3 items should be grouped as duplicates (same episode)
        assert decision["keep"], "Should have an item to keep"
        assert len(decision["delete"]) == 2, f"Should delete 2 items, got {len(decision.get('delete', []))}"

        # Verify the kept item is one of the expected IDs
        keep_id = decision["keep"]["id"]
        assert keep_id in ["20200212", "20224286", "20254909"]

        # Verify the deleted items are the other two
        delete_ids = [item["id"] for item in decision["delete"]]
        assert len(delete_ids) == 2
        assert keep_id not in delete_ids

    def test_tv_episode_number_normalization(self):
        """Test that episode numbers E6 and E06 are normalized to the same value.

        This ensures that files with different episode number formats
        (e.g., S02E6 vs S02E06) are correctly grouped as duplicates.
        """
        items = [
            {
                "Id": "item_e06",
                "Name": "Test Episode",
                "SeriesName": "TestShow",
                "Path": "/shows/TestShow.S01E06.mkv",  # With leading zero
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920},
                    {"Type": "Audio", "Codec": "aac", "Channels": 2}
                ],
                "Size": 1000000000
            },
            {
                "Id": "item_e6",
                "Name": "Test Episode",
                "SeriesName": "TestShow",
                "Path": "/shows/TestShow.S01E6.mkv",  # Without leading zero
                "MediaStreams": [
                    {"Type": "Video", "Codec": "h264", "Height": 720, "Width": 1280},
                    {"Type": "Audio", "Codec": "aac", "Channels": 2}
                ],
                "Size": 500000000
            }
        ]

        decision = determine_items_to_delete(["item_e06", "item_e6"], items)

        # Both items should be recognized as the same episode
        assert decision["keep"], "Should keep one item"
        assert len(decision["delete"]) == 1, "Should delete one item"

        # Higher quality (1080p) should be kept
        assert decision["keep"]["id"] == "item_e06"
        assert decision["delete"][0]["id"] == "item_e6"
