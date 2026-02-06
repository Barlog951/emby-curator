"""
Tests for the main module
"""
import pytest
from unittest.mock import patch
import sys


class TestMain:
    """Tests for the main module."""

    @patch('emby_dedupe.cli.main.main')
    def test_main_calls_cli_main(self, mock_cli_main):
        """Test that the main module calls the CLI's main function."""
        # We need to patch sys.argv to avoid argparse errors
        with patch.object(sys, 'argv', ['emby_dedupe']):
            # Import the module here to avoid argparse parsing during collection
            from emby_dedupe.__main__ import main
            main()
        mock_cli_main.assert_called_once()