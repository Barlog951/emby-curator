"""
Tests for EmbyChecker functionality.

This module provides comprehensive behavioral tests for the EmbyChecker class,
focusing on provider ID lookups, caching, quality checking, and configuration management.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import Mock, MagicMock, patch

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
    name: Optional[str] = None
    year: Optional[int] = None
    imdb: Optional[str] = None
    tmdb: Optional[str] = None
    tvdb: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    hdr: Optional[str] = None
    audio: Optional[str] = None
    audio_languages: Optional[list[str]] = None
    size_mb: Optional[int] = None
    bitrate_kbps: Optional[int] = None
    path: Optional[str] = None
    source_quality_tier: Optional[str] = None


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
        with open(cache_path, 'r') as f:
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
        with open(cache_path, 'r') as f:
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
