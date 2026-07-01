"""
Tests for HTTP utilities
"""
from unittest.mock import Mock, patch

import httpx
import pytest

from emby_dedupe.utils.http import handle_giveup, make_http_request, should_give_up


class TestHttpUtils:
    """Tests for HTTP utility functions."""

    def test_should_give_up_client_error(self):
        """Test that should_give_up returns True for client errors."""
        # Client error - 4xx status code
        mock_response = Mock()
        mock_response.status_code = 404
        error = Mock(spec=httpx.HTTPStatusError)
        error.response = mock_response

        result = should_give_up(error)

        assert result is True

    def test_should_give_up_server_error(self):
        """Test that should_give_up returns False for server errors."""
        # Server error - 5xx status code
        mock_response = Mock()
        mock_response.status_code = 500
        error = Mock(spec=httpx.HTTPStatusError)
        error.response = mock_response

        result = should_give_up(error)

        assert result is False

    def test_should_give_up_non_http_error(self):
        """Test that should_give_up returns False for non-HTTP errors."""
        # Not an HTTPStatusError
        error = httpx.RequestError("Connection error", request=Mock())

        result = should_give_up(error)

        assert result is False

    def test_should_give_up_permission_denied_500(self):
        """A 500 caused by a filesystem permission error is permanent → give up
        immediately (regression for the 'stuck delete' caused by Emby being unable to
        delete a file in a folder it lacks write access to)."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = (
            "System.IO.IOException: Permission denied\n   at DeleteFile(String path)"
        )
        error = Mock(spec=httpx.HTTPStatusError)
        error.response = mock_response

        assert should_give_up(error) is True

    def test_should_give_up_transient_500_still_retries(self):
        """A generic 500 (no permission/IO marker) stays retryable."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        error = Mock(spec=httpx.HTTPStatusError)
        error.response = mock_response

        assert should_give_up(error) is False

    def test_should_give_up_503_still_retries(self):
        """Transient gateway/unavailable 5xx remain retryable."""
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        error = Mock(spec=httpx.HTTPStatusError)
        error.response = mock_response

        assert should_give_up(error) is False

    def test_should_give_up_500_unreadable_body_does_not_crash(self):
        """If the 500 body can't be read, default to retryable (don't raise)."""
        mock_response = Mock()
        mock_response.status_code = 500
        type(mock_response).text = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("stream consumed"))
        )
        error = Mock(spec=httpx.HTTPStatusError)
        error.response = mock_response

        assert should_give_up(error) is False

    def test_handle_giveup_generic(self):
        """Test handle_giveup with a generic exception."""
        details = {'tries': 5, 'exception': RuntimeError("timeout")}
        handle_giveup(details)

    def test_handle_giveup_http_status_error(self):
        """Test handle_giveup includes HTTP status and URL for HTTPStatusError."""
        mock_request = Mock()
        mock_request.url = "http://emby/Users/u1/Items/42"
        mock_response = Mock()
        mock_response.status_code = 404
        exc = httpx.HTTPStatusError("404", request=mock_request, response=mock_response)
        details = {'tries': 1, 'exception': exc}
        # Should not raise; just log
        handle_giveup(details)

    def test_handle_giveup_no_exception(self):
        """Test handle_giveup when no exception key present."""
        details = {'tries': 3}
        handle_giveup(details)

    def test_make_http_request_success(self):
        """Test make_http_request with a successful response."""
        mock_client = Mock()
        mock_response = Mock()
        mock_client.request.return_value = mock_response

        result = make_http_request(mock_client, 'GET', 'http://example.com')

        assert result == mock_response
        mock_client.request.assert_called_once_with('GET', 'http://example.com', timeout=120)
        mock_response.raise_for_status.assert_called_once()

    def test_make_http_request_with_custom_timeout(self):
        """Test make_http_request with a custom timeout."""
        mock_client = Mock()
        mock_response = Mock()
        mock_client.request.return_value = mock_response

        result = make_http_request(mock_client, 'GET', 'http://example.com', timeout=60)

        assert result == mock_response
        mock_client.request.assert_called_once_with('GET', 'http://example.com', timeout=60)

    def test_make_http_request_with_additional_args(self):
        """Test make_http_request with additional arguments."""
        mock_client = Mock()
        mock_response = Mock()
        mock_client.request.return_value = mock_response

        result = make_http_request(
            mock_client, 'POST', 'http://example.com',
            json={'key': 'value'},
            headers={'Content-Type': 'application/json'}
        )

        assert result == mock_response
        mock_client.request.assert_called_once_with(
            'POST', 'http://example.com',
            timeout=120,
            json={'key': 'value'},
            headers={'Content-Type': 'application/json'}
        )

    @patch('emby_dedupe.utils.http.backoff')
    def test_make_http_request_error(self, mock_backoff):
        """Test make_http_request with an error response."""
        # Mock backoff to not apply retries
        mock_backoff.on_exception.return_value = lambda f: f

        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 404

        # Create a properly structured HTTPStatusError
        error = httpx.HTTPStatusError(
            "Error",
            request=Mock(),
            response=mock_response
        )
        mock_client.request.side_effect = error

        # Test that the error is propagated
        with pytest.raises(httpx.HTTPStatusError):
            make_http_request(mock_client, 'GET', 'http://example.com')
