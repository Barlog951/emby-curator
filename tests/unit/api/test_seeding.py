"""The seeding check: deletes that take data from a live torrent are named, never held."""

import re
from unittest.mock import MagicMock, patch

import httpx

from emby_dedupe.api import deduplication as dd
from emby_dedupe.api import seeding as sd
from emby_dedupe.api.deduplication import process_deletion_and_generate_report
from emby_dedupe.api.deletion_guard import collect_known_paths

URL = "http://transmission:9091/transmission/rpc"
HOUR = 3600 * 10_000_000

TORRENTS = [
    {"name": "Allen.2026.1080p", "uploadRatio": 0.1, "downloadDir": "/Movies/DiViX/Allen (2026) - 1080p WEB-DL CZ",
     "files": [{"name": "Allen (2026) - 1080p WEB-DL CZ.mkv"}]},
    {"name": "Rivals.S02", "uploadRatio": 0.0, "downloadDir": "/Movies/Serials/Rivals/S02",
     "files": [{"name": "Rivals S02E07 - 1080p.mkv"}, {"name": "Rivals S02E07 - 1080p.en.srt"}]},
    {"name": "From.S04.parked", "uploadRatio": 0.04, "downloadDir": "/Movies/_seeding/from-s04",
     "files": [{"name": "From/S04/From S04E02 - 1080p WEB-DL SK.mkv"}]},
]


def _client(calls=None):
    """A Transmission stand-in: 409 + session id first, then the torrent list."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if sd.SESSION_HEADER not in request.headers:
            return httpx.Response(409, headers={sd.SESSION_HEADER: "sid-1"})
        return httpx.Response(200, json={"arguments": {"torrents": TORRENTS}, "result": "success"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_follows_the_session_handshake_and_maps_files():
    calls = []
    owned = sd.fetch_torrent_files(URL, _client(calls))
    assert len(calls) == 2
    assert calls[1].headers[sd.SESSION_HEADER] == "sid-1"
    path = "/Movies/DiViX/Allen (2026) - 1080p WEB-DL CZ/Allen (2026) - 1080p WEB-DL CZ.mkv"
    assert owned[path] == {"torrent": "Allen.2026.1080p", "ratio": 0.1}
    assert "/Movies/_seeding/from-s04/From/S04/From S04E02 - 1080p WEB-DL SK.mkv" in owned


def test_per_title_folder_is_removed_whole():
    path = "/Movies/HD/Allen (2026) - 1080p/Allen (2026) - 1080p.mkv"
    assert sd.removal_scope(path, [path]) == ("/Movies/HD/Allen (2026) - 1080p", True)


def test_season_folder_with_survivors_loses_only_the_file():
    path, other = "/S/Rivals/S02/Rivals S02E07 - 1080p.mkv", "/S/Rivals/S02/Rivals S02E08.mkv"
    assert sd.removal_scope(path, [path, other]) == (path, False)


def test_folder_whose_only_neighbours_also_go_is_removed_whole():
    path, other = "/S/Show/S01/a.mkv", "/S/Show/S01/b.mkv"
    assert sd.removal_scope(path, [path, other], delete_paths=[other]) == ("/S/Show/S01", True)


def test_hits_the_seeded_file_and_its_sidecars_only():
    owned = sd.fetch_torrent_files(URL, _client())
    path = "/Movies/Serials/Rivals/S02/Rivals S02E07 - 1080p.mkv"
    survivor = "/Movies/Serials/Rivals/S02/Rivals S02E08 - 2160p.mkv"
    assert sd.seeding_hits(path, owned, [path, survivor]) == [path.replace(".mkv", ".en.srt"), path]
    assert sd.seeding_hits(survivor, owned, [path, survivor]) == []


def test_folder_delete_hits_any_seeded_file_inside():
    owned = {"/Movies/HD/Film (2020)/extras/sample.mkv": {"torrent": "Film.2020", "ratio": 1.0}}
    assert sd.seeding_hits("/Movies/HD/Film (2020)/Film (2020).mkv", owned, []) == [
        "/Movies/HD/Film (2020)/extras/sample.mkv"]


def test_a_parked_seed_is_not_reported():
    owned = sd.fetch_torrent_files(URL, _client())
    path = "/Movies/Serials/From/S04/From S04E02 - 1080p WEB-DL SK.mkv"
    assert sd.seeding_hits(path, owned, [path, "/Movies/Serials/From/S04/From S04E03.mkv"]) == []


def _decisions():
    return [
        {"keep": {"id": "k1", "path": "/Movies/HD/Allen (2026) - 1080p WEB-DL CZ/Allen (2026) - 1080p WEB-DL CZ (2).mkv",
                  "runtime_ticks": HOUR},
         "delete": [{"id": "d1", "path": "/Movies/DiViX/Allen (2026) - 1080p WEB-DL CZ/Allen (2026) - 1080p WEB-DL CZ.mkv",
                     "runtime_ticks": HOUR}]},
        {"keep": {"id": "k2", "path": "/Movies/HD/Other (2020)/Other (2020).mkv", "runtime_ticks": HOUR},
         "delete": [{"id": "d2", "path": "/Movies/DiViX/Other (2020)/Other (2020).mkv", "runtime_ticks": HOUR}]},
    ]


def test_annotate_records_and_logs_the_seeded_copy():
    decisions = _decisions()
    with patch.object(sd, "logger") as log:
        assert sd.annotate_seeding(URL, decisions, [], http=_client()) == 1
    assert decisions[0]["delete"][0]["seeding"] == [{"torrent": "Allen.2026.1080p", "ratio": 0.1}]
    assert "seeding" not in decisions[1]["delete"][0]
    assert "Allen.2026.1080p (ratio 0.10)" in log.warning.call_args_list[0].args[0]


def test_no_url_checks_nothing():
    decisions = _decisions()
    assert sd.annotate_seeding(None, decisions, []) == 0
    assert all("seeding" not in d["delete"][0] for d in decisions)


def test_unreadable_transmission_records_nothing_and_holds_nothing():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    decisions = _decisions()
    assert sd.annotate_seeding(URL, decisions, [], http=httpx.Client(transport=httpx.MockTransport(boom))) == 0
    assert all("seeding" not in d["delete"][0] for d in decisions)


def test_describe_names_each_torrent_once():
    text = sd._describe([{"torrent": "B", "ratio": None}, {"torrent": "A", "ratio": 1.5}, {"torrent": "A", "ratio": 1.5}])
    assert text == "A (ratio 1.50), B"


def test_a_seeded_copy_is_never_refused():
    item = {"id": "d", "path": "/x/a.mkv", "runtime_ticks": HOUR, "seeding": [{"torrent": "T", "ratio": 0.0}]}
    keep = {"id": "k", "path": "/y/a.mkv", "runtime_ticks": HOUR}
    assert dd._refusal_reason(item, keep) is None


def _run(doit, decisions):
    owned = sd.fetch_torrent_files(URL, _client())
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.api.seeding.fetch_torrent_files", return_value=owned), \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        mock_del.side_effect = lambda *a, **k: {"id": a[2], "status": "success", "error": None}
        process_deletion_and_generate_report(MagicMock(), "http://emby", decisions, doit, "u", "p", "k",
                                             library_paths=collect_known_paths(decisions), torrent_url=URL)
    return mock_del


def test_real_run_deletes_the_seeded_copy_and_keeps_the_record():
    decisions = _decisions()
    mock_del = _run(True, decisions)
    assert [c.args[2] for c in mock_del.call_args_list] == ["d1", "d2"]
    assert decisions[0]["delete"][0]["seeding"][0]["torrent"] == "Allen.2026.1080p"


def test_dry_run_records_without_blocking():
    decisions = _decisions()
    mock_del = _run(False, decisions)
    mock_del.assert_not_called()
    assert decisions[0]["delete"][0]["deletion_result"]["status"] == "not_attempted"
    assert decisions[0]["delete"][0]["seeding"][0]["torrent"] == "Allen.2026.1080p"


def test_dedupe_help_lists_the_option():
    from typer.testing import CliRunner

    from emby_dedupe.cli.app import app

    result = CliRunner().invoke(app, ["dedupe", "--help"],
                                env={"COLUMNS": "200", "NO_COLOR": "1", "FORCE_COLOR": None, "TERM": "dumb"})
    assert result.exit_code == 0
    assert "--transmission-url" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)


def test_pipeline_hands_the_url_to_the_deletion_step():
    from emby_dedupe.cli import main

    decisions = [{"keep": {"id": "k"}, "delete": [{"id": "d"}]}]
    with patch.object(main, "identify_duplicates", return_value={}), \
         patch.object(main, "rationalize_duplicates", return_value={}), \
         patch.object(main, "process_duplicate_groups", return_value=(decisions, {})), \
         patch.object(main, "fetch_library_paths", return_value=([], True)), \
         patch.object(main, "process_deletion_and_generate_report", return_value="RPT") as step:
        main._run_deduplication_pipeline(MagicMock(), "http://emby", {}, [], [], "k", False, "u", "p",
                                         frame_check=False, torrent_url=URL)
    assert step.call_args.args[-1] == URL
