"""Tests for poster inlining — the API key must never reach a report file."""
import base64
from unittest import mock

import httpx

from emby_dedupe.reports.images import (
    MAX_INLINE_BYTES,
    extract_api_key,
    inline_poster_urls,
    strip_credentials,
)

# Obviously-fake placeholder. NEVER paste a real key here — this file is public.
KEY = "abc123def4560000000000000000abcd"
URL = f"https://emby.example.com/Items/42/Images/Primary?maxWidth=200&api_key={KEY}"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def _client_returning(content=PNG, content_type="image/jpeg", status=200):
    """Patch httpx.Client so no network is touched, capturing the request headers."""
    captured = {}

    class _Resp:
        def __init__(self):
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            if status >= 400:
                raise httpx.HTTPStatusError("boom", request=None, response=None)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp()

    return _Client, captured


# ---- credential stripping ---------------------------------------------------

def test_strip_credentials_removes_api_key_keeps_other_params():
    out = strip_credentials(URL)
    assert "api_key" not in out
    assert KEY not in out
    assert "maxWidth=200" in out


def test_strip_credentials_handles_variants_and_no_query():
    assert "tok" not in strip_credentials("https://h/x?X-Emby-Token=tok")
    assert strip_credentials("https://h/x") == "https://h/x"
    assert strip_credentials("") == ""


def test_extract_api_key():
    assert extract_api_key(URL) == KEY
    assert extract_api_key("https://h/x") is None
    assert extract_api_key("") is None


# ---- inlining ---------------------------------------------------------------

def test_poster_is_embedded_and_key_moves_to_a_header():
    client, captured = _client_returning()
    with mock.patch("httpx.Client", client):
        out = inline_poster_urls([URL])

    assert out[URL].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(out[URL].split(",", 1)[1]) == PNG
    # the key travelled as a header, never in the URL
    assert captured["headers"]["X-Emby-Token"] == KEY
    assert "api_key" not in captured["url"]


def test_failed_fetch_falls_back_to_url_without_the_key():
    client, _ = _client_returning(status=500)
    with mock.patch("httpx.Client", client):
        out = inline_poster_urls([URL])

    assert not out[URL].startswith("data:")
    assert KEY not in out[URL], "a failed fetch must not leak the key into the report"


def test_oversized_and_non_image_payloads_are_not_embedded():
    for content, ctype in ((b"y" * (MAX_INLINE_BYTES + 1), "image/jpeg"), (b"<html>", "text/html")):
        client, _ = _client_returning(content=content, content_type=ctype)
        with mock.patch("httpx.Client", client):
            out = inline_poster_urls([URL])
        assert not out[URL].startswith("data:")
        assert KEY not in out[URL]


def test_urls_without_a_credential_are_left_alone():
    placeholder = "https://emby.example.com/web/assets/img/media.png"
    assert inline_poster_urls([placeholder]) == {placeholder: placeholder}


def test_empty_input_does_no_work():
    assert inline_poster_urls([]) == {}
    assert inline_poster_urls(["", None]) == {}


def test_duplicate_urls_are_fetched_once():
    calls = []

    client, captured = _client_returning()
    orig_get = client.get

    def counting_get(self, url, headers=None):
        calls.append(url)
        return orig_get(self, url, headers)

    client.get = counting_get
    with mock.patch("httpx.Client", client):
        inline_poster_urls([URL, URL, URL])
    assert len(calls) == 1


# ---- end-to-end: no credential may survive into a rendered report -----------

def test_rendered_dedupe_report_contains_no_api_key():
    """Render the REAL Jinja template and assert the live key is absent.

    This is the regression that matters: reports get opened and shared, and the scan
    builds poster URLs carrying ?api_key=<live key>.
    """
    from emby_dedupe.reports.html import format_html_report

    poster = f"http://emby.example.com/Items/1/Images/Primary?tag=t&api_key={KEY}"
    decisions = [
        {
            "keep": {
                "id": "1", "name": "Keeper", "serverid": "s1", "image_url": poster,
                "quality_description": {
                    "video": {"codec": "h265", "resolution": "4K"},
                    "audio": {"codec": "dts", "channels": 6, "languages": ["eng"]},
                    "date_added": "2026-01-15", "path": "/Movies/a/keep.mkv",
                },
            },
            "delete": [
                {
                    "id": "2", "name": "Dup", "image_url": poster.replace("Items/1", "Items/2"),
                    "deletion_result": {"status": "success", "error": None},
                    "quality_description": {
                        "video": {"codec": "h264", "resolution": "1080p"},
                        "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                        "date_added": "2026-01-01", "path": "/Movies/a/dup.mkv",
                    },
                }
            ],
        }
    ]

    client, _ = _client_returning()
    with mock.patch("httpx.Client", client):
        html = format_html_report("http://emby.example.com", decisions)

    assert KEY not in html, "the live API key leaked into the rendered report"
    assert "api_key=" not in html
    assert "data:image/jpeg;base64," in html, "posters should be embedded"


def test_rendered_report_has_no_key_even_when_posters_fail_to_load():
    from emby_dedupe.reports.html import format_html_report

    poster = f"http://emby.example.com/Items/1/Images/Primary?api_key={KEY}"
    decisions = [
        {
            "keep": {
                "id": "1", "name": "Keeper", "serverid": "s1", "image_url": poster,
                "quality_description": {
                    "video": {"codec": "h265", "resolution": "4K"},
                    "audio": {"codec": "dts", "channels": 6, "languages": ["eng"]},
                    "date_added": "2026-01-15", "path": "/Movies/a/keep.mkv",
                },
            },
            "delete": [],
        }
    ]

    client, _ = _client_returning(status=500)
    with mock.patch("httpx.Client", client):
        html = format_html_report("http://emby.example.com", decisions)

    assert KEY not in html
    assert "api_key=" not in html
