"""Shared pagination for Emby ``/Items`` list endpoints.

Emby paginates list endpoints with ``StartIndex``/``Limit`` and reports the full
result count in ``TotalRecordCount``. This generator centralises the
StartIndex advancement, stop condition, and error handling that was previously
copy-pasted across the cleanup and genre fetchers (five near-identical
``while True`` loops). There is no mature official Emby Python SDK, so the right
fix is one internal helper rather than a third-party package.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger


def paginate_emby_items(
    client: httpx.Client,
    endpoint: str,
    params: dict,
    *,
    error_context: str = "fetch items",
) -> Iterator[tuple[list[dict], int]]:
    """Yield ``(page_items, total_record_count)`` for each page of an Emby endpoint.

    ``StartIndex`` is advanced internally. Iteration stops when all records have
    been consumed, a page comes back empty, or a request fails — on failure it
    logs ``error_context`` and stops, preserving any pages already yielded
    (partial results), matching the previous hand-rolled loops. The empty-page
    guard also closes a latent infinite-loop risk when an endpoint reports a
    larger ``TotalRecordCount`` than it actually returns.

    Args:
        client: Configured httpx client with auth headers.
        endpoint: Emby Items endpoint URL.
        params: Query parameters; ``StartIndex`` is set per page (the caller is
            responsible for ``Limit``/``ParentId``/etc.).
        error_context: Phrase for the error log, e.g.
            ``"fetch episodes from library 5"``.

    Yields:
        Tuples of (page items, total record count), one per page.
    """
    start_index = 0
    while True:
        page_params = {**params, "StartIndex": str(start_index)}
        try:
            data = make_http_request(client, "GET", endpoint, params=page_params).json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error(f"Failed to {error_context}: {e}")
            return

        page_items = data.get("Items", [])
        total = data.get("TotalRecordCount", 0)
        yield page_items, total

        start_index += len(page_items)
        if start_index >= total or not page_items:
            return
