"""
Tests for logging utilities
"""
import logging
import pytest
from io import StringIO
from unittest.mock import Mock, patch

from emby_dedupe.utils.logging import (
    logger,
    SensitiveDataFilter,
    set_logging_level
)
from emby_dedupe.utils.constants import LOGGING_LEVELS


class TestSensitiveDataFilter:
    """Tests for the SensitiveDataFilter class."""

    def test_init_default_patterns(self):
        """Test initialization with default patterns."""
        filter = SensitiveDataFilter()
        assert len(filter._patterns) > 0  # Should have some default patterns

    def test_init_custom_patterns(self):
        """Test initialization with custom patterns."""
        custom_patterns = [Mock(), Mock()]
        filter = SensitiveDataFilter(custom_patterns)
        assert filter._patterns == custom_patterns

    def test_filter_redacts_api_key(self):
        """Test that API keys are redacted."""
        filter = SensitiveDataFilter()
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='API key: api_key=abcdef1234567890', args=(), exc_info=None
        )
        
        result = filter.filter(record)
        
        assert result is True  # Should always return True
        assert 'REDACTED' in record.msg
        assert 'abcdef1234567890' not in record.msg

    def test_filter_redacts_password(self):
        """Test that passwords are redacted."""
        filter = SensitiveDataFilter()
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='Password: password=SuperSecretPassword123', args=(), exc_info=None
        )
        
        result = filter.filter(record)
        
        assert result is True
        assert 'REDACTED' in record.msg
        assert 'SuperSecretPassword123' not in record.msg

    def test_filter_redacts_long_hex_strings(self):
        """Test that long hexadecimal strings are redacted."""
        filter = SensitiveDataFilter()
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='Token: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b', args=(), exc_info=None
        )
        
        result = filter.filter(record)
        
        assert result is True
        assert 'REDACTED' in record.msg
        assert '1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b' not in record.msg


class TestSetLoggingLevel:
    """Tests for the set_logging_level function."""

    @patch('emby_dedupe.utils.logging.logger')
    def test_set_level_from_env(self, mock_logger):
        """Test setting log level from environment variable."""
        set_logging_level(0, "DEBUG")
        mock_logger.setLevel.assert_called_with(logging.DEBUG)

    @patch('emby_dedupe.utils.logging.logger')
    def test_set_level_from_verbosity(self, mock_logger):
        """Test setting log level from verbosity count."""
        set_logging_level(2, None)
        mock_logger.setLevel.assert_called_with(logging.INFO)

    @patch('emby_dedupe.utils.logging.logger')
    def test_set_level_default(self, mock_logger):
        """Test setting default log level."""
        set_logging_level(0, None)
        mock_logger.setLevel.assert_called_with(logging.ERROR)

    @patch('emby_dedupe.utils.logging.logger')
    def test_verbosity_precedence(self, mock_logger):
        """Test that verbosity count takes precedence over environment variable."""
        set_logging_level(1, "ERROR")
        mock_logger.setLevel.assert_called_with(logging.WARNING)

    @patch('emby_dedupe.utils.logging.logger')
    def test_add_filter(self, mock_logger):
        """Test that SensitiveDataFilter is added to the logger."""
        set_logging_level(0, None)
        mock_logger.addFilter.assert_called_once()
        # Check that a SensitiveDataFilter was added
        filter_arg = mock_logger.addFilter.call_args[0][0]
        assert isinstance(filter_arg, SensitiveDataFilter)

    @patch('emby_dedupe.utils.logging.logger')
    def test_console_handler_added(self, mock_logger):
        """Test that a console handler is added to the logger."""
        set_logging_level(0, None)
        mock_logger.addHandler.assert_called_once()
        # Check that a console handler was added with the right level
        handler_arg = mock_logger.addHandler.call_args[0][0]
        assert isinstance(handler_arg, logging.StreamHandler)
        assert handler_arg.level == logging.ERROR

    def test_filter_accumulation_prevention(self):
        """Test that calling set_logging_level twice results in exactly 1 filter, not 2."""
        # Call set_logging_level twice
        set_logging_level(0, None)
        set_logging_level(0, None)

        # Verify that exactly 1 filter is present (not 2 accumulated filters)
        assert len(logger.filters) == 1
        assert isinstance(logger.filters[0], SensitiveDataFilter)