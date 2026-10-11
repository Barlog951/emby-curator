"""A collection (franchise) is deleted whole or not at all — delete time and reports.

Fuki 2026-10-11: cleanup must never leave half a franchise behind. The pipeline only
marks a collection when every owned part fails the rules; these tests cover the delete
loop (a guarded or failed part holds the rest) and how both reports show it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from emby_dedupe.cli import cleanup as cli
from emby_dedupe.models.cleanup import CleanupCandidate, CleanupConfig
from emby_dedupe.reports.cleanup import (
    _format_cleanup_report_console,
    _format_cleanup_report_json,
    _generate_cleanup_html_report,
)

STATS = {
    "total_analyzed": 10, "age_filtered": 0, "excluded_filtered": 0, "play_protected": 0,
    "interest_protected": 0, "actor_protected": 0, "path_protected": 0, "rating_protected": 0,
    "collection_protected": 3, "collections_removed": 1, "final_candidates": 3,
}


def _cand(item_id, name, collection=None, size=0, path=None):
    return CleanupCandidate(
        item_id=item_id, name=name, year=2000, rating=4.0, critic_rating=None, threshold=6.0,
        age_years=5.0, library="HD", size_bytes=2**30, path=path or f"/Movies/HD/{name}/{name}.mkv",
        collection=collection, collection_size=size,
    )


def _saga():
    return [_cand("a", "Saga 1", "Saga Collection", 2), _cand("b", "Saga 2", "Saga Collection", 2)]


def _run_deletions(candidates, unsafe=(), fail=()):
    def guard(path, known, delete_paths, delete_is_container=False):
        return (False, "shares a folder") if any(u in path for u in unsafe) else (True, "")

    def delete(client, base_url, item_id, **kw):
        return {"status": "failed" if item_id in fail else "success"}

    with patch.object(cli, "fetch_all_media_paths", return_value=set()), \
         patch.object(cli, "is_cleanup_delete_safe", side_effect=guard), \
         patch.object(cli, "delete_item", side_effect=delete) as deleted:
        cli._perform_deletions(MagicMock(), "http://emby", candidates, "u", "p", "k")
    return [call.args[2] for call in deleted.call_args_list]


def test_whole_collection_is_deleted():
    assert _run_deletions(_saga()) == ["a", "b"]


def test_one_guarded_part_keeps_every_part():
    candidates = _saga()
    assert _run_deletions(candidates, unsafe=("Saga 2",)) == []
    assert all(c.deletion_result["status"] == "skipped_unsafe" for c in candidates)
    assert "collection kept whole" in candidates[0].deletion_result["error"]


def test_failed_delete_stops_the_rest_of_its_collection():
    candidates = _saga()
    assert _run_deletions(candidates, fail=("a",)) == ["a"]
    assert candidates[1].deletion_result["status"] == "skipped_unsafe"


def test_held_collection_is_a_survivor_for_its_neighbours():
    """A held part stays on disk, so the guard is re-run without it in the delete set."""
    seen = []

    def guard(path, known, delete_paths, delete_is_container=False):
        seen.append(list(delete_paths))
        return (False, "shares a folder") if "Saga 2" in path else (True, "")

    candidates = [*_saga(), _cand("z", "Standalone")]
    with patch.object(cli, "is_cleanup_delete_safe", side_effect=guard):
        verdicts, held = cli._guard_verdicts(candidates, set(), delete_is_container=False)
    assert set(held) == {"Saga Collection"}
    assert all("Saga 1" not in p for p in seen[-1])
    assert verdicts["z"][0] is True


def test_console_report_shows_collection_column_and_counts(capsys):
    _format_cleanup_report_console([*_saga(), _cand("z", "Standalone")], STATS, CleanupConfig())
    out = capsys.readouterr().out
    assert "Collection kept:     3" in out
    assert "Collections removed: 1" in out
    assert "Saga Collection (2 parts)" in out


def test_json_report_carries_the_collection():
    data = _format_cleanup_report_json(_saga(), STATS, CleanupConfig())
    assert data["candidates"][0]["collection"] == "Saga Collection"
    assert data["candidates"][0]["collection_size"] == 2


def test_html_report_groups_a_collection_under_one_header():
    with patch("emby_dedupe.reports.cleanup.inline_poster_urls", return_value={}):
        html = _generate_cleanup_html_report(
            "http://emby", [*_saga(), _cand("z", "Standalone")], STATS, CleanupConfig(), doit=False,
        )
    assert html.count('class="collection-head"') == 1
    assert "Collection: <b>Saga Collection</b>" in html
    assert html.count("badge-collection") >= 2
    assert "1 collection removed as a whole" in html
    assert "Collection: another part stays" in html
    assert "Franchise" not in html
