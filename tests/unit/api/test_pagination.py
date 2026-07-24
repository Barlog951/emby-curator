"""Tests for the shared Emby pagination helper (api.pagination)."""

from unittest.mock import Mock, patch

import httpx
import pytest

from emby_dedupe.api.pagination import paginate_emby_items


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
        with pytest.raises(httpx.RequestError):
            list(paginate_emby_items(Mock(), "http://emby/Items", {}, raise_on_error=True))

    @patch("emby_dedupe.api.pagination.make_http_request")
    def test_raise_on_error_reraises_after_partial_pages(self, mock_req):
        """First page succeeds, second raises — with raise_on_error the failure surfaces
        rather than silently returning the partial first page."""
        mock_req.side_effect = [_page([{"Id": "1"}], 100), httpx.RequestError("mid")]
        gen = paginate_emby_items(Mock(), "http://emby/Items", {}, raise_on_error=True)
        assert next(gen)[0] == [{"Id": "1"}]  # first page yielded
        with pytest.raises(httpx.RequestError):
            next(gen)  # second page raises
