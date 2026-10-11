"""The report opens with a one-sentence verdict that is true for the run.

From the 2026-10-11 design review: the answer to "was it safe, what happened, what failed"
was spread over nine number tiles. The verdict counts files, the same unit as the sections.
"""

from __future__ import annotations

from unittest.mock import patch

from emby_dedupe.reports.html import format_html_report

BASE = "https://emby.example.com:443"
GB = 1024**3


def _group(gid, status, error=None, size_gb=1.0):
    def item(item_id, path, **extra):
        return {"id": item_id, "name": item_id, "serverid": "s1", "path": path, "rating": 50,
                "quality_description": {"size": int(size_gb * GB), "path": path,
                                        "video": {"codec": "h264", "resolution": "1080p"},
                                        "audio": {"codec": "aac", "channels": 2, "languages": ["cze"]},
                                        "date_added": "2026-10-01"}, **extra}
    keep = item(f"k{gid}", f"/M/HD/Film {gid}/Film {gid} - 2160p.mkv", rating=90)
    dele = item(f"d{gid}", f"/M/LQ/Film {gid}/Film {gid} - 1080p.mkv",
                deletion_result={"status": status, "error": error})
    return {"keep": keep, "delete": [dele]}


def _render(decisions, metadata=None):
    with patch("emby_dedupe.reports.images.inline_poster_urls", side_effect=lambda urls, *a, **k: {}):
        return format_html_report(BASE, decisions, metadata or {})


def test_real_run_verdict():
    html = _render([_group(1, "success"), _group(2, "success"),
                    _group(3, "skipped_unsafe", "duration-mismatch (keeper 1s vs delete 99s)")])

    assert "2 files removed, 2.00 GB freed." in html
    assert "1 more was kept by the safety check, and nothing failed." in html
    assert "Real run (--doit)" in html


def test_failures_are_named_in_the_verdict():
    html = _render([_group(1, "success"), _group(2, "failed", "HTTP 500")])

    assert "and 1 failed." in html
    assert 'class="run-seg seg-failed"' in html


def test_dry_run_verdict_counts_what_the_guard_will_refuse():
    html = _render([_group(1, "not_attempted"), _group(2, "would_block", "duration-mismatch (x)")])

    assert "Dry run: 1 file would be removed, 1.00 GB would be freed." in html
    assert "1 more would be kept by the safety check. Nothing was deleted." in html
    assert "Dry run · nothing deleted" in html


def test_empty_run_verdict():
    html = _render([], {"excluded_ids": ["tt1"], "excluded_groups_count": 27, "excluded_titles": {}})

    assert "No duplicates found." in html
    assert "27 duplicate groups skipped by your --exclude-ids." in html
    assert 'class="run-bar"' not in html


def test_bar_has_a_text_alternative_and_zero_states_are_outlined():
    html = _render([_group(1, "success")])

    assert 'aria-label="1 removed, 0 kept by safety check, 0 failed"' in html
    assert 'class="run-swatch seg-failed zero"' in html


def test_risky_rows_are_announced():
    """A kept copy scoring lower than the deleted one must be called out at the top."""
    decisions = [_group(1, "success")]
    decisions[0]["keep"]["rating"] = 10

    assert "1 row below needs a look" in _render(decisions)


def test_stylesheet_is_embedded_not_linked():
    html = _render([_group(1, "success")])

    assert '<link rel="stylesheet"' not in html
    assert ".run-summary {" in html
