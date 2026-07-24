"""
Custom exceptions for the Emby Dedupe tool.

Taxonomy (all library errors derive from EmbyDedupeError so consumers can catch
one base instead of string-matching messages):

    EmbyDedupeError
    ├── EmbyServerConnectionError
    └── EmbyConfigError (also a ValueError — existing ``except ValueError`` keeps working)
        └── EmbyConfigMissingError (also a FileNotFoundError — consumers that catch
            FileNotFoundError around EmbyChecker.from_config() get the right branch)
"""


class EmbyDedupeError(Exception):
    """Base class for all emby-curator library errors."""


class EmbyServerConnectionError(EmbyDedupeError):
    """
    Exception raised when there is an issue connecting to the Emby server.
    This could be due to invalid credentials, network issues, or server problems.
    """


class EmbyConfigError(EmbyDedupeError, ValueError):
    """Configuration is invalid or incomplete (e.g. host/api_key missing).

    Subclasses ValueError so callers with ``except ValueError`` continue to work.
    """


class EmbyConfigMissingError(EmbyConfigError, FileNotFoundError):
    """The config file does not exist and no overrides supplied the required values.

    Subclasses FileNotFoundError so callers with ``except FileNotFoundError`` around
    ``EmbyChecker.from_config()`` catch exactly this case.
    """
