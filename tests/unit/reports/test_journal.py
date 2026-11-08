"""The deletion journal records every copy a real run deleted, and nothing else."""
from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from emby_dedupe.reports import journal
from emby_dedupe.reports.common import report_dir

RUN_AT = "2026-11-08T20:39:34+01:00"


def _copy(item_id: str, path: str, ids: dict[str, str], **extra) -> dict:
    return {"id": item_id, "name": "Youth", "path": path, "provider_ids": ids,
            "quality_description": {"path": path, "size": 4_000}, **extra}


def _decision(status: str, episode: bool = True) -> dict:
    ep = {"is_episode": True, "series_name": "Youth", "season_number": 1, "episode_number": 8} if episode \
        else {"is_episode": False}
    keep = _copy("keep1", "/M/S01/E08 [HDR10].mkv", {"Imdb": "tt1", "Tmdb": "10", "Tvdb": "20"}, **ep)
    item = _copy("del1", "/M/S01/E08.mkv", {"Imdb": "tt1", "Tmdb": "11"}, **ep)
    item["deletion_result"] = {"id": "del1", "status": status, "error": None}
    return {"keep": keep, "delete": [item]}


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_only_deleted_copies_are_journalled():
    statuses = ("success", "fold_safe_removed", "not_attempted", "would_block", "skipped_unsafe",
                "failed", "fold_safe_would_remove")
    rows = journal.dedupe_rows([_decision(s) for s in statuses], RUN_AT)

    assert [r["file_only"] for r in rows] == [False, True]


def test_dedupe_row_carries_both_copies_ids_and_the_episode():
    (row,) = journal.dedupe_rows([_decision("success")], RUN_AT)

    assert row["schema"] == journal.SCHEMA
    assert row["kind"] == "dedupe_delete"
    assert row["deleted_at"] == RUN_AT
    assert row["type"] == "Episode"
    assert (row["series"], row["season"], row["episode_number"]) == ("Youth", 1, 8)
    assert row["deleted"]["id"] == "del1"
    assert row["kept"]["id"] == "keep1"
    assert row["deleted"]["provider_ids"] == {"imdb": "tt1", "tmdb": "11"}
    assert row["kept"]["provider_ids"] == {"imdb": "tt1", "tmdb": "10", "tvdb": "20"}
    assert row["shared_provider_ids"] == {"imdb": "tt1"}


def test_movie_row_has_no_episode_fields():
    (row,) = journal.dedupe_rows([_decision("success", episode=False)], RUN_AT)

    assert row["type"] == "Movie"
    assert "series" not in row


def test_cleanup_rows_only_for_successful_deletes():
    def cand(item_id: str, status: str | None) -> SimpleNamespace:
        return SimpleNamespace(item_id=item_id, name="Flashback", year=2023, path="/M/F.mkv", size_bytes=9,
                               library="LQ - Movies", collection=None,
                               deletion_result={"status": status} if status else None)

    rows = journal.cleanup_rows([cand("a", "success"), cand("b", "failed"), cand("c", None),
                                 cand("d", "skipped_unsafe")], RUN_AT, "Movie")

    assert [r["deleted"]["id"] for r in rows] == ["a"]
    assert rows[0]["kind"] == "cleanup_delete"
    assert rows[0]["kept"] is None


def test_append_accumulates_lines(tmp_path):
    path = tmp_path / "j" / "deletions.jsonl"
    rows = journal.dedupe_rows([_decision("success")], RUN_AT)

    assert journal.append_rows(rows, path) == 1
    assert journal.append_rows(rows, path) == 1
    assert len(_read(path)) == 2


def test_nothing_to_write_touches_nothing(tmp_path):
    path = tmp_path / "deletions.jsonl"

    assert journal.append_rows([], path) == 0
    assert not path.exists()


def test_unwritable_journal_never_raises(tmp_path):
    rows = journal.dedupe_rows([_decision("success")], RUN_AT)

    assert journal.append_rows(rows, tmp_path) == 0  # a directory, not a file


def test_default_path_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(journal.ENV_FILE, str(tmp_path / "x.jsonl"))

    assert journal.journal_path() == tmp_path / "x.jsonl"


def test_no_remote_call_without_a_host(tmp_path):
    calls = []
    rows = journal.dedupe_rows([_decision("success")], RUN_AT)

    journal.append_rows(rows, tmp_path / "d.jsonl", runner=lambda *a: calls.append(a) or (0, ""))

    assert calls == []


def test_remote_append_sends_the_same_lines(tmp_path, monkeypatch):
    monkeypatch.setenv(journal.ENV_HOST, "user@media-host")
    monkeypatch.setenv(journal.ENV_REMOTE_FILE, "/srv/emby journal/deletions.jsonl")
    calls = []
    rows = journal.dedupe_rows([_decision("success")], RUN_AT)

    journal.append_rows(rows, tmp_path / "d.jsonl", runner=lambda *a: calls.append(a) or (0, ""))

    ((host, remote, data),) = calls
    assert (host, remote) == ("user@media-host", "/srv/emby journal/deletions.jsonl")
    assert data == (tmp_path / "d.jsonl").read_text(encoding="utf-8")


def test_remote_needs_an_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv(journal.ENV_HOST, "user@media-host")
    monkeypatch.setenv(journal.ENV_REMOTE_FILE, "~/deletions.jsonl")
    calls = []

    written = journal.append_rows(journal.dedupe_rows([_decision("success")], RUN_AT), tmp_path / "d.jsonl",
                                  runner=lambda *a: calls.append(a) or (0, ""))

    assert calls == []
    assert written == 1


def test_remote_failure_keeps_the_local_lines(tmp_path, monkeypatch):
    monkeypatch.setenv(journal.ENV_HOST, "user@media-host")
    monkeypatch.setenv(journal.ENV_REMOTE_FILE, "/srv/j/deletions.jsonl")

    def broken(*_a):
        raise OSError("no route to host")

    assert journal.append_rows(journal.dedupe_rows([_decision("success")], RUN_AT), tmp_path / "d.jsonl",
                               runner=broken) == 1


def test_remote_script_keeps_the_file_world_readable(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs.get("input")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(journal.subprocess, "run", fake_run)
    journal._ssh_append("user@host", "/srv/emby journal/d.jsonl", "line\n")

    script = seen["cmd"][-1]
    assert script.startswith("umask 022; ")
    assert "'/srv/emby journal/d.jsonl'" in script
    assert seen["input"] == "line\n"
    assert "BatchMode=yes" in seen["cmd"]


def test_dry_run_writes_no_journal(tmp_path, monkeypatch):
    from emby_dedupe.cli.app import _journal_deletions

    path = tmp_path / "deletions.jsonl"
    monkeypatch.setenv(journal.ENV_FILE, str(path))

    _journal_deletions([_decision("success")], doit=False)
    assert not path.exists()
    _journal_deletions([_decision("success")], doit=True)
    assert len(_read(path)) == 1


def test_journal_runs_after_fold_safe_and_before_reports():
    """Fold-safe removals must be in the journal, and a report failure must not lose it."""
    from emby_dedupe.cli import app

    body = inspect.getsource(app.dedupe_cmd)
    assert body.index("_run_fold_safe_delete(") < body.index("_journal_deletions(") < body.index("_generate_reports(")


def test_cleanup_journals_after_its_deletions():
    from emby_dedupe.cli import cleanup

    body = inspect.getsource(cleanup._execute_cleanup)
    assert body.index('series_candidates, username, password, api_key, "series")') \
        < body.index("_journal_cleanup(candidates, series_candidates, doit)") \
        < body.index("_save_cleanup_html_report(")


def test_report_dir_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_REPORT_DIR", str(tmp_path / "reports"))

    assert report_dir() == str(tmp_path / "reports")
    assert (tmp_path / "reports").is_dir()


def test_report_dir_falls_back_to_temp(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("DEDUPE_REPORT_DIR", str(blocker / "reports"))

    assert report_dir() == tempfile.gettempdir()


def test_tests_never_write_reports_to_the_real_folders():
    assert report_dir() != str(Path.home() / ".emby-dedupe" / "reports")
    assert os.environ.get(journal.ENV_HOST) is None


def test_cleanup_dry_run_writes_no_journal(tmp_path, monkeypatch):
    from emby_dedupe.cli.cleanup import _journal_cleanup

    path = tmp_path / "deletions.jsonl"
    monkeypatch.setenv(journal.ENV_FILE, str(path))
    done = SimpleNamespace(item_id="a", name="Flashback", year=2023, path="/M/F.mkv", size_bytes=9,
                           library="LQ - Movies", collection=None, deletion_result={"status": "success"})

    _journal_cleanup([done], [], doit=False)
    assert not path.exists()
    _journal_cleanup([done], [], doit=True)
    assert [r["deleted"]["id"] for r in _read(path)] == ["a"]

