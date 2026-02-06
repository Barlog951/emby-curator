"""
Constants and default values used throughout the Emby Dedupe tool.
"""

import logging

# General constants
MAX_RETRIES = 20  # The maximum number of retries for HTTP requests
MAX_BACKOFF_TIME = 600  # Maximum total backoff time in seconds
HTTP_TIMEOUT = 120  # HTTP timeout in seconds (2 minutes)
PAGE_SIZE = 1000  # The page size for paginated requests
EMOJI_CHECK = "✅"
EMOJI_CROSS = "❌"

# Report generation constants
ITEMS_TO_DELETE_HEADER = "Items to Delete"

# Language normalization mapping
# Maps various language code variants to their canonical ISO 639-1 codes
# Used for normalizing Slovak and Czech language codes across different formats
LANGUAGE_NORMALIZATION_MAP = {
    "slo": "sk",  # Slovak ISO 639-2 -> ISO 639-1
    "slovak": "sk",  # Slovak full name
    "sk": "sk",   # Slovak ISO 639-1
    "cze": "cs",  # Czech ISO 639-2 -> ISO 639-1
    "ces": "cs",  # Czech ISO 639-2 alternate
    "czech": "cs",  # Czech full name
    "cs": "cs"    # Czech ISO 639-1
}

# Environment variable names
ENV_DEDUPE_LOGGING = "DEDUPE_LOGGING"
ENV_DEDUPE_EMBY_HOST = "DEDUPE_EMBY_HOST"
ENV_DEDUPE_EMBY_PORT = "DEDUPE_EMBY_PORT"
ENV_DEDUPE_EMBY_API_KEY = "DEDUPE_EMBY_API_KEY"
ENV_DEDUPE_EMBY_LIBRARY = "DEDUPE_EMBY_LIBRARY"
ENV_DEDUPE_DOIT = "DEDUPE_DOIT"
ENV_DEDUPE_EMBY_USERNAME = "DEDUPE_EMBY_USERNAME"
# nosec B105: This constant is just the name of an environment variable, not a hardcoded password
ENV_DEDUPE_EMBY_PASSWORD = "DEDUPE_EMBY_PASSWORD"
ENV_DEDUPE_HTML_REPORT = "DEDUPE_HTML_REPORT"
ENV_DEDUPE_HTML_ONLY = "DEDUPE_HTML_ONLY"
ENV_DEDUPE_LANG_PRIO = "DEDUPE_LANG_PRIO"
ENV_DEDUPE_EXCLUDE_IDS = "DEDUPE_EXCLUDE_IDS"

# Default port values
DEFAULT_PORT_HTTP = 80
DEFAULT_PORT_HTTPS = 443
DEFAULT_PORT_EMBY = 8096

# Logging levels
LOGGING_LEVELS = {
    "": logging.ERROR,  # Default to ERROR if no verbosity
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def should_quality_override_language(
    quality_ratio: float,
    lang_item_has_priority_lang: bool,
    quality_item_has_priority_lang: bool,
    is_single_lang_scenario: bool
) -> bool:
    """
    Determine if quality should override language priority based on smart override rules.

    This implements the "smart override" logic used in both deduplication and quality
    comparison workflows. Quality can win over language priority in two scenarios:

    1. Single-lang vs multi-lang: When the language-priority item has only one audio
       track but the quality item has multiple tracks (2+) and is 1.5x better quality.

    2. No priority language: When the quality item lacks the priority language but is
       3x better quality than the language-priority item.

    Args:
        quality_ratio: Ratio of quality_score / lang_score (must be > 0)
        lang_item_has_priority_lang: True if language-priority item has priority language
        quality_item_has_priority_lang: True if quality item has priority language
        is_single_lang_scenario: True if lang item has 1 audio track and quality item has 2+

    Returns:
        True if quality should override language priority, False otherwise
    """
    # Scenario 1: Single-lang vs multi-lang (1.5x threshold)
    if is_single_lang_scenario and quality_ratio > 1.5:
        return True

    # Scenario 2: Quality item lacks priority language but is 3x+ better
    if lang_item_has_priority_lang and not quality_item_has_priority_lang and quality_ratio > 3.0:
        return True

    return False
