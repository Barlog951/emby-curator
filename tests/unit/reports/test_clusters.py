"""Report rows (one per title and outcome) must never hide a deletion.

From the 2026-10-11 design review of the clustered report: the outcome must be per file,
not per group; rows must not merge across season folders, guard reasons or risk flags;
and the counts must reconcile with the report statistics.
"""

from __future__ import annotations

from emby_dedupe.reports.clusters import build_clusters
from emby_dedupe.reports.common import calculate_report_statistics

BASE = "https://emby.example.com:443"
GB = 1024**3


def _item(item_id, path, status=None, rating=50.0, langs=("cze",), res="1080p", size_gb=1.0,
          series="", season=None, episode=None, error=None):
    item = {
        "id": item_id, "name": f"name-{item_id}", "path": path, "serverid": "s1", "rating": rating,
        "runtime_ticks": 2700 * 10_000_000,
        "quality_description": {"size": int(size_gb * GB), "video": {"resolution": res},
                                "audio": {"languages": list(langs)}, "source_quality_multiplier": 1.0},
    }
    if series:
        item.update(is_episode=True, series_name=series, season_number=season, episode_number=episode)
    if status:
        item["deletion_result"] = {"status": status, "error": error}
    return item


def _episode(series, season, episode, status="success", **kw):
    folder = f"/S/{series}/S{season:02d}"
    name = f"{series} S{season:02d}E{episode:02d}"
    keep = _item(f"k{season}{episode}", f"{folder}/{name} - 2160p.mkv", rating=100, res="4K",
                 series=series, season=season, episode=episode)
    dele = _item(f"d{season}{episode}", f"{folder}/{name} - 1080p.mkv", status=status,
                 series=series, season=season, episode=episode, **kw)
    return {"keep": keep, "delete": [dele]}


def _rows(clusters, section):
    return clusters["sections"][section]


def test_counts_reconcile_with_report_statistics():
    decisions = [_episode("Show", 1, e) for e in range(1, 4)] + [
        _episode("Show", 2, 1, status="failed"),
        _episode("Show", 2, 2, status="skipped_unsafe", error="duration-mismatch (keeper 3127s vs delete 3506s)"),
        {"keep": _item("m", "/M/HD/Film (2020)/Film.mkv", rating=90),
         "delete": [_item("m1", "/M/LQ/Film (2020)/Film.mkv", status="success"),
                    _item("m2", "/M/LQ2/Film (2020)/Film.mkv", status="fold_safe_removed")]},
    ]
    clusters = build_clusters(decisions, BASE)
    stats = calculate_report_statistics(decisions)

    assert clusters["total_files"] == stats["total_items_to_delete"]
    assert clusters["deleted_files"] == stats["deleted_items"]
    assert clusters["totals"]["failed"] == stats["failed_deletions"]


def test_a_mixed_group_shows_in_both_sections():
    """One group, one file deleted and one refused: the deletion must stay visible."""
    keep = _item("k", "/M/HD/Film (2020)/Film.mkv", rating=90)
    decisions = [{"keep": keep, "delete": [
        _item("a", "/M/LQ/Film (2020)/Film.mkv", status="success"),
        _item("b", "/M/LQ2/Film (2020)/Film.mkv", status="skipped_unsafe", error="duration-mismatch (x)"),
    ]}]
    clusters = build_clusters(decisions, BASE)

    assert clusters["totals"]["removed"] == 1
    assert clusters["totals"]["blocked"] == 1


def test_rows_never_merge_across_season_folders():
    decisions = [_episode("Show", 1, 1), _episode("Show", 1, 2), _episode("Show", 2, 1)]
    rows = _rows(build_clusters(decisions, BASE), "removed")

    assert sorted((r["season_folder"], r["count"]) for r in rows) == [(1, 2), (2, 1)]
    assert {r["sub"] for r in rows} == {"S01 · 2 files", "S02E01"}


def test_rows_never_merge_across_guard_reasons():
    decisions = [
        _episode("Show", 1, 1, status="skipped_unsafe", error="duration-mismatch (keeper 1s vs delete 99s)"),
        _episode("Show", 1, 2, status="skipped_unsafe", error="keeper co-located in the folder Emby would delete"),
    ]
    rows = _rows(build_clusters(decisions, BASE), "blocked")

    assert sorted(r["reason_code"] for r in rows) == ["guard", "runtime"]


def test_a_risky_deletion_gets_its_own_row_first():
    """Kept copy scores lower (The Sinner, 2026-10-05): not folded into the clean upgrades."""
    decisions = [_episode("Show", 1, e) for e in range(1, 6)] + [_episode("Show", 1, 9, rating=500)]
    rows = _rows(build_clusters(decisions, BASE), "removed")

    assert rows[0]["risk"] is True
    assert rows[0]["count"] == 1
    assert "kept copy scores lower" in rows[0]["risk_text"]
    assert rows[1]["count"] == 5
    assert rows[1]["risk"] is False


def test_language_loss_is_flagged():
    """Our Sticky Love (2026-10-10): the deleted copy had Korean + English, the kept one Czech only."""
    decisions = [_episode("Love", 1, 1, langs=("kor", "eng", "cze"))]
    row = _rows(build_clusters(decisions, BASE), "removed")[0]

    assert "language_loss" in row["flags"]
    assert row["risk"]
    assert row["files"][0]["delete"]["langs"] == "kor eng cs"


def test_dup_suffix_is_flagged_only_when_the_runtimes_differ():
    """Initial D hid a different episode behind "(2)"; the same runtime means the same episode."""
    keep = _item("k", "/M/HD/Film (2020)/Film.mkv")
    other = _item("d", "/M/HD/Film (2020)/Film (2).mkv", status="success")
    other["runtime_ticks"] = 3300 * 10_000_000
    row = _rows(build_clusters([{"keep": keep, "delete": [other]}], BASE), "removed")[0]
    assert "dup_suffix" in row["flags"]

    same = _item("d2", "/M/HD/Film (2020)/Film (2).mkv", status="success")  # 2700 s, like the keeper
    row = _rows(build_clusters([{"keep": keep, "delete": [same]}], BASE), "removed")[0]
    assert "dup_suffix" not in row["flags"]


def test_dry_run_sections():
    decisions = [_episode("Show", 1, 1, status="not_attempted"),
                 _episode("Show", 1, 2, status="would_block", error="duration-mismatch (x)")]
    clusters = build_clusters(decisions, BASE)

    assert clusters["totals"]["would_remove"] == 1
    assert clusters["totals"]["would_block"] == 1
    assert clusters["deleted_files"] == 0


def test_unknown_status_is_never_dropped():
    decisions = [_episode("Show", 1, 1, status="something_new")]

    assert build_clusters(decisions, BASE)["totals"]["other"] == 1


def test_quality_label_keeps_dolby_vision():
    keep = _item("k", "/M/4K/Film (2020)/Film.mkv", res="4K Dolby Vision")
    decisions = [{"keep": keep, "delete": [_item("d", "/M/HD/Film (2020)/Film.mkv", status="success")]}]
    row = _rows(build_clusters(decisions, BASE), "removed")[0]

    assert row["keep_quality"].startswith("4K Dolby Vision")


def test_every_file_keeps_its_evidence():
    row = _rows(build_clusters([_episode("Show", 1, 1)], BASE), "removed")[0]
    f = row["files"][0]

    assert f["keep"]["path"].endswith("2160p.mkv")
    assert f["delete"]["path"].endswith("1080p.mkv")
    assert f["keep"]["score"] == 100
    assert f["delete"]["score"] == 50
    assert f["keep"]["runtime"] == "45:00"
    assert "id=k11" in f["keep"]["url"]
    assert "id=d11" in f["delete"]["url"]
