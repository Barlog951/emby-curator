"""
Command-line interface for missing episodes functionality.
Reuses existing CLI infrastructure without modifying the main deduplication functionality.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from emby_dedupe.api.client import (
    check_emby_connection,
    get_library_id,
    handle_host_and_port,
)
from emby_dedupe.api.missing_episodes import (
    process_missing_episodes_for_libraries,
)
from emby_dedupe.cli.arguments import (
    get_env_variable,
    override_warning,
    validate_required_arguments,
)
from emby_dedupe.utils.constants import (
    ENV_DEDUPE_EMBY_API_KEY,
    ENV_DEDUPE_EMBY_HOST,
    ENV_DEDUPE_EMBY_LIBRARY,
    ENV_DEDUPE_EMBY_PASSWORD,
    ENV_DEDUPE_EMBY_PORT,
    ENV_DEDUPE_EMBY_USERNAME,
    ENV_DEDUPE_HTML_ONLY,
    ENV_DEDUPE_HTML_REPORT,
    ENV_DEDUPE_LOGGING,
)
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.file_ops import dump_object_to_file
from emby_dedupe.utils.logging import logger, set_logging_level


def generate_default_filename(format_type: str) -> str:
    """
    Generate default filename for JSON output with timestamp.

    Args:
        format_type (str): The output format type

    Returns:
        str: Generated filename with timestamp
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if format_type == "structured_json":
        return f"missing_episodes_structured-{timestamp}.json"
    else:
        return f"missing_episodes-{timestamp}.json"


def write_to_file(content: str, output_path: str) -> None:
    """
    Write content to file, creating parent directories as needed.

    Args:
        content (str): Content to write
        output_path (str): Path to output file
    """
    try:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(f"Missing episodes report saved to: {output_path}")
    except Exception as e:
        logger.error(f"Failed to write to file {output_path}: {e}")
        raise


def format_structured_json_report(analysis_results: dict) -> str:
    """
    Format missing episodes analysis results as structured JSON.
    Includes 'searched' field set to False by default for both series and episodes.

    Args:
        analysis_results (dict): Analysis results from missing episodes processing.

    Returns:
        str: Formatted structured JSON content.
    """
    structured_data: dict[str, Any] = {
        "metadata": {
            "report_type": "missing_episodes",
            "generated_at": str(datetime.now().isoformat()),
            "total_missing_episodes": analysis_results.get("statistics", {}).get("total_missing_episodes", 0),
            "total_series_affected": analysis_results.get("statistics", {}).get("total_series_affected", 0),
            "total_seasons_affected": analysis_results.get("statistics", {}).get("total_seasons_affected", 0),
            "libraries_processed": analysis_results.get("processed_libraries", [])
        },
        "series": []
    }

    by_series = analysis_results.get("by_series", {})

    for series_name, series_data in by_series.items():
        # Group episodes by season for better structure
        seasons: dict[int, list] = {}
        for episode in series_data["episodes"]:
            season_num = episode.get("season", 0)
            if season_num not in seasons:
                seasons[season_num] = []

            episode_entry = {
                "episode_number": episode.get("episode", 0),
                "name": episode.get("name", "Unknown Episode"),
                "air_date": episode.get("air_date", ""),
                "searched": False  # Default searched status
            }
            seasons[season_num].append(episode_entry)

        # Create series entry
        series_entry: dict[str, Any] = {
            "series_name": series_name,
            "original_series_name": series_data.get("original_series_name", ""),
            "series_id": series_data.get("series_id", ""),
            "total_missing_episodes": series_data.get("total_missing", 0),
            "searched": False,  # Default searched status for series
            "seasons": []
        }

        # Add seasons with sorted episodes
        for season_num in sorted(seasons.keys()):
            season_episodes = sorted(seasons[season_num], key=lambda x: x["episode_number"])
            season_entry = {
                "season_number": season_num,
                "episode_count": len(season_episodes),
                "episodes": season_episodes
            }
            series_entry["seasons"].append(season_entry)

        structured_data["series"].append(series_entry)

    # Sort series by total missing episodes (descending)
    structured_data["series"].sort(key=lambda x: x["total_missing_episodes"], reverse=True)

    return json.dumps(structured_data, indent=2, default=str, ensure_ascii=False)


def _format_statistics_section(stats: dict) -> list:
    """Format statistics section of the report."""
    lines = ["## Summary Statistics"]
    lines.append(f"- **Total Missing Episodes**: {stats.get('total_missing_episodes', 0)}")
    lines.append(f"- **Series Affected**: {stats.get('total_series_affected', 0)}")
    lines.append(f"- **Seasons Affected**: {stats.get('total_seasons_affected', 0)}")

    if stats.get('most_missing_series'):
        lines.append(f"- **Series with Most Missing**: {stats.get('most_missing_series')}")

    if stats.get('average_missing_per_series'):
        avg = stats.get('average_missing_per_series', 0)
        lines.append(f"- **Average Missing per Series**: {avg:.1f} episodes")

    lines.append("")
    return lines


def _group_episodes_by_season(episodes: list) -> dict:
    """Group episodes by season number."""
    episodes_by_season: dict[int, list] = {}
    for episode in episodes:
        season = episode.get("season", 0)
        if season not in episodes_by_season:
            episodes_by_season[season] = []
        episodes_by_season[season].append(episode)
    return episodes_by_season


def _format_episode_line(episode: dict) -> str:
    """Format a single episode line with name and air date."""
    ep_num = episode.get("episode", "?")
    ep_name = episode.get("name", "Unknown Episode")
    air_date = episode.get("air_date", "")

    line = f"- Episode {ep_num}: {ep_name}"
    if air_date:
        # Extract just the date part if it's a full datetime
        if "T" in air_date:
            air_date = air_date.split("T")[0]
        line += f" (Air Date: {air_date})"

    return line


def _format_season_episodes(season_num: int, episodes: list) -> list:
    """Format episodes for a single season."""
    lines = []

    if season_num > 0:
        lines.append(f"**Season {season_num}**:")
    else:
        lines.append("**Specials/Unknown Season**:")

    for episode in sorted(episodes, key=lambda x: x.get("episode", 0)):
        lines.append(_format_episode_line(episode))

    lines.append("")
    return lines


def _format_series_section(by_series: dict) -> list:
    """Format missing episodes by series section."""
    lines = ["## Missing Episodes by Series", ""]

    # Sort series by number of missing episodes (descending)
    sorted_series = sorted(by_series.items(), key=lambda x: x[1]["total_missing"], reverse=True)

    for series_name, series_data in sorted_series:
        lines.append(f"### {series_name}")
        lines.append(f"**Missing Episodes**: {series_data['total_missing']}")
        lines.append("")

        # Group episodes by season
        episodes_by_season = _group_episodes_by_season(series_data["episodes"])

        # Display episodes by season
        for season_num in sorted(episodes_by_season.keys()):
            episodes = episodes_by_season[season_num]
            lines.extend(_format_season_episodes(season_num, episodes))

        lines.append("")

    return lines


def format_missing_episodes_report(analysis_results: dict, format_type: str = "console") -> str:
    """
    Format missing episodes analysis results for different output types.
    Reuses existing report formatting patterns.

    Args:
        analysis_results (dict): Analysis results from missing episodes processing.
        format_type (str): Output format ('console', 'html', 'json').

    Returns:
        str: Formatted report content.
    """
    if format_type == "json":
        return json.dumps(analysis_results, indent=2, default=str, ensure_ascii=False)

    if format_type == "structured_json":
        return format_structured_json_report(analysis_results)

    # Console/Markdown format (reuses existing markdown patterns)
    report_lines = ["# Missing Episodes Report", ""]

    # Statistics
    stats = analysis_results.get("statistics", {})
    report_lines.extend(_format_statistics_section(stats))

    # Libraries processed
    if "processed_libraries" in analysis_results:
        libraries = analysis_results["processed_libraries"]
        report_lines.append(f"**Libraries Processed**: {', '.join(libraries)}")
        report_lines.append("")

    # Missing episodes by series
    by_series = analysis_results.get("by_series", {})
    if by_series:
        report_lines.extend(_format_series_section(by_series))

    return "\n".join(report_lines)


def _parse_and_override_env_vars(args):
    """Parse environment variables and check for command-line overrides."""
    env_verbosity = get_env_variable(ENV_DEDUPE_LOGGING)
    env_host = get_env_variable(ENV_DEDUPE_EMBY_HOST)
    env_port = get_env_variable(ENV_DEDUPE_EMBY_PORT)
    env_api_key = get_env_variable(ENV_DEDUPE_EMBY_API_KEY)
    env_library_str = get_env_variable(ENV_DEDUPE_EMBY_LIBRARY)
    env_library = [lib.strip() for lib in env_library_str.split(',')] if env_library_str else None
    env_html_report = get_env_variable(ENV_DEDUPE_HTML_REPORT) in ("true", "True", "TRUE", "1")
    env_html_only = get_env_variable(ENV_DEDUPE_HTML_ONLY) in ("true", "True", "TRUE", "1")

    set_logging_level(args.verbosity, env_verbosity)
    override_warning("--verbosity", args.verbosity and str(logger.level), env_verbosity)
    override_warning("--host", args.host, env_host)
    override_warning("--port", args.port and str(args.port), env_port)
    override_warning("--api-key", args.api_key, env_api_key)
    override_warning("--library", args.library, env_library_str)

    return {
        'env_host': env_host,
        'env_port': env_port,
        'env_api_key': env_api_key,
        'env_library': env_library,
        'env_html_report': env_html_report,
        'env_html_only': env_html_only
    }


def _handle_report_output(analysis_results, args, env_html_report, env_html_only):
    """Handle report output based on format and write to appropriate destination."""
    output_format = getattr(args, 'format', 'console')
    output_file = getattr(args, 'output', None)

    if output_format in ["json", "structured_json"]:
        # Generate report content
        report_content = format_missing_episodes_report(analysis_results, output_format)

        # Determine output file path
        if output_file:
            output_path = output_file
        else:
            output_path = generate_default_filename(output_format)

        # Write to file
        write_to_file(report_content, output_path)

        # For structured JSON, exit early to avoid summary logs
        if output_format == "structured_json":
            sys.exit(0)

    elif output_format == "html" or args.html_report or env_html_report or args.html_only or env_html_only:
        # For HTML output, we'll need to adapt the existing HTML report generation
        # For now, output formatted console report
        logger.info("HTML format for missing episodes not yet implemented, using console format")
        report_content = format_missing_episodes_report(analysis_results, "console")
        print(report_content)
    else:
        # Console output
        report_content = format_missing_episodes_report(analysis_results, "console")
        print(report_content)


def _log_final_summary(analysis_results):
    """Log final summary of missing episodes found."""
    stats = analysis_results.get("statistics", {})
    total_missing = stats.get("total_missing_episodes", 0)
    total_series = stats.get("total_series_affected", 0)

    if total_missing > 0:
        logger.info(f"Missing episodes search completed: {total_missing} episodes missing across {total_series} series")
    else:
        logger.info("No missing episodes found!")


def run_missing_episodes_command(args) -> None:
    """
    Main entry point for missing episodes command.
    Reuses existing connection and library handling logic.
    """
    # Parse environment variables and check for overrides
    env_vars = _parse_and_override_env_vars(args)

    logger.debug("Collecting final values for missing episodes search")
    host = args.host or env_vars['env_host']
    port = args.port or env_vars['env_port'] or None
    api_key = args.api_key or env_vars['env_api_key']
    library = args.library or env_vars['env_library'] or []

    # For missing episodes, we may need user authentication for certain endpoints
    username = getattr(args, 'username', None) or get_env_variable(ENV_DEDUPE_EMBY_USERNAME)
    password = getattr(args, 'password', None) or get_env_variable(ENV_DEDUPE_EMBY_PASSWORD)

    # Validate required arguments (reuse existing validation, no doit needed for read-only)
    validate_required_arguments(host, api_key, library, doit=False, username=username, password=password)

    # Validate and handle host and port information (reuse existing logic)
    validated_host, validated_port = handle_host_and_port(host, port)

    logger.debug(
        f"Using configurations for missing episodes search: "
        f"Host: {validated_host}, Port: {validated_port}, API Key: {api_key}, "
        f"Libraries: {', '.join(library)}"
    )

    try:
        base_url = f"{validated_host}:{validated_port}"
        client = httpx.Client(headers={"X-Emby-Token": api_key})

        # Reuse existing connection check
        connection_url = f"{base_url}/System/Info"
        if not check_emby_connection(client, connection_url):
            logger.error(f"Unable to connect to the Emby server at {base_url}.")
            sys.exit(1)

        # Process missing episodes for all specified libraries
        logger.info("Starting missing episodes analysis...")
        analysis_results = process_missing_episodes_for_libraries(
            client, base_url, library, get_library_id, username, password
        )

        # Debug output
        if logger.isEnabledFor(logging.DEBUG):
            dump_object_to_file(analysis_results, "testing/missing_episodes_analysis")

        # Handle report output
        _handle_report_output(analysis_results, args, env_vars['env_html_report'], env_vars['env_html_only'])

        # Log final summary
        _log_final_summary(analysis_results)

    except EmbyServerConnectionError as e:
        logger.error(str(e))
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON: {str(e)}")
        sys.exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred during missing episodes search: {str(e)}")
        logger.error(e)
        sys.exit(1)
