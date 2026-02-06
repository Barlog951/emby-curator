"""
Tests for the DisjointSet class
"""
import pytest
from emby_dedupe.models.disjoint_set import DisjointSet


class TestDisjointSet:
    """Tests for the DisjointSet class."""

    def test_init(self):
        """Test initialization of DisjointSet."""
        ds = DisjointSet()
        assert ds.parent == {}

    def test_find_string_id(self):
        """Test find method with string IDs."""
        ds = DisjointSet()
        ds.parent = {"a": "a", "b": "a", "c": "b"}
        
        # Test find with path compression
        assert ds.find("c") == "a"
        # After path compression, c should point directly to a
        assert ds.parent["c"] == "a"

    def test_find_with_dict(self):
        """Test find method with dictionary objects."""
        ds = DisjointSet()
        ds.parent = {"item1": "item1", "item2": "item1", "item3": "item2"}
        
        # Test find with a dictionary object
        item = {"id": "item3", "name": "Item 3"}
        assert ds.find(item) == "item1"
        assert ds.parent["item3"] == "item1"  # Path compression should occur

    def test_union_string_ids(self):
        """Test union method with string IDs."""
        ds = DisjointSet()
        ds.parent = {"a": "a", "b": "b", "c": "c"}
        
        # Union a and b
        ds.union("a", "b")
        assert ds.find("a") == ds.find("b")
        
        # Union b and c
        ds.union("b", "c")
        assert ds.find("a") == ds.find("c")

    def test_union_with_dicts(self):
        """Test union method with dictionary objects."""
        ds = DisjointSet()
        item1 = {"id": "item1", "name": "Item 1"}
        item2 = {"id": "item2", "name": "Item 2"}
        item3 = {"id": "item3", "name": "Item 3"}
        
        # Initialize parent manually
        ds.parent = {"item1": "item1", "item2": "item2", "item3": "item3"}
        
        # Union item1 and item2
        ds.union(item1, item2)
        assert ds.find(item1) == ds.find(item2)
        
        # Union item2 and item3
        ds.union(item2, item3)
        assert ds.find(item1) == ds.find(item3)

    def test_complex_scenario(self):
        """Test a more complex scenario with multiple unions and finds."""
        ds = DisjointSet()
        # Initialize with 10 separate items
        for i in range(10):
            ds.parent[str(i)] = str(i)
        
        # Create two separate groups: (0,1,2,3,4) and (5,6,7,8,9)
        for i in range(4):
            ds.union(str(i), str(i+1))
            ds.union(str(i+5), str(i+6))
        
        # All items in first group should have same root
        root0 = ds.find("0")
        for i in range(5):
            assert ds.find(str(i)) == root0
        
        # All items in second group should have same root
        root5 = ds.find("5")
        for i in range(5, 10):
            assert ds.find(str(i)) == root5
        
        # The two groups should have different roots
        assert root0 != root5
        
        # Now unite the groups
        ds.union("0", "5")
        
        # Now all items should have the same root
        new_root = ds.find("0")
        for i in range(10):
            assert ds.find(str(i)) == new_root