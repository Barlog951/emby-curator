"""
Common reporting functions used by both markdown and HTML reports.
"""

from typing import Any, Dict, List


def calculate_report_statistics(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate streamlined statistics from the decisions data for reporting.

    Args:
        decisions (List[Dict[str, Any]]): List of decision objects containing items to keep and delete.

    Returns:
        Dict[str, Any]: Dictionary containing various statistics.
    """
    # Initialize statistics dictionary with only essential metrics
    stats: Dict[str, Any] = {
        "total_groups": 0,
        "total_items_to_delete": 0,
        "total_items_to_keep": 0,
        "deleted_items": 0,
        "failed_deletions": 0,
        "skipped_deletions": 0,
        "total_size_to_delete": 0,
        "total_size_to_keep": 0,
    }

    # Filter valid decisions
    valid_decisions = []

    for decision in decisions:
        # Regular validation for decisions
        if not decision.get("keep"):
            continue
        if "id" not in decision.get("keep", {}):
            continue
        if not decision.get("delete"):
            continue
        valid_decisions.append(decision)

    stats["total_groups"] = len(valid_decisions)

    # Process each decision
    for decision in valid_decisions:
        keep_item = decision["keep"]
        delete_items = decision["delete"]

        stats["total_items_to_keep"] += 1

        # Get item size from quality description
        keep_size = keep_item.get("quality_description", {}).get("size", 0)
        try:
            keep_size = int(keep_size)
        except (ValueError, TypeError):
            keep_size = 0

        stats["total_size_to_keep"] += keep_size

        # Process delete items
        for item in delete_items:
            stats["total_items_to_delete"] += 1

            # Process deletion status
            deletion_status = item.get("deletion_result", {}).get("status", "skipped")
            if deletion_status == "success":
                stats["deleted_items"] += 1
            elif deletion_status == "failed":
                stats["failed_deletions"] += 1
            else:
                stats["skipped_deletions"] += 1

            # Get delete item size
            delete_size = item.get("quality_description", {}).get("size", 0)
            try:
                delete_size = int(delete_size)
            except (ValueError, TypeError):
                delete_size = 0

            stats["total_size_to_delete"] += delete_size

    # Calculate space savings
    stats["space_saved"] = stats["total_size_to_delete"]
    stats["percentage_saved"] = 0.0
    if (stats["total_size_to_keep"] + stats["total_size_to_delete"]) > 0:
        stats["percentage_saved"] = (stats["total_size_to_delete"] /
                                    (stats["total_size_to_keep"] + stats["total_size_to_delete"])) * 100.0

    # Format byte sizes to human-readable format
    stats["formatted_size_to_delete"] = format_size(stats["total_size_to_delete"])
    stats["formatted_size_to_keep"] = format_size(stats["total_size_to_keep"])
    stats["formatted_space_saved"] = format_size(stats["space_saved"])

    # Store the formatted values in separate keys
    # The original numeric keys remain integers

    # No library-specific statistics needed

    return stats


def format_size(size_bytes: int) -> str:
    """
    Format a size in bytes to a human-readable string.

    Args:
        size_bytes (int): Size in bytes

    Returns:
        str: Formatted size string (e.g., "4.2 GB")
    """
    if size_bytes == 0:
        return "0 B"

    size_names = ("B", "KB", "MB", "GB", "TB", "PB")
    i = 0
    size_value = float(size_bytes)
    while size_value >= 1024 and i < len(size_names) - 1:
        size_value /= 1024.0
        i += 1

    return f"{size_value:.2f} {size_names[i]}"
