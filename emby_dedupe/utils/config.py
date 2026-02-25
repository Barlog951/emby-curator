"""
Configuration file loading for emby-dedupe.

Supports loading configuration from:
1. ~/.emby-dedupe/config.yaml (user config)
2. Environment variables
3. CLI arguments (highest priority)
"""

import os
from pathlib import Path
from typing import Any, Optional

from emby_dedupe.utils.logging import logger

# Optional YAML support - gracefully handle if not installed
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


CONFIG_DIR = Path.home() / ".emby-dedupe"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
CACHE_DIR = CONFIG_DIR / "cache"


def get_config_path() -> Path:
    """Get the path to the config file.

    Returns:
        Path: Path to the config file.
    """
    return CONFIG_FILE


def ensure_config_dir() -> Path:
    """Ensure the config directory exists.

    Returns:
        Path: Path to the config directory.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


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
    if not YAML_AVAILABLE:
        logger.debug("YAML not available, skipping config file loading")
        return {}

    if not CONFIG_FILE.exists():
        logger.debug(f"Config file not found at {CONFIG_FILE}")
        return {}

    try:
        with open(CONFIG_FILE, 'r') as f:
            config = yaml.safe_load(f) or {}
            logger.debug(f"Loaded config from {CONFIG_FILE}")
            return config
    except Exception as e:
        logger.warning(f"Error loading config file: {e}")
        return {}


def save_config(config: dict[str, Any]) -> bool:
    """Save configuration to the config file.

    Args:
        config: Configuration dictionary to save.

    Returns:
        bool: True if saved successfully, False otherwise.
    """
    if not YAML_AVAILABLE:
        logger.warning("YAML not available, cannot save config file")
        return False

    try:
        ensure_config_dir()
        with open(CONFIG_FILE, 'w') as f:
            yaml.safe_dump(config, f, default_flow_style=False)
        logger.debug(f"Saved config to {CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving config file: {e}")
        return False


def get_config_value(key: str, default: Any = None, config: Optional[dict] = None) -> Any:
    """Get a configuration value with fallback to environment variable.

    Priority:
    1. Provided config dict
    2. Config file
    3. Environment variable (DEDUPE_{KEY})
    4. Default value

    Args:
        key: Configuration key (e.g., 'host', 'api_key', 'libraries')
        default: Default value if not found
        config: Optional pre-loaded config dict

    Returns:
        The configuration value.
    """
    # Check provided config
    if config and key in config:
        return config[key]

    # Check config file
    file_config = load_config()
    if key in file_config:
        return file_config[key]

    # Check environment variable using DEDUPE_ prefix (e.g., DEDUPE_EMBY_HOST, DEDUPE_EMBY_API_KEY)
    # Note: The old EMBY_DEDUPE_ prefix is no longer recognized
    env_key = f"DEDUPE_{key.upper()}"
    env_value = os.environ.get(env_key)
    if env_value is not None:
        # Handle list values from environment (comma-separated)
        if key in ('libraries', 'lang_priorities'):
            return [v.strip() for v in env_value.split(',')]
        return env_value

    return default


class Config:
    """Configuration object for emby-dedupe check functionality."""

    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        libraries: Optional[list[str]] = None,
        lang_priorities: Optional[list[str]] = None,
        exclude_ids: Optional[list[str]] = None,
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
    def from_config_file(cls, **overrides) -> 'Config':
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
    def _apply_cli_overrides(cls, config: 'Config', args) -> None:
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
    def from_cli_args(cls, args, **overrides) -> 'Config':
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
