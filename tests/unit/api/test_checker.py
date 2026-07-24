"""
Tests for EmbyChecker functionality.

This module provides comprehensive behavioral tests for the EmbyChecker class,
focusing on provider ID lookups, caching, quality checking, and configuration management.
"""

import json
import time
from dataclasses import dataclass
from unittest.mock import Mock, patch

import httpx
import pytest

from emby_dedupe.api.checker import EmbyChecker
from emby_dedupe.api.quality_compare import ComparisonResult
from emby_dedupe.utils.config import Config


@dataclass
class CheckConfig:
    """Configuration object to bundle parameters for check() and should_download().

    This solves SonarQube S107 issues (too many parameters) by grouping related
    parameters into a single config object.
    """
    name: str | None = None
    year: int | None = None
    imdb: str | None = None
    tmdb: str | None = None
    tvdb: str | None = None
    season: int | None = None
    episode: int | None = None
    resolution: str | None = None
    codec: str | None = None
    hdr: str | None = None
    audio: str | None = None
    audio_languages: list[str] | None = None
    size_mb: int | None = None
    bitrate_kbps: int | None = None
    path: str | None = None
    source_quality_tier: str | None = None


class TestEmbyChecker:
    """Tests for EmbyChecker class."""

    # ========== Initialization Tests ==========

    def test_init_with_direct_params(self):
        """Test EmbyChecker initialization with direct parameters."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            libraries=["Movies", "TV Shows"],
            lang_priorities=["sk", "cs", "en"],
            exclude_ids=["tt1234567"],
            use_cache=True,
            cache_ttl_minutes=10,
        )

        assert checker.host == "http://emby.local"
        assert checker.api_key == "test-key"
        assert checker.libraries == ["Movies", "TV Shows"]
        assert checker.lang_priorities == ["sk", "cs", "en"]
        assert checker.exclude_ids == ["tt1234567"]
        assert checker.use_cache is True
        assert checker.cache_ttl_minutes == 10

    def test_init_with_config_object(self):
        """Test EmbyChecker initialization with Config object."""
        config = Config(
            host="http://emby.local",
            api_key="test-key",
            libraries=["Movies"],
            lang_priorities=["sk"],
            exclude_ids=["tt9999999"],
            cache_enabled=False,
            cache_ttl_minutes=5,
        )

        checker = EmbyChecker(config=config)

        assert checker.host == "http://emby.local"
        assert checker.api_key == "test-key"
        assert checker.libraries == ["Movies"]
        assert checker.lang_priorities == ["sk"]
        assert checker.exclude_ids == ["tt9999999"]
        assert checker.use_cache is False
        assert checker.cache_ttl_minutes == 5

    @patch('emby_dedupe.api.checker.Config.from_config_file')
    def test_from_config_classmethod(self, mock_from_config):
        """Test EmbyChecker.from_config() classmethod."""
        mock_config = Mock()
        mock_config.host = "http://emby.local"
        mock_config.api_key = "test-key"
        mock_config.libraries = ["Movies"]
        mock_config.lang_priorities = []
        mock_config.exclude_ids = []
        mock_config.cache_enabled = True
        mock_config.cache_ttl_minutes = 10
        mock_from_config.return_value = mock_config

        checker = EmbyChecker.from_config()

        assert checker.host == "http://emby.local"
        assert checker.api_key == "test-key"
        mock_from_config.assert_called_once()

    # ========== Cache Operations Tests ==========

    def test_cache_hit(self, tmp_path):
        """Test cache hit returns cached data."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=True,
            cache_ttl_minutes=10,
        )
        checker._cache_dir = tmp_path

        # Create a cache file
        cache_key = "test_key"
        cache_path = tmp_path / f"{cache_key}.json"
        cache_data = {
            "timestamp": time.time(),
            "items": [{"id": "12345", "name": "Test Item"}],
        }
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)

        # Test cache retrieval
        result = checker._get_from_cache(cache_key)

        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == "12345"
        assert result[0]["name"] == "Test Item"

    def test_cache_miss_file_not_exists(self, tmp_path):
        """Test cache miss when file doesn't exist."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=True,
        )
        checker._cache_dir = tmp_path

        result = checker._get_from_cache("nonexistent_key")

        assert result is None

    def test_cache_expired(self, tmp_path):
        """Test cache miss when cache has expired."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=True,
            cache_ttl_minutes=1,  # 1 minute TTL
        )
        checker._cache_dir = tmp_path

        # Create expired cache (2 minutes old)
        cache_key = "test_key"
        cache_path = tmp_path / f"{cache_key}.json"
        cache_data = {
            "timestamp": time.time() - 120,  # 2 minutes ago
            "items": [{"id": "12345"}],
        }
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)

        # Should return None for expired cache
        result = checker._get_from_cache(cache_key)

        assert result is None

    def test_cache_disabled(self, tmp_path):
        """Test cache operations when caching is disabled."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )
        checker._cache_dir = tmp_path

        # Even if cache file exists, should return None when disabled
        cache_key = "test_key"
        cache_path = tmp_path / f"{cache_key}.json"
        with open(cache_path, 'w') as f:
            json.dump({"timestamp": time.time(), "items": [{"id": "12345"}]}, f)

        result = checker._get_from_cache(cache_key)

        assert result is None

    def test_save_to_cache(self, tmp_path):
        """Test saving data to cache."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=True,
        )
        checker._cache_dir = tmp_path

        cache_key = "test_key"
        items = [{"id": "12345", "name": "Test Item"}]

        checker._save_to_cache(cache_key, items)

        # Verify cache file was created
        cache_path = tmp_path / f"{cache_key}.json"
        assert cache_path.exists()

        # Verify content
        with open(cache_path) as f:
            data = json.load(f)
        assert "timestamp" in data
        assert data["items"] == items

    def test_save_to_cache_disabled(self, tmp_path):
        """Test save to cache does nothing when caching disabled."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )
        checker._cache_dir = tmp_path

        cache_key = "test_key"
        items = [{"id": "12345"}]

        checker._save_to_cache(cache_key, items)

        # Cache file should NOT be created
        cache_path = tmp_path / f"{cache_key}.json"
        assert not cache_path.exists()

    # ========== Provider Table Management Tests ==========

    @patch('emby_dedupe.api.checker.fetch_and_process_media_items')
    @patch('emby_dedupe.api.checker.get_library_id')
    def test_build_provider_tables(self, mock_get_lib_id, mock_fetch_items):
        """Test building provider tables from libraries."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            libraries=["Movies"],
            use_cache=False,  # Disable cache for this test
        )

        # Mock library ID fetch
        mock_get_lib_id.return_value = "lib-123"

        # Mock fetch_and_process_media_items
        mock_fetch_items.return_value = {
            "imdb": {
                "tt1234567": [{"id": "item1"}],
            },
            "tmdb": {
                "5678": [{"id": "item1"}],
            },
            "tvdb": {},
            "library_name": "Movies",
        }

        tables = checker._build_provider_tables()

        assert "tt1234567" in tables["imdb"]
        assert "5678" in tables["tmdb"]
        assert len(tables["imdb"]["tt1234567"]) == 1

    def test_load_provider_tables_from_cache(self, tmp_path):
        """Test loading provider tables from cache."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=True,
            cache_ttl_minutes=10,
        )
        checker._cache_dir = tmp_path

        # Create provider tables cache
        cache_path = tmp_path / "provider_tables.json"
        cache_data = {
            "timestamp": time.time(),
            "tables": {
                "imdb": {"tt1234567": [{"id": "item1"}]},
                "tmdb": {},
                "tvdb": {},
            },
        }
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)

        tables = checker._load_provider_tables()

        assert tables is not None
        assert "tt1234567" in tables["imdb"]

    def test_save_provider_tables_to_cache(self, tmp_path):
        """Test saving provider tables to cache."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=True,
        )
        checker._cache_dir = tmp_path

        tables = {
            "imdb": {"tt1234567": [{"id": "item1"}]},
            "tmdb": {},
            "tvdb": {},
        }

        checker._save_provider_tables(tables)

        # Verify cache file exists
        cache_path = tmp_path / "provider_tables.json"
        assert cache_path.exists()

        # Verify content
        with open(cache_path) as f:
            data = json.load(f)
        assert "timestamp" in data
        assert data["tables"]["imdb"]["tt1234567"] == [{"id": "item1"}]

    @patch('emby_dedupe.api.checker.fetch_and_process_media_items')
    @patch('emby_dedupe.api.checker.get_library_id')
    def test_provider_tables_memory_cache(self, mock_get_lib_id, mock_fetch_items):
        """Test provider tables are cached in memory after first load."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            libraries=["Movies"],
            use_cache=False,
        )

        mock_get_lib_id.return_value = "lib-123"
        mock_fetch_items.return_value = {
            "imdb": {"tt1234567": [{"id": "item1"}]},
            "tmdb": {},
            "tvdb": {},
            "library_name": "Movies",
        }

        # First call should build tables
        tables1 = checker._get_provider_tables()

        # Second call should use memory cache
        tables2 = checker._get_provider_tables()

        # Should be the same object
        assert tables1 is tables2
        # Should only call fetch once
        assert mock_fetch_items.call_count == 1

    # ========== Provider ID Lookup Tests ==========

    @patch('emby_dedupe.api.checker.fetch_items_details')
    def test_lookup_by_provider_id_found(self, mock_fetch_details):
        """Test successful provider ID lookup."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        # Mock provider tables
        checker._provider_tables = {
            "imdb": {"tt1234567": [{"id": "item1"}]},
            "tmdb": {},
            "tvdb": {},
        }

        # Mock fetch_items_details
        mock_fetch_details.return_value = [
            {"Id": "item1", "Name": "Test Movie"}
        ]

        items = checker._lookup_by_provider_id("tt1234567", "imdb")

        assert len(items) == 1
        assert items[0]["Id"] == "item1"
        assert items[0]["Name"] == "Test Movie"

    @patch('emby_dedupe.api.checker.fetch_items_details')
    def test_lookup_by_provider_id_not_found(self, mock_fetch_details):
        """Test provider ID lookup when ID not in tables."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        checker._provider_tables = {
            "imdb": {},
            "tmdb": {},
            "tvdb": {},
        }

        items = checker._lookup_by_provider_id("tt9999999", "imdb")

        assert items == []
        mock_fetch_details.assert_not_called()

    @patch('emby_dedupe.api.checker.fetch_items_details')
    def test_lookup_by_provider_id_case_insensitive(self, mock_fetch_details):
        """Test provider ID lookup is case-insensitive."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        checker._provider_tables = {
            "imdb": {"tt1234567": [{"id": "item1"}]},
            "tmdb": {},
            "tvdb": {},
        }

        mock_fetch_details.return_value = [{"Id": "item1"}]

        # Lookup with uppercase should work
        items = checker._lookup_by_provider_id("TT1234567", "imdb")

        assert len(items) == 1
        assert items[0]["Id"] == "item1"

    # ========== Check Flow Tests ==========

    @patch('emby_dedupe.api.checker.compare_quality')
    @patch('emby_dedupe.api.checker.fetch_items_details')
    def test_check_with_excluded_imdb_id(self, mock_fetch_details, mock_compare):
        """Test check() skips excluded IMDB IDs."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            exclude_ids=["tt1234567"],
        )

        config = CheckConfig(
            imdb="tt1234567",
            name="Test Movie",
            resolution="2160p",
        )

        result = checker.check(**config.__dict__)

        assert result.recommendation == "skip"
        assert result.reason == "excluded_id"
        assert result.status == "excluded"
        mock_compare.assert_not_called()
        mock_fetch_details.assert_not_called()

    @patch('emby_dedupe.api.checker.compare_quality')
    @patch('emby_dedupe.api.checker.fetch_items_details')
    def test_check_with_imdb_lookup(self, mock_fetch_details, mock_compare):
        """Test check() with IMDB ID provider lookup."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        checker._provider_tables = {
            "imdb": {"tt1234567": [{"id": "item1"}]},
            "tmdb": {},
            "tvdb": {},
        }

        mock_fetch_details.return_value = [{"Id": "item1", "Name": "Existing Movie"}]
        mock_compare.return_value = ComparisonResult(
            recommendation="skip",
            reason="existing_better",
            status="existing_better",
        )

        config = CheckConfig(
            imdb="tt1234567",
            name="Test Movie",
            resolution="2160p",
        )

        result = checker.check(**config.__dict__)

        assert result.recommendation == "skip"
        mock_fetch_details.assert_called_once()
        mock_compare.assert_called_once()

    @patch('emby_dedupe.api.checker.compare_quality')
    @patch('emby_dedupe.api.checker.search_media')
    def test_check_with_name_search_fallback(self, mock_search, mock_compare):
        """Test check() falls back to name search when no provider ID."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            libraries=["Movies"],
            use_cache=False,
        )

        checker._provider_tables = {"imdb": {}, "tmdb": {}, "tvdb": {}}

        mock_search.return_value = [{"Id": "item1", "Name": "Test Movie"}]
        mock_compare.return_value = ComparisonResult(
            recommendation="download",
            reason="upgrade",
            status="upgrade",
        )

        config = CheckConfig(
            name="Test Movie",
            year=2020,
            resolution="2160p",
        )

        result = checker.check(**config.__dict__)

        assert result.recommendation == "download"
        mock_search.assert_called_once()
        mock_compare.assert_called_once()

    @patch('emby_dedupe.api.checker.compare_quality')
    @patch('emby_dedupe.api.checker.search_media', return_value=[])
    def test_check_not_found(self, mock_search, mock_compare):
        """Test check() when media not found in Emby."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        checker._provider_tables = {"imdb": {}, "tmdb": {}, "tvdb": {}}

        mock_compare.return_value = ComparisonResult(
            recommendation="download",
            reason="not_found",
            status="not_found",
        )

        config = CheckConfig(
            imdb="tt9999999",
            name="Unknown Movie",
            resolution="2160p",
        )

        result = checker.check(**config.__dict__)

        assert result.recommendation == "download"
        assert result.reason == "not_found"

    # ========== should_download / check_batch / Context Manager Tests ==========

    @patch('emby_dedupe.api.checker.EmbyChecker.check')
    def test_should_download_returns_boolean(self, mock_check):
        """Test should_download() returns simple boolean."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )

        mock_check.return_value = ComparisonResult(
            recommendation="download",
            reason="upgrade",
            status="upgrade",
        )

        # should_download() only accepts 14 params (no path, source_quality_tier)
        result = checker.should_download(name="Test Movie", resolution="2160p")

        assert result is True
        mock_check.assert_called_once()

    @patch('emby_dedupe.api.checker.EmbyChecker.check')
    def test_should_download_false_when_skip(self, mock_check):
        """Test should_download() returns False when recommendation is skip."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )

        mock_check.return_value = ComparisonResult(
            recommendation="skip",
            reason="existing_better",
            status="existing_better",
        )

        result = checker.should_download(name="Test Movie", resolution="1080p")

        assert result is False

    def test_context_manager(self):
        """Test EmbyChecker as context manager closes HTTP client."""
        with EmbyChecker(host="http://emby.local", api_key="test-key") as checker:
            assert checker._client is None  # Not created until first use

        # Context manager should have cleaned up

    # ========== Error Handling / Validation Tests ==========

    def test_validate_missing_host(self):
        """Test validation fails when host is missing."""
        checker = EmbyChecker(api_key="test-key")

        errors = checker.validate()

        assert "host is required" in errors

    def test_validate_missing_api_key(self):
        """Test validation fails when API key is missing."""
        checker = EmbyChecker(host="http://emby.local")

        errors = checker.validate()

        assert "api_key is required" in errors

    def test_validate_success(self):
        """Test validation passes with required fields."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )

        errors = checker.validate()

        assert errors == []

    def test_check_with_invalid_config_raises(self):
        """Test check() raises ValueError with invalid configuration."""
        checker = EmbyChecker(host="http://emby.local")  # Missing api_key

        config = CheckConfig(name="Test Movie")

        with pytest.raises(ValueError, match="Invalid configuration"):
            checker.check(**config.__dict__)

    @patch('emby_dedupe.api.checker.httpx.Client')
    def test_connection_failure_handling(self, mock_client_class):
        """Test handling of connection failures."""
        mock_client = Mock()
        mock_client.request.side_effect = httpx.ConnectError("Connection failed")
        mock_client_class.return_value = mock_client

        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )

        # Connection failure should propagate
        with pytest.raises(httpx.ConnectError):
            checker._get_client().request("GET", "http://emby.local/test")

    # ========== Series Provider ID Fallback Tests ==========

    @patch('emby_dedupe.api.checker.make_http_request')
    def test_lookup_episode_via_series_found(self, mock_request):
        """Test finding episode via series IMDB ID when episode-level lookup fails.

        Reproduces the Doctor Who / Pán času bug: series has IMDB tt0436992 but
        individual episodes don't carry it in their ProviderIds. The fallback
        should find the series by IMDB, then locate the episode within it.
        """
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )
        checker._client = Mock()

        # First call: search for Series by IMDB ID
        series_response = Mock()
        series_response.json.return_value = {
            "Items": [{"Id": "series-123", "Name": "Pán času", "ProviderIds": {"Imdb": "tt0436992"}}]
        }

        # Second call: fetch episodes within the series
        episodes_response = Mock()
        episodes_response.json.return_value = {
            "Items": [
                {"Id": "ep1", "Name": "New Earth", "ParentIndexNumber": 2, "IndexNumber": 1,
                 "MediaStreams": [], "Path": "/Series/Pan Casu/S02/S02E01.mkv"},
                {"Id": "ep2", "Name": "Tooth and Claw", "ParentIndexNumber": 2, "IndexNumber": 2,
                 "MediaStreams": [], "Path": "/Series/Pan Casu/S02/S02E02.mkv"},
            ]
        }

        mock_request.side_effect = [series_response, episodes_response]

        result = checker._lookup_episode_via_series("tt0436992", None, None, season=2, episode=1)

        assert result is not None
        assert len(result) == 1
        assert result[0]["Id"] == "ep1"
        assert result[0]["Name"] == "New Earth"
        assert result[0]["SeriesName"] == "Pán času"

    @patch('emby_dedupe.api.checker.make_http_request')
    def test_lookup_episode_via_series_episode_not_in_series(self, mock_request):
        """Test when series is found but specific episode doesn't exist."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )
        checker._client = Mock()

        series_response = Mock()
        series_response.json.return_value = {
            "Items": [{"Id": "series-123", "Name": "Pán času", "ProviderIds": {"Imdb": "tt0436992"}}]
        }

        episodes_response = Mock()
        episodes_response.json.return_value = {
            "Items": [
                {"Id": "ep1", "ParentIndexNumber": 2, "IndexNumber": 1},
            ]
        }

        mock_request.side_effect = [series_response, episodes_response]

        # Looking for S02E99 which doesn't exist
        result = checker._lookup_episode_via_series("tt0436992", None, None, season=2, episode=99)

        # Should return empty list (not None) — series was found, episode doesn't exist
        assert result is not None
        assert result == []

    @patch('emby_dedupe.api.checker.make_http_request')
    def test_lookup_episode_via_series_no_series_found(self, mock_request):
        """Test when no series matches the provider ID."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )
        checker._client = Mock()

        # No series found for IMDB ID
        series_response = Mock()
        series_response.json.return_value = {"Items": []}

        mock_request.return_value = series_response

        result = checker._lookup_episode_via_series("tt9999999", None, None, season=1, episode=1)

        # Should return None — no series found, caller should try name search
        assert result is None

    @patch('emby_dedupe.api.checker.make_http_request')
    def test_lookup_episode_via_series_tmdb_fallback(self, mock_request):
        """Test that TMDB ID is tried when IMDB lookup returns no series."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )
        checker._client = Mock()

        # IMDB search returns nothing
        imdb_response = Mock()
        imdb_response.json.return_value = {"Items": []}

        # TMDB search finds the series
        tmdb_series_response = Mock()
        tmdb_series_response.json.return_value = {
            "Items": [{"Id": "series-456", "Name": "Doctor Who", "ProviderIds": {"Tmdb": "57243"}}]
        }

        # Episode fetch
        episodes_response = Mock()
        episodes_response.json.return_value = {
            "Items": [
                {"Id": "ep5", "ParentIndexNumber": 2, "IndexNumber": 5, "SeriesName": "Doctor Who"},
            ]
        }

        mock_request.side_effect = [imdb_response, tmdb_series_response, episodes_response]

        result = checker._lookup_episode_via_series("tt9999999", "57243", None, season=2, episode=5)

        assert result is not None
        assert len(result) == 1
        assert result[0]["Id"] == "ep5"

    @patch('emby_dedupe.api.checker.compare_quality')
    @patch('emby_dedupe.api.checker.make_http_request')
    def test_check_uses_series_fallback_for_tv_episodes(self, mock_request, mock_compare):
        """Test that check() uses series fallback when provider table misses for TV episodes.

        End-to-end test: IMDB ID is on the series (not in episode-level provider tables),
        and the series has a different name than expected. The fallback finds the episode.
        """
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        # Provider tables have no entry for the series IMDB ID
        checker._provider_tables = {"imdb": {}, "tmdb": {}, "tvdb": {}}
        checker._client = Mock()

        # Series search by IMDB
        series_response = Mock()
        series_response.json.return_value = {
            "Items": [{"Id": "series-123", "Name": "Pán času", "ProviderIds": {"Imdb": "tt0436992"}}]
        }

        # Episode fetch
        episodes_response = Mock()
        episodes_response.json.return_value = {
            "Items": [
                {"Id": "ep1", "Name": "New Earth", "ParentIndexNumber": 2, "IndexNumber": 1,
                 "MediaStreams": [{"Type": "Video", "Codec": "h264", "Width": 1280}]},
            ]
        }

        mock_request.side_effect = [series_response, episodes_response]
        mock_compare.return_value = ComparisonResult(
            recommendation="skip",
            reason="existing_same",
            status="existing_same",
        )

        result = checker.check(
            name="Doctor Who",
            imdb="tt0436992",
            season=2,
            episode=1,
            resolution="1080p",
        )

        # Should have found the episode via series fallback, not via name search
        assert result.recommendation == "skip"
        mock_compare.assert_called_once()
        # The existing items passed to compare_quality should contain the episode
        call_args = mock_compare.call_args
        existing_items = call_args[0][1]
        assert len(existing_items) == 1
        assert existing_items[0]["Id"] == "ep1"

    @patch('emby_dedupe.api.checker.compare_quality')
    @patch('emby_dedupe.api.checker.search_media')
    @patch('emby_dedupe.api.checker.make_http_request')
    def test_check_skips_name_search_when_series_found(self, mock_request, mock_search, mock_compare):
        """Test that name search is skipped when series was found via provider ID (even if episode missing)."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
            use_cache=False,
        )

        checker._provider_tables = {"imdb": {}, "tmdb": {}, "tvdb": {}}
        checker._client = Mock()

        # Series found but episode doesn't exist
        series_response = Mock()
        series_response.json.return_value = {
            "Items": [{"Id": "series-123", "Name": "Pán času", "ProviderIds": {"Imdb": "tt0436992"}}]
        }
        episodes_response = Mock()
        episodes_response.json.return_value = {"Items": []}

        mock_request.side_effect = [series_response, episodes_response]
        mock_compare.return_value = ComparisonResult(
            recommendation="download",
            reason="not_found",
            status="not_found",
        )

        result = checker.check(
            name="Doctor Who",
            imdb="tt0436992",
            season=2,
            episode=99,
            resolution="1080p",
        )

        # Name search should NOT have been called — series was positively identified
        mock_search.assert_not_called()
        assert result.recommendation == "download"

    @patch('emby_dedupe.api.checker.make_http_request')
    def test_lookup_episode_via_series_ignores_unrelated_series(self, mock_request):
        """Regression: Emby may ignore AnyImdbId filter and return ALL series.

        When searching for 'Memory of a Killer' (tt35707374), the API returned
        '2 Broke Girls' (first alphabetically). The code must validate that the
        returned series actually has the expected provider ID.
        """
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )
        checker._client = Mock()

        # Emby returns unrelated series (ignoring AnyImdbId filter)
        series_response = Mock()
        series_response.json.return_value = {
            "Items": [
                {"Id": "wrong-1", "Name": "2 Broke Girls", "ProviderIds": {"Imdb": "tt2069997"}},
                {"Id": "wrong-2", "Name": "Another Show", "ProviderIds": {"Imdb": "tt1111111"}},
            ]
        }

        mock_request.return_value = series_response

        result = checker._lookup_episode_via_series("tt35707374", None, None, season=1, episode=6)

        # Should return None — none of the returned series match tt35707374
        assert result is None

    @patch('emby_dedupe.api.checker.make_http_request')
    def test_lookup_episode_via_series_picks_correct_from_multiple(self, mock_request):
        """When API returns multiple series, pick the one with matching provider ID."""
        checker = EmbyChecker(
            host="http://emby.local",
            api_key="test-key",
        )
        checker._client = Mock()

        # API returns multiple series, only one matches
        series_response = Mock()
        series_response.json.return_value = {
            "Items": [
                {"Id": "wrong-1", "Name": "2 Broke Girls", "ProviderIds": {"Imdb": "tt2069997"}},
                {"Id": "correct", "Name": "Memory of a Killer", "ProviderIds": {"Imdb": "tt35707374"}},
            ]
        }

        episodes_response = Mock()
        episodes_response.json.return_value = {
            "Items": [
                {"Id": "ep1", "Name": "Episode 6", "ParentIndexNumber": 1, "IndexNumber": 6},
            ]
        }

        mock_request.side_effect = [series_response, episodes_response]

        result = checker._lookup_episode_via_series("tt35707374", None, None, season=1, episode=6)

        assert result is not None
        assert len(result) == 1
        assert result[0]["Id"] == "ep1"


class TestFromConfigErrorContract:
    """Regression (code review 2026-07-10): from_config() must fail loudly, not build a
    checker with host=None that only errors on the first check()."""

    def test_missing_config_file_raises_filenotfound_compatible(self, tmp_path, monkeypatch):
        from emby_dedupe.utils.exceptions import EmbyConfigMissingError, EmbyDedupeError

        monkeypatch.setattr("emby_dedupe.utils.config.CONFIG_FILE", tmp_path / "nope.yaml")
        # Consumers (torrents repo) wrap from_config() in `except FileNotFoundError` —
        # that branch must actually fire.
        with pytest.raises(FileNotFoundError) as exc:
            EmbyChecker.from_config()
        assert "nope.yaml" in str(exc.value)
        assert isinstance(exc.value, EmbyConfigMissingError)
        assert isinstance(exc.value, EmbyDedupeError)

    def test_incomplete_config_file_raises_valueerror_compatible(self, tmp_path, monkeypatch):
        from emby_dedupe.utils.exceptions import EmbyConfigError, EmbyConfigMissingError

        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: http://emby:8096\n")  # api_key missing
        monkeypatch.setattr("emby_dedupe.utils.config.CONFIG_FILE", cfg)
        with pytest.raises(ValueError) as exc:
            EmbyChecker.from_config()
        assert isinstance(exc.value, EmbyConfigError)
        assert not isinstance(exc.value, EmbyConfigMissingError)  # file exists
        assert "api_key" in str(exc.value)

    def test_overrides_alone_suffice_without_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("emby_dedupe.utils.config.CONFIG_FILE", tmp_path / "nope.yaml")
        checker = EmbyChecker.from_config(host="http://emby:8096", api_key="k")
        assert checker.host == "http://emby:8096"
        assert checker.api_key == "k"

    def test_check_config_error_is_still_a_valueerror(self):
        from emby_dedupe.utils.exceptions import EmbyConfigError

        checker = EmbyChecker(host=None, api_key=None)
        with pytest.raises(ValueError) as exc:  # pinned pre-existing contract
            checker.check(name="Test")
        assert isinstance(exc.value, EmbyConfigError)


class TestCheckKwargContract:
    """Regression (code review 2026-07-10): check() routes kwargs through CheckConfig,
    so an unknown/typo'd kwarg now raises TypeError instead of being silently dropped."""

    def test_unknown_kwarg_raises_typeerror(self):
        checker = EmbyChecker(host="http://emby", api_key="key")
        with pytest.raises(TypeError):
            checker.check(name="Inception", bogus_field="x")

    def test_valid_kwargs_still_accepted(self):
        from emby_dedupe.api.quality_compare import ComparisonResult

        checker = EmbyChecker(host="http://emby", api_key="key")
        with patch.object(checker, "_lookup_by_any_provider_id", return_value=None), \
             patch.object(checker, "_search_by_name", return_value=[]):
            result = checker.check(
                name="Inception", year=2010, resolution="2160p", codec="hevc",
                hdr="HDR10", size_mb=15000, audio_languages=["eng"],
            )
        assert isinstance(result, ComparisonResult)
        assert result.status == "not_found"

    def test_check_batch_forwards_item_kwargs(self):
        checker = EmbyChecker(host="http://emby", api_key="key")
        with patch.object(checker, "_lookup_by_any_provider_id", return_value=None), \
             patch.object(checker, "_search_by_name", return_value=[]):
            results = checker.check_batch([
                {"name": "A", "resolution": "1080p"},
                {"name": "B", "resolution": "2160p"},
            ])
        assert len(results) == 2


class TestTestConnection:
    """EmbyChecker.test_connection() — a real connectivity probe (code review 2026-07-10)."""

    def test_connection_success(self):
        checker = EmbyChecker(host="http://emby", api_key="key")
        with patch("emby_dedupe.api.checker.check_emby_connection", return_value=True) as mock_conn:
            assert checker.test_connection() is True
        # probes /System/Info on the configured host
        args = mock_conn.call_args[0]
        assert args[1] == "http://emby/System/Info"

    def test_connection_propagates_server_error(self):
        from emby_dedupe.utils.exceptions import EmbyServerConnectionError

        checker = EmbyChecker(host="http://emby", api_key="key")
        with patch(
            "emby_dedupe.api.checker.check_emby_connection",
            side_effect=EmbyServerConnectionError("down"),
        ):
            with pytest.raises(EmbyServerConnectionError):
                checker.test_connection()

    def test_connection_requires_config(self):
        from emby_dedupe.utils.exceptions import EmbyConfigError

        checker = EmbyChecker(host=None, api_key=None)
        with pytest.raises(EmbyConfigError):
            checker.test_connection()


class TestCheckEpisodes:
    """EmbyChecker.check_episodes() aggregate API (code review 2026-07-10)."""

    def _checker_with_verdicts(self, verdicts):
        """verdicts: dict[(season,episode)] -> ('download'|'skip'|'missing')."""
        from emby_dedupe.api.quality_compare import ComparisonResult, ExistingQuality

        checker = EmbyChecker(host="http://emby", api_key="key")

        def fake_check(config=None, **kwargs):
            s, e = config.season, config.episode
            verdict = verdicts[(s, e)]
            if verdict == "missing":
                return ComparisonResult(recommendation="download", reason="not_found",
                                        status="not_found", existing=None)
            existing = ExistingQuality(id=f"{s}-{e}", name="ep", width=1920, height=1080)
            if verdict == "download":
                return ComparisonResult(recommendation="download", reason="better_quality",
                                        status="found", existing=existing)
            return ComparisonResult(recommendation="skip", reason="same_or_worse",
                                    status="found", existing=existing)

        checker.check = fake_check  # type: ignore[method-assign]
        return checker

    def test_range_all_present_same_or_better_skips(self):
        v = {(1, 8): "skip", (1, 9): "skip", (1, 10): "skip"}
        checker = self._checker_with_verdicts(v)
        res = checker.check_episodes({1: [8, 9, 10]}, resolution="1080p")
        assert res.episodes_checked == 3
        assert res.episodes_found == 3
        assert res.all_same_or_better is True
        assert res.should_download is False
        assert res.episodes_to_download == {}
        assert res.first_existing is not None

    def test_range_one_missing_triggers_download(self):
        v = {(1, 8): "skip", (1, 9): "missing", (1, 10): "skip"}
        checker = self._checker_with_verdicts(v)
        res = checker.check_episodes({1: [8, 9, 10]}, resolution="1080p")
        assert res.episodes_found == 2
        assert res.should_download is True
        assert res.episodes_to_download == {1: [9]}

    def test_range_one_upgradeable_triggers_download(self):
        v = {(1, 8): "skip", (1, 9): "download", (1, 10): "skip"}
        checker = self._checker_with_verdicts(v)
        res = checker.check_episodes({1: [8, 9, 10]}, resolution="2160p")
        assert res.episodes_found == 3  # all exist...
        assert res.all_same_or_better is False  # ...but one is upgradeable
        assert res.episodes_to_download == {1: [9]}
        assert res.should_download is True

    def test_multi_season(self):
        v = {(1, 1): "skip", (1, 2): "missing", (2, 1): "skip"}
        checker = self._checker_with_verdicts(v)
        res = checker.check_episodes({1: [1, 2], 2: [1]}, resolution="1080p")
        assert res.episodes_checked == 3
        assert res.episodes_to_download == {1: [2]}
        assert res.should_download is True

    def test_per_episode_size_override_and_callback(self):
        seen_sizes = {}
        collected = []

        from emby_dedupe.api.quality_compare import ComparisonResult, ExistingQuality
        checker = EmbyChecker(host="http://emby", api_key="key")

        def fake_check(config=None, **kwargs):
            seen_sizes[(config.season, config.episode)] = config.size_mb
            return ComparisonResult(recommendation="skip", reason="same_or_worse",
                                    status="found",
                                    existing=ExistingQuality(id="x", name="e", width=1, height=1))
        checker.check = fake_check  # type: ignore[method-assign]

        res = checker.check_episodes(
            {1: [1, 2]},
            resolution="1080p", size_mb=1000,
            episode_sizes={(1, 1): 500, (1, 2): 900},
            on_episode=lambda er: collected.append((er.season, er.episode)),
        )
        assert seen_sizes == {(1, 1): 500, (1, 2): 900}  # per-episode overrides applied
        assert collected == [(1, 1), (1, 2)]  # callback fired per episode
        assert len(res.results) == 2

    def test_empty_episode_set_does_not_recommend_download(self):
        checker = self._checker_with_verdicts({})
        res = checker.check_episodes({}, resolution="1080p")
        assert res.episodes_checked == 0
        assert res.should_download is False
