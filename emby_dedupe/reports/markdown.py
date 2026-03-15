"""
Markdown report generation for the Emby Dedupe tool.
"""

import io
import sys
from typing import Any, Dict, List, Optional

from tqdm import tqdm

from emby_dedupe.reports.common import calculate_report_statistics
from emby_dedupe.utils.constants import EMOJI_CHECK, EMOJI_CROSS
from emby_dedupe.utils.file_ops import truncate_string
from emby_dedupe.utils.logging import logger


def get_emoji_for_status(status: str) -> str:
    """
    Gets the appropriate emoji for a deletion status.

    Args:
        status (str): The deletion status.

    Returns:
        str: The emoji representing the status.
    """
    return EMOJI_CHECK if status == "success" else EMOJI_CROSS


def format_individual_item(item: Dict[str, Any], base_url: str, decision: Dict[str, Any]) -> str:
    """
    Formats an individual item to be marked for deletion with an emoji and as a markdown link.

    Args:
        item (dict): The item information.
        base_url (str): The base URL of the Emby server.
        decision (dict): The decision information contains the item to keep.

    Returns:
        str: Formatted markdown string with emoji and link for the item.
    """
    name_match_emoji = (
        EMOJI_CHECK if item["name"] == decision["keep"]["name"] else EMOJI_CROSS
    )
    item_link = f"[{item['id']}]({base_url}/web/index.html#!/item?id={item['id']}&serverId={decision['keep']['serverid']})"
    deletion_status = item["deletion_result"]
    status_emoji = get_emoji_for_status(deletion_status.get("status", "skipped"))
    error_message = deletion_status.get("error")
    error_message_string = (
        f" Error: {error_message}" if error_message is not None else ""
    )
    return f"{name_match_emoji} {item_link} {truncate_string(item['name'],10)}{status_emoji} {error_message_string}"


def _format_provider_exclusions(buffer: io.StringIO, excluded_ids: List[str], stats: Dict[str, Any]) -> None:
    """Format provider ID exclusion information.

    Args:
        buffer: StringIO buffer to write to.
        excluded_ids: List of excluded provider IDs.
        stats: Statistics dictionary.
    """
    buffer.write(f"- **Provider IDs excluded from deduplication**: {', '.join(excluded_ids)}\n")

    # Show the excluded titles if we have them
    excluded_titles = stats.get("excluded_titles", {})
    excluded_groups_count = stats.get("excluded_groups_count", 0)

    if excluded_groups_count > 0:
        buffer.write(f"- **Duplicate groups excluded**: {excluded_groups_count}\n")

    if excluded_titles:
        buffer.write("- **Excluded titles**:\n")
        for provider_id, item in sorted(excluded_titles.items()):
            if isinstance(item, dict):
                title = item.get("title", provider_id)
                buffer.write(f"  - {title} ({provider_id})\n")
            else:
                buffer.write(f"  - {item} ({provider_id})\n")


def _format_term_exclusions(buffer: io.StringIO, stats: Dict[str, Any]) -> None:
    """Format exclusion term information.

    Args:
        buffer: StringIO buffer to write to.
        stats: Statistics dictionary.
    """
    excluded_count = stats.get('excluded_groups', 0)
    excluded_items = stats.get('excluded_items', 0)

    buffer.write(f"- **Groups excluded from deduplication**: {excluded_count} ({excluded_items} items)\n")

    # Get exclusion terms from stats
    if stats.get('excluded_terms'):
        buffer.write(f"- **Exclusion terms used**: {', '.join(sorted(stats.get('excluded_terms', [])))}\n")


def format_statistics_section(stats: Dict[str, Any], excluded_ids: Optional[List[str]] = None) -> str:
    """
    Format statistics into a markdown string.

    Args:
        stats (dict): Statistics dictionary from calculate_report_statistics
        excluded_ids (list, optional): List of provider IDs excluded from deduplication

    Returns:
        str: Formatted markdown statistics
    """
    with io.StringIO() as buffer:
        buffer.write("# Emby Deduplication Report\n\n")

        # Overall summary
        buffer.write("## Summary\n\n")
        buffer.write(f"- **Total duplicate groups**: {stats['total_groups']}\n")
        buffer.write(f"- **Items being kept**: {stats['total_items_to_keep']}\n")
        buffer.write(f"- **Items to be removed**: {stats['total_items_to_delete']}\n")

        # Exclusion info - Provider IDs
        if excluded_ids and len(excluded_ids) > 0:
            _format_provider_exclusions(buffer, excluded_ids, stats)

        # Exclusion info - Exclusion Terms
        if stats.get('excluded_groups', 0) > 0:
            _format_term_exclusions(buffer, stats)
        buffer.write("\n")

        # Deletion status
        if stats['deleted_items'] > 0 or stats['failed_deletions'] > 0:
            buffer.write("### Deletion Status\n\n")
            buffer.write(f"- **Successfully deleted**: {stats['deleted_items']}\n")
            buffer.write(f"- **Failed deletions**: {stats['failed_deletions']}\n")
            buffer.write(f"- **Skipped deletions**: {stats['skipped_deletions']}\n\n")

        # Space statistics
        buffer.write("### Space Analysis\n\n")
        buffer.write(f"- **Total size kept**: {stats['formatted_size_to_keep']}\n")
        buffer.write(f"- **Total size removed**: {stats['formatted_size_to_delete']}\n")
        buffer.write(f"- **Space saved**: {stats['formatted_space_saved']} ({stats['percentage_saved']:.1f}%)\n\n")

        buffer.write("## Detailed Results\n\n")
        return buffer.getvalue()


def _extract_metadata_info(metadata: Optional[Dict[str, Any]], stats: Dict[str, Any]) -> tuple:
    """Extract metadata information and update stats."""
    excluded_ids = None
    excluded_titles = {}
    excluded_groups_count = 0

    if metadata:
        excluded_ids = metadata.get("excluded_ids", [])
        excluded_titles = metadata.get("excluded_titles", {})
        excluded_groups_count = metadata.get("excluded_groups_count", 0)

        # Add the excluded information to the stats dictionary for reporting
        stats["excluded_titles"] = excluded_titles
        stats["excluded_groups_count"] = excluded_groups_count

    return excluded_ids, excluded_titles, excluded_groups_count


def _validate_decision(decision: Dict[str, Any]) -> bool:
    """Validate that a decision has required fields."""
    if not decision.get("keep"):
        logger.debug("Skipping decision with no 'keep' item")
        return False
    if "id" not in decision.get("keep", {}):
        logger.debug(f"Skipping decision with no ID in keep item: {decision.get('keep')}")
        return False
    if not decision.get("delete"):
        logger.debug("Skipping decision with no items to delete")
        return False
    return True


def _format_title_for_episode(keep: Dict[str, Any]) -> str:
    """Format title for TV episode with series info."""
    title = truncate_string(keep["name"], 15)
    if keep.get("is_episode") and keep.get("series_name"):
        series_info = keep.get("series_name", "")
        season = keep.get("season_number", "")
        episode = keep.get("episode_number", "")

        if season and episode:
            title = f"{series_info} S{season}E{episode}"
        else:
            title = f"{series_info} - {title}"

    return title


def _build_table_row(decision: Dict[str, Any], base_url: str) -> Dict[str, str]:
    """Build a single table row from a decision."""
    keep = decision["keep"]
    title = _format_title_for_episode(keep)

    return {
        "ID": f"[{keep['id']}]({base_url}/web/index.html#!/item?id={keep['id']}&serverId={keep['serverid']})",
        "Title": title,
        "Codec": keep["quality_description"].get("video", {}).get("codec", "unknown"),
        "Size": str(keep["quality_description"].get("size", 0)),
        "ITEMS_TO_DELETE_HEADER": "<br>".join(
            format_individual_item(item, base_url, decision)
            for item in decision["delete"]
        ),
    }


def format_markdown_table(base_url: str, decisions: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Efficiently formats the decisions into a markdown table.

    Args:
        base_url (str): The base URL of the Emby server.
        decisions (list): List of decision objects containing items to keep and delete.
        metadata (dict, optional): Additional metadata such as excluded IDs.

    Returns:
        str: The generated markdown table as a string.
    """
    # Calculate statistics
    stats = calculate_report_statistics(decisions)

    # Extract metadata if provided (only using excluded_ids)
    excluded_ids, _excluded_groups_count, _excluded_titles = _extract_metadata_info(metadata, stats)

    # Filter decisions to only valid ones
    valid_decisions = [d for d in decisions if _validate_decision(d)]

    logger.debug(f"Found {len(valid_decisions)} valid decisions out of {len(decisions)} total")

    # Format statistics section
    statistics_markdown = format_statistics_section(stats, excluded_ids)

    # Store the rows as a list of dictionaries
    table_rows = []

    # Collect row entries, this is not memory-intensive - using with context manager
    with tqdm(total=len(valid_decisions), desc="Preparing result table", unit="row") as progress_bar_data:
        for decision in valid_decisions:
            row = _build_table_row(decision, base_url)
            table_rows.append(row)
            progress_bar_data.update(1)

    # Determine maximum column widths once all rows are built
    max_widths = {
        "ID": max(len(row["ID"]) for row in table_rows) if table_rows else 0,
        "Title": max(len(row["Title"]) for row in table_rows) if table_rows else 0,
        "Codec": max(len(row["Codec"]) for row in table_rows) if table_rows else 0,
        "Size": max(len(row["Size"]) for row in table_rows) if table_rows else 0,
        "ITEMS_TO_DELETE_HEADER": max(len(row["ITEMS_TO_DELETE_HEADER"]) for row in table_rows) if table_rows else 0,
    }

    headers = ["ID", "Title", "Codec", "Size", "ITEMS_TO_DELETE_HEADER"]

    # Using StringIO to efficiently build the table
    with io.StringIO() as buffer:

        # Write header
        buffer.write(
            "| "
            + " | ".join(f"{header:<{max_widths[header]}}" for header in headers)
            + " |\n"
        )
        buffer.write(
            "|-"
            + "-|-".join(f"{'':-<{max_widths[header]}}" for header in headers)
            + "-|\n"
        )

        # Initialize the progress bar
        progress_bar = tqdm(
            total=len(table_rows), desc="Formatting markdown table", unit="row"
        )

        # Must use a try to catch excessive memory usage
        try:
            # Write all rows and update the progress bar for each row.
            for row in table_rows:
                buffer.write(
                    "| "
                    + " | ".join(
                        f"{str(row[header]):<{max_widths[header]}}"
                        for header in headers
                    )
                    + " |\n"
                )
                progress_bar.update(1)
                current_memory_usage = sys.getsizeof(buffer.getvalue())
        except MemoryError:
            logger.error(
                f"Memory usage exceeded limit while formatting markdown table. "
                f"Current usage: {current_memory_usage}"
            )
            progress_bar.close()
            return ""

        progress_bar.close()

        # Retrieve the complete table as a string from the buffer
        # Return the full report with statistics and table
        return statistics_markdown + buffer.getvalue()


def output_report_to_stdout(report_content: str) -> None:
    """
    Outputs a markdown report to stdout. This output can be captured by CI/CD systems.

    Args:
        report_content (str): The report data in markdown format.
    """
    # Unique delimiters for the report data –– make sure these don't appear in the report content
    start_delimiter = "EMBY_DEDUPE_REPORT_START"
    end_delimiter = "EMBY_DEDUPE_REPORT_END"

    # Output the report data as markdown to stdout between delimiters
    print(start_delimiter)
    print(report_content)
    print(end_delimiter)
