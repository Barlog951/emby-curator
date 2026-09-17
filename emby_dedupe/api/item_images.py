"""Upload artwork to Emby items.

Emby's ``POST /Items/{Id}/Images/{Type}`` takes the image as a base64-encoded
body with the image's own MIME type as ``Content-Type``. The call needs an
administrator token, which the API key already is.
"""
from __future__ import annotations

import base64

import httpx

from emby_dedupe.utils.logging import logger


def upload_primary_image(
    client: httpx.Client,
    base_url: str,
    item_id: str,
    data: bytes,
    content_type: str = "image/jpeg",
) -> bool:
    """Set an item's poster (Primary image) from raw image bytes.

    Args:
        client: httpx client carrying the Emby auth header.
        base_url: Emby base URL (scheme, host, port).
        item_id: The Emby item to update.
        data: Raw image bytes.
        content_type: MIME type of ``data``.

    Returns:
        True when Emby accepted the upload.
    """
    try:
        resp = client.post(
            f"{base_url}/Items/{item_id}/Images/Primary",
            content=base64.b64encode(data),
            headers={"Content-Type": content_type},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        logger.error(f"Poster upload failed for {item_id}: {exc}")
        return False
    if not resp.is_success:
        logger.error(f"Poster upload rejected for {item_id}: HTTP {resp.status_code}")
        return False
    logger.info(f"Poster uploaded for {item_id} ({len(data)} bytes)")
    return True
