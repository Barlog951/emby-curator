"""
Tests for common reporting functionality
"""
import pytest
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
        
