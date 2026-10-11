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


def _section(html: str) -> str:
    start = html.index('id="excluded"')
    return html[start:html.index("</section>", start)]


def test_excluded_section_shows_a_count_and_folds_the_ids_away():
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS))
    section = _section(html)

    assert "Skipped by --exclude-ids · 0 groups" in section
    assert "all 52 configured ids" in section
    assert '<details class="exclusion-ids">' in section
    # Every ID is still in the report, but inside the collapsed <details>, not the headline.
    details = section[section.index('<details class="exclusion-ids">'):]
    assert all(pid in details for pid in MANY_IDS)
    assert "The following provider IDs were excluded" not in html
    assert 'class="exclusion-banner"' not in html


def test_excluded_section_counts_groups_skipped_this_run():
    titles = {"tt00000001": {"title": "Call the Midwife", "year": 2012, "image_url": ""}}
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS, titles))

    assert "Skipped by --exclude-ids · 1 group" in _section(html)
    assert 'Excluded <strong>1</strong></a>' in html


def test_excluded_section_singular_wording_for_one_id():
    html = format_html_report(BASE, _decision(), _metadata(["tt0120737"]))

    assert "all 1 configured id<" in _section(html)


def test_excluded_section_renders_only_groups_that_were_skipped():
    titles = {
        "tt00000001": {"title": "Call the Midwife", "year": 2012, "image_url": ""},
        "9500003": {"title": "Imma Tataranni", "year": 2019, "image_url": ""},
    }
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS, titles))

    section = _section(html)
    assert section.count('<li><span class="thumb"') == 2
    assert "<strong>Call the Midwife</strong> (2012) · <code>tt00000001</code>" in section
    assert "<strong>Imma Tataranni</strong> (2019) · <code>9500003</code>" in section
    assert "Excluded Media ID:" not in html
    # The list is folded away: it repeats every run, so it must not fill the report.
    folded = section[section.index('<details class="excluded-groups">'):]
    assert "the 2 skipped groups</summary>" in folded
    assert folded.index("Call the Midwife") < folded.index("</details>")


def test_no_excluded_section_when_no_group_was_skipped():
    html = format_html_report(BASE, _decision(), _metadata(MANY_IDS))

    assert '<details class="excluded-groups">' not in html
    assert "Skipped by --exclude-ids · 0 groups" in html


def test_no_banner_without_exclusions():
    html = format_html_report(BASE, _decision(), _metadata([]))

    assert 'class="exclusion-banner"' not in html
    assert "No ids excluded." in html
    assert "No ids excluded." in html
