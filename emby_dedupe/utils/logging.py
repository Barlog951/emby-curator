"""
Logging utilities for the Emby Dedupe tool.
"""

import logging
import re
import sys
from typing import List, Optional

from emby_dedupe.utils.constants import LOGGING_LEVELS

# Create a logger for the tool
logger = logging.getLogger("EmbyDedupe")
logger.setLevel(logging.ERROR)  # Default log level for the tool's logger


class SensitiveDataFilter(logging.Filter):
    """
    Log filter that redacts sensitive information in log messages such as keys or passwords.
    """

    def __init__(self, patterns: Optional[List[re.Pattern]] = None):
        super().__init__()
        # Add more patterns for keys or tokens that do not have a clear prefix/suffix.
        self._patterns = patterns or [
            re.compile(r"(?<=api_key=)[\w-]{10,}"),  # API key with prefix
            re.compile(r"(?<=password=)[\w-]{10,}"),  # Passwords with prefix
            re.compile(
                r"\b[A-Z0-9]{20,}\b"
            ),  # Matches uppercase strings of 20+ chars (possible keys or tokens)
            re.compile(
                r"\b[0-9a-fA-F-]{30,}\b"
            ),  # Matches hexadecimal strings (often used in keys and tokens) of 30+ chars
            re.compile(
                r"\b[0-9A-Za-z_\-]{32,}\b"
            ),  # Alphanumeric strings with underscores/dashes, 32+ chars long
            # ... Add additional patterns as necessary
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Redact sensitive information in the message before logging.

        Args:
            record (logging.LogRecord): The log record being processed.

        Returns:
            bool: True if the log record should be logged, False otherwise. This implementation
                  always returns True as we simply want to modify the record, not reject it.
        """
        original = record.getMessage()
        for pattern in self._patterns:
            # Replace occurrences of the pattern with 'REDACTED'
            original = pattern.sub("REDACTED", original)
        # Set the modified message
        record.msg = original
        return True


def set_logging_level(verbosity_count: int, env_verbosity: Optional[str] = None) -> None:
    """
    Set logging level based on verbosity count and environment variable.

    Args:
        verbosity_count (int): Count of verbose flags (-v) in the command line.
        env_verbosity (str, optional): Verbosity level from the environment variable.
    """
    # Determine the logging level
    levels = ["ERROR", "WARNING", "INFO", "DEBUG"]
    level_name = env_verbosity or "ERROR"
    if verbosity_count:
        level_name = levels[min(verbosity_count, len(levels) - 1)]
    level = LOGGING_LEVELS.get(level_name, logging.ERROR)

    # Set the level for the tool's logger instead of the root logger
    logger.setLevel(level)

    # Create a log filter and formatter
    sensitive_data_filter = SensitiveDataFilter()
    logger.addFilter(sensitive_data_filter)  # Add the custom filter to the logger

    # To avoid duplicate logging if the function is called multiple times, clear any previously added handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # Configure a console handler for the tool's logger
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info(f"Logging level set to {logging.getLevelName(level)}")
