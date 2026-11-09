"""A dedupe delete is held when someone watched the copy and the kept copy shares no provider id."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from emby_dedupe.api import deduplication as dd
from emby_dedupe.api import watch_history as wh
from emby_dedupe.utils.providers import shares_provider_id

USERS = [{"Id": "u1", "Name": "alice"}, {"Id": "u2", "Name": "bob"}]


def response(data):
    resp = MagicMock()
    resp.json.return_value = data
    return resp


def fake_emby(played: dict[str, set[str]]):
    """make_http_request stand-in: /Users lists USERS, /Users/{u}/Items/{i} says if u played i."""
    def request(_client, _method, url, **_kw):
        if url.endswith("/Users"):
            return response(USERS)
        user, item = url.split("/Users/")[1].split("/Items/")
        return response({"UserData": {"Played": user in played.get(item, set())}})
    return request


class TestSharesProviderId:
    def test_any_common_id_counts_case_insensitively(self):
        assert shares_provider_id({"Imdb": "tt1"}, {"IMDB": "TT1", "Tmdb": "5"})
        assert shares_provider_id({"Tvdb": "9"}, {"tvdb": "9"})

    def test_different_or_missing_ids(self):
        assert not shares_provider_id({"Imdb": "tt1"}, {"Imdb": "tt2"})
        assert not shares_provider_id({"Imdb": "tt1"}, {"Tmdb": "5"})
        assert not shares_provider_id(None, {"Imdb": "tt1"})
        assert not shares_provider_id({"Imdb": ""}, {"Imdb": ""})


class TestWatchedCopyReason:
    def test_watched_copy_without_a_shared_id_is_held(self):
        reason = wh.watched_copy_reason({"played_by": ["alice"], "provider_ids": {"Imdb": "tt2"}},
                                        {"provider_ids": {"Imdb": "tt1"}})
        assert reason.startswith(f"{wh.WATCHED_COPY}: alice watched the copy to delete")

    def test_shared_id_or_nobody_watched_passes(self):
        assert wh.watched_copy_reason({"played_by": ["alice"], "provider_ids": {"Tvdb": "9"}},
                                      {"provider_ids": {"Tvdb": "9"}}) is None
        assert wh.watched_copy_reason({"played_by": [], "provider_ids": {}}, {"provider_ids": {}}) is None
        assert wh.watched_copy_reason({"provider_ids": {}}, {"provider_ids": {}}) is None


class TestAnnotateWatchedCopies:
    def test_reads_only_pairs_without_a_shared_id(self, mocker):
        request = mocker.patch.object(wh, "make_http_request", side_effect=fake_emby({"d2": {"u1"}}))
        shared = {"id": "d1", "provider_ids": {"Imdb": "tt1"}}
        unshared = {"id": "d2", "provider_ids": {}}
        decisions = [{"keep": {"provider_ids": {"Imdb": "tt1"}}, "delete": [shared, unshared]}]
        assert wh.annotate_watched_copies(MagicMock(), "http://emby", decisions) == 1
        assert unshared["played_by"] == ["alice"]
        assert "played_by" not in shared
        urls = [c.args[2] for c in request.call_args_list]
        assert "http://emby/Users/u1/Items/d2" in urls
        assert not any("/Items/d1" in u for u in urls)
        assert not any("Ids=" in u for u in urls)

    def test_no_candidates_makes_no_request(self, mocker):
        request = mocker.patch.object(wh, "make_http_request")
        decisions = [{"keep": {"provider_ids": {"Tvdb": "9"}}, "delete": [{"id": "d", "provider_ids": {"Tvdb": "9"}}]}]
        assert wh.annotate_watched_copies(MagicMock(), "http://emby", decisions) == 0
        request.assert_not_called()

    def test_unreadable_state_holds_the_delete(self, mocker):
        def failing(_client, _method, url, **_kw):
            if url.endswith("/Users"):
                return response(USERS)
            raise httpx.ConnectError("down")
        mocker.patch.object(wh, "make_http_request", side_effect=failing)
        item = {"id": "d", "provider_ids": {}}
        wh.annotate_watched_copies(MagicMock(), "http://emby", [{"keep": {}, "delete": [item]}])
        assert item["played_by"] == [wh.UNREADABLE]
        assert wh.watched_copy_reason(item, {}) is not None

    def test_user_list_failure_holds_every_candidate(self, mocker):
        mocker.patch.object(wh, "make_http_request", side_effect=httpx.ConnectError("down"))
        items = [{"id": "a", "provider_ids": {}}, {"id": "b", "provider_ids": {}}]
        assert wh.annotate_watched_copies(MagicMock(), "http://emby", [{"keep": {}, "delete": items}]) == 0
        assert all(i["played_by"] == [wh.UNREADABLE] for i in items)


class TestGuardIntegration:
    def test_refusal_reason_includes_the_watched_copy_hold(self):
        item = {"id": "d", "path": "/m/A (2010)/A.mkv", "provider_ids": {}, "played_by": ["alice"],
                "runtime_ticks": 60_000_000_000}
        keeper = {"path": "/m/A (2010) 4K/A.mkv", "provider_ids": {"Imdb": "tt1"}, "runtime_ticks": 60_000_000_000}
        reason = dd._refusal_reason(item, keeper)
        assert reason is not None
        assert reason.startswith(wh.WATCHED_COPY)

    def test_dry_run_marks_the_pair_would_block(self, mocker):
        mocker.patch.object(wh, "make_http_request", side_effect=fake_emby({"d": {"u2"}}))
        mocker.patch.object(dd.markdown, "format_markdown_table", return_value="report")
        item = {"id": "d", "path": "/m/A (2010)/A.mkv", "provider_ids": {}, "runtime_ticks": 60_000_000_000}
        keeper = {"id": "k", "path": "/m/A (2010) 4K/A.mkv", "provider_ids": {"Imdb": "tt1"},
                  "runtime_ticks": 60_000_000_000}
        decisions = [{"keep": keeper, "delete": [item]}]
        dd.process_deletion_and_generate_report(MagicMock(), "http://emby", decisions, False, "u", "p", "k")
        assert item["played_by"] == ["bob"]
        assert item["deletion_result"]["status"] == "would_block"
        assert item["deletion_result"]["error"].startswith(wh.WATCHED_COPY)

    def test_doit_keeps_both_copies(self, mocker):
        mocker.patch.object(wh, "make_http_request", side_effect=fake_emby({"d": {"u1"}}))
        mocker.patch.object(dd.markdown, "format_markdown_table", return_value="report")
        delete = mocker.patch.object(dd, "delete_item")
        item = {"id": "d", "path": "/m/A (2010)/A.mkv", "provider_ids": {}, "runtime_ticks": 60_000_000_000}
        keeper = {"id": "k", "path": "/m/A (2010) 4K/A.mkv", "provider_ids": {"Imdb": "tt1"},
                  "runtime_ticks": 60_000_000_000}
        dd.process_deletion_and_generate_report(MagicMock(), "http://emby", [{"keep": keeper, "delete": [item]}],
                                                True, "u", "p", "k")
        delete.assert_not_called()
        assert item["deletion_result"]["status"] == "skipped_unsafe"
