"""
Tests for the CLI main module
"""
from unittest.mock import patch

from emby_dedupe.cli.main import (
    _parse_excluded_ids,
    _parse_language_priorities,
    _resolve_auth_credentials,
    _resolve_configuration,
)


class TestMainHelpers:
    """Tests for helper functions extracted from main()."""

    def test_parse_language_priorities_empty(self):
        """Test parsing empty language priority string."""
        result = _parse_language_priorities("")

        assert result == []

    def test_parse_language_priorities_normalization(self):
        """Test language code normalization."""
        result = _parse_language_priorities("slo,cze,eng")

        assert result == ["sk", "cs", "eng"]

    def test_parse_language_priorities_deduplication(self):
        """Test that duplicate normalized languages are removed."""
        result = _parse_language_priorities("slo,sk,slovak,cze,cs,czech")

        # Should only have 2 languages (sk and cs) despite 6 inputs
        assert len(result) == 2
        assert "sk" in result
        assert "cs" in result

    def test_parse_excluded_ids_empty(self):
        """Test parsing empty excluded IDs string."""
        result = _parse_excluded_ids("")

        assert result == []

    def test_parse_excluded_ids_single(self):
        """Test parsing single excluded ID."""
        result = _parse_excluded_ids("tt1234567")

        assert result == ["tt1234567"]

    def test_parse_excluded_ids_multiple(self):
        """Test parsing multiple excluded IDs."""
        result = _parse_excluded_ids("tt1234567, tmdb:5678 , tvdb:9012")

        assert result == ["tt1234567", "tmdb:5678", "tvdb:9012"]

    @patch('emby_dedupe.cli.main.get_env_variable')
    @patch('emby_dedupe.cli.main.set_logging_level')
    def test_resolve_configuration_basic(self, mock_set_log, mock_get_env):
        """_resolve_configuration returns a ResolvedConfig built from the parsed args.

        Env resolution happens in typer's envvar= layer BEFORE this function; the old
        duplicate env re-read (source of false "CLI overrides env" warnings) is gone.
        """
        mock_get_env.return_value = None

        from argparse import Namespace
        args = Namespace(
            verbosity=0,
            host="http://emby.local",
            port=8096,
            api_key="test-key",
            library=["TV Shows"],
            doit=False,
            lang_prio="sk,cs",
            exclude_ids="tt123, tt456",
            username=None,
            password=None,
            html_report=False,
            html_only=True,
            no_open=False,
        )

        resolved = _resolve_configuration(args)

        assert resolved.host == "http://emby.local"
        assert resolved.api_key == "test-key"
        assert resolved.library == ["TV Shows"]
        assert resolved.lang_priorities == ["sk", "cs"]
        assert resolved.excluded_ids == ["tt123", "tt456"]
        assert resolved.html_only is True
        assert resolved.html_report is True  # derived: html_only implies html_report
        # DEDUPE_LOGGING is the one env var still read here.
        mock_set_log.assert_called_once_with(0, None)

    @patch('emby_dedupe.cli.main.get_env_variable')
    @patch('emby_dedupe.cli.main.set_logging_level')
    @patch('emby_dedupe.cli.arguments.override_warning')
    def test_resolve_configuration_emits_no_override_warnings(
        self, mock_override, mock_set_log, mock_get_env
    ):
        """Regression (code review 2026-07-10): with values coming from typer's envvar
        resolution (the prod/systemd case), no false "CLI overrides env" warning fires."""
        mock_get_env.return_value = None

        from argparse import Namespace
        args = Namespace(
            verbosity=0, host="http://env-host", port=None, api_key="env-key",
            library=["Env Lib"], doit=False, lang_prio=None, exclude_ids=None,
            username=None, password=None, html_report=False, html_only=False,
            no_open=False,
        )

        _resolve_configuration(args)

        mock_override.assert_not_called()

    @patch('emby_dedupe.cli.main.get_env_variable')
    def test_resolve_auth_credentials_when_doit_true(self, mock_get_env):
        """Test resolving auth credentials when doit is True."""
        from argparse import Namespace

        mock_get_env.side_effect = lambda key: {
            "DEDUPE_EMBY_USERNAME": "env-user",
            "DEDUPE_EMBY_PASSWORD": "env-pass",
        }.get(key.replace("ENV_DEDUPE_", "DEDUPE_"))

        args = Namespace(username="cli-user", password="cli-pass")

        username, password = _resolve_auth_credentials(args, doit=True)

        # CLI args should take precedence
        assert username == "cli-user"
        assert password == "cli-pass"

    @patch('emby_dedupe.cli.main.get_env_variable')
    def test_resolve_auth_credentials_when_doit_false(self, mock_get_env):
        """Test resolving auth credentials when doit is False."""
        from argparse import Namespace

        args = Namespace(username="cli-user", password="cli-pass")

        username, password = _resolve_auth_credentials(args, doit=False)

        # Should return None, None when doit is False
        assert username is None
        assert password is None
        # get_env_variable should not be called
        mock_get_env.assert_not_called()

    @patch('emby_dedupe.cli.main.get_env_variable')
    def test_resolve_auth_credentials_fallback_to_env(self, mock_get_env):
        """Test falling back to environment variables for auth."""
        from argparse import Namespace

        mock_get_env.side_effect = lambda key: {
            "DEDUPE_EMBY_USERNAME": "env-user",
            "DEDUPE_EMBY_PASSWORD": "env-pass",
        }.get(key.replace("ENV_DEDUPE_", "DEDUPE_"))

        args = Namespace(username=None, password=None)

        username, password = _resolve_auth_credentials(args, doit=True)

        # Should use environment variables
        assert username == "env-user"
        assert password == "env-pass"


class TestLibraryEnvCommaSplit:
    """Regression (code review 2026-07-10): DEDUPE_EMBY_LIBRARY="Movies,TV Shows" was
    whitespace-split by click's list-envvar handling into ['Movies,TV', 'Shows']. The
    env var is now read manually with the README-documented comma semantics."""

    def _audit_args(self, mocker, env, cli_args=()):
        from typer.testing import CliRunner

        from emby_dedupe.cli.app import app

        mock_run = mocker.patch("emby_dedupe.cli.genres.run_genres_command")
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--host", "http://emby", "--api-key", "k", *cli_args, "genres", "audit"],
            env=env,
        )
        assert result.exit_code == 0, result.output
        return mock_run.call_args[0][0]

    def test_env_library_comma_split_preserves_spaces(self, mocker):
        args = self._audit_args(mocker, {"DEDUPE_EMBY_LIBRARY": "Movies,TV Shows"})
        assert args.library == ["Movies", "TV Shows"]

    def test_cli_library_flags_beat_env(self, mocker):
        args = self._audit_args(
            mocker,
            {"DEDUPE_EMBY_LIBRARY": "Movies,TV Shows"},
            cli_args=["--library", "CLI Lib"],
        )
        assert args.library == ["CLI Lib"]

    def test_no_library_anywhere_is_empty(self, mocker):
        args = self._audit_args(mocker, {"DEDUPE_EMBY_LIBRARY": ""})
        assert args.library == []
