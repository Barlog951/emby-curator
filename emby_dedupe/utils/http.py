"""
HTTP utilities for making requests to the Emby server with retries and error handling.
"""

from typing import Any, Dict

import backoff
import httpx
from httpx import HTTPStatusError, ReadTimeout, RequestError

from emby_dedupe.utils.constants import HTTP_TIMEOUT, MAX_BACKOFF_TIME, MAX_RETRIES
from emby_dedupe.utils.logging import logger


def should_give_up(e: Exception) -> bool:
    """
    Determine whether the given exception should stop retries.

    Args:
        e: The exception to check.
    Returns:
        bool: True if the exception indicates we should stop retrying.
    """
    # Client errors (4xx) should not be retried
    is_client_error = (
        isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500
    )
    return is_client_error


def handle_giveup(details: Dict[str, Any]) -> None:
    """
    A callback function that will be called when the retry loop has been
    terminated and is giving up.
    Args:
        details: Details about the retries that were attempted.
    """
    logger.error(f"Giving up on request after retries: {details['tries']}")


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
