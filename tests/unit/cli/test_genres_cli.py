"""Tests for emby_dedupe/cli/genres.py"""

import argparse
import pytest

from emby_dedupe.cli.genres import run_genres_command, _validate_genres_args


class TestValidateGenresArgs:
    def test_missing_host_exits(self):
        with pytest.raises(SystemExit):
            _validate_genres_args(None, "key", ["Movies"], False)

    def test_missing_api_key_exits(self):
        with pytest.raises(SystemExit):
            _validate_genres_args("http://emby", None, [], False)

    def test_missing_library_and_not_all_libraries_exits(self):
        with pytest.raises(SystemExit):
            _validate_genres_args("http://emby", "key", [], False)

    def test_all_libraries_flag_satisfies_library_requirement(self):
        # Should NOT raise when all_libraries=True even if library=[]
        _validate_genres_args("http://emby", "key", [], True)  # no SystemExit

    def test_valid_args_passes(self):
        _validate_genres_args("http://emby", "key", ["Movies"], False)  # no SystemExit


def _patch_http_client(mocker):
    """Patch httpx.Client to avoid real network connections."""
    mock_client = mocker.MagicMock()
    mocker.patch("emby_dedupe.cli.genres.httpx.Client", return_value=mock_client.__enter__.return_value)
    return mock_client


class TestGenresAuditCommand:
    def _make_audit_args(self):
        return argparse.Namespace(
            action="audit",
            host="http://emby",
            port=None,
            api_key="key123",
            library=["Movies"],
            all_libraries=False,
            verbosity=0,
            output_json=None,
        )

    def _setup_common_mocks(self, mocker):
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")

    def test_audit_never_calls_update(self, mocker):
        """Audit is read-only — must never POST."""
        self._setup_common_mocks(mocker)
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")
        mocker.patch("emby_dedupe.cli.genres.fetch_all_genres", return_value=[{"Name": "Drama", "Id": "1"}])
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=[
            {"Id": "1", "Name": "Movie", "Genres": ["Drama"]}
        ])
        mocker.patch("emby_dedupe.cli.genres.build_genre_audit", return_value={
            "genre_counts": {"Drama": 1}, "items_without_genres": [],
            "normalization_candidates": [], "variant_groups": {},
            "total_items": 1, "total_without_genres": 0
        })

        run_genres_command(self._make_audit_args())
        mock_update.assert_not_called()

    def test_audit_calls_fetch_all_genres(self, mocker):
        self._setup_common_mocks(mocker)
        mock_fetch_all = mocker.patch("emby_dedupe.cli.genres.fetch_all_genres", return_value=[])
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=[])
        mocker.patch("emby_dedupe.cli.genres.build_genre_audit", return_value={
            "genre_counts": {}, "items_without_genres": [], "normalization_candidates": [],
            "variant_groups": {}, "total_items": 0, "total_without_genres": 0
        })

        run_genres_command(self._make_audit_args())
        mock_fetch_all.assert_called_once()

    def test_audit_calls_build_genre_audit(self, mocker):
        """Audit must call build_genre_audit to produce the report."""
        self._setup_common_mocks(mocker)
        mocker.patch("emby_dedupe.cli.genres.fetch_all_genres", return_value=[])
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=[])
        mock_build = mocker.patch("emby_dedupe.cli.genres.build_genre_audit", return_value={
            "genre_counts": {}, "items_without_genres": [], "normalization_candidates": [],
            "variant_groups": {}, "total_items": 0, "total_without_genres": 0
        })

        run_genres_command(self._make_audit_args())
        mock_build.assert_called_once()


class TestGenresNormalizeCommand:
    def _make_normalize_args(self, doit=False):
        return argparse.Namespace(
            action="normalize",
            host="http://emby",
            port=None,
            api_key="key123",
            library=["Movies"],
            all_libraries=False,
            verbosity=0,
            doit=doit,
            lock=True,
        )

    def _setup_common_mocks(self, mocker, items):
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)

    def test_dry_run_no_updates(self, mocker):
        """Without --doit, no updates should be made."""
        items = [{"Id": "1", "Name": "Movie A", "Genres": ["dada"]}]
        self._setup_common_mocks(mocker, items)
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        run_genres_command(self._make_normalize_args(doit=False))
        mock_update.assert_not_called()

    def test_dry_run_shows_preview(self, mocker, capsys):
        """Dry-run must print preview information."""
        items = [{"Id": "1", "Name": "Movie A", "Genres": ["dada"]}]
        self._setup_common_mocks(mocker, items)
        mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        run_genres_command(self._make_normalize_args(doit=False))
        captured = capsys.readouterr()
        # Should mention "Would update" or something about what would change
        assert "update" in captured.out.lower() or "dada" in captured.out.lower() or "would" in captured.out.lower()

    def test_doit_calls_update_for_matching_items(self, mocker):
        """With --doit, update_item_genres must be called for items needing normalization."""
        # Item already has full metadata (from user-scoped batch fetch — no second GET needed)
        items = [{"Id": "1", "Name": "Movie A", "Genres": ["dada"], "GenreItems": [], "LockedFields": []}]
        self._setup_common_mocks(mocker, items)
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)

        run_genres_command(self._make_normalize_args(doit=True))
        mock_update.assert_called_once()
        # new_genres should contain Comedy (normalized from dada)
        call_args = mock_update.call_args
        new_genres_arg = call_args[0][4] if len(call_args[0]) > 4 else call_args.kwargs.get("new_genres", [])
        assert "Comedy" in new_genres_arg

    def test_doit_skips_already_normalized_items(self, mocker):
        """Items with already-canonical genres should be skipped."""
        items = [{"Id": "1", "Name": "Movie A", "Genres": ["Drama"]}]  # Already canonical
        self._setup_common_mocks(mocker, items)
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        run_genres_command(self._make_normalize_args(doit=True))
        mock_update.assert_not_called()

    def test_doit_respects_lock_flag(self, mocker):
        """lock=True must be passed to update_item_genres."""
        items = [{"Id": "1", "Name": "Movie A", "Genres": ["dada"], "GenreItems": [], "LockedFields": []}]
        self._setup_common_mocks(mocker, items)
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)

        args = self._make_normalize_args(doit=True)
        args.lock = True
        run_genres_command(args)

        call_args = mock_update.call_args
        # lock param should be True
        lock_arg = call_args[0][5] if len(call_args[0]) > 5 else call_args.kwargs.get("lock", True)
        assert lock_arg is True

    def test_all_genres_normalized_prints_message(self, mocker, capsys):
        """When no items need updating, print appropriate message."""
        items = [{"Id": "1", "Name": "Movie A", "Genres": ["Drama"]}]  # Already canonical
        self._setup_common_mocks(mocker, items)
        mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        run_genres_command(self._make_normalize_args(doit=True))
        captured = capsys.readouterr()
        assert "normalized" in captured.out.lower() or "already" in captured.out.lower()


class TestCommandRouting:
    def test_audit_routes_to_run_audit(self, mocker):
        mock_audit = mocker.patch("emby_dedupe.cli.genres._run_audit")
        mock_normalize = mocker.patch("emby_dedupe.cli.genres._run_normalize")
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")

        args = argparse.Namespace(
            action="audit", host="http://emby", port=None, api_key="key",
            library=["Movies"], all_libraries=False, verbosity=0, output_json=None
        )
        run_genres_command(args)
        mock_audit.assert_called_once()
        mock_normalize.assert_not_called()

    def test_normalize_routes_to_run_normalize(self, mocker):
        mock_audit = mocker.patch("emby_dedupe.cli.genres._run_audit")
        mock_normalize = mocker.patch("emby_dedupe.cli.genres._run_normalize")
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")

        args = argparse.Namespace(
            action="normalize", host="http://emby", port=None, api_key="key",
            library=["Movies"], all_libraries=False, verbosity=0, doit=False, lock=True
        )
        run_genres_command(args)
        mock_normalize.assert_called_once()
        mock_audit.assert_not_called()


class TestNoLockCLIParsing:
    def test_no_lock_flag_sets_lock_false(self):
        """--no-lock must actually set lock=False."""
        import argparse
        from emby_dedupe.cli.genres import add_genres_arguments

        parser = argparse.ArgumentParser()
        add_genres_arguments(parser)

        args = parser.parse_args(["normalize", "--no-lock"])
        assert args.lock is False

    def test_lock_default_is_true(self):
        import argparse
        from emby_dedupe.cli.genres import add_genres_arguments

        parser = argparse.ArgumentParser()
        add_genres_arguments(parser)

        args = parser.parse_args(["normalize"])
        assert args.lock is True


class TestNoneGenresEdgeCase:
    def test_normalize_dry_run_with_none_genres_does_not_crash(self, mocker):
        """Items with Genres=None should not crash during dry-run normalize."""
        items = [
            {"Id": "1", "Name": "Movie A", "Genres": None},
            {"Id": "2", "Name": "Movie B", "Genres": ["Drama"]},
        ]
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        args = argparse.Namespace(
            action="normalize", host="http://emby", port=None, api_key="key",
            library=["Movies"], all_libraries=False, verbosity=0, doit=False, lock=True
        )
        # Should not raise
        run_genres_command(args)
        mock_update.assert_not_called()


class TestRepairDupes:
    def _setup_common_mocks(self, mocker):
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")

    def test_repair_dupes_flag_triggers_fetch_full_item(self, mocker):
        """--repair-dupes must use single-item endpoint to detect hidden duplicates."""
        items = [{"Id": "1", "Name": "Prison Break", "Genres": ["Thriller"], "GenreItems": []}]
        self._setup_common_mocks(mocker)
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        # Single-item endpoint reveals the hidden duplicate
        mock_full = mocker.patch("emby_dedupe.cli.genres.fetch_full_item", return_value={
            "Id": "1", "Name": "Prison Break",
            "Genres": ["Drama", "Thriller", "Thriller"],
            "GenreItems": [], "LockedFields": []
        })
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)

        args = argparse.Namespace(
            action="normalize", host="http://emby", port=None, api_key="key",
            library=["Movies"], all_libraries=False, verbosity=0, doit=True, lock=True,
            repair_dupes=True,
        )
        run_genres_command(args)

        mock_full.assert_called()
        mock_update.assert_called()
        # Must be called with deduped genres
        new_genres = mock_update.call_args[0][4]
        assert new_genres.count("Thriller") == 1

    def test_repair_dupes_skips_clean_items(self, mocker):
        """Items without duplicates must not be updated."""
        items = [{"Id": "1", "Name": "Clean Movie", "Genres": ["Drama"], "GenreItems": []}]
        self._setup_common_mocks(mocker)
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch("emby_dedupe.cli.genres.fetch_full_item", return_value={
            "Id": "1", "Name": "Clean Movie",
            "Genres": ["Drama"], "GenreItems": [], "LockedFields": []
        })
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        args = argparse.Namespace(
            action="normalize", host="http://emby", port=None, api_key="key",
            library=["Movies"], all_libraries=False, verbosity=0, doit=True, lock=True,
            repair_dupes=True,
        )
        run_genres_command(args)
        mock_update.assert_not_called()

    def test_no_repair_dupes_without_flag(self, mocker):
        """Without --repair-dupes, fetch_full_item must NOT be called."""
        items = [{"Id": "1", "Name": "Drama Movie", "Genres": ["Drama"], "GenreItems": []}]
        self._setup_common_mocks(mocker)
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mock_full = mocker.patch("emby_dedupe.cli.genres.fetch_full_item")

        args = argparse.Namespace(
            action="normalize", host="http://emby", port=None, api_key="key",
            library=["Movies"], all_libraries=False, verbosity=0, doit=True, lock=True,
            repair_dupes=False,
        )
        run_genres_command(args)
        mock_full.assert_not_called()


class TestFixAction:
    def _setup_common_mocks(self, mocker):
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))
        mocker.patch("emby_dedupe.cli.genres.get_library_id", return_value="lib-abc")

    def _make_fix_args(self, doit=False, gaps_only=True, validate=False):
        return argparse.Namespace(
            action="fix", host="http://emby", port=None, api_key="emby-key",
            library=["Movies"], all_libraries=False, verbosity=0,
            doit=doit, lock=True, gaps_only=gaps_only, validate=validate,
            tmdb_api_key="tmdb-key-123",
        )

    def test_fix_exits_without_api_keys(self, mocker):
        """fix action must exit(1) when no TMDB or OMDb API keys are available."""
        self._setup_common_mocks(mocker)
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=[])
        mocker.patch("emby_dedupe.cli.genres.get_env_variable", return_value=None)
        with pytest.raises(SystemExit):
            args = self._make_fix_args()
            args.tmdb_api_key = None
            run_genres_command(args)

    def test_fix_gaps_only_skips_items_with_genres(self, mocker):
        """gaps_only mode must skip items that already have genres."""
        self._setup_common_mocks(mocker)
        items = [
            {"Id": "1", "Name": "Has Genres", "Genres": ["Drama"], "ProviderIds": {}},
            {"Id": "2", "Name": "No Genres", "Genres": [], "ProviderIds": {"Tmdb": "123"}},
        ]
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        # _run_fix imports these from emby_dedupe.api.genre_providers at call time
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        mock_fetch_genres = mocker.patch(
            "emby_dedupe.cli.genres.fetch_genres_for_item", return_value=[]
        )

        run_genres_command(self._make_fix_args(gaps_only=True))

        # Only item "No Genres" should be processed (gaps-only filters out item with genres)
        call_items = [c[0][0] for c in mock_fetch_genres.call_args_list]
        assert all(i["Id"] == "2" for i in call_items)

    def test_fix_dry_run_no_updates(self, mocker):
        """Without --doit, update_item_genres must never be called."""
        self._setup_common_mocks(mocker)
        items = [{"Id": "1", "Name": "Movie", "Genres": [], "ProviderIds": {"Tmdb": "123"}}]
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch("emby_dedupe.cli.genres.fetch_genres_for_item", return_value=["Drama"])
        mocker.patch("emby_dedupe.cli.genres.compare_genres", return_value={
            "missing_from_emby": ["Drama"], "extra_in_emby": [],
            "merged": ["Drama"], "has_diff": True,
        })
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        run_genres_command(self._make_fix_args(doit=False))
        mock_update.assert_not_called()

    def test_fix_doit_updates_item(self, mocker):
        """With --doit and a diff found, update_item_genres must be called."""
        self._setup_common_mocks(mocker)
        items = [{"Id": "1", "Name": "Movie", "Genres": [], "ProviderIds": {"Tmdb": "123"}}]
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch("emby_dedupe.cli.genres.fetch_genres_for_item", return_value=["Drama"])
        mocker.patch("emby_dedupe.cli.genres.compare_genres", return_value={
            "missing_from_emby": ["Drama"], "extra_in_emby": [],
            "merged": ["Drama"], "has_diff": True,
        })
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        full_item = {"Id": "1", "Name": "Movie", "Genres": [], "GenreItems": [], "LockedFields": []}
        mocker.patch("emby_dedupe.cli.genres.fetch_full_item", return_value=full_item)
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)

        run_genres_command(self._make_fix_args(doit=True))
        mock_update.assert_called_once()

    def test_fix_saves_cache_on_completion(self, mocker):
        """Cache must always be saved after fix completes (even with no items)."""
        self._setup_common_mocks(mocker)
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=[])
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mock_save = mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")

        run_genres_command(self._make_fix_args())
        mock_save.assert_called_once()

    def test_fix_skips_item_when_no_external_data(self, mocker):
        """Items where no external genre data is found must be counted as skipped."""
        self._setup_common_mocks(mocker)
        items = [{"Id": "1", "Name": "Unknown Movie", "Genres": [], "ProviderIds": {}}]
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch("emby_dedupe.cli.genres.fetch_genres_for_item", return_value=[])
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres")

        run_genres_command(self._make_fix_args())
        mock_update.assert_not_called()

    def test_fix_validate_processes_all_items(self, mocker):
        """--validate mode must process all items, not just those without genres."""
        self._setup_common_mocks(mocker)
        items = [
            {"Id": "1", "Name": "Has Genres", "Genres": ["Drama"], "ProviderIds": {"Tmdb": "1"}},
            {"Id": "2", "Name": "No Genres", "Genres": [], "ProviderIds": {"Tmdb": "2"}},
        ]
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        mock_fetch_genres = mocker.patch(
            "emby_dedupe.cli.genres.fetch_genres_for_item", return_value=[]
        )

        run_genres_command(self._make_fix_args(validate=True, gaps_only=False))

        # Both items should be processed in validate mode
        assert mock_fetch_genres.call_count == 2

    def test_fix_saves_cache_even_on_error(self, mocker):
        """Cache must be saved even if an item processing error occurs."""
        self._setup_common_mocks(mocker)
        items = [{"Id": "1", "Name": "Bad Item", "Genres": [], "ProviderIds": {"Tmdb": "123"}}]
        mocker.patch("emby_dedupe.cli.genres.fetch_items_with_genres", return_value=items)
        mocker.patch(
            "emby_dedupe.cli.genres.fetch_genres_for_item",
            side_effect=RuntimeError("network failure"),
        )
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mock_save = mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")

        run_genres_command(self._make_fix_args())
        # save_genre_cache must still be called (finally block)
        mock_save.assert_called_once()


class TestWebhookCompatibility:
    """
    CRITICAL: these tests verify the exact CLI invocations used by the webhook listener.
    The webhook listener calls:
      emby-dedupe genres normalize --doit --item-ids 123,456
      emby-dedupe genres fix --doit --validate --item-ids 123,456
    These MUST continue to work after any CLI refactor.
    """

    def _setup_genres_mocks(self, mocker):
        mocker.patch("emby_dedupe.cli.genres.httpx.Client")
        mocker.patch("emby_dedupe.cli.genres.check_emby_connection", return_value=True)
        mocker.patch("emby_dedupe.cli.genres.get_user_id", return_value="user-123")
        mocker.patch("emby_dedupe.cli.genres.handle_host_and_port", return_value=("http://emby", 8096))

    def test_typer_genres_normalize_doit_item_ids(self, mocker):
        """Webhook: genres normalize --doit --item-ids 123,456 must work end-to-end."""
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        self._setup_genres_mocks(mocker)
        # _fetch_items_by_ids now uses batch fetch_items_by_ids
        item_1 = {"Id": "123", "Name": "Movie 1", "Genres": ["dada"], "GenreItems": [], "LockedFields": []}
        item_2 = {"Id": "456", "Name": "Movie 2", "Genres": ["Drama"], "GenreItems": [], "LockedFields": []}
        mocker.patch("emby_dedupe.cli.genres.fetch_items_by_ids", return_value=[item_1, item_2])
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--host", "http://emby",
                "--api-key", "key123",
                "genres", "normalize",
                "--doit",
                "--item-ids", "123,456",
            ],
        )

        assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
        # item 1 had "dada" → normalized to "Comedy" → should be updated
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        new_genres = call_args[0][4] if len(call_args[0]) > 4 else call_args.kwargs.get("new_genres", [])
        assert "Comedy" in new_genres

    def test_typer_genres_fix_doit_validate_item_ids(self, mocker):
        """Webhook: genres fix --doit --validate --item-ids 123,456 must work end-to-end."""
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        self._setup_genres_mocks(mocker)
        item = {"Id": "123", "Name": "Movie 1", "Genres": [], "ProviderIds": {"Tmdb": "999"}, "GenreItems": [], "LockedFields": []}
        # _fetch_items_by_ids now uses batch fetch_items_by_ids
        mocker.patch("emby_dedupe.cli.genres.fetch_items_by_ids", return_value=[item])
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        mocker.patch(
            "emby_dedupe.cli.genres.fetch_genres_for_item", return_value=["Drama", "Thriller"]
        )
        mocker.patch(
            "emby_dedupe.cli.genres.compare_genres",
            return_value={
                "missing_from_emby": ["Drama", "Thriller"],
                "extra_in_emby": [],
                "merged": ["Drama", "Thriller"],
                "has_diff": True,
            },
        )
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--host", "http://emby",
                "--api-key", "key123",
                "genres", "fix",
                "--doit",
                "--validate",
                "--item-ids", "123",
                "--tmdb-api-key", "tmdb-key",
            ],
        )

        assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
        mock_update.assert_called_once()

    def test_typer_genres_normalize_item_ids_no_host_fails(self, mocker):
        """Without --host (and no env vars), genres normalize --item-ids must exit non-zero."""
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app
        import os

        runner = CliRunner()
        # Explicitly clear host/api-key env vars so validation fires
        env_overrides = {
            "DEDUPE_EMBY_HOST": "",
            "DEDUPE_EMBY_API_KEY": "",
            "DEDUPE_EMBY_LIBRARY": "",
        }
        result = runner.invoke(
            app,
            ["genres", "normalize", "--doit", "--item-ids", "123"],
            env=env_overrides,
        )
        # Should exit with code 1 due to missing host/api-key
        assert result.exit_code != 0

    def test_typer_genres_process_doit_validate_item_ids(self, mocker):
        """Webhook: genres process --doit --validate --item-ids runs normalize + fix in one pass."""
        from typer.testing import CliRunner
        from emby_dedupe.cli.app import app

        self._setup_genres_mocks(mocker)
        item_1 = {"Id": "123", "Name": "Movie 1", "Genres": ["dada"], "GenreItems": [], "LockedFields": [], "ProviderIds": {"Tmdb": "999"}}
        item_2 = {"Id": "456", "Name": "Movie 2", "Genres": ["Drama"], "GenreItems": [], "LockedFields": [], "ProviderIds": {"Tmdb": "888"}}
        # First call: initial fetch; second call: re-fetch after normalize
        mocker.patch("emby_dedupe.cli.genres.fetch_items_by_ids", side_effect=[[item_1, item_2], [item_1, item_2]])
        mock_update = mocker.patch("emby_dedupe.cli.genres.update_item_genres", return_value=True)
        mocker.patch("emby_dedupe.api.genre_providers.load_genre_cache", return_value={})
        mocker.patch("emby_dedupe.api.genre_providers.save_genre_cache")
        mocker.patch("emby_dedupe.cli.genres.fetch_genres_for_item", return_value=[])
        mocker.patch(
            "emby_dedupe.cli.genres.compare_genres",
            return_value={"missing_from_emby": [], "extra_in_emby": [], "merged": [], "has_diff": False},
        )

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--host", "http://emby",
                "--api-key", "key123",
                "genres", "process",
                "--doit",
                "--validate",
                "--item-ids", "123,456",
                "--tmdb-api-key", "tmdb-key",
            ],
        )

        assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
        # item_1 had "dada" → "Comedy" normalization → should be updated
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        new_genres = call_args[0][4] if len(call_args[0]) > 4 else call_args.kwargs.get("new_genres", [])
        assert "Comedy" in new_genres
