"""
Tests for CLI missing episodes command
"""
import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from emby_dedupe.cli.missing_episodes import (
    format_missing_episodes_report,
    format_structured_json_report,
    generate_default_filename,
    run_missing_episodes_command,
    write_to_file,
)


class TestGenerateDefaultFilename:
    """Tests for default filename generation."""

    @patch("emby_dedupe.cli.missing_episodes.datetime")
    def test_generate_default_filename_json(self, mock_datetime):
        """Test default filename for JSON format."""
        mock_datetime.now.return_value = datetime(2024, 1, 15, 14, 30, 45)

        filename = generate_default_filename("json")

        assert filename == "missing_episodes-20240115_143045.json"

    @patch("emby_dedupe.cli.missing_episodes.datetime")
    def test_generate_default_filename_structured_json(self, mock_datetime):
        """Test default filename for structured JSON format."""
        mock_datetime.now.return_value = datetime(2024, 3, 20, 9, 15, 30)

        filename = generate_default_filename("structured_json")

        assert filename == "missing_episodes_structured-20240320_091530.json"


class TestWriteToFile:
    """Tests for file writing functionality."""

    def test_write_to_file_success(self, tmp_path):
        """Test successful file writing."""
        output_file = tmp_path / "output.json"
        content = '{"test": "data"}'

        write_to_file(content, str(output_file))

        assert output_file.exists()
        assert output_file.read_text() == content

    def test_write_to_file_creates_directory(self, tmp_path):
        """Test that write_to_file creates parent directories."""
        nested_path = tmp_path / "sub" / "dir" / "output.json"
        content = "test content"

        write_to_file(content, str(nested_path))

        assert nested_path.exists()
        assert nested_path.read_text() == content

    def test_write_to_file_handles_unicode(self, tmp_path):
        """Test writing Unicode content."""
        output_file = tmp_path / "unicode.txt"
        content = "Testing: č š ž ř é á"

        write_to_file(content, str(output_file))

        assert output_file.read_text(encoding='utf-8') == content

    def test_write_to_file_error_handling(self, tmp_path):
        """Test error handling for invalid paths."""
        # Use an invalid path (null byte in filename)
        invalid_path = str(tmp_path / "test\x00invalid.txt")

        with pytest.raises(Exception):
            write_to_file("content", invalid_path)


class TestFormatStructuredJsonReport:
    """Tests for structured JSON report formatting."""

    def test_format_structured_json_basic(self):
        """Test basic structured JSON formatting."""
        analysis_results = {
            "statistics": {
                "total_missing_episodes": 10,
                "total_series_affected": 2,
                "total_seasons_affected": 3,
            },
            "processed_libraries": ["TV Shows"],
            "by_series": {
                "Breaking Bad": {
                    "original_series_name": "Breaking Bad",
                    "series_id": "12345",
                    "total_missing": 5,
                    "episodes": [
                        {"season": 1, "episode": 2, "name": "Cat's in the Bag...", "air_date": "2008-01-27"},
                        {"season": 1, "episode": 5, "name": "Gray Matter", "air_date": "2008-02-24"},
                    ],
                },
            },
        }

        result = format_structured_json_report(analysis_results)
        data = json.loads(result)

        # Check metadata
        assert data["metadata"]["total_missing_episodes"] == 10
        assert data["metadata"]["total_series_affected"] == 2
        assert data["metadata"]["libraries_processed"] == ["TV Shows"]

        # Check series structure
        assert len(data["series"]) == 1
        series = data["series"][0]
        assert series["series_name"] == "Breaking Bad"
        assert series["total_missing_episodes"] == 5
        assert series["searched"] is False  # Default value

        # Check episodes have searched field
        season = series["seasons"][0]
        assert season["season_number"] == 1
        assert season["episode_count"] == 2
        for episode in season["episodes"]:
            assert episode["searched"] is False  # Default value

    def test_format_structured_json_multiple_seasons(self):
        """Test structured JSON with multiple seasons."""
        analysis_results = {
            "statistics": {},
            "processed_libraries": [],
            "by_series": {
                "Test Show": {
                    "original_series_name": "Test Show",
                    "series_id": "99",
                    "total_missing": 4,
                    "episodes": [
                        {"season": 2, "episode": 1, "name": "Ep 1", "air_date": ""},
                        {"season": 1, "episode": 3, "name": "Ep 3", "air_date": ""},
                        {"season": 2, "episode": 5, "name": "Ep 5", "air_date": ""},
                        {"season": 1, "episode": 1, "name": "Ep 1", "air_date": ""},
                    ],
                },
            },
        }

        result = format_structured_json_report(analysis_results)
        data = json.loads(result)

        series = data["series"][0]
        # Should have 2 seasons
        assert len(series["seasons"]) == 2

        # Seasons should be sorted
        assert series["seasons"][0]["season_number"] == 1
        assert series["seasons"][1]["season_number"] == 2

        # Episodes within seasons should be sorted
        season1_episodes = series["seasons"][0]["episodes"]
        assert season1_episodes[0]["episode_number"] == 1
        assert season1_episodes[1]["episode_number"] == 3


class TestFormatMissingEpisodesReport:
    """Tests for missing episodes report formatting."""

    def test_format_report_json(self):
        """Test JSON format output."""
        analysis_results = {"test": "data", "count": 42}

        result = format_missing_episodes_report(analysis_results, "json")
        data = json.loads(result)

        assert data == {"test": "data", "count": 42}

    def test_format_report_structured_json(self):
        """Test structured JSON format delegates to helper."""
        analysis_results = {
            "statistics": {"total_missing_episodes": 5},
            "processed_libraries": [],
            "by_series": {},
        }

        result = format_missing_episodes_report(analysis_results, "structured_json")
        data = json.loads(result)

        # Should have structured format with metadata
        assert "metadata" in data
        assert "series" in data

    def test_format_report_console_with_stats(self):
        """Test console format with statistics."""
        analysis_results = {
            "statistics": {
                "total_missing_episodes": 15,
                "total_series_affected": 3,
                "total_seasons_affected": 5,
                "most_missing_series": "Breaking Bad",
                "average_missing_per_series": 5.0,
            },
            "processed_libraries": ["TV Shows", "Anime"],
            "by_series": {},
        }

        result = format_missing_episodes_report(analysis_results, "console")

        assert "# Missing Episodes Report" in result
        assert "**Total Missing Episodes**: 15" in result
        assert "**Series Affected**: 3" in result
        assert "**Seasons Affected**: 5" in result
        assert "**Series with Most Missing**: Breaking Bad" in result
        assert "**Average Missing per Series**: 5.0 episodes" in result
        assert "**Libraries Processed**: TV Shows, Anime" in result

    def test_format_report_console_with_episodes(self):
        """Test console format with episode details."""
        analysis_results = {
            "statistics": {"total_missing_episodes": 2},
            "by_series": {
                "Test Series": {
                    "total_missing": 2,
                    "episodes": [
                        {"season": 1, "episode": 5, "name": "Episode Five", "air_date": "2024-01-15T10:00:00"},
                        {"season": 1, "episode": 2, "name": "Episode Two", "air_date": "2024-01-10"},
                    ],
                },
            },
        }

        result = format_missing_episodes_report(analysis_results, "console")

        assert "### Test Series" in result
        assert "**Missing Episodes**: 2" in result
        assert "**Season 1**:" in result
        assert "Episode 2: Episode Two" in result
        assert "Episode 5: Episode Five" in result
        # Should extract date from ISO format
        assert "(Air Date: 2024-01-15)" in result
        assert "(Air Date: 2024-01-10)" in result

    def test_format_report_console_season_zero(self):
        """Test console format handles season 0 (specials)."""
        analysis_results = {
            "statistics": {},
            "by_series": {
                "Show": {
                    "total_missing": 1,
                    "episodes": [
                        {"season": 0, "episode": 1, "name": "Special", "air_date": ""},
                    ],
                },
            },
        }

        result = format_missing_episodes_report(analysis_results, "console")

        assert "**Specials/Unknown Season**:" in result
        assert "Episode 1: Special" in result


class TestRunMissingEpisodesCommand:
    """Tests for the main command runner."""

    @patch("emby_dedupe.cli.missing_episodes.process_missing_episodes_for_libraries")
    @patch("emby_dedupe.cli.missing_episodes.get_library_id")
    @patch("emby_dedupe.cli.missing_episodes.check_emby_connection")
    @patch("emby_dedupe.cli.missing_episodes.httpx.Client")
    def test_run_command_console_output(self, mock_client_class, mock_check, mock_get_lib, mock_process, capsys):
        """Test running command with console output."""
        # Setup mocks
        mock_check.return_value = True
        mock_process.return_value = {
            "statistics": {"total_missing_episodes": 5, "total_series_affected": 1},
            "by_series": {},
        }

        args = Namespace(
            host="http://emby.local",
            port=None,
            api_key="test-key",
            library=["TV Shows"],
            verbosity=0,
            format="console",
            output=None,
            html_report=False,
            html_only=False,
        )

        # Run command (console mode doesn't call sys.exit on success)
        run_missing_episodes_command(args)

        # Check console output
        captured = capsys.readouterr()
        assert "# Missing Episodes Report" in captured.out
        assert "**Total Missing Episodes**: 5" in captured.out

    @patch("emby_dedupe.cli.missing_episodes.process_missing_episodes_for_libraries")
    @patch("emby_dedupe.cli.missing_episodes.get_library_id")
    @patch("emby_dedupe.cli.missing_episodes.check_emby_connection")
    @patch("emby_dedupe.cli.missing_episodes.httpx.Client")
    def test_run_command_json_output(self, mock_client_class, mock_check, mock_get_lib, mock_process, tmp_path):
        """Test running command with JSON output to file."""
        # Setup mocks
        mock_check.return_value = True
        mock_process.return_value = {
            "statistics": {"total_missing_episodes": 3},
            "by_series": {},
        }

        output_file = tmp_path / "output.json"
        args = Namespace(
            host="http://emby.local",
            port=8096,
            api_key="test-key",
            library=["Library"],
            verbosity=0,
            format="json",
            output=str(output_file),
            html_report=False,
            html_only=False,
        )

        # Run command (JSON mode doesn't call sys.exit on success)
        run_missing_episodes_command(args)

        # Check file was created
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["statistics"]["total_missing_episodes"] == 3

    @patch("emby_dedupe.cli.missing_episodes.process_missing_episodes_for_libraries")
    @patch("emby_dedupe.cli.missing_episodes.get_library_id")
    @patch("emby_dedupe.cli.missing_episodes.check_emby_connection")
    @patch("emby_dedupe.cli.missing_episodes.httpx.Client")
    def test_run_command_structured_json_output(self, mock_client_class, mock_check, mock_get_lib, mock_process, tmp_path):
        """Test running command with structured JSON output."""
        # Setup mocks
        mock_check.return_value = True
        mock_process.return_value = {
            "statistics": {"total_missing_episodes": 2},
            "processed_libraries": ["TV"],
            "by_series": {},
        }

        output_file = tmp_path / "structured.json"
        args = Namespace(
            host="http://emby.local",
            port=None,
            api_key="test-key",
            library=["TV"],
            verbosity=0,
            format="structured_json",
            output=str(output_file),
            html_report=False,
            html_only=False,
        )

        # Run command (should exit with code 0 after writing structured JSON)
        with pytest.raises(SystemExit) as exc_info:
            run_missing_episodes_command(args)

        assert exc_info.value.code == 0

        # Check structured format
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert "metadata" in data
        assert "series" in data

    @patch("emby_dedupe.cli.missing_episodes.check_emby_connection")
    @patch("emby_dedupe.cli.missing_episodes.httpx.Client")
    def test_run_command_connection_error(self, mock_client_class, mock_check):
        """Test handling of connection errors."""
        # Setup mock to fail connection
        mock_check.return_value = False

        args = Namespace(
            host="http://emby.local",
            port=None,
            api_key="test-key",
            library=["TV"],
            verbosity=0,
            format="console",
            output=None,
            html_report=False,
            html_only=False,
        )

        # Should exit with error code
        with pytest.raises(SystemExit) as exc_info:
            run_missing_episodes_command(args)

        assert exc_info.value.code == 1

    @patch("emby_dedupe.cli.missing_episodes.process_missing_episodes_for_libraries")
    @patch("emby_dedupe.cli.missing_episodes.get_library_id")
    @patch("emby_dedupe.cli.missing_episodes.check_emby_connection")
    @patch("emby_dedupe.cli.missing_episodes.httpx.Client")
    def test_run_command_no_missing_episodes(self, mock_client_class, mock_check, mock_get_lib, mock_process, capsys):
        """Test output when no missing episodes found."""
        # Setup mocks
        mock_check.return_value = True
        mock_process.return_value = {
            "statistics": {"total_missing_episodes": 0, "total_series_affected": 0},
            "by_series": {},
        }

        args = Namespace(
            host="http://emby.local",
            port=None,
            api_key="test-key",
            library=["TV"],
            verbosity=0,
            format="console",
            output=None,
            html_report=False,
            html_only=False,
        )

        # Run command (console mode doesn't call sys.exit on success)
        run_missing_episodes_command(args)

        # Check console output shows no missing episodes
        captured = capsys.readouterr()
        assert "# Missing Episodes Report" in captured.out
        assert "**Total Missing Episodes**: 0" in captured.out
