"""
Tests for missing episodes detection functionality.

This module provides comprehensive behavioral tests for missing episode detection,
enrichment with series metadata, and alternative detection methods.
"""

from unittest.mock import Mock, patch

import httpx

from emby_dedupe.api.missing_episodes import (
    analyze_missing_episodes,
    enrich_episodes_with_series_metadata,
    get_missing_episodes,
    get_missing_episodes_alternative,
)


class TestMissingEpisodes:
    """Tests for missing episodes functionality."""

    # ========== analyze_missing_episodes Tests (Pure Function) ==========

    def test_analyze_missing_episodes_empty(self):
        """Test analyze_missing_episodes with empty list."""
        result = analyze_missing_episodes([])

        assert result["total_missing"] == 0
        assert result["by_series"] == {}
        assert result["by_season"] == {}

    def test_analyze_missing_episodes_single_episode(self):
        """Test analyze_missing_episodes with single episode."""
        episodes = [
            {
                "SeriesName": "Test Series",
                "ParentIndexNumber": 1,  # Season number
                "IndexNumber": 1,  # Episode number
            }
        ]

        result = analyze_missing_episodes(episodes)

        assert result["total_missing"] == 1
        assert "Test Series" in result["by_series"]

    def test_analyze_missing_episodes_multiple_series(self):
        """Test analyze_missing_episodes with multiple series."""
        episodes = [
            {"SeriesName": "Series A", "ParentIndexNumber": 1, "IndexNumber": 1},
            {"SeriesName": "Series A", "ParentIndexNumber": 1, "IndexNumber": 2},
            {"SeriesName": "Series B", "ParentIndexNumber": 2, "IndexNumber": 5},
        ]

        result = analyze_missing_episodes(episodes)

        assert result["total_missing"] == 3
        assert "Series A" in result["by_series"]
        assert "Series B" in result["by_series"]

    def test_analyze_missing_episodes_deduplication(self):
        """Test analyze_missing_episodes deduplicates episodes."""
        episodes = [
            {"SeriesName": "Series A", "ParentIndexNumber": 1, "IndexNumber": 1, "Id": "ep1"},
            {"SeriesName": "Series A", "ParentIndexNumber": 1, "IndexNumber": 1, "Id": "ep1"},  # Duplicate
        ]

        result = analyze_missing_episodes(episodes)

        # Should only count unique episodes
        assert result["total_missing"] == 1

    def test_analyze_missing_episodes_statistics(self):
        """Test analyze_missing_episodes provides correct statistics."""
        episodes = [
            {"SeriesName": "Series A", "ParentIndexNumber": 1, "IndexNumber": 1},
            {"SeriesName": "Series A", "ParentIndexNumber": 1, "IndexNumber": 2},
            {"SeriesName": "Series A", "ParentIndexNumber": 2, "IndexNumber": 1},
        ]

        result = analyze_missing_episodes(episodes)

        assert result["total_missing"] == 3
        assert "Series A" in result["by_series"]
        # Should have statistics for season counts
        assert "by_season" in result

    # ========== enrich_episodes_with_series_metadata Tests ==========

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_enrich_episodes_empty_list(self, mock_make_request):
        """Test enrichment with empty episode list does nothing."""
        client = Mock()
        episodes = []

        # Call should return None (in-place mutation)
        result = enrich_episodes_with_series_metadata(client, "http://emby.local", episodes)

        assert result is None
        assert episodes == []
        mock_make_request.assert_not_called()

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_enrich_episodes_with_series_metadata(self, mock_make_request):
        """Test enrichment adds series metadata to episodes."""
        client = Mock()
        episodes = [
            {"SeriesId": "series1", "EpisodeNumber": 1},
            {"SeriesId": "series1", "EpisodeNumber": 2},
        ]

        # Mock users response (for user ID)
        users_response = Mock()
        users_response.json.return_value = [{"Id": "user1"}]

        # Mock series metadata response
        series_response = Mock()
        series_response.json.return_value = {
            "Name": "Test Series",
            "OriginalTitle": "Original Test Series",
        }

        mock_make_request.side_effect = [users_response, series_response]

        # Enrich episodes (in-place mutation)
        enrich_episodes_with_series_metadata(client, "http://emby.local", episodes)

        # Verify enrichment happened
        assert episodes[0]["SeriesName"] == "Test Series"
        assert episodes[0]["OriginalSeriesName"] == "Original Test Series"
        assert episodes[1]["SeriesName"] == "Test Series"
        assert episodes[1]["OriginalSeriesName"] == "Original Test Series"

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_enrich_episodes_missing_series_id(self, mock_make_request):
        """Test enrichment handles episodes without SeriesId."""
        client = Mock()
        episodes = [
            {"EpisodeNumber": 1},  # No SeriesId
        ]

        # Enrich episodes
        enrich_episodes_with_series_metadata(client, "http://emby.local", episodes)

        # Should add Unknown Series for episodes without SeriesId
        assert episodes[0]["SeriesName"] == "Unknown Series"
        assert episodes[0]["OriginalSeriesName"] == "Unknown Series"

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_enrich_episodes_api_failure(self, mock_make_request):
        """Test enrichment handles API failures gracefully."""
        client = Mock()
        episodes = [
            {"SeriesId": "series1", "EpisodeNumber": 1},
        ]

        # Mock users response
        users_response = Mock()
        users_response.json.return_value = [{"Id": "user1"}]

        # Mock series metadata failure
        mock_make_request.side_effect = [users_response, Exception("API Error")]

        # Should not raise exception
        enrich_episodes_with_series_metadata(client, "http://emby.local", episodes)

        # Should have fallback Unknown Series
        assert episodes[0]["SeriesName"] == "Unknown Series"
        assert episodes[0]["OriginalSeriesName"] == "Unknown Series"

    # ========== get_missing_episodes Tests ==========

    @patch('emby_dedupe.api.missing_episodes.enrich_episodes_with_series_metadata')
    def test_get_missing_episodes_success(self, mock_enrich):
        """Test successful missing episodes retrieval."""
        client = Mock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "Items": [
                {"SeriesId": "s1", "EpisodeNumber": 1},
                {"SeriesId": "s1", "EpisodeNumber": 2},
            ]
        }
        client.get.return_value = response

        episodes = get_missing_episodes(client, "http://emby.local")

        assert len(episodes) == 2
        assert episodes[0]["EpisodeNumber"] == 1
        mock_enrich.assert_called_once()

    @patch('emby_dedupe.api.missing_episodes.enrich_episodes_with_series_metadata')
    def test_get_missing_episodes_list_response(self, mock_enrich):
        """Test missing episodes with direct list response."""
        client = Mock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = [  # Direct list, not dict
            {"SeriesId": "s1", "EpisodeNumber": 1},
        ]
        client.get.return_value = response

        episodes = get_missing_episodes(client, "http://emby.local")

        assert len(episodes) == 1
        mock_enrich.assert_called_once()

    @patch('emby_dedupe.api.missing_episodes.enrich_episodes_with_series_metadata')
    @patch('emby_dedupe.api.client.create_http_client')  # Correct import path
    def test_get_missing_episodes_with_auth(self, mock_create_client, mock_enrich):
        """Test missing episodes with user authentication."""
        # Mock authenticated client
        auth_client = Mock()
        auth_response = Mock()
        auth_response.status_code = 200
        auth_response.json.return_value = {"Items": [{"EpisodeNumber": 1}]}
        auth_client.get.return_value = auth_response

        mock_create_client.return_value = (auth_client, "auth_token", "user1")

        # Regular client (unused due to auth)
        client = Mock()

        episodes = get_missing_episodes(
            client, "http://emby.local",
            username="testuser", password="testpass"
        )

        assert len(episodes) == 1
        mock_create_client.assert_called_once()
        mock_enrich.assert_called_once()

    @patch('emby_dedupe.api.missing_episodes.get_missing_episodes_alternative')
    def test_get_missing_episodes_fallback_on_404(self, mock_alternative):
        """Test fallback to alternative method on 404."""
        client = Mock()
        response = Mock()
        response.status_code = 404
        client.get.return_value = response

        mock_alternative.return_value = [{"EpisodeNumber": 1}]

        episodes = get_missing_episodes(client, "http://emby.local")

        # Should have called alternative method
        mock_alternative.assert_called_once()
        assert len(episodes) == 1

    @patch('emby_dedupe.api.missing_episodes.get_missing_episodes_alternative')
    def test_get_missing_episodes_fallback_on_timeout(self, mock_alternative):
        """Test fallback to alternative method on timeout."""
        client = Mock()
        client.get.side_effect = httpx.TimeoutException("Timeout")

        mock_alternative.return_value = []

        get_missing_episodes(client, "http://emby.local")

        mock_alternative.assert_called_once()

    @patch('emby_dedupe.api.missing_episodes.enrich_episodes_with_series_metadata')
    def test_get_missing_episodes_with_library_id(self, mock_enrich):
        """Test missing episodes with library ID filter."""
        client = Mock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"Items": []}
        client.get.return_value = response

        get_missing_episodes(client, "http://emby.local", library_id="lib123")

        # Verify library_id was passed in params
        call_args = client.get.call_args
        assert "params" in call_args.kwargs
        assert call_args.kwargs["params"]["ParentId"] == "lib123"

    # ========== get_missing_episodes_alternative Tests ==========

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_alternative_success(self, mock_make_request):
        """Test alternative method successfully finds missing episodes."""
        client = Mock()

        # Mock series list response
        series_response = Mock()
        series_response.json.return_value = {
            "Items": [
                {"Id": "series1", "Name": "Test Series"}
            ]
        }

        # Mock seasons response
        seasons_response = Mock()
        seasons_response.json.return_value = {
            "Items": [
                {"IndexNumber": 1}
            ]
        }

        # Mock episodes response with missing episode
        episodes_response = Mock()
        episodes_response.json.return_value = {
            "Items": [
                {"IndexNumber": 2}  # Episode 1 is missing
            ]
        }

        mock_make_request.side_effect = [series_response, seasons_response, episodes_response]

        result = get_missing_episodes_alternative(client, "http://emby.local")

        # Should detect missing episode 1
        assert isinstance(result, list)

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_alternative_empty_library(self, mock_make_request):
        """Test alternative method with empty library."""
        client = Mock()

        # Mock empty series response
        response = Mock()
        response.json.return_value = {"Items": []}
        mock_make_request.return_value = response

        result = get_missing_episodes_alternative(client, "http://emby.local")

        assert result == []

    @patch('emby_dedupe.api.missing_episodes.make_http_request')
    def test_alternative_error_handling(self, mock_make_request):
        """Test alternative method handles errors gracefully."""
        client = Mock()
        mock_make_request.side_effect = Exception("API Error")

        result = get_missing_episodes_alternative(client, "http://emby.local")

        # Should return empty list on error
        assert result == []

    # ========== Error Paths ==========

    def test_get_missing_episodes_http_error(self):
        """Test handling of HTTP errors."""
        client = Mock()
        client.get.side_effect = httpx.HTTPStatusError(
            "Error",
            request=Mock(),
            response=Mock(status_code=500)
        )

        # Should fallback to alternative method without raising
        with patch('emby_dedupe.api.missing_episodes.get_missing_episodes_alternative') as mock_alt:
            mock_alt.return_value = []
            result = get_missing_episodes(client, "http://emby.local")
            assert result == []

    def test_get_missing_episodes_request_error(self):
        """Test handling of request errors."""
        client = Mock()
        client.get.side_effect = httpx.RequestError("Connection failed")

        # Should fallback to alternative method
        with patch('emby_dedupe.api.missing_episodes.get_missing_episodes_alternative') as mock_alt:
            mock_alt.return_value = []
            result = get_missing_episodes(client, "http://emby.local")
            assert result == []

    @patch('emby_dedupe.api.missing_episodes.enrich_episodes_with_series_metadata')
    def test_get_missing_episodes_unexpected_format(self, mock_enrich):
        """Test handling of unexpected response format."""
        client = Mock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = "unexpected_string"  # Not dict or list
        client.get.return_value = response

        episodes = get_missing_episodes(client, "http://emby.local")

        # Should return empty list for unexpected format
        assert episodes == []
