"""Tests for emby_dedupe.api.item_images — poster upload contract."""
from __future__ import annotations

import base64

import httpx

from emby_dedupe.api.item_images import upload_primary_image


def test_upload_posts_base64_body_with_image_content_type():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"X-Emby-Token": "k"})
    assert upload_primary_image(client, "http://emby:8096", "42", b"\xff\xd8\xff", "image/jpeg")
    assert seen["url"] == "http://emby:8096/Items/42/Images/Primary"
    assert seen["content_type"] == "image/jpeg"
    assert seen["body"] == base64.b64encode(b"\xff\xd8\xff")


def test_upload_reports_failure_on_rejection_and_transport_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert upload_primary_image(client, "http://emby:8096", "42", b"x") is False

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    assert upload_primary_image(httpx.Client(transport=httpx.MockTransport(down)),
                                "http://emby:8096", "42", b"x") is False
