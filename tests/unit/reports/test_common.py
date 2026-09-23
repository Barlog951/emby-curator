"""
Tests for common reporting functionality
"""
from emby_dedupe.reports.common import calculate_report_statistics


class TestReportCommon:
    """Tests for common reporting functionality."""


    def test_calculate_report_statistics(self):
        """Test calculating report statistics."""
        decisions = [
            {
                "keep": {
                    "id": "id1",
                    "name": "Item 1",
                    "quality_description": {
                        "size": 1000000000,  # 1 GB
                        "video": {"codec": "h264"},
                        "audio": {"codec": "aac"}
                    }
                },
                "delete": [
                    {
                        "id": "id2",
                        "name": "Item 2",
                        "quality_description": {
                            "size": 500000000,  # 500 MB
                            "video": {"codec": "h264"},
                            "audio": {"codec": "aac"}
                        },
                        "deletion_result": {"status": "success"}
                    },
                    {
                        "id": "id3",
                        "name": "Item 3",
                        "quality_description": {
                            "size": 300000000,  # 300 MB
                            "video": {"codec": "h264"},
                            "audio": {"codec": "aac"}
                        },
                        "deletion_result": {"status": "failed"}
                    }
                ]
            },
            {
                "keep": {
                    "id": "id4",
                    "name": "Item 4",
                    "quality_description": {
                        "size": 2000000000,  # 2 GB
                        "video": {"codec": "h265"},
                        "audio": {"codec": "dts"}
                    }
                },
                "delete": [
                    {
                        "id": "id5",
                        "name": "Item 5",
                        "quality_description": {
                            "size": 1500000000,  # 1.5 GB
                            "video": {"codec": "h264"},
                            "audio": {"codec": "ac3"}
                        },
                        "deletion_result": {"status": "success"}
                    }
                ]
            }
        ]

        stats = calculate_report_statistics(decisions)

        # Check the calculated statistics
        assert stats["total_groups"] == 2
        assert stats["total_items_to_keep"] == 2
        assert stats["total_items_to_delete"] == 3
        assert stats["deleted_items"] == 2  # Successful deletions
        assert stats["failed_deletions"] == 1

        # Check total sizes
        assert stats["total_size_to_delete"] > 0
        assert stats["total_size_to_keep"] > 0



GB = 1_000_000_000


def _decision(status: str | None, delete_size: int = GB, keep_size: int = 2 * GB) -> dict:
    """One keep/delete group; ``status`` None means a dry run (nothing attempted)."""
    item = {"id": "d", "name": "Dup", "quality_description": {"size": delete_size}}
    if status is not None:
        item["deletion_result"] = {"status": status}
    return {"keep": {"id": "k", "name": "Keep", "quality_description": {"size": keep_size}},
            "delete": [item]}


def test_fold_safe_removals_count_as_deleted_and_blocked_space_is_not_removed():
    """Regression 2026-09-23 (real --doit report): 10 Emby deletes + 4 fold-safe removals +
    8 guard-blocked duplicates read as '10 deleted, 12 skipped, 26 GB removed'. The truth was
    14 deleted, 8 kept on disk, and only the 14 removed files' space reclaimed."""
    decisions = ([_decision("success")] * 10 + [_decision("fold_safe_removed")] * 4
                 + [_decision("skipped_unsafe")] * 8)
    stats = calculate_report_statistics(decisions)
    assert (stats["deleted_items"], stats["skipped_deletions"], stats["failed_deletions"]) == (14, 8, 0)
    assert stats["deletion_attempted"] is True
    assert stats["total_size_to_delete"] == 22 * GB  # planned
    assert stats["space_saved"] == stats["total_size_removed"] == 14 * GB  # actually reclaimed
    assert stats["percentage_saved"] == 14 / (22 + 44) * 100.0


def test_dry_run_still_reports_the_planned_space():
    stats = calculate_report_statistics([_decision(None), _decision(None)])
    assert stats["deletion_attempted"] is False
    assert stats["deleted_items"] == 0 and stats["skipped_deletions"] == 2
    assert stats["space_saved"] == stats["total_size_to_delete"] == 2 * GB
