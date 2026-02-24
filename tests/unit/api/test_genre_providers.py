"""Tests for emby_dedupe/api/genre_providers.py"""
import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from emby_dedupe.api.genre_providers import (
    RateLimiter,
    compare_genres,
    fetch_genres_for_item,
    fetch_omdb_genres,
    fetch_tmdb_genres,
    load_genre_cache,
    save_genre_cache,
)


class TestRateLimiter:
    def test_acquire_sleeps_when_called_too_fast(self, mocker):
        mock_sleep = mocker.patch("emby_dedupe.api.genre_providers.time.sleep")
        limiter = RateLimiter(10.0)  # 0.1s interval
        limiter._last = time.monotonic()  # simulate just-called
        limiter.acquire()
        mock_sleep.assert_called_once()
        assert mock_sleep.call_args[0][0] > 0

    def test_acquire_no_sleep_when_enough_time_passed(self, mocker):
        mock_sleep = mocker.patch("emby_dedupe.api.genre_providers.time.sleep")
        limiter = RateLimiter(10.0)
        limiter._last = 0.0  # very old last call
        limiter.acquire()
        mock_sleep.assert_not_called()


class TestGenreCache:
    def test_load_returns_empty_when_file_missing(self, mocker):
        mocker.patch(
            "emby_dedupe.api.genre_providers.CACHE_PATH",
            Path("/tmp/nonexistent_cache_xyz.json"),
        )
        result = load_genre_cache()
        assert result == {}

    def test_load_returns_empty_on_corrupt_json(self, mocker, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not valid json")
        mocker.patch("emby_dedupe.api.genre_providers.CACHE_PATH", cache_file)
        result = load_genre_cache()
        assert result == {}

    def test_save_and_load_roundtrip(self, mocker, tmp_path):
        cache_file = tmp_path / "cache.json"
        mocker.patch("emby_dedupe.api.genre_providers.CACHE_PATH", cache_file)
        data = {"tmdb_123_movie": ["Drama", "Crime"]}
        save_genre_cache(data)
        assert cache_file.exists()
        loaded = load_genre_cache()
        assert loaded == data


class TestFetchTmdbGenres:
    def _make_mock_client(self, mocker, genres, status_code=200):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.status_code = status_code
        mock_resp.is_success = status_code < 400
        mock_resp.json.return_value = {"genres": [{"id": i, "name": g} for i, g in enumerate(genres)]}
        # raise_for_status should not raise for 2xx
        if status_code >= 400:
            import httpx
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=mocker.MagicMock(), response=mock_resp
            )
        else:
            mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp
        return mock_client

    def test_returns_genre_names(self, mocker):
        mock_client = self._make_mock_client(mocker, ["Drama", "Crime"])
        limiter = mocker.MagicMock()
        result = fetch_tmdb_genres(mock_client, limiter, "12345", cache={})
        assert "Drama" in result
        assert "Crime" in result

    def test_uses_cache_on_second_call(self, mocker):
        mock_client = self._make_mock_client(mocker, ["Drama"])
        limiter = mocker.MagicMock()
        cache = {}
        fetch_tmdb_genres(mock_client, limiter, "12345", cache=cache)
        mock_client.get.reset_mock()
        # Second call — should use cache
        result = fetch_tmdb_genres(mock_client, limiter, "12345", cache=cache)
        mock_client.get.assert_not_called()
        assert "Drama" in result

    def test_returns_empty_on_404(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.status_code = 404
        mock_resp.is_success = False
        mock_resp.raise_for_status.return_value = None  # 404 handled before raise_for_status
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        result = fetch_tmdb_genres(mock_client, limiter, "99999", cache={})
        assert result == []

    def test_acquires_rate_limiter(self, mocker):
        mock_client = self._make_mock_client(mocker, ["Drama"])
        limiter = mocker.MagicMock()
        fetch_tmdb_genres(mock_client, limiter, "12345", cache={})
        limiter.acquire.assert_called_once()

    def test_uses_tv_media_type(self, mocker):
        mock_client = self._make_mock_client(mocker, ["Drama"])
        limiter = mocker.MagicMock()
        fetch_tmdb_genres(mock_client, limiter, "12345", media_type="tv", cache={})
        call_url = mock_client.get.call_args[0][0]
        assert "/tv/" in call_url

    def test_normalizes_genre_names(self, mocker):
        """Genres returned by TMDB that match normalization map should be normalized."""
        mock_client = self._make_mock_client(mocker, ["Suspense"])
        limiter = mocker.MagicMock()
        result = fetch_tmdb_genres(mock_client, limiter, "12345", cache={})
        # "Suspense" normalizes to "Thriller"
        assert "Thriller" in result
        assert "Suspense" not in result

    def test_deduplicates_genres(self, mocker):
        """After normalization, duplicates should be removed."""
        # "Suspense" and "Thriller" both normalize to "Thriller"
        mock_client = self._make_mock_client(mocker, ["Suspense", "Thriller"])
        limiter = mocker.MagicMock()
        result = fetch_tmdb_genres(mock_client, limiter, "12345", cache={})
        assert result.count("Thriller") == 1

    def test_returns_empty_list_on_http_error(self, mocker):
        """Non-404 HTTP errors should return empty list (not raise)."""
        import httpx
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=mocker.MagicMock(), response=mock_resp
        )
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        result = fetch_tmdb_genres(mock_client, limiter, "12345", cache={})
        assert result == []


class TestFetchOmdbGenres:
    def test_returns_parsed_genres(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"Response": "True", "Genre": "Drama, Crime, Thriller"}
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        result = fetch_omdb_genres(mock_client, limiter, "tt1234567", ["key1"], cache={})
        assert "Drama" in result
        assert "Crime" in result
        assert "Thriller" in result

    def test_rotates_key_on_rate_limit(self, mocker):
        mock_client = mocker.MagicMock()
        # First call: rate limited. Second call: success.
        mock_resp1 = mocker.MagicMock()
        mock_resp1.raise_for_status.return_value = None
        mock_resp1.json.return_value = {"Response": "False", "Error": "Request limit reached!"}
        mock_resp2 = mocker.MagicMock()
        mock_resp2.raise_for_status.return_value = None
        mock_resp2.json.return_value = {"Response": "True", "Genre": "Drama"}
        mock_client.get.side_effect = [mock_resp1, mock_resp2]
        limiter = mocker.MagicMock()
        result = fetch_omdb_genres(mock_client, limiter, "tt1234567", ["key1", "key2"], cache={})
        assert "Drama" in result
        assert mock_client.get.call_count == 2

    def test_returns_empty_when_all_keys_exhausted(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"Response": "False", "Error": "Request limit reached!"}
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        result = fetch_omdb_genres(mock_client, limiter, "tt1234567", ["key1", "key2"], cache={})
        assert result == []

    def test_uses_cache(self, mocker):
        mock_client = mocker.MagicMock()
        limiter = mocker.MagicMock()
        cache = {"imdb_tt1234567": ["Drama"]}
        result = fetch_omdb_genres(mock_client, limiter, "tt1234567", ["key1"], cache=cache)
        mock_client.get.assert_not_called()
        assert result == ["Drama"]

    def test_returns_empty_on_invalid_id(self, mocker):
        """Non-rate-limit OMDb errors (invalid ID) return empty without rotating keys."""
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"Response": "False", "Error": "Incorrect IMDb ID."}
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        result = fetch_omdb_genres(mock_client, limiter, "tt0000000", ["key1", "key2"], cache={})
        assert result == []
        # Should stop after first key since it's not a rate limit error
        assert mock_client.get.call_count == 1

    def test_acquires_rate_limiter_per_key_attempt(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"Response": "True", "Genre": "Drama"}
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        fetch_omdb_genres(mock_client, limiter, "tt1234567", ["key1"], cache={})
        limiter.acquire.assert_called_once()

    def test_saves_to_cache_on_success(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"Response": "True", "Genre": "Drama"}
        mock_client.get.return_value = mock_resp
        limiter = mocker.MagicMock()
        cache = {}
        fetch_omdb_genres(mock_client, limiter, "tt1234567", ["key1"], cache=cache)
        assert "imdb_tt1234567" in cache
        assert "Drama" in cache["imdb_tt1234567"]


class TestFetchGenresForItem:
    def test_uses_tmdb_when_available(self, mocker):
        mock_fetch_tmdb = mocker.patch(
            "emby_dedupe.api.genre_providers.fetch_tmdb_genres",
            return_value=["Drama"],
        )
        item = {"Id": "1", "Type": "Movie", "ProviderIds": {"Tmdb": "123"}}
        result = fetch_genres_for_item(
            item, mocker.MagicMock(), mocker.MagicMock(),
            None, None, [], {},
        )
        mock_fetch_tmdb.assert_called_once()
        assert result == ["Drama"]

    def test_falls_back_to_omdb(self, mocker):
        mocker.patch("emby_dedupe.api.genre_providers.fetch_tmdb_genres", return_value=[])
        mock_fetch_omdb = mocker.patch(
            "emby_dedupe.api.genre_providers.fetch_omdb_genres",
            return_value=["Comedy"],
        )
        item = {"Id": "1", "Type": "Movie", "ProviderIds": {"Imdb": "tt123", "Tmdb": "456"}}
        result = fetch_genres_for_item(
            item, mocker.MagicMock(), mocker.MagicMock(),
            mocker.MagicMock(), mocker.MagicMock(), ["key1"], {},
        )
        mock_fetch_omdb.assert_called_once()
        assert result == ["Comedy"]

    def test_series_uses_tv_media_type(self, mocker):
        mock_fetch_tmdb = mocker.patch(
            "emby_dedupe.api.genre_providers.fetch_tmdb_genres",
            return_value=["Drama"],
        )
        item = {"Id": "1", "Type": "Series", "ProviderIds": {"Tmdb": "123"}}
        fetch_genres_for_item(
            item, mocker.MagicMock(), mocker.MagicMock(),
            None, None, [], {},
        )
        call_kwargs = mock_fetch_tmdb.call_args
        # media_type="tv" should be passed as positional or keyword arg
        assert "tv" in str(call_kwargs)

    def test_returns_empty_when_no_provider_ids(self, mocker):
        item = {"Id": "1", "Type": "Movie", "ProviderIds": {}}
        result = fetch_genres_for_item(item, None, None, None, None, [], {})
        assert result == []

    def test_skips_tmdb_when_client_is_none(self, mocker):
        mock_fetch_omdb = mocker.patch(
            "emby_dedupe.api.genre_providers.fetch_omdb_genres",
            return_value=["Drama"],
        )
        mock_fetch_tmdb = mocker.patch("emby_dedupe.api.genre_providers.fetch_tmdb_genres")
        item = {"Id": "1", "Type": "Movie", "ProviderIds": {"Tmdb": "123", "Imdb": "tt123"}}
        fetch_genres_for_item(
            item, None, None,
            mocker.MagicMock(), mocker.MagicMock(), ["key1"], {},
        )
        mock_fetch_tmdb.assert_not_called()
        mock_fetch_omdb.assert_called_once()

    def test_movie_uses_movie_media_type(self, mocker):
        mock_fetch_tmdb = mocker.patch(
            "emby_dedupe.api.genre_providers.fetch_tmdb_genres",
            return_value=["Action"],
        )
        item = {"Id": "1", "Type": "Movie", "ProviderIds": {"Tmdb": "789"}}
        fetch_genres_for_item(
            item, mocker.MagicMock(), mocker.MagicMock(),
            None, None, [], {},
        )
        call_kwargs = mock_fetch_tmdb.call_args
        assert "movie" in str(call_kwargs)


class TestCompareGenres:
    def test_identifies_missing_from_emby(self):
        result = compare_genres(["Drama"], ["Drama", "Crime"])
        assert "Crime" in result["missing_from_emby"]

    def test_identifies_extra_in_emby(self):
        result = compare_genres(["Drama", "Comedy"], ["Drama"])
        assert "Comedy" in result["extra_in_emby"]

    def test_merged_is_union(self):
        result = compare_genres(["Drama"], ["Crime"])
        assert set(result["merged"]) == {"Drama", "Crime"}

    def test_has_diff_false_when_identical(self):
        result = compare_genres(["Drama", "Crime"], ["Drama", "Crime"])
        assert result["has_diff"] is False

    def test_has_diff_true_when_tmdb_has_more(self):
        result = compare_genres(["Drama"], ["Drama", "Crime"])
        assert result["has_diff"] is True

    def test_has_diff_false_when_only_emby_has_extra(self):
        # Emby has extra genres not in TMDB — has_diff is False (additive only)
        result = compare_genres(["Drama", "Comedy"], ["Drama"])
        assert result["has_diff"] is False

    def test_normalizes_before_comparing(self):
        # "Suspense" normalizes to "Thriller" — should equal TMDB's "Thriller"
        result = compare_genres(["Suspense"], ["Thriller"])
        assert result["has_diff"] is False

    def test_empty_emby_all_external_are_missing(self):
        result = compare_genres([], ["Drama", "Crime"])
        assert set(result["missing_from_emby"]) == {"Drama", "Crime"}
        assert result["has_diff"] is True

    def test_both_empty_no_diff(self):
        result = compare_genres([], [])
        assert result["has_diff"] is False
        assert result["missing_from_emby"] == []
        assert result["extra_in_emby"] == []
        assert result["merged"] == []

    def test_missing_from_emby_is_sorted(self):
        result = compare_genres([], ["Thriller", "Action", "Drama"])
        assert result["missing_from_emby"] == sorted(result["missing_from_emby"])

    def test_extra_in_emby_is_sorted(self):
        result = compare_genres(["Thriller", "Action", "Drama"], [])
        assert result["extra_in_emby"] == sorted(result["extra_in_emby"])

    def test_merged_is_sorted(self):
        result = compare_genres(["Drama"], ["Action"])
        assert result["merged"] == sorted(result["merged"])
