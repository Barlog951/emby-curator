"""Tests for the config module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from emby_dedupe.utils.config import (
    Config,
    ensure_cache_dir,
    ensure_config_dir,
    get_config_path,
    get_config_value,
    load_config,
    save_config,
)


class TestConfigPaths:
    """Tests for config path functions."""

    def test_get_config_path_returns_path(self):
        """Test that get_config_path returns a Path object."""
        path = get_config_path()
        assert isinstance(path, Path)
        assert path.name == "config.yaml"

    def test_ensure_config_dir_creates_directory(self):
        """Test that ensure_config_dir creates the directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('emby_dedupe.utils.config.CONFIG_DIR', Path(tmpdir) / "test_config"):
                path = ensure_config_dir()
                assert path.exists()
                assert path.is_dir()

    def test_ensure_cache_dir_creates_directory(self):
        """Test that ensure_cache_dir creates the directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('emby_dedupe.utils.config.CACHE_DIR', Path(tmpdir) / "test_cache"):
                path = ensure_cache_dir()
                assert path.exists()
                assert path.is_dir()


class TestLoadSaveConfig:
    """Tests for load_config and save_config functions."""

    def test_load_config_returns_empty_when_no_file(self):
        """Test that load_config returns empty dict when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('emby_dedupe.utils.config.CONFIG_FILE', Path(tmpdir) / "nonexistent.yaml"):
                config = load_config()
                assert config == {}

    def test_load_config_returns_empty_when_yaml_unavailable(self):
        """Test that load_config returns empty dict when YAML is not available."""
        with patch('emby_dedupe.utils.config.YAML_AVAILABLE', False):
            config = load_config()
            assert config == {}

    def test_save_config_returns_false_when_yaml_unavailable(self):
        """Test that save_config returns False when YAML is not available."""
        with patch('emby_dedupe.utils.config.YAML_AVAILABLE', False):
            result = save_config({"test": "value"})
            assert result is False


class TestGetConfigValue:
    """Tests for get_config_value function."""

    def test_get_config_value_returns_default(self):
        """Test that get_config_value returns default when not found."""
        with patch('emby_dedupe.utils.config.load_config', return_value={}):
            value = get_config_value("nonexistent", default="default_value")
            assert value == "default_value"

    def test_get_config_value_from_provided_config(self):
        """Test that get_config_value uses provided config dict."""
        value = get_config_value("test_key", config={"test_key": "test_value"})
        assert value == "test_value"

    def test_get_config_value_from_env(self):
        """Test that get_config_value reads from environment variable."""
        with patch.dict(os.environ, {"EMBY_DEDUPE_TEST_KEY": "env_value"}):
            with patch('emby_dedupe.utils.config.load_config', return_value={}):
                value = get_config_value("test_key")
                assert value == "env_value"

    def test_get_config_value_env_list_parsing(self):
        """Test that get_config_value parses comma-separated lists from env."""
        with patch.dict(os.environ, {"EMBY_DEDUPE_LIBRARIES": "Movies,TV Shows,Music"}):
            with patch('emby_dedupe.utils.config.load_config', return_value={}):
                value = get_config_value("libraries")
                assert value == ["Movies", "TV Shows", "Music"]


class TestConfigClass:
    """Tests for the Config class."""

    def test_config_init_with_all_params(self):
        """Test Config initialization with all parameters."""
        config = Config(
            host="http://test.local",
            api_key="test_key",
            libraries=["Movies"],
            lang_priorities=["sk", "cs", "en"],
            exclude_ids=["tt123"],
            cache_enabled=True,
            cache_ttl_minutes=5,
        )
        assert config.host == "http://test.local"
        assert config.api_key == "test_key"
        assert config.libraries == ["Movies"]
        assert config.lang_priorities == ["sk", "cs", "en"]
        assert config.exclude_ids == ["tt123"]
        assert config.cache_enabled is True
        assert config.cache_ttl_minutes == 5

    def test_config_init_defaults(self):
        """Test Config initialization with defaults."""
        config = Config()
        assert config.host is None
        assert config.api_key is None
        assert config.libraries is None
        assert config.lang_priorities is None
        assert config.exclude_ids == []
        assert config.cache_enabled is True
        assert config.cache_ttl_minutes == 10

    def test_config_validate_missing_host(self):
        """Test that validate returns error for missing host."""
        config = Config(api_key="test_key")
        errors = config.validate()
        assert "host is required" in errors

    def test_config_validate_missing_api_key(self):
        """Test that validate returns error for missing api_key."""
        config = Config(host="http://test.local")
        errors = config.validate()
        assert "api_key is required" in errors

    def test_config_validate_valid(self):
        """Test that validate returns empty list for valid config."""
        config = Config(host="http://test.local", api_key="test_key")
        errors = config.validate()
        assert errors == []

    def test_config_to_dict(self):
        """Test Config to_dict method."""
        config = Config(
            host="http://test.local",
            api_key="test_key",
            libraries=["Movies"],
        )
        d = config.to_dict()
        assert d["host"] == "http://test.local"
        assert d["api_key"] == "test_key"
        assert d["libraries"] == ["Movies"]

    def test_config_from_config_file_with_overrides(self):
        """Test Config.from_config_file with overrides."""
        with patch('emby_dedupe.utils.config.load_config', return_value={
            "host": "http://config.local",
            "api_key": "config_key",
        }):
            config = Config.from_config_file(host="http://override.local")
            assert config.host == "http://override.local"
            assert config.api_key == "config_key"

    def test_config_from_cli_args(self):
        """Test Config.from_cli_args."""
        class MockArgs:
            host = "http://cli.local"
            api_key = "cli_key"
            library = ["Movies", "TV"]
            lang_prio = "sk,cs,en"
            exclude_ids = "tt123,tt456"
            cache = True
            all_libraries = False

        with patch('emby_dedupe.utils.config.load_config', return_value={}):
            config = Config.from_cli_args(MockArgs())
            assert config.host == "http://cli.local"
            assert config.api_key == "cli_key"
            assert config.libraries == ["Movies", "TV"]
            assert config.lang_priorities == ["sk", "cs", "en"]
            assert config.exclude_ids == ["tt123", "tt456"]

    def test_config_from_cli_args_all_libraries(self):
        """Test Config.from_cli_args with all_libraries flag."""
        class MockArgs:
            host = "http://cli.local"
            api_key = "cli_key"
            library = None
            lang_prio = None
            exclude_ids = None
            cache = None
            all_libraries = True

        with patch('emby_dedupe.utils.config.load_config', return_value={"libraries": ["Default"]}):
            config = Config.from_cli_args(MockArgs())
            assert config.libraries is None  # None means all libraries
