"""
Configuration file loading for emby-dedupe.

Loads ~/.emby-dedupe/config.yaml (user config) with explicit overrides on top
(CLI arguments / EmbyChecker.from_config(**overrides)). Environment variables are
resolved by the CLI layer (typer ``envvar=``), not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from emby_dedupe.utils.logging import logger

CONFIG_DIR = Path.home() / ".emby-dedupe"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
CACHE_DIR = CONFIG_DIR / "cache"


def get_config_path() -> Path:
    """Get the path to the config file.

    Returns:
        Path: Path to the config file.
    """
    return CONFIG_FILE


def ensure_cache_dir() -> Path:
    """Ensure the cache directory exists.

    Returns:
        Path: Path to the cache directory.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def load_config() -> dict[str, Any]:
    """Load configuration from the config file.

    Returns:
        dict: Configuration dictionary. Empty if file doesn't exist or YAML not available.
    """
    if not CONFIG_FILE.exists():
        logger.debug(f"Config file not found at {CONFIG_FILE}")
        return {}

    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f) or {}
            logger.debug(f"Loaded config from {CONFIG_FILE}")
            return config
    except Exception as e:
        logger.warning(f"Error loading config file: {e}")
        return {}


class Config:
    """Configuration object for emby-dedupe check functionality."""

    def __init__(
        self,
        host: str | None = None,
        api_key: str | None = None,
        libraries: list[str] | None = None,
        lang_priorities: list[str] | None = None,
        exclude_ids: list[str] | None = None,
        cache_enabled: bool = True,
        cache_ttl_minutes: int = 10,
    ):
        """Initialize configuration.

        Args:
            host: Emby server URL.
            api_key: Emby API key.
            libraries: List of libraries to search. None = all libraries.
            lang_priorities: Language priority list (e.g., ['sk', 'cs', 'en']).
            exclude_ids: Provider IDs to exclude from checking.
            cache_enabled: Whether to enable caching.
            cache_ttl_minutes: Cache TTL in minutes.
        """
        self.host = host
        self.api_key = api_key
        self.libraries = libraries
        self.lang_priorities = lang_priorities
        self.exclude_ids = exclude_ids or []
        self.cache_enabled = cache_enabled
        self.cache_ttl_minutes = cache_ttl_minutes

    @classmethod
    def from_config_file(cls, **overrides) -> Config:
        """Load configuration from config file with optional overrides.

        Args:
            **overrides: Values to override from config file.

        Returns:
            Config: Configuration object.
        """
        file_config = load_config()

        return cls(
            host=overrides.get('host') or file_config.get('host'),
            api_key=overrides.get('api_key') or file_config.get('api_key'),
            libraries=overrides.get('libraries') or file_config.get('libraries'),
            lang_priorities=overrides.get('lang_priorities') or file_config.get('lang_priorities'),
            exclude_ids=overrides.get('exclude_ids') or file_config.get('exclude_ids'),
            cache_enabled=overrides.get('cache_enabled', file_config.get('cache_enabled', True)),
            cache_ttl_minutes=overrides.get('cache_ttl_minutes', file_config.get('cache_ttl_minutes', 10)),
        )

    @classmethod
    def _apply_cli_overrides(cls, config: Config, args) -> None:
        """Apply CLI argument overrides to config (in-place)."""
        if hasattr(args, 'host') and args.host:
            config.host = args.host
        if hasattr(args, 'api_key') and args.api_key:
            config.api_key = args.api_key
        if hasattr(args, 'library') and args.library:
            config.libraries = args.library
        if hasattr(args, 'lang_prio') and args.lang_prio:
            config.lang_priorities = [lang.strip() for lang in args.lang_prio.split(',')]
        if hasattr(args, 'exclude_ids') and args.exclude_ids:
            config.exclude_ids = [i.strip() for i in args.exclude_ids.split(',')]
        if hasattr(args, 'cache') and args.cache is not None:
            config.cache_enabled = args.cache
        if hasattr(args, 'all_libraries') and args.all_libraries:
            config.libraries = None

    @classmethod
    def from_cli_args(cls, args, **overrides) -> Config:
        """Create configuration from CLI arguments.

        Args:
            args: Parsed argparse namespace.
            **overrides: Additional overrides.

        Returns:
            Config: Configuration object.
        """
        config = cls.from_config_file()
        cls._apply_cli_overrides(config, args)

        # Apply any additional overrides
        for key, value in overrides.items():
            if value is not None:
                setattr(config, key, value)

        return config

    def validate(self) -> list[str]:
        """Validate the configuration.

        Returns:
            list: List of validation error messages. Empty if valid.
        """
        errors = []

        if not self.host:
            errors.append("host is required")
        if not self.api_key:
            errors.append("api_key is required")

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary.

        Returns:
            dict: Configuration as dictionary.
        """
        return {
            'host': self.host,
            'api_key': self.api_key,
            'libraries': self.libraries,
            'lang_priorities': self.lang_priorities,
            'exclude_ids': self.exclude_ids,
            'cache_enabled': self.cache_enabled,
            'cache_ttl_minutes': self.cache_ttl_minutes,
        }
