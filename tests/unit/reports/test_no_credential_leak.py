"""No generated report may contain a live Emby API key.

Regression for 2026-08-02. A real dedupe report was found carrying the **live**
``?api_key=<key>`` in three poster URLs, months after `3948610` supposedly fixed this.

Why the earlier fix and its 196 tests missed it: :mod:`emby_dedupe.reports.images` is
correct — every one of its return paths strips the credential. The defect was in the
*caller*. ``_inline_group_posters`` enumerated ``duplicate_groups`` only, so the
"Excluded Media" section (rendered from ``metadata["excluded_titles"]``) never reached
the inliner and kept its raw URL. A test at the images-module layer cannot catch a
section the caller never passes it.

So these tests assert on the **rendered HTML string** from the real template, which is
the only layer where "no report contains a credential" is actually a true statement.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from emby_dedupe.reports.html import format_html_report
from emby_dedupe.reports.images import inline_images_in_place

# Same allowlisted placeholder shape as test_images.py. A 32-hex literal assigned to a
# KEY/SECRET/TOKEN name is exactly what the gitleaks 'bare-key-assignment' rule hunts for,
# so it must start with the known-fake prefix or the scanner flags this file.
SECRET = "abc123def4560000000000000000feed"
BASE = "https://emby.example.com:443"


def _poster(item_id: str) -> str:
    return f"{BASE}/Items/{item_id}/Images/Primary?tag=abc&quality=90&maxHeight=300&api_key={SECRET}"


def _decisions() -> list[dict]:
    return [
        {
            "keep": {
                "id": "keep1",
                "name": "Keep Item",
                "serverid": "server1",
                "image_url": _poster("keep1"),
                "quality_description": {
                    "video": {"codec": "h265", "resolution": "4K"},
                    "audio": {"codec": "dts", "channels": 6, "languages": ["eng"]},
                    "date_added": "2023-01-15",
                },
            },
            "delete": [
                {
                    "id": "del1",
                    "name": "Delete Item",
                    "image_url": _poster("del1"),
                    "deletion_result": {"status": "success", "error": None},
                    "quality_description": {
                        "video": {"codec": "h264", "resolution": "1080p"},
                        "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                        "date_added": "2022-12-31",
                    },
                }
            ],
        }
    ]


def _metadata_with_excluded() -> dict:
    """Metadata carrying the section that leaked: excluded media with posters."""
    return {
        "excluded_ids": ["tt0120737", "tt0167260"],
        "excluded_groups_count": 2,
        "excluded_titles": {
            "tt0120737": {
                "title": "The Fellowship of the Ring",
                "year": 2001,
                "image_url": _poster("21402814"),
            },
            "tt0167260": {
                "title": "The Return of the King",
                "year": 2003,
                "image_url": _poster("21402866"),
            },
        },
    }


@pytest.fixture
def failing_fetch():
    """Poster fetches all fail — the fallback must still be credential-free."""
    with patch("emby_dedupe.reports.images.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = httpx.ConnectError("no network in tests")
        yield


@pytest.fixture
def succeeding_fetch():
    """Poster fetches succeed — the URL is replaced by a data: URI."""
    with patch("emby_dedupe.reports.images.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.content = b"\xff\xd8\xff\xe0JPEGDATA"
        response.headers = {"content-type": "image/jpeg"}
        response.raise_for_status = MagicMock()
        client.get.return_value = response
        yield


def test_excluded_media_posters_do_not_leak_the_key(failing_fetch):
    """The exact 2026-08-02 leak: the excluded-media section kept ?api_key=<live key>."""
    html = format_html_report(BASE, _decisions(), _metadata_with_excluded())

    assert SECRET not in html
    assert "api_key" not in html


def test_no_credential_survives_when_posters_inline_successfully(succeeding_fetch):
    """The success path must also be clean, and must actually embed the poster."""
    html = format_html_report(BASE, _decisions(), _metadata_with_excluded())

    assert SECRET not in html
    assert "api_key" not in html
    assert "data:image/jpeg;base64," in html


def test_inline_images_in_place_walks_arbitrary_nesting(failing_fetch):
    """A future template section must be covered without anyone remembering to add it."""
    context = {
        "groups": [{"keep": {"image_url": _poster("a")}}],
        "excluded_titles": {"tt1": {"image_url": _poster("b")}},
        "some": {"future": {"section": [{"image_url": _poster("c")}]}},
        "not_an_image": "leave me alone",
        "empty": {"image_url": ""},
    }

    inline_images_in_place(context, SECRET)

    found = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("image_url"), str):
                found.append(node["image_url"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(context)
    assert found, "expected to find image_url values"
    assert all(SECRET not in url for url in found)
    assert all("api_key" not in url for url in found)
    assert context["not_an_image"] == "leave me alone"


def test_cleanup_report_posters_do_not_leak_the_key(failing_fetch):
    """The cleanup report builds its own poster URLs with the key; it must scrub them too."""
    from emby_dedupe.models.cleanup import CleanupCandidate, CleanupConfig
    from emby_dedupe.reports.cleanup import _generate_cleanup_html_report

    candidate = CleanupCandidate(
        item_id="900",
        name="Stale Movie",
        year=2005,
        rating=4.0,
        critic_rating=None,
        threshold=5.0,
        age_years=19.0,
        library="Movies",
        size_bytes=1_000_000,
        path="/Movies/Stale Movie (2005)/Stale Movie (2005).mkv",
    )

    html = _generate_cleanup_html_report(
        base_url=BASE,
        candidates=[candidate],
        protection_stats={},
        config=CleanupConfig(),
        doit=False,
        server_id="srv",
        api_key=SECRET,
    )

    assert SECRET not in html
    assert "api_key" not in html
