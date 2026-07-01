"""
Tests for constants module
"""
from emby_dedupe.utils.constants import (
    DEFAULT_PORT_EMBY,
    DEFAULT_PORT_HTTP,
    DEFAULT_PORT_HTTPS,
    ENV_DEDUPE_DOIT,
    ENV_DEDUPE_EMBY_API_KEY,
    ENV_DEDUPE_EMBY_HOST,
    ENV_DEDUPE_EMBY_LIBRARY,
    ENV_DEDUPE_EMBY_PORT,
    ENV_DEDUPE_LOGGING,
    should_quality_override_language,
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
        """Test quality override in single-lang scenario above the 2.5x threshold."""
        # Quality item is 2.6x better, single-lang scenario
        result = should_quality_override_language(
            quality_ratio=2.6,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=True
        )

        # Should override since ratio > 2.5x in single-lang scenario
        assert result is True

    def test_should_quality_override_single_lang_scenario_below_threshold(self):
        """Test quality override in single-lang scenario below the 2.5x threshold."""
        result = should_quality_override_language(
            quality_ratio=2.4,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=True
        )

        # Should NOT override since ratio < 2.5x
        assert result is False

    def test_should_quality_override_no_priority_lang_above_threshold(self):
        """Test quality override when quality item lacks priority lang, above 5x threshold."""
        # Quality item is 5.5x better, no priority language
        result = should_quality_override_language(
            quality_ratio=5.5,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=False
        )

        # Should override since ratio > 5x
        assert result is True

    def test_should_quality_override_no_priority_lang_below_threshold(self):
        """Test quality override when quality item lacks priority lang, below 5x."""
        result = should_quality_override_language(
            quality_ratio=4.5,
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=False,
            is_single_lang_scenario=False
        )

        # Should NOT override since ratio < 5x
        assert result is False

    def test_should_quality_override_both_have_priority_lang_high_ratio(self):
        """Test override when both have priority language and quality is 4x+ better."""
        result = should_quality_override_language(
            quality_ratio=4.5,  # 4.5x quality difference (multi-tier jump)
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=True,
            is_single_lang_scenario=False
        )

        # Scenario 3: both have priority langs but quality is 4x+ better → override
        assert result is True

    def test_should_quality_override_both_have_priority_lang_low_ratio(self):
        """Test no override when both have priority language and quality gap is < 4x."""
        result = should_quality_override_language(
            quality_ratio=3.5,  # 3.5x — a single-tier jump, below the 4x threshold
            lang_item_has_priority_lang=True,
            quality_item_has_priority_lang=True,
            is_single_lang_scenario=False
        )

        # Single-tier quality gap < 4x → language priority holds
        assert result is False
