"""
DisjointSet data structure for efficiently grouping related items.
"""
from typing import Dict


class DisjointSet:
    """
    A disjoint-set data structure implementation for efficiently tracking
    sets of elements, with optimizations for find and union operations.

    This is used to group duplicate media items from different provider IDs.
    """
    def __init__(self) -> None:
        # Initially, each item is its own parent
        self.parent: Dict[str, str] = {}

    def find(self, item: str | dict) -> str:
        """
        Find the root parent of the item recursively.
        Uses path compression for optimization.

        Args:
            item: The item to find the root parent for. Can be a string ID or a dict with an 'id' key.

        Returns:
            The root parent of the item.
        """
        # Extract the ID if it's a dictionary object
        item_id = item["id"] if isinstance(item, dict) else item

        if self.parent[item_id] != item_id:
            self.parent[item_id] = self.find(self.parent[item_id])  # Path compression
        return self.parent[item_id]

    def union(self, set1: str | dict, set2: str | dict) -> None:
        """
        Perform union of two sets represented by their root items.

        Args:
            set1: The first item. Can be a string ID or a dict with an 'id' key.
            set2: The second item. Can be a string ID or a dict with an 'id' key.
        """
        # Extract the IDs if they're dictionary objects
        set1_id = set1["id"] if isinstance(set1, dict) else set1
        set2_id = set2["id"] if isinstance(set2, dict) else set2

        root1 = self.find(set1_id)
        root2 = self.find(set2_id)
        if root1 != root2:
            # Attach one tree's root to the other
            self.parent[root1] = root2
