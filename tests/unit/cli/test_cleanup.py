"""
Tests for emby_dedupe.cli.cleanup — argument validation and the
run_cleanup_command entry point.

Covers the CLI-related DA fixes:
  #4  _validate_cleanup_args supports --all-libraries
  #11 --username required with --doit; dry-run warns instead of exiting
"""
from unittest.mock import MagicMock, patch

import pytest

from emby_dedupe.cli.cleanup import _validate_cleanup_args

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_response(data: dict) -> MagicMock:
    """Create a mock HTTP response returning the given data as JSON."""
    resp = MagicMock()
    resp.json.return_value = data
    return resp


# ---------------------------------------------------------------------------
# TestValidateCleanupArgs  (DA fix #4, #11)
# ---------------------------------------------------------------------------


class TestValidateCleanupArgs:
    """Tests for _validate_cleanup_args."""

    def _call(self, **kwargs):
        defaults = dict(
            host="http://emby",
            api_key="key",
            libraries=["Movies"],
            all_libraries=False,
            doit=False,
            username=None,
            password=None,
        )
        defaults.update(kwargs)
        _validate_cleanup_args(**defaults)

    def test_valid_with_library(self):
        """Valid args with explicit library name — no error."""
        self._call()  # should not raise

    def test_valid_with_all_libraries(self):
        """--all-libraries allows empty library list (DA fix #4)."""
        self._call(libraries=[], all_libraries=True)  # no error

    def test_missing_host_raises_sys_exit(self):
        """Missing host → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(host=None)

    def test_missing_api_key_raises_sys_exit(self):
        """Missing api_key → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(api_key=None)

    def test_no_library_and_no_all_libraries_exits(self):
        """No library and no --all-libraries → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(libraries=[], all_libraries=False)

    def test_doit_without_username_exits(self):
        """--doit without --username → sys.exit(1) (DA fix #11)."""
        with pytest.raises(SystemExit):
            self._call(doit=True, username=None, password="pw")

    def test_doit_without_password_exits(self):
        """--doit without --password → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(doit=True, username="user", password=None)

    def test_dry_run_no_username_warns_not_exits(self):
        """Dry-run without username warns but does NOT exit (DA fix #11)."""
        with patch("emby_dedupe.cli.cleanup.logger") as mock_logger:
            self._call(doit=False, username=None)  # must NOT raise
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# TestRunCleanupCommand  (Bug fix #2: empty primary_user_id abort)
# ---------------------------------------------------------------------------


class TestRunCleanupCommand:
    """Integration-level tests for run_cleanup_command entry point."""

    def _minimal_args(self, **overrides):
        """Build minimal valid args Namespace for run_cleanup_command."""
        from argparse import Namespace

        defaults = dict(
            host="http://emby",
            port=None,
            api_key="key",
            library=["Movies"],
            all_libraries=False,
            doit=False,
            username="Barlog",
            password=None,
            min_age_years=3,
            protect_paths=["/Dokumenty/"],
            base_rating=6.0,
            decay_step=0.5,
            max_rating=8.0,

            exclude_ids="",
            format="console",
            html_report=False,
            html_only=False,
            no_open=True,
        )
        defaults.update(overrides)
        return Namespace(**defaults)

    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.api.cleanup_pipeline._fetch_all_library_movies")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_empty_primary_user_id_aborts_cleanly(
        self, mock_client_cls, mock_fetch, mock_check, mock_resolve
    ):
        """When _resolve_primary_user_id returns '', run_cleanup_command returns early.

        Prevents malformed /Users//Items URL from being constructed (Bug fix #2).
        """
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = self._minimal_args()
        run_cleanup_command(args)

        # _fetch_all_library_movies must NOT have been called — we aborted before pipeline
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunCleanupCommandWithSeries
# ---------------------------------------------------------------------------


class TestRunCleanupCommandWithSeries:
    """Integration tests for run_cleanup_command with series pipeline."""

    def _minimal_args(self, **overrides):
        """Build minimal valid args Namespace for run_cleanup_command."""
        from argparse import Namespace

        defaults = dict(
            host="http://emby",
            port=None,
            api_key="key",
            library=["SERIALS"],
            all_libraries=False,
            doit=False,
            username="Barlog",
            password=None,
            min_age_years=3,
            protect_paths=["/Dokumenty/"],
            base_rating=6.0,
            decay_step=0.5,
            max_rating=8.0,
            exclude_ids="",
            format="console",
            html_report=False,
            html_only=False,
            no_open=True,
        )
        defaults.update(overrides)
        return Namespace(**defaults)

    @patch("emby_dedupe.cli.cleanup._run_series_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._run_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._probe_library_content", return_value=(0, 100))
    @patch("emby_dedupe.cli.cleanup.get_library_ids_by_name", return_value=["lib_series"])
    @patch("emby_dedupe.cli.cleanup.get_all_library_ids", return_value=[{"id": "lib_series", "name": "SERIALS"}])
    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="uid1")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup.make_http_request")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_series_only_library_runs_series_pipeline(
        self, mock_client_cls, mock_http, mock_check, mock_resolve,
        mock_all_libs, mock_lib_ids, mock_probe, mock_movie_pipeline, mock_series_pipeline
    ):
        """When library contains only series, series pipeline runs and movie pipeline does not."""
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_http.return_value = _make_response({"Id": "server1"})
        mock_series_pipeline.return_value = ([], {
            "total_analyzed": 0, "stale_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "favorite_protected": 0, "path_protected": 0,
            "rating_protected": 0, "final_candidates": 0,
        }, [])

        args = self._minimal_args()
        run_cleanup_command(args)

        mock_movie_pipeline.assert_not_called()
        mock_series_pipeline.assert_called_once()

    @patch("emby_dedupe.cli.cleanup._run_series_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._run_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._probe_library_content", return_value=(500, 200))
    @patch("emby_dedupe.cli.cleanup.get_library_ids_by_name", return_value=["lib_mixed"])
    @patch("emby_dedupe.cli.cleanup.get_all_library_ids", return_value=[{"id": "lib_mixed", "name": "Mixed"}])
    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="uid1")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup.make_http_request")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_mixed_library_runs_both_pipelines(
        self, mock_client_cls, mock_http, mock_check, mock_resolve,
        mock_all_libs, mock_lib_ids, mock_probe, mock_movie_pipeline, mock_series_pipeline
    ):
        """Mixed library (movies + series) runs both pipelines."""
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_http.return_value = _make_response({"Id": "server1"})
        mock_movie_pipeline.return_value = ([], {
            "total_analyzed": 0, "age_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "interest_protected": 0, "actor_protected": 0,
            "franchise_protected": 0, "path_protected": 0, "rating_protected": 0,
            "final_candidates": 0,
        }, [])
        mock_series_pipeline.return_value = ([], {
            "total_analyzed": 0, "stale_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "favorite_protected": 0, "path_protected": 0,
            "rating_protected": 0, "final_candidates": 0,
        }, [])

        args = self._minimal_args(library=["Mixed"])
        run_cleanup_command(args)

        mock_movie_pipeline.assert_called_once()
        mock_series_pipeline.assert_called_once()

    @patch("emby_dedupe.cli.cleanup._run_series_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._run_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._probe_library_content", return_value=(500, 0))
    @patch("emby_dedupe.cli.cleanup.get_library_ids_by_name", return_value=["lib_movies"])
    @patch("emby_dedupe.cli.cleanup.get_all_library_ids", return_value=[{"id": "lib_movies", "name": "Movies"}])
    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="uid1")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup.make_http_request")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_movie_only_library_skips_series_pipeline(
        self, mock_client_cls, mock_http, mock_check, mock_resolve,
        mock_all_libs, mock_lib_ids, mock_probe, mock_movie_pipeline, mock_series_pipeline
    ):
        """Movie-only library runs movie pipeline, skips series."""
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_http.return_value = _make_response({"Id": "server1"})
        mock_movie_pipeline.return_value = ([], {
            "total_analyzed": 0, "age_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "interest_protected": 0, "actor_protected": 0,
            "franchise_protected": 0, "path_protected": 0, "rating_protected": 0,
            "final_candidates": 0,
        }, [])

        args = self._minimal_args(library=["Movies"])
        run_cleanup_command(args)

        mock_movie_pipeline.assert_called_once()
        mock_series_pipeline.assert_not_called()
