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
