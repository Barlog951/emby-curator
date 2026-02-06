"""
Pytest configuration file with fixtures.
"""
import os
import json
import pytest
from unittest.mock import Mock, patch


@pytest.fixture
def sample_media_item():
    """Returns a sample media item for testing."""
    return {
        "Id": "12345",
        "Name": "Test Item",
        "Path": "/movies/test_item.mkv",
        "ServerId": "server1",
        "MediaStreams": [
            {
                "Type": "Video",
                "Codec": "h264",
                "Height": 1080,
                "Width": 1920,
                "BitRate": 10000000,
                "BitDepth": 8,
                "IsInterlaced": False,
                "DisplayTitle": "1080p"
            },
            {
                "Type": "Audio",
                "Codec": "aac",
                "Channels": 6,
                "BitRate": 384000,
                "Language": "eng"
            }
        ],
        "Size": 5000000000,
        "ProviderIds": {
            "Imdb": "tt1234567",
            "Tmdb": "1234",
            "Tvdb": "5678"
        }
    }


@pytest.fixture
def sample_duplicate_group():
    """Returns a sample group of duplicate media items."""
    return ["12345", "67890", "24680"]


@pytest.fixture
def mock_httpx_client():
    """Returns a mock httpx client for testing."""
    mock_client = Mock()
    mock_response = Mock()
    mock_response.json.return_value = {"Items": []}
    mock_response.is_success = True
    mock_client.request.return_value = mock_response
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    return mock_client


@pytest.fixture
def mock_emby_response():
    """Returns a mock Emby API response."""
    return {
        "Items": [
            {
                "Id": "12345",
                "Name": "Test Movie",
                "Path": "/movies/test_movie.mkv",
                "IsFolder": False,
                "ProviderIds": {
                    "Imdb": "tt1234567",
                    "Tmdb": "1234"
                }
            },
            {
                "Id": "67890",
                "Name": "Another Test Movie",
                "Path": "/movies/another_test_movie.mkv",
                "IsFolder": False,
                "ProviderIds": {
                    "Imdb": "tt1234567",
                    "Tmdb": "1234"
                }
            }
        ],
        "TotalRecordCount": 2
    }


@pytest.fixture
def fixture_path():
    """Returns the path to the fixture files directory."""
    return os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def load_fixture(fixture_path):
    """Returns a function to load a fixture file."""
    def _load_fixture(filename):
        filepath = os.path.join(fixture_path, filename)
        with open(filepath, 'r') as f:
            return json.load(f)
    return _load_fixture