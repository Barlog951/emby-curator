"""A dry-run report must say what --doit would really do.

Regression for 2026-10-11. The dry run promised 30 deletions and 42 GB; the real run three
minutes later deleted 5 and blocked 24 (runtime mismatches the guard refuses). The guard
already ran in the dry run, but its verdict only reached the log. Also, every dry-run
item was marked ``not_attempted`` and the statistics read that as "deletion attempted",
so the report said "Size Removed 0 B".
"""

from __future__ import annotations

from unittest.mock import patch

from emby_dedupe.api.deduplication import _mark_items_as_not_attempted, _warn_unsafe_deletions
from emby_dedupe.reports.common import calculate_report_statistics
from emby_dedupe.reports.html import format_html_report

BASE = "https://emby.example.com:443"
GB = 1024**3
MIN = 60 * 10_000_000  # Emby RunTimeTicks per minute


class _Bar:
    def update(self, _n):
        pass


def _item(item_id: str, path: str, minutes: float, size_gb: float) -> dict:
    return {
        "id": item_id, "name": item_id, "serverid": "s1", "path": path, "runtime_ticks": int(minutes * MIN),
        "quality_description": {"size": int(size_gb * GB), "path": path,
                                "video": {"codec": "h264", "resolution": "1080p"},
                                "audio": {"codec": "aac", "channels": 2, "languages": ["cze"]},
                                "date_added": "2026-10-01"},
    }


def _dry_run_decisions() -> list[dict]:
    """Group 1: same runtime (a real duplicate). Group 2: 52 vs 58 min (Czech TV cut)."""
    d = [
        {"keep": _item("k1", "/M/HD/Film A (2020)/Film A - 2160p.mkv", 92, 6),
         "delete": [_item("d1", "/M/DiViX/Film A (2020)/Film A - 1080p.mkv", 92, 2)]},
        {"keep": _item("k2", "/M/S/Show/S12/Show S12E01 - CZ.mkv", 52, 1.2),
         "delete": [_item("d2", "/M/S/Show/S12/Show S12E01 - HDTV.mkv", 58.4, 1.3)]},
    ]
    paths = [i["path"] for g in d for i in [g["keep"], *g["delete"]]]
    _warn_unsafe_deletions(d, paths, [g["delete"][0]["path"] for g in d])
    _mark_items_as_not_attempted(d, _Bar())
    return d


def test_dry_run_records_what_the_guard_would_refuse():
    d = _dry_run_decisions()

    assert d[0]["delete"][0]["deletion_result"]["status"] == "not_attempted"
    blocked = d[1]["delete"][0]["deletion_result"]
    assert blocked["status"] == "would_block"
    assert "duration-mismatch" in blocked["error"]


def test_dry_run_statistics_are_not_an_attempt_and_exclude_blocked_space():
    stats = calculate_report_statistics(_dry_run_decisions())

    assert stats["deletion_attempted"] is False
    assert stats["would_block"] == 1
    assert stats["would_delete"] == 1
    assert stats["space_saved"] == 2 * GB, "only the delete --doit will make counts"


def test_real_run_is_still_an_attempt():
    d = _dry_run_decisions()
    d[0]["delete"][0]["deletion_result"]["status"] = "success"

    assert calculate_report_statistics(d)["deletion_attempted"] is True


def test_dry_run_report_shows_the_blocked_pair_and_the_real_numbers():
    with patch("emby_dedupe.reports.images.inline_poster_urls", side_effect=lambda urls, *a, **k: {}):
        html = format_html_report(BASE, _dry_run_decisions())

    assert "Dry run: 1 file would be removed, 2.00 GB would be freed." in html
    assert "Would be blocked by the safety guard · 1 file" in html
    assert "2.00 GB would be freed." in html, "space to free must exclude the blocked delete"
    assert "GB freed.</h1>" not in html
    assert "Dry run · nothing deleted" in html


def _context(decisions: list[dict], metadata: dict | None = None) -> dict:
    from emby_dedupe.reports.html import build_report_context

    with patch("emby_dedupe.reports.images.inline_poster_urls", side_effect=lambda urls, *a, **k: {}):
        return build_report_context(BASE, decisions, metadata)


def test_real_run_with_nothing_to_delete_is_not_labelled_dry_run():
    """Regression 2026-10-21: a --doit run with nothing to delete was labelled "Dry run"."""
    ctx = _context([], {"run": {"doit": True}})

    assert ctx["run"]["dry"] is False
    assert ctx["run"]["mode"] == "Real run (--doit)"
    assert ctx["run_summary"]["dry"] is False


def test_doit_flag_false_is_a_dry_run_even_with_results():
    d = _dry_run_decisions()
    d[0]["delete"][0]["deletion_result"]["status"] = "success"

    ctx = _context(d, {"run": {"doit": False}})

    assert ctx["run"]["dry"] is True
    assert ctx["run_summary"]["dry"] is True


def test_without_the_doit_flag_the_mode_is_guessed_from_the_results():
    assert _context(_dry_run_decisions())["run"]["dry"] is True
    d = _dry_run_decisions()
    d[0]["delete"][0]["deletion_result"]["status"] = "success"
    assert _context(d)["run"]["dry"] is False
