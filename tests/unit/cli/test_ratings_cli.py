"""`ratings fill` / `ratings undo`: only empty, unlocked, older items; verified writes; journal and undo."""
from __future__ import annotations

import json
from argparse import Namespace
from collections import Counter
from unittest.mock import MagicMock

import pytest

from emby_dedupe.api.ratings import RatingChoice
from emby_dedupe.cli import ratings as cli


@pytest.fixture(autouse=True)
def journal(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv(cli.ENV_JOURNAL, str(path))
    monkeypatch.setattr(cli, "CACHE_PATH", tmp_path / "cache.json")
    return path


def page(items):
    return iter([(items, len(items))])


class TestFetchUnrated:
    def test_keeps_only_empty_older_items_of_the_asked_types(self, mocker):
        items = [
            {"Id": "1", "Type": "Movie", "OfficialRating": "", "DateCreated": "2026-01-01T10:00:00.0000000Z"},
            {"Id": "2", "Type": "Movie", "OfficialRating": "SK-12", "DateCreated": "2026-01-01T10:00:00.0000000Z"},
            {"Id": "3", "Type": "Movie", "DateCreated": "2099-01-01T10:00:00.0000000Z"},
            {"Id": "4", "Type": "Series", "OfficialRating": " ", "DateCreated": "2026-01-01T10:00:00.0000000Z"},
            {"Id": "5", "Type": "Movie"},
        ]
        mocker.patch.object(cli, "paginate_emby_items", return_value=page(items))
        found = cli.fetch_unrated(MagicMock(), "http://emby", [], ["Movie"], 48)
        assert [i["Id"] for i in found] == ["1", "5"]


class TestWrite:
    def test_writes_an_empty_unlocked_item_and_verifies(self, mocker):
        full = {"Id": "1", "Name": "Film", "OfficialRating": "", "LockedFields": ["Genres"], "OriginalTitle": "F"}
        mocker.patch.object(cli, "fetch_full_item", side_effect=[full, {**full, "OfficialRating": "SK-15"}])
        post = mocker.patch.object(cli, "post_item_update", return_value=True)
        assert cli._write(MagicMock(), "http://emby", "u", {"Id": "1"}, "SK-15") == "written"
        payload = post.call_args.args[3]
        assert payload["OfficialRating"] == "SK-15"
        assert payload["OriginalTitle"] == "F"
        assert payload["LockedFields"] == ["Genres"]  # no lock added

    def test_never_replaces_a_rating_set_meanwhile(self, mocker):
        mocker.patch.object(cli, "fetch_full_item", return_value={"Id": "1", "OfficialRating": "R"})
        post = mocker.patch.object(cli, "post_item_update")
        assert cli._write(MagicMock(), "http://emby", "u", {"Id": "1"}, "SK-15") == "skipped_now_rated"
        post.assert_not_called()

    @pytest.mark.parametrize("full", [{"Id": "1", "LockData": True}, {"Id": "1", "LockedFields": ["OfficialRating"]}])
    def test_locked_items_are_skipped(self, mocker, full):
        mocker.patch.object(cli, "fetch_full_item", return_value=full)
        post = mocker.patch.object(cli, "post_item_update")
        assert cli._write(MagicMock(), "http://emby", "u", {"Id": "1"}, "SK-15") == "skipped_locked"
        post.assert_not_called()

    def test_a_value_that_did_not_stick_is_a_failure(self, mocker):
        mocker.patch.object(cli, "fetch_full_item", side_effect=[{"Id": "1"}, {"Id": "1", "OfficialRating": ""}])
        mocker.patch.object(cli, "post_item_update", return_value=True)
        assert cli._write(MagicMock(), "http://emby", "u", {"Id": "1"}, "SK-15") == "failed"


class TestRunFill:
    def _args(self, doit, tmp_path):
        return Namespace(types="Movie", min_age_hours=48, limit=None, tmdb_api_key="k", doit=doit,
                         json_out=str(tmp_path / "out.json"))

    def _setup(self, mocker, choices):
        mocker.patch.object(cli, "fetch_unrated", return_value=[
            {"Id": i, "Type": "Movie", "Name": f"Film {i}"} for i in choices])
        mocker.patch.object(cli, "CertificateSource", return_value=MagicMock())
        mocker.patch.object(cli, "_resolve_choice", side_effect=lambda _s, item: choices[item["Id"]])

    def test_dry_run_writes_nothing_and_reports(self, mocker, tmp_path, journal):
        self._setup(mocker, {"1": RatingChoice("SK-15", "neighbours 1", {"CZ": ["15+"]}),
                             "2": RatingChoice(None, "no certificate")})
        write = mocker.patch.object(cli, "_write")
        counts = cli.run_fill(MagicMock(), "http://emby", "u", [], self._args(False, tmp_path))
        write.assert_not_called()
        assert counts == Counter({"would write SK-15": 1, "empty: no certificate": 1})
        assert not journal.exists()
        report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        assert report["items"][0]["certificates"] == {"CZ": ["15+"]}

    def test_doit_journals_each_write_and_undo_clears_it(self, mocker, tmp_path, journal):
        self._setup(mocker, {"1": RatingChoice("SK-15", "neighbours 1", {"CZ": ["15+"]})})
        mocker.patch.object(cli, "_write", return_value="written")
        counts = cli.run_fill(MagicMock(), "http://emby", "u", [], self._args(True, tmp_path))
        assert counts == Counter({"written SK-15": 1})
        entry = json.loads(journal.read_text(encoding="utf-8"))
        assert (entry["id"], entry["old"], entry["new"]) == ("1", "", "SK-15")

        mocker.patch.object(cli, "fetch_full_item", return_value={"Id": "1", "OfficialRating": "SK-15"})
        post = mocker.patch.object(cli, "post_item_update", return_value=True)
        undo = cli.run_undo(MagicMock(), "http://emby", "u", Namespace(run=entry["run"], doit=True))
        assert undo == Counter({"cleared": 1})
        assert post.call_args.args[3]["OfficialRating"] == ""

    def test_undo_keeps_a_value_changed_since(self, mocker, journal):
        cli.append_journal({"run": "r1", "id": "1", "old": "", "new": "SK-15"})
        mocker.patch.object(cli, "fetch_full_item", return_value={"Id": "1", "OfficialRating": "SK-12"})
        post = mocker.patch.object(cli, "post_item_update")
        assert cli.run_undo(MagicMock(), "http://emby", "u", Namespace(run="r1", doit=True)) == Counter(
            {"changed since, kept": 1})
        post.assert_not_called()


class TestResolveChoice:
    def test_falls_back_to_the_imdb_id(self):
        source = MagicMock()
        source.tmdb_id_for_imdb.return_value = "55"
        source.certificates.return_value = {"DE": ["12"]}
        choice = cli._resolve_choice(source, {"Type": "Movie", "ProviderIds": {"IMDB": "tt1"}})
        assert choice.rating == "SK-12"
        source.certificates.assert_called_once_with("55", "movie")

    def test_no_ids_and_unknown_items_stay_empty(self):
        source = MagicMock()
        source.certificates.return_value = None
        assert cli._resolve_choice(source, {"Type": "Movie", "ProviderIds": {}}).reason == "no tmdb id"
        assert cli._resolve_choice(source, {"Type": "Movie", "ProviderIds": {"Tmdb": "1"}}).reason == "unknown to tmdb"


def test_failures_and_error_share():
    assert cli.run_failed(Counter({"failed": 1, "written SK-15": 50}))
    assert not cli.run_failed(Counter({"errors": 2, "written SK-15": 500}))
    assert cli.run_failed(Counter({"errors": 30, "written SK-15": 100}))


def test_ratings_commands_are_registered():
    from typer.testing import CliRunner

    from emby_dedupe.cli.app import app

    runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "FORCE_COLOR": None, "TERM": "dumb"})
    assert "--min-age-hours" in runner.invoke(app, ["ratings", "fill", "--help"]).output
    assert "--run" in runner.invoke(app, ["ratings", "undo", "--help"]).output
