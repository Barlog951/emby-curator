"""
Tests for CLI check command
"""
import json
from argparse import Namespace
from unittest.mock import MagicMock, Mock, patch

from emby_dedupe.cli.check import (
    _extract_quality_params,
    _extract_search_params,
    _get_output_format,
    add_check_arguments,
    run_check,
)


class TestParameterExtraction:
    """Tests for parameter extraction from args."""

    def test_extract_search_params_all_fields(self):
        """Test extraction with all search parameters provided."""
        args = Namespace(
            name="Inception",
            year=2010,
            imdb="tt1375666",
            tmdb="27205",
            tvdb="12345",
            season=1,
            episode=5,
        )

        params = _extract_search_params(args)

        assert params == {
            "name": "Inception",
            "year": 2010,
            "imdb": "tt1375666",
            "tmdb": "27205",
            "tvdb": "12345",
            "season": 1,
            "episode": 5,
        }

    def test_extract_search_params_minimal(self):
        """Test extraction with only required parameters."""
        args = Namespace(name="Breaking Bad")

        params = _extract_search_params(args)

        assert params == {"name": "Breaking Bad"}

    def test_extract_search_params_season_zero(self):
        """Test that season 0 is included (special case for None check)."""
        args = Namespace(name="Show", season=0, episode=0)

        params = _extract_search_params(args)

        assert params["season"] == 0
        assert params["episode"] == 0

    def test_extract_quality_params_all_fields(self):
        """Test extraction with all quality parameters provided."""
        args = Namespace(
            resolution="2160p",
            codec="x265",
            hdr="HDR10+",
            audio="Atmos",
            audio_lang="cze,eng,sk",
            size_mb=15000,
            bitrate_kbps=20000,
        )

        params = _extract_quality_params(args)

        assert params == {
            "resolution": "2160p",
            "codec": "x265",
            "hdr": "HDR10+",
            "audio": "Atmos",
            "audio_languages": ["cze", "eng", "sk"],
            "size_mb": 15000,
            "bitrate_kbps": 20000,
        }

    def test_extract_quality_params_minimal(self):
        """Test extraction with no quality parameters."""
        args = Namespace()

        params = _extract_quality_params(args)

        assert params == {}

    def test_extract_quality_params_audio_lang_parsing(self):
        """Test that audio_lang is properly split and stripped."""
        args = Namespace(audio_lang=" eng , cze , sk ")

        params = _extract_quality_params(args)

        assert params["audio_languages"] == ["eng", "cze", "sk"]


class TestOutputFormat:
    """Tests for output format detection."""

    def test_get_output_format_json_default(self):
        """Test that JSON is the default format."""
        args = Namespace(simple=False, exit_code=False)

        format_type = _get_output_format(args)

        assert format_type == "json"

    def test_get_output_format_simple(self):
        """Test simple output format."""
        args = Namespace(simple=True, exit_code=False)

        format_type = _get_output_format(args)

        assert format_type == "simple"

    def test_get_output_format_exit_code(self):
        """Test exit code only format."""
        args = Namespace(simple=False, exit_code=True)

        format_type = _get_output_format(args)

        assert format_type == "exit_code"


class TestRunCheck:
    """Tests for the run_check command."""

    @patch("emby_dedupe.cli.check.EmbyChecker")
    @patch("emby_dedupe.cli.check.Config")
    def test_run_check_download_recommendation_json(self, mock_config_class, mock_checker_class, capsys):
        """Test successful check with download recommendation in JSON format."""
        # Setup mocks
        mock_config = Mock()
        mock_config.validate.return_value = []
        mock_config_class.from_cli_args.return_value = mock_config

        mock_checker = MagicMock()
        mock_result = Mock()
        mock_result.should_download = True
        mock_result.recommendation = "download"
        mock_result.to_dict.return_value = {
            "recommendation": "download",
            "reason": "Better quality available",
        }
        mock_checker.check.return_value = mock_result
        mock_checker_class.return_value = mock_checker

        args = Namespace(
            name="Inception",
            year=2010,
            resolution="2160p",
            simple=False,
            exit_code=False,
        )

        # Run check
        exit_code = run_check(args)

        # Verify
        assert exit_code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["recommendation"] == "download"
        mock_checker.close.assert_called_once()

    @patch("emby_dedupe.cli.check.EmbyChecker")
    @patch("emby_dedupe.cli.check.Config")
    def test_run_check_skip_recommendation_simple(self, mock_config_class, mock_checker_class, capsys):
        """Test skip recommendation with simple output format."""
        # Setup mocks
        mock_config = Mock()
        mock_config.validate.return_value = []
        mock_config_class.from_cli_args.return_value = mock_config

        mock_checker = MagicMock()
        mock_result = Mock()
        mock_result.should_download = False
        mock_result.recommendation = "skip"
        mock_checker.check.return_value = mock_result
        mock_checker_class.return_value = mock_checker

        args = Namespace(
            name="Breaking Bad",
            season=1,
            episode=1,
            simple=True,
            exit_code=False,
        )

        # Run check
        exit_code = run_check(args)

        # Verify
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out.strip() == "skip"
        mock_checker.close.assert_called_once()

    @patch("emby_dedupe.cli.check.EmbyChecker")
    @patch("emby_dedupe.cli.check.Config")
    def test_run_check_exit_code_only(self, mock_config_class, mock_checker_class, capsys):
        """Test exit code only format (no output)."""
        # Setup mocks
        mock_config = Mock()
        mock_config.validate.return_value = []
        mock_config_class.from_cli_args.return_value = mock_config

        mock_checker = MagicMock()
        mock_result = Mock()
        mock_result.should_download = True
        mock_checker.check.return_value = mock_result
        mock_checker_class.return_value = mock_checker

        args = Namespace(
            imdb="tt1375666",
            resolution="4k",
            simple=False,
            exit_code=True,
        )

        # Run check
        exit_code = run_check(args)

        # Verify
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == ""  # No output in exit_code mode
        mock_checker.close.assert_called_once()

    @patch("emby_dedupe.cli.check.EmbyChecker")
    @patch("emby_dedupe.cli.check.Config")
    def test_run_check_validation_error_json(self, mock_config_class, mock_checker_class, capsys):
        """Test configuration validation error with JSON output."""
        # Setup mocks
        mock_config = Mock()
        mock_config.validate.return_value = ["Missing API key", "Invalid host"]
        mock_config_class.from_cli_args.return_value = mock_config

        mock_checker = MagicMock()
        mock_checker_class.return_value = mock_checker

        args = Namespace(
            name="Test",
            simple=False,
            exit_code=False,
        )

        # Run check
        exit_code = run_check(args)

        # Verify
        assert exit_code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "error" in output
        assert "Configuration error" in output["error"]
        assert output["recommendation"] == "error"
        # Checker should not be created or closed on validation error
        assert not mock_checker.check.called

    @patch("emby_dedupe.cli.check.EmbyChecker")
    @patch("emby_dedupe.cli.check.Config")
    def test_run_check_exception_handling(self, mock_config_class, mock_checker_class, capsys):
        """Test exception handling during check execution."""
        # Setup mocks
        mock_config = Mock()
        mock_config.validate.return_value = []
        mock_config_class.from_cli_args.return_value = mock_config

        mock_checker = MagicMock()
        mock_checker.check.side_effect = ValueError("Network error")
        mock_checker_class.return_value = mock_checker

        args = Namespace(
            name="Test",
            simple=False,
            exit_code=False,
        )

        # Run check
        exit_code = run_check(args)

        # Verify
        assert exit_code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "error" in output
        assert "Network error" in output["error"]
        # Checker should still be closed on exception
        mock_checker.close.assert_called_once()

    @patch("emby_dedupe.cli.check.EmbyChecker")
    @patch("emby_dedupe.cli.check.Config")
    def test_run_check_checker_always_closed(self, mock_config_class, mock_checker_class):
        """Test that checker.close() is always called even on exception."""
        # Setup mocks
        mock_config = Mock()
        mock_config.validate.return_value = []
        mock_config_class.from_cli_args.return_value = mock_config

        mock_checker = MagicMock()
        mock_checker.check.side_effect = RuntimeError("Unexpected error")
        mock_checker_class.return_value = mock_checker

        args = Namespace(name="Test", simple=True, exit_code=False)

        # Run check (should not raise exception)
        exit_code = run_check(args)

        # Verify cleanup happened
        assert exit_code == 2
        mock_checker.close.assert_called_once()


class TestAddCheckArguments:
    """Tests for argument parser configuration."""

    def test_add_check_arguments_creates_arguments(self):
        """Test that all required arguments are added to the parser."""
        parser = Mock()

        add_check_arguments(parser)

        # Verify add_argument was called (at least for key arguments)
        assert parser.add_argument.call_count > 20  # Should be ~30+ arguments

        # Check some key arguments were added
        call_args_list = [str(call) for call in parser.add_argument.call_args_list]
        assert any("--name" in str(call) for call in call_args_list)
        assert any("--host" in str(call) for call in call_args_list)
        assert any("--api-key" in str(call) for call in call_args_list)
        assert any("--resolution" in str(call) for call in call_args_list)
        assert any("--simple" in str(call) for call in call_args_list)
        assert any("--exit-code" in str(call) for call in call_args_list)
