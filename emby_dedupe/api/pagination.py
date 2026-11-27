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

from emby_dedupe.utils.exceptions import EmbyReadError
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger


def paginate_emby_items(
    client: httpx.Client,
    endpoint: str,
    params: dict,
    *,
    error_context: str = "fetch items",
    raise_on_error: bool = False,
    complete: bool = False,
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
        raise_on_error: When True, re-raise a request failure instead of logging
            and stopping. Use for callers whose partial result must not be treated
            as complete (e.g. the provider-table build aborts the library on error).
        complete: When True, the walk yields every record or raises
            :class:`EmbyReadError`: on a failed request, on a page whose
            ``TotalRecordCount`` differs from the first page's, and when the number
            of records read differs from that total. Pages are read until one is
            shorter than ``Limit`` (which ``params`` must set), so a reported total
            that is too low is noticed as well.

    Yields:
        Tuples of (page items, total record count), one per page.
    """
    if complete:
        yield from _paginate_complete(client, endpoint, params, error_context)
        return

    start_index = 0
    while True:
        page_params = {**params, "StartIndex": str(start_index)}
        try:
            data = make_http_request(client, "GET", endpoint, params=page_params).json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            if raise_on_error:
                raise
            logger.error(f"Failed to {error_context}: {e}")
            return

        page_items = data.get("Items", [])
        total = data.get("TotalRecordCount", 0)
        yield page_items, total

        start_index += len(page_items)
        if start_index >= total or not page_items:
            return


def _paginate_complete(
    client: httpx.Client,
    endpoint: str,
    params: dict,
    error_context: str,
) -> Iterator[tuple[list[dict], int]]:
    """Walk every page and raise :class:`EmbyReadError` unless all records were read.

    The walk does not stop at ``TotalRecordCount``: it reads until a page comes back
    shorter than ``Limit``, then compares the number of records read with the total
    the first page reported.
    """
    limit = int(params.get("Limit") or 0)
    if limit <= 0:
        raise ValueError("a complete walk needs a positive Limit")
    start_index = 0
    expected: int | None = None
    while True:
        page_params = {**params, "StartIndex": str(start_index)}
        try:
            data = make_http_request(client, "GET", endpoint, params=page_params).json()
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
            raise EmbyReadError(f"could not {error_context}: {e}") from e

        page_items = data.get("Items", [])
        total = data.get("TotalRecordCount", 0)
        if expected is None:
            expected = total
        elif total != expected:
            raise EmbyReadError(
                f"could not {error_context}: the reported total changed from {expected} to {total} during the read"
            )
        yield page_items, total

        start_index += len(page_items)
        if len(page_items) < limit:
            if start_index != expected:
                raise EmbyReadError(f"could not {error_context}: read {start_index} records, Emby reported {expected}")
            return
