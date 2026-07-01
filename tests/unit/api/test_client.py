"""
Tests for Emby API client functionality
"""
from unittest.mock import Mock, patch

import httpx
import pytest

from emby_dedupe.api.client import (
    build_provider_id_tables,
    check_emby_connection,
    create_http_client,
    delete_item,
    ensure_authenticated_for_delete,
    fetch_and_process_media_items,
    fetch_items_details,
    get_auth_token,
    get_library_id,
    handle_host_and_port,
    logout,
    make_http_request,
)
from emby_dedupe.utils.constants import DEFAULT_PORT_HTTP, DEFAULT_PORT_HTTPS
from emby_dedupe.utils.exceptions import EmbyServerConnectionError


class TestClient:
    """Tests for Emby API client functionality."""

    def test_build_provider_id_tables(self):
        """Test building provider ID tables from media items."""
        provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": "Test Library"}

        # Create test media items
        media_items = [
            {
                "Id": "12345",
                "Name": "Test Movie",
                "IsFolder": False,
                "ProviderIds": {
                    "Imdb": "tt1234567",
                    "Tmdb": "1234"
                }
            },
            {
                "Id": "67890",
                "Name": "Another Test Movie",
                "IsFolder": False,
                "ProviderIds": {
                    "Imdb": "tt1234567",  # Same IMDB ID
                    "Tmdb": "1234"  # Same TMDB ID
                }
            }
        ]

        build_provider_id_tables(media_items, provider_tables)

        # Check that provider IDs were extracted correctly
        assert "tt1234567" in provider_tables["imdb"]
        assert "1234" in provider_tables["tmdb"]

        # Check that the same provider ID maps to both item IDs
        imdb_items = provider_tables["imdb"]["tt1234567"]
        tmdb_items = provider_tables["tmdb"]["1234"]

        # Verify item counts
        assert len(imdb_items) == 2
        assert len(tmdb_items) == 2

        # Verify item IDs
        assert imdb_items[0]["id"] == "12345"
        assert imdb_items[1]["id"] == "67890"
        assert tmdb_items[0]["id"] == "12345"
        assert tmdb_items[1]["id"] == "67890"

        # Verify metadata
        for item in imdb_items:
            assert item["library_name"] == "Test Library"
            assert item["is_episode"] is False
            assert "provider_id" in item

    def test_build_provider_id_tables_with_ignored_imdb(self):
        """Test that ignored IMDB IDs are skipped."""
        provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": "Test Library"}
        media_items = [
            {
                "Id": "12345",
                "Name": "Test Movie",
                "IsFolder": False,
                "ProviderIds": {
                    "Imdb": "tt0000000",  # This is the ignored IMDB ID
                    "Tmdb": "1234"
                }
            }
        ]

        build_provider_id_tables(media_items, provider_tables)

        # The IMDB ID should be ignored, but TMDB ID should be included
        assert "tt0000000" not in provider_tables["imdb"]
        assert "1234" in provider_tables["tmdb"]

        # Verify the TMDB entry
        tmdb_items = provider_tables["tmdb"]["1234"]
        assert len(tmdb_items) == 1
        assert tmdb_items[0]["id"] == "12345"

    def test_build_provider_id_tables_skips_folders(self):
        """Test that folders are skipped when building provider ID tables."""
        provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": "Test Library"}
        media_items = [
            {
                "Id": "12345",
                "Name": "Test Folder",
                "IsFolder": True,
                "ProviderIds": {
                    "Imdb": "tt1234567",
                    "Tmdb": "1234"
                }
            }
        ]

        build_provider_id_tables(media_items, provider_tables)

        # The folder should be skipped
        assert "tt1234567" not in provider_tables["imdb"]
        assert "1234" not in provider_tables["tmdb"]

    def test_build_provider_id_tables_case_insensitive_provider_ids(self):
        """Test that provider IDs are matched case-insensitively.

        The Emby API returns inconsistent casing for provider ID keys:
        - Some items have "Imdb" (mixed case)
        - Some items have "IMDB" (uppercase)

        This test ensures both variants are handled correctly.
        """
        provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": "Test Library"}

        # Create test media items with different casing for provider ID keys
        media_items = [
            {
                "Id": "12345",
                "Name": "Movie with mixed case Imdb",
                "IsFolder": False,
                "ProviderIds": {
                    "Imdb": "tt1234567",  # Mixed case (season 1 episodes)
                    "Tvdb": "111111",
                    "Tmdb": "1234"
                }
            },
            {
                "Id": "67890",
                "Name": "Movie with uppercase IMDB",
                "IsFolder": False,
                "ProviderIds": {
                    "IMDB": "tt1234567",  # Uppercase (season 2 episodes)
                    "Tvdb": "111111",
                    "TMDB": "1234"  # Also uppercase TMDB
                }
            },
            {
                "Id": "11111",
                "Name": "Movie with lowercase imdb",
                "IsFolder": False,
                "ProviderIds": {
                    "imdb": "tt1234567",  # Lowercase
                    "tvdb": "111111",
                    "tmdb": "1234"
                }
            }
        ]

        build_provider_id_tables(media_items, provider_tables)

        # All three items should be grouped under the same provider IDs
        assert "tt1234567" in provider_tables["imdb"]
        assert "111111" in provider_tables["tvdb"]
        assert "1234" in provider_tables["tmdb"]

        # Verify all 3 items are in the same IMDB group
        imdb_items = provider_tables["imdb"]["tt1234567"]
        assert len(imdb_items) == 3, f"Expected 3 items, got {len(imdb_items)}"

        item_ids = [item["id"] for item in imdb_items]
        assert "12345" in item_ids
        assert "67890" in item_ids
        assert "11111" in item_ids

        # Verify TVDB and TMDB groups also have all 3 items
        tvdb_items = provider_tables["tvdb"]["111111"]
        assert len(tvdb_items) == 3

        tmdb_items = provider_tables["tmdb"]["1234"]
        assert len(tmdb_items) == 3

    def test_build_provider_id_tables_series_episode_grouping(self):
        """Test that episodes are grouped by SeriesName+Season+Episode even without provider IDs."""
        provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": "Test Library"}

        media_items = [
            {
                "Id": "aaa",
                "Name": "Doctor Who S01E03 720p",
                "IsFolder": False,
                "SeriesName": "Doctor Who",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "ProviderIds": {},  # No provider IDs at all
            },
            {
                "Id": "bbb",
                "Name": "Doctor Who S01E03 1080p",
                "IsFolder": False,
                "SeriesName": "Doctor Who",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "ProviderIds": {"Tvdb": "295296", "Imdb": "tt0563001"},
            },
        ]

        build_provider_id_tables(media_items, provider_tables)

        # Item "aaa" has no provider IDs → NOT in imdb/tvdb/tmdb
        assert "aaa" not in [i["id"] for t in ["imdb", "tvdb", "tmdb"] for i in sum(provider_tables[t].values(), [])]

        # Both items grouped in series_episode
        se_key = "Doctor Who|S1E3"
        assert se_key in provider_tables["series_episode"]
        se_items = provider_tables["series_episode"][se_key]
        assert len(se_items) == 2
        assert {i["id"] for i in se_items} == {"aaa", "bbb"}

    def test_build_provider_id_tables_series_episode_no_duplicates(self):
        """Test that series_episode doesn't add the same item twice."""
        provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": "Test Library"}

        media_items = [
            {
                "Id": "xyz",
                "Name": "Show S02E05",
                "IsFolder": False,
                "SeriesName": "Show",
                "ParentIndexNumber": 2,
                "IndexNumber": 5,
                "ProviderIds": {"Tvdb": "999"},
            },
        ]

        build_provider_id_tables(media_items, provider_tables)

        # Single item → series_episode has 1 entry, not a duplicate
        se_key = "Show|S2E5"
        assert se_key in provider_tables["series_episode"]
        assert len(provider_tables["series_episode"][se_key]) == 1

    @patch('emby_dedupe.api.client.make_http_request')
    def test_fetch_and_process_media_items(self, mock_make_http_request):
        """Test fetching and processing media items."""
        mock_client = Mock()

        # Mock first response to get total count
        mock_total_response = Mock()
        mock_total_response.json.return_value = {"TotalRecordCount": 2}

        # Mock second response with actual items
        mock_items_response = Mock()
        mock_items_response.json.return_value = {
            "Items": [
                {
                    "Id": "12345",
                    "IsFolder": False,
                    "ProviderIds": {
                        "Imdb": "tt1234567",
                        "Tmdb": "1234"
                    }
                },
                {
                    "Id": "67890",
                    "IsFolder": False,
                    "ProviderIds": {
                        "Imdb": "tt7654321",
                        "Tmdb": "4321"
                    }
                }
            ]
        }

        # Configure the mock to return different responses for different calls
        mock_make_http_request.side_effect = [mock_total_response, mock_items_response]

        # Call the function
        result = fetch_and_process_media_items(mock_client, "http://example.com", "lib1")

        # Verify the result
        assert "imdb" in result
        assert "tt1234567" in result["imdb"]
        assert "tt7654321" in result["imdb"]

        # Verify API calls
        assert mock_make_http_request.call_count == 2

    @patch('emby_dedupe.api.client.PAGE_SIZE', 2)
    @patch('emby_dedupe.api.client.make_http_request')
    def test_fetch_all_media_paths_paginates(self, mock_make_http_request):
        """fetch_all_media_paths walks every page and returns each item's verbatim Path.

        Feeds the deletion safety guard real folder visibility (see deletion_guard)."""
        from emby_dedupe.api.client import fetch_all_media_paths

        page1 = Mock()
        page1.json.return_value = {"Items": [
            {"Id": "1", "Path": "/Movies/A/A.mkv"},
            {"Id": "2", "Path": "/Movies/B/B.mkv"},   # full page (==PAGE_SIZE) → fetch again
        ]}
        page2 = Mock()
        page2.json.return_value = {"Items": [
            {"Id": "3", "Path": "/Movies/C/C.mkv"},
            {"Id": "4"},                               # no Path → skipped, not crashing
        ]}                                             # also full (==PAGE_SIZE) → fetch again
        page3 = Mock()
        page3.json.return_value = {"Items": []}        # empty trailing page → stop
        mock_make_http_request.side_effect = [page1, page2, page3]

        result = fetch_all_media_paths(Mock(), "http://emby")

        assert result == ["/Movies/A/A.mkv", "/Movies/B/B.mkv", "/Movies/C/C.mkv"]
        assert mock_make_http_request.call_count == 3

    @patch('emby_dedupe.api.client.make_http_request')
    def test_fetch_all_media_paths_degrades_to_empty_on_failure(self, mock_make_http_request):
        """A fetch failure must degrade to [] so the guard falls back to over-refusing
        (safe) rather than ever under-refusing."""
        from emby_dedupe.api.client import fetch_all_media_paths

        mock_make_http_request.side_effect = httpx.RequestError("boom")
        assert fetch_all_media_paths(Mock(), "http://emby") == []

    @patch('emby_dedupe.api.client.make_http_request')
    @patch('emby_dedupe.api.client.ensure_authenticated_for_delete')
    def test_delete_item_success(self, mock_ensure_auth, mock_make_http_request):
        """Test successful item deletion."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.is_success = True

        mock_make_http_request.return_value = mock_response
        mock_ensure_auth.return_value = ("auth_token", "user_id")

        result = delete_item(
            mock_client, "http://example.com", "item123",
            True, "username", "password", "api_key"
        )

        assert result["status"] == "success"
        assert result["id"] == "item123"
        assert mock_ensure_auth.called
        mock_make_http_request.assert_called_once()

    @patch('emby_dedupe.api.client.ensure_authenticated_for_delete')
    def test_delete_item_no_auth(self, mock_ensure_auth):
        """Test item deletion with authentication failure."""
        mock_client = Mock()
        mock_ensure_auth.return_value = (None, None)  # Auth failed

        result = delete_item(
            mock_client, "http://example.com", "item123",
            True, "username", "password", "api_key"
        )

        assert result["status"] == "failed"
        assert "Authentication failed" in result["error"]
        assert not mock_client.request.called  # No request should be made

    def test_delete_item_skipped(self):
        """Test item deletion in dry-run mode."""
        mock_client = Mock()

        result = delete_item(
            mock_client, "http://example.com", "item123",
            False, "username", "password", "api_key"
        )

        assert result["status"] == "skipped"
        assert not mock_client.request.called  # No request should be made

    @patch('emby_dedupe.api.client.hashlib')
    def test_get_auth_token_success(self, mock_hashlib):
        """Test successful authentication token retrieval."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "AccessToken": "test_token",
            "User": {"Id": "user123"}
        }
        mock_client.post.return_value = mock_response

        # Mock the SHA1 hash
        mock_sha = Mock()
        mock_sha.hexdigest.return_value = "hashed_password"
        mock_hashlib.sha1.return_value = mock_sha

        token, user_id = get_auth_token(mock_client, "http://example.com", "testuser", "testpass")

        assert token == "test_token"
        assert user_id == "user123"
        assert mock_client.post.called

    @patch('emby_dedupe.api.client.get_auth_token')
    def test_create_http_client(self, mock_get_auth_token):
        """Test creating an HTTP client with authentication."""
        mock_get_auth_token.return_value = ("test_token", "user123")

        client, token, user_id = create_http_client(
            "http://example.com", "testuser", "testpass"
        )

        assert token == "test_token"
        assert user_id == "user123"
        assert "X-Emby-Token" in client.headers
        assert client.headers["X-Emby-Token"] == "test_token"

    def test_handle_host_and_port_with_scheme(self):
        """Test handling host and port with scheme."""
        # Test with http scheme
        host, port = handle_host_and_port("http://emby.example.com", None)
        assert host == "http://emby.example.com"
        assert port == DEFAULT_PORT_HTTP

        # Test with https scheme
        host, port = handle_host_and_port("https://emby.example.com", None)
        assert host == "https://emby.example.com"
        assert port == DEFAULT_PORT_HTTPS

    def test_handle_host_and_port_with_explicit_port(self):
        """Test handling host and port with explicit port in URL."""
        host, port = handle_host_and_port("http://emby.example.com:8080", None)
        assert host == "http://emby.example.com"
        assert port == 8080

    def test_handle_host_and_port_with_arg_port(self):
        """Test handling host and port with port specified as argument."""
        # Argument port should override URL port
        host, port = handle_host_and_port("http://emby.example.com:8080", 9090)
        assert host == "http://emby.example.com"
        assert port == 9090

        # Argument port should be used if no port in URL
        host, port = handle_host_and_port("http://emby.example.com", 9090)
        assert host == "http://emby.example.com"
        assert port == 9090

    def test_handle_host_and_port_no_scheme(self):
        """Test handling host and port with no scheme."""
        host, port = handle_host_and_port("emby.example.com", None)
        assert host == "http://emby.example.com"
        assert port == DEFAULT_PORT_HTTP

    @patch('emby_dedupe.api.client.make_http_request')
    def test_check_emby_connection_success(self, mock_make_http_request):
        """Test checking Emby connection with successful response."""
        mock_client = Mock()
        mock_make_http_request.return_value = Mock()

        result = check_emby_connection(mock_client, "http://emby.example.com:8096")

        assert result is True
        mock_make_http_request.assert_called_once_with(
            mock_client, "GET", "http://emby.example.com:8096"
        )

    @patch('emby_dedupe.api.client.make_http_request')
    def test_check_emby_connection_http_error(self, mock_make_http_request):
        """Test checking Emby connection with HTTP error response."""
        mock_client = Mock()
        mock_make_http_request.side_effect = httpx.HTTPStatusError(
            "Error", request=Mock(), response=Mock()
        )
        mock_make_http_request.side_effect.response.content = b"Error message"

        with pytest.raises(EmbyServerConnectionError):
            check_emby_connection(mock_client, "http://emby.example.com:8096")

    @patch('emby_dedupe.api.client.make_http_request')
    def test_check_emby_connection_request_error(self, mock_make_http_request):
        """Test checking Emby connection with request error."""
        mock_client = Mock()
        mock_make_http_request.side_effect = httpx.RequestError("Error", request=Mock())

        with pytest.raises(EmbyServerConnectionError):
            check_emby_connection(mock_client, "http://emby.example.com:8096")

    @patch('emby_dedupe.api.client.make_http_request')
    def test_get_library_id_found(self, mock_make_http_request):
        """Test getting library ID when the library is found."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.json.return_value = [
            {"Name": "Movies", "Id": "lib1"},
            {"Name": "TV Shows", "Id": "lib2"}
        ]
        mock_make_http_request.return_value = mock_response

        result = get_library_id(mock_client, "http://emby.example.com:8096", "Movies")

        assert result == "lib1"
        mock_make_http_request.assert_called_once_with(
            mock_client, "GET", "http://emby.example.com:8096/Library/VirtualFolders"
        )

    @patch('emby_dedupe.api.client.make_http_request')
    def test_get_library_id_not_found(self, mock_make_http_request):
        """Test getting library ID when the library is not found."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.json.return_value = [
            {"Name": "Movies", "Id": "lib1"},
            {"Name": "TV Shows", "Id": "lib2"}
        ]
        mock_make_http_request.return_value = mock_response

        result = get_library_id(mock_client, "http://emby.example.com:8096", "Music")

        assert result is None
        mock_make_http_request.assert_called_once_with(
            mock_client, "GET", "http://emby.example.com:8096/Library/VirtualFolders"
        )

    @patch('emby_dedupe.api.client.make_http_request')
    def test_get_library_id_http_error(self, mock_make_http_request):
        """Test getting library ID with HTTP error."""
        mock_client = Mock()
        mock_make_http_request.side_effect = httpx.HTTPStatusError(
            "Error", request=Mock(), response=Mock()
        )

        result = get_library_id(mock_client, "http://emby.example.com:8096", "Movies")

        assert result is None

    def test_make_http_request_success(self):
        """Test making an HTTP request with successful response."""
        mock_client = Mock()
        mock_response = Mock()
        mock_client.request.return_value = mock_response

        result = make_http_request(mock_client, "GET", "http://emby.example.com:8096/endpoint")

        assert result == mock_response
        mock_client.request.assert_called_once_with(
            "GET", "http://emby.example.com:8096/endpoint", timeout=120
        )
        mock_response.raise_for_status.assert_called_once()

    @patch('emby_dedupe.api.client.make_http_request')
    def test_fetch_items_details(self, mock_make_http_request):
        """Test fetching detailed information for media items."""
        mock_client = Mock()
        mock_response = Mock()
        # Correct response format with Items array
        mock_response.json.return_value = {
            "Items": [
                {"Id": "item1", "Name": "Movie 1", "Path": "/path/to/movie1.mkv"},
                {"Id": "item2", "Name": "Movie 2", "Path": "/path/to/movie2.mkv"}
            ]
        }
        mock_make_http_request.return_value = mock_response

        result = fetch_items_details(mock_client, "http://example.com", ["item1", "item2"])

        # Verify the result
        assert len(result) == 2
        assert result[0]["Id"] == "item1"
        assert result[1]["Id"] == "item2"

        # Verify the API call
        mock_make_http_request.assert_called_once()
        call_args = mock_make_http_request.call_args
        assert call_args[0][2] == "http://example.com/Items"
        params = call_args[1]["params"]
        assert "item1,item2" in params["Ids"]

    @patch('emby_dedupe.api.client.make_http_request')
    def test_fetch_items_details_error(self, mock_make_http_request):
        """Test error handling when fetching item details."""
        mock_client = Mock()
        mock_make_http_request.side_effect = httpx.HTTPStatusError(
            "Error", request=Mock(), response=Mock()
        )

        result = fetch_items_details(mock_client, "http://example.com", ["item1", "item2"])

        # Should return empty list on error
        assert result == []
        mock_make_http_request.assert_called_once()

    @patch('emby_dedupe.api.client.get_auth_token')
    def test_ensure_authenticated_for_delete_success(self, mock_get_auth_token):
        """Test successful authentication for delete operations."""
        mock_get_auth_token.return_value = ("test_token", "user123")

        token, user_id = ensure_authenticated_for_delete(
            Mock(), "http://example.com", "testuser", "testpass"
        )

        assert token == "test_token"
        assert user_id == "user123"
        mock_get_auth_token.assert_called_once()

    def test_ensure_authenticated_for_delete_missing_credentials(self):
        """Test authentication failure due to missing credentials."""
        # Just testing the basic principle - the function shouldn't
        # try to authenticate with missing credentials
        with patch('emby_dedupe.api.client.logger'):  # To suppress logging
            # Save original values
            import emby_dedupe.api.client as client_module
            original_token = client_module.auth_state.token_for_delete
            original_user_id = client_module.auth_state.user_id

            try:
                # Reset auth state to test initial authentication
                client_module.auth_state.token_for_delete = None
                client_module.auth_state.user_id = None

                # Create a mock get_auth_token function that returns a token for valid credentials
                # but raises an exception for missing credentials
                def mock_get_auth_token_side_effect(client, base_url, username, password):
                    if username and password:
                        return ("test_token", "user123")
                    else:
                        raise Exception("Missing credentials")

                with patch('emby_dedupe.api.client.get_auth_token',
                           side_effect=mock_get_auth_token_side_effect):
                    # Try with valid credentials
                    token, user_id = ensure_authenticated_for_delete(
                        Mock(), "http://example.com", "testuser", "testpass"
                    )
                    assert token == "test_token"
                    assert user_id == "user123"

                    # Reset auth state for next test
                    client_module.auth_state.token_for_delete = None
                    client_module.auth_state.user_id = None

                    # Try with missing credentials
                    token, user_id = ensure_authenticated_for_delete(
                        Mock(), "http://example.com", "", ""
                    )
                    assert token is None
                    assert user_id is None

            finally:
                # Restore original values to not affect other tests
                client_module.auth_state.token_for_delete = original_token
                client_module.auth_state.user_id = original_user_id

    def test_ensure_authenticated_for_delete_auth_failure(self):
        """Test authentication failure when get_auth_token fails."""
        # Use a more direct approach to verify behavior when authentication fails
        with patch('emby_dedupe.api.client.get_auth_token') as mock_get_auth_token:
            # Mock get_auth_token to raise an exception
            mock_get_auth_token.side_effect = Exception("Authentication failed")

            # Mock logger to avoid actual logging
            with patch('emby_dedupe.api.client.logger'):
                # Save original values
                import emby_dedupe.api.client as client_module
                original_token = client_module.auth_state.token_for_delete
                original_user_id = client_module.auth_state.user_id

                try:
                    # Reset auth state to test initial authentication
                    client_module.auth_state.token_for_delete = None
                    client_module.auth_state.user_id = None

                    # Call the function
                    token, user_id = ensure_authenticated_for_delete(
                        Mock(), "http://example.com", "testuser", "testpass"
                    )

                    # The function should handle the exception and return None, None
                    assert token is None
                    assert user_id is None

                finally:
                    # Restore original values to not affect other tests
                    client_module.auth_state.token_for_delete = original_token
                    client_module.auth_state.user_id = original_user_id

    @patch('emby_dedupe.api.client.httpx')
    def test_logout_success(self, mock_httpx):
        """Test successful logout."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response

        # Mock logger to avoid actual logging
        with patch('emby_dedupe.api.client.logger'):
            logout(mock_client, "http://example.com", "test_token")

            # Logout doesn't return anything, it's a procedure not a function
            # Just verify the correct calls were made
            mock_client.post.assert_called_once()
            url = mock_client.post.call_args[0][0]
            assert "http://example.com/Sessions/Logout" in url

    def test_logout_failure(self):
        """Test logout failure."""
        mock_client = Mock()
        mock_client.post.side_effect = Exception("Logout failed")

        # Mock logger to avoid actual logging
        with patch('emby_dedupe.api.client.logger'):
            # The logout function should catch any exceptions
            # and not propagate them
            try:
                logout(mock_client, "http://example.com", "test_token")
                # If we reach here, the exception was caught as expected
            except Exception:
                pytest.fail("logout() should have caught the exception")

            # Verify the call was attempted
            mock_client.post.assert_called_once()
            url = mock_client.post.call_args[0][0]
            assert "http://example.com/Sessions/Logout" in url
