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
    DEFAULT_PORT_EMBY,
    should_quality_override_language
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

    # ========== Phase 1 Helper Tests (for Quality Gate Coverage) ==========

    def test_should_quality_override_single_lang_scenario_above_threshold(self):
        """Test quality override in single-lang scenario with 1.5x threshold."""
        # Quality item is 1.6x better, single-lang scenario
        result = should_quality_override_language(
            quality_ratio=1.6,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=True
        )

        # Should override since ratio > 1.5x in single-lang scenario
        assert result is True

    def test_should_quality_override_single_lang_scenario_below_threshold(self):
        """Test quality override in single-lang scenario below 1.5x threshold."""
        result = should_quality_override_language(
            quality_ratio=1.3,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=True
        )

        # Should NOT override since ratio < 1.5x
        assert result is False

    def test_should_quality_override_no_priority_lang_above_threshold(self):
        """Test quality override when quality item lacks priority lang, 3x threshold."""
        # Quality item is 3.5x better, no priority language
        result = should_quality_override_language(
            quality_ratio=3.5,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=False
        )

        # Should override since ratio > 3x
        assert result is True

    def test_should_quality_override_no_priority_lang_below_threshold(self):
        """Test quality override when quality item lacks priority lang, below 3x."""
        result = should_quality_override_language(
            quality_ratio=2.5,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=False
        )

        # Should NOT override since ratio < 3x
        assert result is False

    def test_should_quality_override_both_have_priority_lang_high_ratio(self):
        """Test override when both have priority language and quality is 2x+ better."""
        result = should_quality_override_language(
            quality_ratio=5.0,  # 5x quality difference
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=True,
            is_single_lang_scenario=False
        )

        # Scenario 3: both have priority langs but quality is 2x+ better → override
        assert result is True

    def test_should_quality_override_both_have_priority_lang_low_ratio(self):
        """Test no override when both have priority language and quality gap is small."""
        result = should_quality_override_language(
            quality_ratio=1.5,  # Only 1.5x — below 2x threshold
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=True,
            is_single_lang_scenario=False
        )

        # Quality gap < 2x, language priority should hold
        assert result is False