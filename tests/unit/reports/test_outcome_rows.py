"""The report's outcome rows show every file's evidence and never hide a risky deletion.

From the 2026-10-11 design review: rows replace the per-group cards as the first thing
below the verdict, the old cards stay available folded, and a blocked pair offers the
--exclude-ids value that silences it.
"""

from __future__ import annotations

from unittest.mock import patch

from emby_dedupe.reports.html import format_html_report

BASE = "https://emby.example.com:443"
GB = 1024**3


def _ep(n, status, keep_rating=90, error=None, runtimes=(2700, 2700), ids=None):
    folder = "/S/Show/S01"
    def item(item_id, path, rating, secs, **extra):
        return {"id": item_id, "name": f"Ep {n}", "serverid": "s1", "path": path, "rating": rating,
                "runtime_ticks": secs * 10_000_000, "is_episode": True, "series_name": "Show",
                "season_number": 1, "episode_number": n, "provider_ids": ids or {},
                "quality_description": {"size": GB, "path": path, "video": {"codec": "h264", "resolution": "1080p"},
                                        "audio": {"codec": "aac", "channels": 2, "languages": ["cze"]},
                                        "date_added": "2026-10-01"}, **extra}
    keep = item(f"k{n}", f"{folder}/Show S01E{n:02d} - CZ.mkv", keep_rating, runtimes[0])
    dele = item(f"d{n}", f"{folder}/Show S01E{n:02d} - HDTV.mkv", 50, runtimes[1],
                deletion_result={"status": status, "error": error})
    return {"keep": keep, "delete": [dele]}


def _render(decisions):
    with patch("emby_dedupe.reports.images.inline_poster_urls", side_effect=lambda urls, *a, **k: {}):
        return format_html_report(BASE, decisions, {})


def test_rows_show_each_files_paths_and_scores():
    html = _render([_ep(1, "success"), _ep(2, "success")])

    assert 'data-outcome="removed"' in html
    assert "Removed · 2 files" in html
    assert '<dt>Folder</dt><dd><span class="fact-path">/S/Show/S01</span></dd>' in html, "the shared folder is said once"
    assert 'title="/S/Show/S01/Show S01E01 - CZ.mkv">CZ.mkv</span>' in html
    assert 'title="/S/Show/S01/Show S01E02 - HDTV.mkv">HDTV.mkv</span>' in html
    assert "90.0 vs 50.0" in html


def test_deleted_copy_has_no_dead_emby_link():
    html = _render([_ep(1, "success")])
    body = html[html.index('id="removed" data-outcome="removed"'):html.index('id="excluded"')]

    assert body.count('class="url-link"') == 1, "only the kept copy still exists in Emby"
    assert "Open the other copy" not in body


def test_risky_row_is_marked_check():
    html = _render([_ep(1, "success", keep_rating=10)])

    assert 'class="crow risky"' in html
    assert 'class="chip chip-check" title="kept copy scores lower">scores lower</span>' in html


def test_blocked_row_explains_the_runtime_and_offers_the_exclude_ids_value():
    ids = {"Tvdb": "9514669", "Imdb": "tt24786748"}
    html = _render([_ep(1, "skipped_unsafe", error="duration-mismatch (keeper 3127s vs delete 3506s)",
                        runtimes=(3127, 3506), ids=ids)])

    assert "Blocked by the safety guard · 1 file" in html
    assert "runtime 52 vs 58 min (×1.12, different cut)" in html
    assert "<span>tt24786748</span>" in html, "IMDb id only"
    assert "9514669" not in html, "IMDb id only"


def test_real_run_always_shows_the_failed_section():
    html = _render([_ep(1, "success")])

    assert "Failed · 0 files" in html
    assert "Nothing failed." in html


def test_dry_run_sections_and_no_failed_section():
    html = _render([_ep(1, "not_attempted"), _ep(2, "would_block", error="duration-mismatch (x)")])

    assert "Would be removed · 1 file" in html
    assert "Would be blocked by the safety guard · 1 file" in html
    assert "Nothing failed." not in html


def test_old_group_cards_are_gone_and_their_facts_live_in_the_row():
    html = _render([_ep(1, "success")])

    assert '<details class="legacy-groups">' not in html
    assert "Group 1:" not in html
    body = html[html.index('class="crow-body"'):]
    assert "1.00 GB" in body
    assert "2026-10-01" in body
    assert "aac 2.0" in body
