"""
Tests for constants module
"""
import os
import pytest
from emby_dedupe.utils.constants import (
    ENV_DEDUPE_LOGGING,
    ENV_DEDUPE_EMBY_HOST,
    ENV_DEDUPE_EMBY_PORT,
    ENV_DEDUPE_EMBY_API_KEY,
    ENV_DEDUPE_EMBY_LIBRARY,
    ENV_DEDUPE_DOIT,
    DEFAULT_PORT_HTTP,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_EMBY
)


class TestConstants:
    """Tests for the constants module."""

    def test_env_variables_defined(self):
        """Test that all environment variables are defined."""
        assert ENV_DEDUPE_LOGGING == "DEDUPE_LOGGING"
        assert ENV_DEDUPE_EMBY_HOST == "DEDUPE_EMBY_HOST"
        assert ENV_DEDUPE_EMBY_PORT == "DEDUPE_EMBY_PORT"
        assert ENV_DEDUPE_EMBY_API_KEY == "DEDUPE_EMBY_API_KEY"
        assert ENV_DEDUPE_EMBY_LIBRARY == "DEDUPE_EMBY_LIBRARY"
        assert ENV_DEDUPE_DOIT == "DEDUPE_DOIT"

    def test_default_ports_defined(self):
        """Test that all default ports are correctly defined."""
        assert DEFAULT_PORT_HTTP == 80
        assert DEFAULT_PORT_HTTPS == 443
        assert DEFAULT_PORT_EMBY == 8096