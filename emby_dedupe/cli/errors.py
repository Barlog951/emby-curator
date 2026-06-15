"""Shared error boundary for CLI command entry points.

Every command translates the same expected failures — Emby connection errors,
JSON decode errors, HTTP timeouts, and an unexpected-error catch-all — into a
logged message and ``exit(1)``. This context manager is the single definition
of that behaviour, replacing the identical except-ladder that was copy-pasted
across command entry points.
"""

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.logging import logger


@contextmanager
def cli_error_boundary(unexpected_context: str = "") -> Iterator[None]:
    """Log and ``exit(1)`` on the failures common to every CLI command.

    ``SystemExit`` raised inside the block (e.g. an explicit ``sys.exit`` on a
    validation failure) is *not* caught — it derives from ``BaseException``, not
    ``Exception`` — so deliberate early exits propagate unchanged.

    Args:
        unexpected_context: Optional phrase appended to the catch-all message,
            e.g. "during missing episodes search".
    """
    try:
        yield
    except EmbyServerConnectionError as e:
        logger.error(str(e))
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON: {str(e)}")
        sys.exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {str(e)}")
        sys.exit(1)
    except Exception as e:
        suffix = f" {unexpected_context}" if unexpected_context else ""
        logger.error(f"An unexpected error occurred{suffix}: {str(e)}")
        logger.error(e)
        sys.exit(1)
