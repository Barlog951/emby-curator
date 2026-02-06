"""
Common reporting functions used by both markdown and HTML reports.
"""

from typing import Any, Dict, List


def _is_valid_decision(decision: Dict[str, Any]) -> bool:
    """Check if decision is valid for statistics."""
    if not decision.get("keep"):
        return False
    if "id" not in decision.get("keep", {}):
        return False
    if not decision.get("delete"):
        return False
    return True


def _safe_int_conversion(value: Any) -> int:
    """Safely convert value to int, return 0 if fails."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _process_deletion_status(item: Dict[str, Any], stats: Dict[str, Any]) -> None:
    """Update stats based on deletion status (in-place)."""
    deletion_status = item.get("deletion_result", {}).get("status", "skipped")
    if deletion_status == "success":
        stats["deleted_items"] += 1
    elif deletion_status == "failed":
        stats["failed_deletions"] += 1
    else:
        stats["skipped_deletions"] += 1


def calculate_report_statistics(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate streamlined statistics from the decisions data for reporting.

    Args:
        decisions (List[Dict[str, Any]]): List of decision objects containing items to keep and delete.

    Returns:
        Dict[str, Any]: Dictionary containing various statistics.
    """
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

    valid_decisions = [d for d in decisions if _is_valid_decision(d)]
    stats["total_groups"] = len(valid_decisions)

    # Process each decision
    for decision in valid_decisions:
        keep_item = decision["keep"]
        delete_items = decision["delete"]

        stats["total_items_to_keep"] += 1
        keep_size = _safe_int_conversion(keep_item.get("quality_description", {}).get("size", 0))
        stats["total_size_to_keep"] += keep_size

        # Process delete items
        for item in delete_items:
            stats["total_items_to_delete"] += 1
            _process_deletion_status(item, stats)

            delete_size = _safe_int_conversion(item.get("quality_description", {}).get("size", 0))
            stats["total_size_to_delete"] += delete_size

    # Calculate space savings
    stats["space_saved"] = stats["total_size_to_delete"]
    stats["percentage_saved"] = 0.0
    total_size = stats["total_size_to_keep"] + stats["total_size_to_delete"]
    if total_size > 0:
        stats["percentage_saved"] = (stats["total_size_to_delete"] / total_size) * 100.0

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
