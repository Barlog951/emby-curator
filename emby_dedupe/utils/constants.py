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
IGNORED_IMDB_ID = "tt0000000"  # Placeholder IMDb ID excluded from duplicate grouping

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

# Genre management constants
GENRE_UPDATE_DELAY_SEC = 0.1  # Rate limit: seconds between Emby POST updates for genres

# Genre normalization mapping
# Maps variant/incorrect names to TMDB-standard canonical names (case-insensitive keys)
# Based on real Emby audit (2026-02-23): 32 genres found across 2500+ items
# NOTE: Musical≠Music, Children≠Family, Biography≠Documentary — intentionally NOT mapped
_SCIENCE_FICTION = "Science Fiction"
GENRE_NORMALIZATION_MAP = {
    # Typos found in real Emby audit
    "hisotry": "History",       # Typo, 1 item
    # Duplicates/standard variants
    "sci-fi": _SCIENCE_FICTION,
    "sf": _SCIENCE_FICTION,
    "scifi": _SCIENCE_FICTION,
    "suspense": "Thriller",     # 126 items
    "reality-tv": "Reality",    # 9 items
    # Non-English (Slovak/Czech) — found in real audits
    "vojnový": "War",               # Slovak
    "dokument": "Documentary",      # Czech
    "dokumenty": "Documentary",     # Slovak plural
    "dokumentárny": "Documentary",  # Slovak adjective form
    "romantický": "Romance",        # Slovak/Czech
    "rodinný": "Family",            # Slovak/Czech
    "historický": "History",        # Slovak/Czech
    "animovaný": "Animation",       # Slovak/Czech
    "akčný": "Action",              # Slovak
    "dobrodružný": "Adventure",     # Slovak
    "krimi": "Crime",               # Slovak/Czech colloquial
    "kriminálny": "Crime",          # Slovak (11 items)
    "vedeckofantastický": _SCIENCE_FICTION,  # Slovak
    "horor": "Horror",              # Typo + Slovak (horor = horror)
    "komédia": "Comedy",            # Slovak (52 items)
    "mysteriózny": "Mystery",       # Slovak (4 items)
    "hudobný": "Music",             # Slovak (4 items)
    # Custom/junk — user confirmed "dada" means Comedy
    "dada": "Comedy",               # 1645 items!
    # 2026-05-27 audit additions
    "reality show": "Reality",      # verbose duplicate of Reality (3 items)
    "sci-fi & fantasy": _SCIENCE_FICTION,  # TMDB TV compound — pick distinct element (2 items)
    "detský": "Kids",               # Slovak for "children's" (2 items)
    "akčný a dobrodružný": "Action",  # Slovak "Action & Adventure" — Action dominant (3 items)
    "tv film": "TV Movie",          # Emby variant of TMDB "TV Movie" (75 items)
}

# TMDB canonical genre names — the authoritative list used for audit suggestions.
# Anything found in Emby that is NOT in this set and NOT already in
# GENRE_NORMALIZATION_MAP is flagged as unknown by `genres audit --suggest`.
TMDB_CANONICAL_GENRES: frozenset[str] = frozenset({
    # TMDB movie genres
    "Action", "Adventure", "Animation", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "History",
    "Horror", "Music", "Mystery", "Romance", "Science Fiction",
    "Thriller", "War", "Western",
    # TMDB TV-specific genres (as used in Emby)
    "Reality", "Kids", "Soap", "Talk", "News",
    # Common extras that Emby / metadata agents use
    "Biography", "Children", "Mini-Series", "Musical",
    "Short", "Special Interest", "Sport", "Talk Show",
    # 2026-05-27 audit additions — legitimate distinct Emby categories
    "TV Movie", "Anime", "Travel", "Food", "Game Show",
    "Martial Arts", "Indie", "Home and Garden",
})

# Environment variable names for Phase 2 external APIs (define early)
ENV_DEDUPE_TMDB_API_KEY = "DEDUPE_TMDB_API_KEY"
ENV_DEDUPE_OMDB_API_KEY = "DEDUPE_OMDB_API_KEY"
ENV_DEDUPE_OMDB_API_KEYS = "DEDUPE_OMDB_API_KEYS"  # comma-separated for rotation

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
    comparison workflows. Quality can win over language priority in three scenarios:

    1. Single-lang vs multi-lang: When the language-priority item has only one audio
       track but the quality item has multiple tracks (2+) and is 1.5x better quality.

    2. No priority language: When the quality item lacks the priority language but is
       3x better quality than the language-priority item.

    3. Both have priority languages: When both items have a priority language (e.g.,
       existing has Slovak, proposed has Czech) but quality is 2x+ better. A massive
       quality upgrade (e.g., REMUX vs WEB-DL) justifies losing a higher-ranked
       language track.

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

    # Scenario 3: Both have priority languages but quality is 2x+ better
    # e.g., existing WEB-DL has Slovak (priority 0) vs proposed REMUX has Czech (priority 1)
    # A 2x quality upgrade justifies losing a higher-ranked language track
    if lang_item_has_priority_lang and quality_item_has_priority_lang and quality_ratio > 2.0:
        return True

    return False
