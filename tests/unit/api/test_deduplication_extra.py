"""
Additional tests for deduplication functionality
"""
import pytest
from unittest.mock import patch, Mock, MagicMock

from emby_dedupe.api.deduplication import (
    build_disjoint_set,
    process_duplicate_groups
)
from emby_dedupe.reports.markdown import format_individual_item


class TestExtraDeduplication:
    """Additional tests for deduplication functionality."""
    
    def test_format_individual_item(self):
        """Test formatting individual item for report."""
        # Test item with success status
        item = {
            "id": "id123",
            "name": "Test Item",
            "deletion_result": {"status": "success"}
        }
        
        decision = {
            "keep": {
                "id": "keep123",
                "name": "Test Item",
                "serverid": "server1"
            }
        }
        
        formatted = format_individual_item(item, "http://example.com", decision)
        
        # Check formatting
        assert "✅" in formatted  # Name match emoji
        assert "✅" in formatted  # Success status emoji
        assert "id123" in formatted
        assert "Test Item" in formatted
        assert "http://example.com/web/index.html" in formatted
        
        # Test item with failure status
        item["deletion_result"] = {"status": "failed", "error": "Test error"}
        
        formatted = format_individual_item(item, "http://example.com", decision)
        
        # Check failure formatting
        assert "❌" in formatted  # Status emoji
        assert "Test error" in formatted
        
        # Test item with name mismatch
        item["name"] = "Different Name"
        
        formatted = format_individual_item(item, "http://example.com", decision)
        
        # Check name mismatch formatting
        assert "❌" in formatted  # Name mismatch emoji