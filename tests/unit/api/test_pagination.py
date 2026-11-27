"""Tests for the shared Emby pagination helper (api.pagination)."""

from unittest.mock import Mock, patch

import httpx
import pytest

from emby_dedupe.api.pagination import paginate_emby_items
from emby_dedupe.utils.exceptions import EmbyReadError


def _page(items, total):
    resp = Mock()
    resp.json.return_value = {"Items": items, "TotalRecordCount": total}
    return resp


class TestPaginateEmbyItems:
    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_walks_pages_until_total(self, mock_req):
        mock_req.side_effect = [
            _page([{"Id": "1"}, {"Id": "2"}], 3),
            _page([{"Id": "3"}], 3),
        ]
        pages = list(paginate_emby_items(Mock(), "http://emby/Items", {"Limit": "2"}))
        assert [len(items) for items, _ in pages] == [2, 1]
        assert mock_req.call_count == 2

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_empty_page_guard_stops(self, mock_req):
        # TotalRecordCount lies (100) but an empty page stops iteration — no infinite loop.
        mock_req.side_effect = [_page([{"Id": "1"}], 100), _page([], 100)]
        pages = list(paginate_emby_items(Mock(), "http://emby/Items", {}))
        assert len(pages) == 2
        assert mock_req.call_count == 2

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_default_swallows_error_and_stops(self, mock_req):
        mock_req.side_effect = httpx.RequestError("boom")
        assert list(paginate_emby_items(Mock(), "http://emby/Items", {})) == []

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_raise_on_error_reraises(self, mock_req):
        """The opt-in raise contract: callers that must not treat a partial result as
        complete (provider-table build) pass raise_on_error=True."""
        mock_req.side_effect = httpx.RequestError("boom")
        pages = paginate_emby_items(Mock(), "http://emby/Items", {}, raise_on_error=True)  # a generator: nothing runs yet
        with pytest.raises(httpx.RequestError):
            list(pages)

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_raise_on_error_reraises_after_partial_pages(self, mock_req):
        """First page succeeds, second raises — with raise_on_error the failure surfaces
        rather than silently returning the partial first page."""
        mock_req.side_effect = [_page([{"Id": "1"}], 100), httpx.RequestError("mid")]
        gen = paginate_emby_items(Mock(), "http://emby/Items", {}, raise_on_error=True)
        assert next(gen)[0] == [{"Id": "1"}]  # first page yielded
        with pytest.raises(httpx.RequestError):
            next(gen)  # second page raises


class TestCompleteWalk:
    """complete=True: every record or EmbyReadError (the reads that protect items from cleanup)."""

    URL = "http://emby/Items"

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_full_walk_passes(self, mock_req):
        mock_req.side_effect = [_page([{"Id": "1"}, {"Id": "2"}], 3), _page([{"Id": "3"}], 3)]
        pages = list(paginate_emby_items(Mock(), self.URL, {"Limit": "2"}, complete=True))
        assert [len(items) for items, _ in pages] == [2, 1]
        assert mock_req.call_count == 2

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_exact_multiple_reads_one_empty_page_and_passes(self, mock_req):
        mock_req.side_effect = [_page([{"Id": "1"}, {"Id": "2"}], 4), _page([{"Id": "3"}, {"Id": "4"}], 4), _page([], 4)]
        pages = list(paginate_emby_items(Mock(), self.URL, {"Limit": "2"}, complete=True))
        assert sum(len(items) for items, _ in pages) == 4
        assert mock_req.call_args.kwargs["params"]["StartIndex"] == "4"

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_too_low_total_is_noticed(self, mock_req):
        """A total one below the truth: the walk reads past it and refuses the mismatch."""
        mock_req.side_effect = [_page([{"Id": "1"}, {"Id": "2"}], 2), _page([{"Id": "3"}], 2)]
        walk = paginate_emby_items(Mock(), self.URL, {"Limit": "2"}, error_context="fetch x", complete=True)
        with pytest.raises(EmbyReadError, match="read 3 records, Emby reported 2"):
            list(walk)

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_fewer_records_than_the_total_raise(self, mock_req):
        mock_req.side_effect = [_page([{"Id": "1"}], 5)]
        walk = paginate_emby_items(Mock(), self.URL, {"Limit": "2"}, complete=True)
        with pytest.raises(EmbyReadError, match="read 1 records, Emby reported 5"):
            list(walk)

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_total_changing_between_pages_raises(self, mock_req):
        mock_req.side_effect = [_page([{"Id": "1"}, {"Id": "2"}], 3), _page([{"Id": "3"}], 4)]
        walk = paginate_emby_items(Mock(), self.URL, {"Limit": "2"}, complete=True)
        with pytest.raises(EmbyReadError, match="changed from 3 to 4"):
            list(walk)

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_request_error_raises_read_error(self, mock_req):
        mock_req.side_effect = [_page([{"Id": "1"}], 3), httpx.RequestError("boom")]
        walk = paginate_emby_items(Mock(), self.URL, {"Limit": "1"}, error_context="fetch x", complete=True)
        with pytest.raises(EmbyReadError, match="could not fetch x: boom") as err:
            list(walk)
        assert isinstance(err.value.__cause__, httpx.RequestError)

    def test_needs_a_limit(self):
        walk = paginate_emby_items(Mock(), self.URL, {}, complete=True)
        with pytest.raises(ValueError, match="Limit"):
            list(walk)
