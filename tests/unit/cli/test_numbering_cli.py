"""`numbering` command: fetching, watched marks per item, report and JSON output."""
from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from emby_dedupe.api.numbering import MIXED, REMAPPED, SHADOWED, NumberingFinding
from emby_dedupe.cli import numbering as cli


def finding(kind, item_id="1", series_id="10", series_name="Show", file_number=(4, 1), emby_number=(1, 1)):
    return NumberingFinding(kind=kind, series_id=series_id, series_name=series_name, item_id=item_id,
                            path=f"/tv/{item_id}.mkv", name="Chapter 1", file_number=file_number,
                            emby_number=emby_number)


def response(data):
    resp = MagicMock()
    resp.json.return_value = data
    return resp


class TestAllowlist:
    def test_explicit_path_wins_over_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv(cli.ENV_ALLOW_FILE, str(tmp_path / "env.txt"))
        assert cli.allowlist_path(str(tmp_path / "x.txt")) == tmp_path / "x.txt"
        assert cli.allowlist_path() == tmp_path / "env.txt"

    def test_missing_file_is_empty(self, tmp_path):
        assert cli.read_allowlist(tmp_path / "none.txt") == set()

    def test_reads_ids(self, tmp_path):
        path = tmp_path / "allow.txt"
        path.write_text("99858 # year seasons\n", encoding="utf-8")
        assert cli.read_allowlist(path) == {"99858"}


class TestFetchEpisodes:
    def test_whole_server_without_libraries(self, mocker):
        pages = mocker.patch.object(cli, "paginate_emby_items", return_value=iter([([{"Id": "a"}, {"Id": "b"}], 2)]))
        assert [e["Id"] for e in cli.fetch_episodes(MagicMock(), "http://emby", [])] == ["a", "b"]
        params = pages.call_args.args[2]
        assert "ParentId" not in params
        assert params["IncludeItemTypes"] == "Episode"

    def test_libraries_are_merged_once_per_episode(self, mocker):
        mocker.patch.object(cli, "paginate_emby_items", side_effect=[
            iter([([{"Id": "a"}], 1)]), iter([([{"Id": "a"}, {"Id": "c"}], 2)])])
        assert sorted(e["Id"] for e in cli.fetch_episodes(MagicMock(), "http://emby", ["L1", "L2"])) == ["a", "c"]


class TestCollectPlayedBy:
    def test_reads_each_user_per_item_for_attention_findings_only(self, mocker):
        users = [{"Id": "u1", "Name": "alice"}, {"Id": "u2", "Name": "bob"}]

        def fake(_client, _method, url, **_kw):
            if url.endswith("/Users"):
                return response(users)
            return response({"UserData": {"Played": "/Users/u1/" in url}})

        request = mocker.patch.object(cli, "make_http_request", side_effect=fake)
        shadowed, remapped = finding(SHADOWED, "s"), finding(REMAPPED, "r")
        assert cli.collect_played_by(MagicMock(), "http://emby", [shadowed, remapped]) == 1
        assert shadowed.played_by == ["alice"]
        assert remapped.played_by == []
        urls = [c.args[2] for c in request.call_args_list]
        assert "http://emby/Users/u1/Items/s" in urls
        assert not any("Ids=" in u for u in urls)

    def test_nothing_to_check_makes_no_request(self, mocker):
        request = mocker.patch.object(cli, "make_http_request")
        assert cli.collect_played_by(MagicMock(), "http://emby", [finding(REMAPPED)]) == 0
        request.assert_not_called()


class TestFormatReport:
    def test_summary_and_series_blocks(self):
        shadowed = finding(SHADOWED, "s", series_name="Show A")
        shadowed.played_by = ["alice"]
        lines = cli.format_report([shadowed, finding(REMAPPED, "r", series_id="20", series_name="Show B")], 2)
        text = "\n".join(lines)
        assert lines[0].startswith("Numbering: 1 episode(s) in 1 series need a look; 1 remapped")
        assert "2 allowlisted series skipped" in lines[0]
        assert "Show A (10): shadowed 1" in text
        assert "file S04E01 -> Emby S1E1 'Chapter 1' [s] | watched: alice" in text
        assert "Show B (20): remapped 1" in text
        assert "allowlist the series id" in text
        assert text.index("Show A") < text.index("Show B")

    def test_remapped_lines_on_request(self):
        text = "\n".join(cli.format_report([finding(REMAPPED, "r")], 0, show_remapped=True))
        assert "remapped  file S04E01" in text

    def test_long_series_are_cut(self):
        many = [finding(MIXED, str(n), file_number=(1, n), emby_number=(2, n)) for n in range(1, 20)]
        text = "\n".join(cli.format_report(many, 0))
        assert "... 7 more (see --json-out)" in text

    def test_unknown_numbers_print_as_question_marks(self):
        text = "\n".join(cli.format_report([finding(MIXED, emby_number=(None, None))], 0))
        assert "Emby S?E?" in text


class TestRunNumbering:
    def test_report_json_and_attention_count(self, mocker, tmp_path, capsys):
        allow = tmp_path / "allow.txt"
        allow.write_text("99858\n", encoding="utf-8")
        episodes = [
            {"Id": "p1", "Path": "/S01/L S01E01.mkv", "ParentIndexNumber": 1, "IndexNumber": 1, "SeriesId": "10", "SeriesName": "Show A"},
            {"Id": "p4", "Path": "/S04/L S04E01.mkv", "ParentIndexNumber": 1, "IndexNumber": 1, "SeriesId": "10", "SeriesName": "Show A"},
            {"Id": "m", "Path": "/S04/M S04E01.mkv", "ParentIndexNumber": 2006, "IndexNumber": 1, "SeriesId": "99858", "SeriesName": "Show B"},
        ]
        mocker.patch.object(cli, "fetch_episodes", return_value=episodes)
        played = mocker.patch.object(cli, "collect_played_by", return_value=1)
        out = tmp_path / "out" / "numbering.json"
        args = Namespace(allow_file=str(allow), marks=True, show_remapped=False, json_out=str(out))
        assert cli.run_numbering(MagicMock(), "http://emby", [], args) == 1
        played.assert_called_once()
        text = capsys.readouterr().out
        assert "Checked 3 episodes" in text
        assert "1 allowlisted series skipped" in text
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["episodes"] == 3
        assert data["allowlisted_series"] == ["99858"]
        assert data["counts"] == {SHADOWED: 1}
        assert data["findings"][0]["item_id"] == "p4"

    def test_command_needs_host_and_key(self):
        args = Namespace(host=None, port=None, api_key=None, library=[])
        with pytest.raises(SystemExit):
            cli.run_numbering_command(args)

    def test_command_connects_and_resolves_libraries(self, mocker):
        mocker.patch.object(cli, "check_emby_connection", return_value=True)
        mocker.patch.object(cli, "get_library_id", return_value="L1")
        run = mocker.patch.object(cli, "run_numbering", return_value=0)
        cli.run_numbering_command(Namespace(host="http://emby", port=8096, api_key="k", library=["TV"]))
        assert run.call_args.args[1] == "http://emby:8096"
        assert run.call_args.args[2] == ["L1"]

    def test_unknown_library_exits(self, mocker):
        mocker.patch.object(cli, "check_emby_connection", return_value=True)
        mocker.patch.object(cli, "get_library_id", return_value=None)
        args = Namespace(host="http://emby", port=8096, api_key="k", library=["Nope"])
        with pytest.raises(SystemExit):
            cli.run_numbering_command(args)


def test_numbering_command_is_registered():
    from typer.testing import CliRunner

    from emby_dedupe.cli.app import app

    result = CliRunner().invoke(app, ["numbering", "--help"])
    assert result.exit_code == 0
    assert "--allow-file" in result.output
    assert "--marks" in result.output
