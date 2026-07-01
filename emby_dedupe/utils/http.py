"""
HTTP utilities for making requests to the Emby server with retries and error handling.
"""

from typing import Any

import backoff
import httpx
from backoff._typing import Details
from httpx import HTTPStatusError, ReadTimeout, RequestError

from emby_dedupe.utils.constants import HTTP_TIMEOUT, MAX_BACKOFF_TIME, MAX_RETRIES
from emby_dedupe.utils.logging import logger

# Substrings found in a 500 response body that mean a PERMANENT server-side failure —
# e.g. Emby cannot delete a media file because the `emby` user lacks write permission on
# the containing folder. Retrying just hammers the same error (20x over ~10 min, which
# looks like a hang), so we give up immediately and let the caller skip+log the item.
_PERMANENT_500_MARKERS = (
    "permission denied",
    "access is denied",
    "unauthorizedaccess",
    "ioexception",
)


def should_give_up(e: Exception) -> bool:
    """
    Determine whether the given exception should stop retries.

    Args:
        e: The exception to check.
    Returns:
        bool: True if the exception indicates we should stop retrying.
    """
    if not isinstance(e, httpx.HTTPStatusError):
        return False  # timeouts / connection errors are transient → keep retrying
    status_code = e.response.status_code
    # Client errors (4xx) are never retried.
    if status_code < 500:
        return True
    # A 500 caused by a filesystem permission/IO error (a DELETE Emby can't perform
    # because it lacks write access to the folder) is permanent — give up instead of
    # retrying it ~20 times. Other 5xx (502/503/504) stay retryable (transient).
    if status_code == 500:
        try:
            body = str(e.response.text or "").lower()
            if any(marker in body for marker in _PERMANENT_500_MARKERS):
                return True
        except Exception:
            pass
    return False


def handle_giveup(details: Details) -> None:
    """
    A callback function that will be called when the retry loop has been
    terminated and is giving up.
    Args:
        details: Details about the retries that were attempted.
    """
    exc = details.get("exception")
    if isinstance(exc, httpx.HTTPStatusError):
        logger.error(
            f"Giving up after {details['tries']} tries: "
            f"HTTP {exc.response.status_code} {exc.request.url}"
        )
    else:
        logger.error(f"Giving up after {details['tries']} tries: {exc}")


@backoff.on_exception(
    backoff.expo,
    (httpx.HTTPStatusError, httpx.ReadTimeout, httpx.RequestError),
    max_tries=MAX_RETRIES,
    max_time=MAX_BACKOFF_TIME,
    giveup=should_give_up,
    on_giveup=handle_giveup,
)
def make_http_request(
    client: httpx.Client, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """
    Make an HTTP request using the given httpx.Client, equipped with exponential backoff
    and retry capabilities in case of certain exceptions.

    The exponential backoff policy will initiate a number of retries with increasing
    delay intervals if a `ReadTimeout` or other specified errors occur during the request.
    Args:
        client (httpx.Client): The HTTP client to use for making the request.
        method (str): The HTTP method to use (GET, POST, etc.)
        url (str): The URL to request.
        **kwargs: Additional arguments to pass to the request.
    Returns:
        httpx.Response: The HTTP response.
    Raises:
        httpx.HTTPStatusError: If the response is an HTTP error.
        httpx.ReadTimeout: If the request times out.
        httpx.RequestError: If there is an error making the request.
    """
    try:
        timeout = kwargs.pop(
            "timeout", HTTP_TIMEOUT
        )  # Use default if timeout is not specified
        response = client.request(method, url, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response
    except (HTTPStatusError, ReadTimeout, RequestError) as exc:
        logger.warning(f"Request failed: {exc}. Retrying...")
        raise
