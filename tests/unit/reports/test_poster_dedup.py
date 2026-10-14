"""Each distinct poster is embedded in the dedupe report once, not once per card.

Regression for 2026-10-11: a 333-group report was 29.6 MB, 27.5 MB of it posters — 670
inlined images of which only 336 were distinct (the kept and the deleted copy of a title
usually carry the same image). The posters now become CSS classes defined once.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from emby_dedupe.reports.html import format_html_report
from emby_dedupe.reports.images import share_inlined_images

BASE = "https://emby.example.com:443"
# Same allowlisted placeholder shape as test_no_credential_leak.py (gitleaks bare-key rule).
SECRET = "abc123def4560000000000000000feed"
PAYLOAD = b"\xff\xd8\xff\xe0SAMEPOSTERBYTES"


def _url(item_id: str) -> str:
    return f"{BASE}/Items/{item_id}/Images/Primary?tag=t&quality=80&maxHeight=300&api_key={SECRET}"


def _decisions() -> list[dict]:
    """Two groups whose four items all return the same poster bytes."""
    def item(item_id: str, **extra) -> dict:
        return {
            "id": item_id, "name": f"Item {item_id}", "serverid": "s1", "image_url": _url(item_id),
            "quality_description": {"video": {"codec": "h264", "resolution": "1080p"},
                                    "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                                    "date_added": "2026-10-01"},
            **extra,
        }
    return [
        {"keep": item("k1"), "delete": [item("d1", deletion_result={"status": "success", "error": None})]},
        {"keep": item("k2"), "delete": [item("d2", deletion_result={"status": "success", "error": None})]},
    ]


@pytest.fixture
def same_poster_everywhere():
    with patch("emby_dedupe.reports.images.httpx.Client") as client_cls:
        response = MagicMock()
        response.content = PAYLOAD
        response.headers = {"content-type": "image/jpeg"}
        response.raise_for_status = MagicMock()
        client_cls.return_value.__enter__.return_value.get.return_value = response
        yield


def test_identical_posters_are_embedded_once(same_poster_everywhere):
    import base64

    html = format_html_report(BASE, _decisions(), {})
    encoded = base64.b64encode(PAYLOAD).decode("ascii")

    assert html.count(encoded) == 1, "the same poster must be written once"
    assert html.count('class="thumb poster img-') == 2, "both rows still show it"
    assert SECRET not in html


def test_share_assigns_one_class_per_distinct_image():
    a = "data:image/jpeg;base64,QUFB"
    b = "data:image/png;base64,QkJC"
    ctx = {"groups": [{"image_url": a}, {"image_url": a}, {"image_url": b}]}

    css = share_inlined_images(ctx)
    classes = [h["image_class"] for h in ctx["groups"]]

    assert classes[0] == classes[1] != classes[2]
    assert css.count("background-image") == 2
    assert a in css
    assert b in css


def test_plain_url_keeps_an_img_tag():
    """A poster that could not be inlined has no class; the template falls back to <img>."""
    ctx = {"image_url": f"{BASE}/Items/1/Images/Primary?tag=t"}

    assert share_inlined_images(ctx) == ""
    assert "image_class" not in ctx


@pytest.mark.parametrize("hostile", [
    'data:image/jpeg;base64,QUFB");}body{display:none}/*',
    "data:image/svg+xml;utf8,<svg onload=alert(1)>",
    "data:text/html;base64,QUFB",
])
def test_only_plain_base64_images_reach_the_stylesheet(hostile):
    """The CSS is emitted unescaped, so anything that could close url("…") is refused."""
    ctx = {"image_url": hostile}

    assert share_inlined_images(ctx) == ""
    assert "image_class" not in ctx
