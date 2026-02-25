"""
Tests for CLI argument parsing (legacy argparse functions) and typer app structure.
"""
import os
import pytest
from unittest.mock import patch

from emby_dedupe.cli.arguments import (
    get_env_variable,
    override_warning,
    validate_required_arguments,
)


class TestGetEnvVariable:
    def test_nonexistent_returns_none(self):
        assert get_env_variable("NONEXISTENT_VAR_XYZ") is None

    def test_existing_returns_value(self):
        with patch.dict("os.environ", {"TEST_VAR_ABC": "test_value"}):
            assert get_env_variable("TEST_VAR_ABC") == "test_value"


class TestOverrideWarning:
    def test_no_warning_if_no_env_value(self):
        result = override_warning("--host", "cmd_value", None)
        assert result is None

    def test_no_warning_if_no_cmd_value(self):
        result = override_warning("--host", None, "env_value")
        assert result is None

    def test_warning_logged_when_both_set(self):
        with patch("emby_dedupe.cli.arguments.logger") as mock_logger:
            override_warning("--host", "cmd_value", "env_value")
            assert mock_logger.warning.called


class TestValidateRequiredArguments:
    def test_valid_without_doit(self):
        validate_required_arguments(
            host="emby.example.com",
            api_key="api_key",
            libraries=["Movies"],
            doit=False,
        )

    def test_valid_with_doit_and_credentials(self):
        validate_required_arguments(
            host="emby.example.com",
            api_key="api_key",
            libraries=["Movies"],
            doit=True,
            username="user",
            password="pass",
        )

    def test_missing_host_exits(self):
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host=None, api_key="api_key", libraries=["Movies"], doit=False
            )

    def test_missing_api_key_exits(self):
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host="emby.example.com", api_key=None, libraries=["Movies"], doit=False
            )

    def test_missing_libraries_exits(self):
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host="emby.example.com", api_key="api_key", libraries=[], doit=False
            )

    def test_doit_missing_username_exits(self):
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host="emby.example.com",
                api_key="api_key",
                libraries=["Movies"],
                doit=True,
                username=None,
                password="pass",
            )

    def test_doit_missing_password_exits(self):
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host="emby.example.com",
                api_key="api_key",
                libraries=["Movies"],
                doit=True,
                username="user",
                password=None,
            )


class TestTyperAppStructure:
    """Verify that the typer app is correctly wired up."""

    def test_app_help_exit_zero(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_app_has_genres_group(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "genres" in result.output

    def test_genres_help_shows_subcommands(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["genres", "--help"])
        assert result.exit_code == 0
        assert "audit" in result.output
        assert "normalize" in result.output
        assert "fix" in result.output

    def test_genres_audit_help(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["genres", "audit", "--help"])
        assert result.exit_code == 0
        assert "--suggest" in result.output

    def test_genres_normalize_help(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["genres", "normalize", "--help"])
        assert result.exit_code == 0
        assert "--doit" in result.output
        assert "--item-ids" in result.output

    def test_genres_fix_help(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["genres", "fix", "--help"])
        assert result.exit_code == 0
        assert "--doit" in result.output
        assert "--validate" in result.output
        assert "--item-ids" in result.output

    def test_dedupe_help(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["dedupe", "--help"])
        assert result.exit_code == 0

    def test_check_help(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["check", "--help"])
        assert result.exit_code == 0

    def test_missing_episodes_help(self):
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["missing-episodes", "--help"])
        assert result.exit_code == 0
