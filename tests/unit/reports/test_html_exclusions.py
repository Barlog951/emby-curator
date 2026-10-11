"""The dedupe report must stay readable when many provider IDs are excluded.

Regression for 2026-10-11: 48 episode IDs were added to ``--exclude-ids`` (pairs reviewed
as "keep both", e.g. a Czech TV cut next to the complete English episode). The report then
opened with a wall of 52 IDs in the summary banner, and the "Excluded Media" section
rendered one card per configured ID ("Excluded Media ID: 9514669" x 52), whether or not
that ID matched anything in the run.

Asserted on the rendered HTML from the real template, like test_no_credential_leak.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from emby_dedupe.reports.html import format_html_report

BASE = "https://emby.example.com:443"

MANY_IDS = [f"tt{n:08d}" for n in range(30)] + [str(9_500_000 + n) for n in range(22)]


def _decision() -> list[dict]:
    return [
        {
            "keep": {
                "id": "keep1",
                "name": "Keep Item",
                "serverid": "server1",
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


def _metadata(ids: list[str], titles: dict | None = None) -> dict:
    titles = titles or {}
    return {"excluded_ids": ids, "excluded_groups_count": len(titles), "excluded_titles": titles}


@pytest.fixture(autouse=True)
def _no_network():
    """Poster inlining must not reach the network in tests."""
    with patch("emby_dedupe.reports.images.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("offline")
        yield


def _banner(html: str) -> str:
    start = html.index('<div class="exclusion-banner">')
    return html[start:html.index("</div>", start)]


def test_banner_shows_a_count_and_folds_the_ids_away():
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS))
    banner = _banner(html)

    assert "<strong>52</strong> provider IDs excluded from deduplication" in banner
    assert '<details class="exclusion-ids">' in banner
    # Every ID is still in the report, but inside the collapsed <details>, not the headline.
    details = banner[banner.index("<details"):]
    assert all(pid in details for pid in MANY_IDS)
    assert "The following provider IDs were excluded" not in html


def test_banner_mentions_groups_skipped_this_run():
    titles = {"tt00000001": {"title": "Call the Midwife", "year": 2012, "image_url": ""}}
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS, titles))

    assert "(1 duplicate group skipped this run)" in _banner(html)


def test_banner_singular_wording_for_one_id():
    html = format_html_report(BASE, _decision(), _metadata(["tt0120737"]))

    assert "<strong>1</strong> provider ID excluded from deduplication" in _banner(html)


def test_excluded_section_renders_only_groups_that_were_skipped():
    titles = {
        "tt00000001": {"title": "Call the Midwife", "year": 2012, "image_url": ""},
        "9500003": {"title": "Imma Tataranni", "year": 2019, "image_url": ""},
    }
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS, titles))

    assert html.count("<h2>Excluded Media: ") == 2
    assert "Excluded Media: Call the Midwife" in html
    assert "Excluded Media: Imma Tataranni" in html
    assert "Excluded Media ID:" not in html
    assert "<strong>2</strong> duplicate groups skipped because of an excluded provider ID." in html
    # The cards are folded away: they repeat every run, so they must not fill the report.
    section = html[html.index('<details class="excluded-groups">'):]
    assert "<summary>Show the 2 skipped groups</summary>" in section
    assert section.index("Excluded Media: Call the Midwife") < section.index("</details>")


def test_no_excluded_section_when_no_group_was_skipped():
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS))

    assert "Excluded Media (Not Deduplicated)" not in html
    assert "<h2>Excluded Media: " not in html


def test_no_banner_without_exclusions():
    html = format_html_report(BASE, _decision(), _metadata([]))

    assert 'class="exclusion-banner"' not in html
