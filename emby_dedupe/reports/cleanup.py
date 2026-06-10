"""
Report formatting for the library cleanup feature.

Renders cleanup pipeline results (movie and series candidates, protection
stats, near-miss lists) as console tables, JSON-serializable dicts and a
Jinja2-based HTML report saved alongside its CSS in the system temp dir.

Consumed by the CLI entry point in emby_dedupe.cli.cleanup.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import webbrowser
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from emby_dedupe.api.cleanup_pipeline import _CRITIC_RATING_DIVISOR
from emby_dedupe.models.cleanup import (
    CleanupCandidate,
    CleanupConfig,
    SeriesCleanupCandidate,
)
from emby_dedupe.reports.common import format_size
from emby_dedupe.utils.logging import logger

# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _print_movie_stats(protection_stats: dict, config: CleanupConfig) -> None:
    """Print movie protection statistics summary.

    Args:
        protection_stats: Dict with filter stage counts for movies.
        config: CleanupConfig used for this run.
    """
    print(f"Total analyzed:      {protection_stats.get('total_analyzed', 0)}")
    print(f"Age filtered:        {protection_stats.get('age_filtered', 0)} (< {config.min_age_years}yr)")
    print(f"Excluded by ID:      {protection_stats.get('excluded_filtered', 0)}")
    print(f"Play protected:      {protection_stats.get('play_protected', 0)}")
    print(f"Interest protected:  {protection_stats.get('interest_protected', 0)}")
    print(f"Actor protected:     {protection_stats.get('actor_protected', 0)}")
    print(f"Franchise protected: {protection_stats.get('franchise_protected', 0)}")
    print(f"Path protected:      {protection_stats.get('path_protected', 0)}")
    print(f"Rating protected:    {protection_stats.get('rating_protected', 0)}")
    print(f"Final candidates:    {protection_stats.get('final_candidates', 0)}")


def _format_rating_str(
    community_rating: Optional[float],
    critic_rating: Optional[float],
) -> str:
    """Format a combined rating string for console display.

    Shows community/critic when both present, single source alone otherwise.

    Args:
        community_rating: CommunityRating (0-10). None if absent.
        critic_rating: CriticRating (0-100). None if absent.

    Returns:
        Formatted string like "5.0/8.0", "5.0", "RT:8.0", or "none".
    """
    if community_rating is not None and critic_rating is not None:
        return f"{community_rating:.1f}/{critic_rating / _CRITIC_RATING_DIVISOR:.1f}"
    if community_rating is not None:
        return f"{community_rating:.1f}"
    if critic_rating is not None:
        return f"RT:{critic_rating / _CRITIC_RATING_DIVISOR:.1f}"
    return "none"


def _print_movie_table(candidates: list[CleanupCandidate]) -> None:
    """Print the movie candidates table to stdout.

    Args:
        candidates: List of CleanupCandidate objects.
    """
    total_size = sum(c.size_bytes for c in candidates)
    print(f"\nTotal movie space to free: {format_size(total_size)}\n")

    col_widths = (4, 40, 6, 8, 10, 6, 20, 10)
    header = (
        f"{'#':<{col_widths[0]}} "
        f"{'Name':<{col_widths[1]}} "
        f"{'Year':<{col_widths[2]}} "
        f"{'Rating':<{col_widths[3]}} "
        f"{'Required':<{col_widths[4]}} "
        f"{'Age':<{col_widths[5]}} "
        f"{'Library':<{col_widths[6]}} "
        f"{'Size':<{col_widths[7]}}"
    )
    print(header)
    print("-" * len(header))

    for i, c in enumerate(candidates, 1):
        rating_str = _format_rating_str(c.rating, c.critic_rating)
        name_trunc = c.name[:col_widths[1]] if len(c.name) > col_widths[1] else c.name
        print(
            f"{i:<{col_widths[0]}} "
            f"{name_trunc:<{col_widths[1]}} "
            f"{str(c.year or ''):<{col_widths[2]}} "
            f"{rating_str:<{col_widths[3]}} "
            f"{c.threshold:<{col_widths[4]}.1f} "
            f"{c.age_years:<{col_widths[5]}.1f} "
            f"{c.library[:col_widths[6]]:<{col_widths[6]}} "
            f"{format_size(c.size_bytes):<{col_widths[7]}}"
        )


def _print_series_table(series_candidates: list[SeriesCleanupCandidate]) -> None:
    """Print a series table to stdout.

    Args:
        series_candidates: List of SeriesCleanupCandidate objects.
    """
    total_size = sum(c.size_bytes for c in series_candidates)
    print(f"Total: {format_size(total_size)}\n")

    col_widths = (4, 40, 6, 8, 10, 8, 6, 20, 10)
    header = (
        f"{'#':<{col_widths[0]}} "
        f"{'Name':<{col_widths[1]}} "
        f"{'Year':<{col_widths[2]}} "
        f"{'Rating':<{col_widths[3]}} "
        f"{'Required':<{col_widths[4]}} "
        f"{'Stale':<{col_widths[5]}} "
        f"{'Eps':<{col_widths[6]}} "
        f"{'Library':<{col_widths[7]}} "
        f"{'Size':<{col_widths[8]}}"
    )
    print(header)
    print("-" * len(header))

    for i, c in enumerate(series_candidates, 1):
        rating_str = _format_rating_str(c.rating, c.critic_rating)
        name_trunc = c.name[:col_widths[1]] if len(c.name) > col_widths[1] else c.name
        print(
            f"{i:<{col_widths[0]}} "
            f"{name_trunc:<{col_widths[1]}} "
            f"{str(c.year or ''):<{col_widths[2]}} "
            f"{rating_str:<{col_widths[3]}} "
            f"{c.threshold:<{col_widths[4]}.1f} "
            f"{c.stale_years:<{col_widths[5]}.1f} "
            f"{c.episode_count:<{col_widths[6]}} "
            f"{c.library[:col_widths[7]]:<{col_widths[7]}} "
            f"{format_size(c.size_bytes):<{col_widths[8]}}"
        )


def _format_days_left(days: Optional[int]) -> str:
    """Format days_left as a human-readable string."""
    if days is None:
        return "never"
    if days == 0:
        return "now"
    if days < 30:
        return f"{days}d"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days / 365.25:.1f}yr"


def _print_near_miss_movie_table(near_miss: list[CleanupCandidate]) -> None:
    """Print near-miss movie table with days_left column."""
    col_widths = (4, 40, 6, 8, 10, 10, 6, 20, 10)
    header = (
        f"{'#':<{col_widths[0]}} "
        f"{'Name':<{col_widths[1]}} "
        f"{'Year':<{col_widths[2]}} "
        f"{'Rating':<{col_widths[3]}} "
        f"{'Required':<{col_widths[4]}} "
        f"{'Days Left':<{col_widths[5]}} "
        f"{'Age':<{col_widths[6]}} "
        f"{'Library':<{col_widths[7]}} "
        f"{'Size':<{col_widths[8]}}"
    )
    print(header)
    print("-" * len(header))

    for i, c in enumerate(near_miss, 1):
        rating_str = _format_rating_str(c.rating, c.critic_rating)
        name_trunc = c.name[:col_widths[1]] if len(c.name) > col_widths[1] else c.name
        print(
            f"{i:<{col_widths[0]}} "
            f"{name_trunc:<{col_widths[1]}} "
            f"{str(c.year or ''):<{col_widths[2]}} "
            f"{rating_str:<{col_widths[3]}} "
            f"{c.threshold:<{col_widths[4]}.1f} "
            f"{_format_days_left(c.days_left):<{col_widths[5]}} "
            f"{c.age_years:<{col_widths[6]}.1f} "
            f"{c.library[:col_widths[7]]:<{col_widths[7]}} "
            f"{format_size(c.size_bytes):<{col_widths[8]}}"
        )


def _print_near_miss_series_table(near_miss: list[SeriesCleanupCandidate]) -> None:
    """Print near-miss series table with days_left column."""
    col_widths = (4, 40, 6, 8, 10, 10, 8, 6, 20, 10)
    header = (
        f"{'#':<{col_widths[0]}} "
        f"{'Name':<{col_widths[1]}} "
        f"{'Year':<{col_widths[2]}} "
        f"{'Rating':<{col_widths[3]}} "
        f"{'Required':<{col_widths[4]}} "
        f"{'Days Left':<{col_widths[5]}} "
        f"{'Stale':<{col_widths[6]}} "
        f"{'Eps':<{col_widths[7]}} "
        f"{'Library':<{col_widths[8]}} "
        f"{'Size':<{col_widths[9]}}"
    )
    print(header)
    print("-" * len(header))

    for i, c in enumerate(near_miss, 1):
        rating_str = _format_rating_str(c.rating, c.critic_rating)
        name_trunc = c.name[:col_widths[1]] if len(c.name) > col_widths[1] else c.name
        print(
            f"{i:<{col_widths[0]}} "
            f"{name_trunc:<{col_widths[1]}} "
            f"{str(c.year or ''):<{col_widths[2]}} "
            f"{rating_str:<{col_widths[3]}} "
            f"{c.threshold:<{col_widths[4]}.1f} "
            f"{_format_days_left(c.days_left):<{col_widths[5]}} "
            f"{c.stale_years:<{col_widths[6]}.1f} "
            f"{c.episode_count:<{col_widths[7]}} "
            f"{c.library[:col_widths[8]]:<{col_widths[8]}} "
            f"{format_size(c.size_bytes):<{col_widths[9]}}"
        )


def _print_series_report(
    series_candidates: list[SeriesCleanupCandidate],
    series_stats: dict,
    config: CleanupConfig,
) -> None:
    """Print the series cleanup report section to stdout.

    Args:
        series_candidates: List of SeriesCleanupCandidate objects.
        series_stats: Dict with filter stage counts for series.
        config: CleanupConfig used for this run.
    """
    print("\n=== Series Cleanup Report ===\n")

    print(f"Total analyzed:      {series_stats.get('total_analyzed', 0)}")
    print(f"Stale filtered:      {series_stats.get('stale_filtered', 0)} (< {config.min_age_years}yr)")
    print(f"Excluded by ID:      {series_stats.get('excluded_filtered', 0)}")
    print(f"Play protected:      {series_stats.get('play_protected', 0)}")
    print(f"Favorite protected:  {series_stats.get('favorite_protected', 0)}")
    print(f"Path protected:      {series_stats.get('path_protected', 0)}")
    print(f"Rating protected:    {series_stats.get('rating_protected', 0)}")
    print(f"Final candidates:    {series_stats.get('final_candidates', 0)}")

    series_total_size = sum(c.size_bytes for c in series_candidates)
    print(f"\nTotal series space to free: {format_size(series_total_size)}\n")

    _print_series_table(series_candidates)


def _format_cleanup_report_console(
    candidates: list[CleanupCandidate],
    protection_stats: dict,
    config: CleanupConfig,
    series_candidates: Optional[list[SeriesCleanupCandidate]] = None,
    series_stats: Optional[dict] = None,
    movie_near_miss: Optional[list[CleanupCandidate]] = None,
    series_near_miss: Optional[list[SeriesCleanupCandidate]] = None,
) -> None:
    """Print a formatted cleanup report to stdout.

    Args:
        candidates: List of CleanupCandidate objects (movies).
        protection_stats: Dict with filter stage counts for movies.
        config: CleanupConfig used for this run.
        series_candidates: Optional list of SeriesCleanupCandidate objects.
        series_stats: Optional dict with filter stage counts for series.
        movie_near_miss: Movies protected only by rating (closest to removal).
        series_near_miss: Series protected only by rating (closest to removal).
    """
    print("\n=== Cleanup Report ===\n")
    _print_movie_stats(protection_stats, config)

    if not candidates:
        print("\nNo movie cleanup candidates found.")
    else:
        _print_movie_table(candidates)

    if movie_near_miss:
        total_size = sum(c.size_bytes for c in movie_near_miss)
        print(f"\n=== Next {len(movie_near_miss)} Movie Candidates (protected only by rating) ===\n")
        print(f"Total: {format_size(total_size)}\n")
        _print_near_miss_movie_table(movie_near_miss)

    if series_candidates and series_stats:
        _print_series_report(series_candidates, series_stats, config)

    if series_near_miss:
        total_size = sum(c.size_bytes for c in series_near_miss)
        print(f"\n=== Next {len(series_near_miss)} Series Candidates (protected only by rating) ===\n")
        print(f"Total: {format_size(total_size)}\n")
        _print_near_miss_series_table(series_near_miss)


def _format_cleanup_report_json(
    candidates: list[CleanupCandidate],
    protection_stats: dict,
    config: CleanupConfig,
    series_candidates: Optional[list[SeriesCleanupCandidate]] = None,
    series_stats: Optional[dict] = None,
) -> dict:
    """Build a JSON-serializable dict representing the full cleanup report.

    Args:
        candidates: List of CleanupCandidate objects (movies).
        protection_stats: Dict with filter stage counts for movies.
        config: CleanupConfig used for this run.
        series_candidates: Optional list of SeriesCleanupCandidate objects.
        series_stats: Optional dict with filter stage counts for series.

    Returns:
        JSON-serializable dict with report data.
    """
    result: dict = {
        "protection_stats": protection_stats,
        "config": {
            "min_age_years": config.min_age_years,
            "protect_paths": config.protect_paths,
            "base_rating": config.base_rating,
            "decay_step": config.decay_step,
            "max_rating": config.max_rating,

            "excluded_provider_ids": list(config.excluded_provider_ids),
        },
        "candidates": [
            {
                "item_id": c.item_id,
                "name": c.name,
                "year": c.year,
                "rating": c.rating,
                "critic_rating": c.critic_rating,
                "threshold": c.threshold,
                "age_years": round(c.age_years, 2),
                "library": c.library,
                "size_bytes": c.size_bytes,
                "size_human": format_size(c.size_bytes),
                "path": c.path,
                "deletion_result": c.deletion_result,
            }
            for c in candidates
        ],
        "total_size_bytes": sum(c.size_bytes for c in candidates),
        "total_size_human": format_size(sum(c.size_bytes for c in candidates)),
    }

    if series_candidates is not None:
        result["series_stats"] = series_stats or {}
        result["series_candidates"] = [
            {
                "item_id": c.item_id,
                "name": c.name,
                "year": c.year,
                "rating": c.rating,
                "critic_rating": c.critic_rating,
                "threshold": c.threshold,
                "stale_years": round(c.stale_years, 2),
                "last_episode_added": c.last_episode_added,
                "episode_count": c.episode_count,
                "library": c.library,
                "size_bytes": c.size_bytes,
                "size_human": format_size(c.size_bytes),
                "path": c.path,
                "deletion_result": c.deletion_result,
            }
            for c in series_candidates
        ]
        series_total = sum(c.size_bytes for c in series_candidates)
        result["series_total_size_bytes"] = series_total
        result["series_total_size_human"] = format_size(series_total)

    return result


def _movie_candidate_to_dict(c: CleanupCandidate, base_url: str, api_key: str) -> dict:
    """Convert a CleanupCandidate to a template-friendly dict."""
    return {
        "item_id": c.item_id,
        "name": c.name,
        "year": c.year,
        "rating": c.rating,
        "rating_str": f"{c.rating:.1f}" if c.rating is not None else "unrated",
        "critic_rating": c.critic_rating,
        "critic_rating_str": (
            f"{c.critic_rating:.0f}%"
            if c.critic_rating is not None else None
        ),
        "threshold": c.threshold,
        "threshold_str": f"{c.threshold:.2f}",
        "age_years": round(c.age_years, 1),
        "library": c.library,
        "size_bytes": c.size_bytes,
        "size_human": format_size(c.size_bytes),
        "path": c.path,
        "deletion_result": c.deletion_result,
        "image_url": (
            f"{base_url}/Items/{c.item_id}/Images/Primary"
            f"?maxWidth=200&api_key={api_key}"
            if api_key else ""
        ),
    }


def _series_candidate_to_dict(c: SeriesCleanupCandidate, base_url: str, api_key: str) -> dict:
    """Convert a SeriesCleanupCandidate to a template-friendly dict."""
    return {
        "item_id": c.item_id,
        "name": c.name,
        "year": c.year,
        "rating": c.rating,
        "rating_str": f"{c.rating:.1f}" if c.rating is not None else "unrated",
        "critic_rating": c.critic_rating,
        "critic_rating_str": (
            f"{c.critic_rating:.0f}%"
            if c.critic_rating is not None else None
        ),
        "threshold": c.threshold,
        "threshold_str": f"{c.threshold:.2f}",
        "stale_years": round(c.stale_years, 1),
        "last_episode_added": c.last_episode_added,
        "episode_count": c.episode_count,
        "library": c.library,
        "size_bytes": c.size_bytes,
        "size_human": format_size(c.size_bytes),
        "path": c.path,
        "deletion_result": c.deletion_result,
        "image_url": (
            f"{base_url}/Items/{c.item_id}/Images/Primary"
            f"?maxWidth=200&api_key={api_key}"
            if api_key else ""
        ),
    }


def _generate_cleanup_html_report(
    base_url: str,
    candidates: list[CleanupCandidate],
    protection_stats: dict,
    config: CleanupConfig,
    doit: bool,
    server_id: str = "",
    api_key: str = "",
    series_candidates: Optional[list[SeriesCleanupCandidate]] = None,
    series_stats: Optional[dict] = None,
    movie_near_miss: Optional[list[CleanupCandidate]] = None,
    series_near_miss: Optional[list[SeriesCleanupCandidate]] = None,
) -> str:
    """Render the cleanup report as an HTML string using Jinja2.

    Args:
        base_url: Emby server base URL (used for external links).
        candidates: List of CleanupCandidate objects (movies).
        protection_stats: Dict with filter stage counts for movies.
        config: CleanupConfig used for this run.
        doit: Whether deletions were performed.
        server_id: Emby server ID for deep links.
        api_key: Emby API key for image URLs.
        series_candidates: Optional list of SeriesCleanupCandidate objects.
        series_stats: Optional dict with filter stage counts for series.
        movie_near_miss: Movies protected only by rating (closest to removal).
        series_near_miss: Series protected only by rating (closest to removal).

    Returns:
        Rendered HTML string.
    """
    templates_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates",
    )

    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
    template = env.get_template("cleanup_report.html")

    candidates_dicts = [_movie_candidate_to_dict(c, base_url, api_key) for c in candidates]
    total_size = sum(c.size_bytes for c in candidates)

    # Build series dicts for template
    series_dicts = []
    series_total_size = 0
    if series_candidates:
        series_dicts = [_series_candidate_to_dict(c, base_url, api_key) for c in series_candidates]
        series_total_size = sum(c.size_bytes for c in series_candidates)

    # Build near-miss dicts (with days_left)
    movie_near_miss_dicts = []
    for mc in (movie_near_miss or []):
        movie_dict = _movie_candidate_to_dict(mc, base_url, api_key)
        movie_dict["days_left"] = mc.days_left
        movie_dict["days_left_str"] = _format_days_left(mc.days_left)
        movie_near_miss_dicts.append(movie_dict)
    series_near_miss_dicts = []
    for sc in (series_near_miss or []):
        series_dict = _series_candidate_to_dict(sc, base_url, api_key)
        series_dict["days_left"] = sc.days_left
        series_dict["days_left_str"] = _format_days_left(sc.days_left)
        series_near_miss_dicts.append(series_dict)
    movie_near_miss_size = sum(c.size_bytes for c in (movie_near_miss or []))
    series_near_miss_size = sum(c.size_bytes for c in (series_near_miss or []))

    return template.render(
        base_url=base_url,
        server_id=server_id,
        candidates=candidates_dicts,
        protection_stats=protection_stats,
        config={
            "min_age_years": config.min_age_years,
            "protect_paths": config.protect_paths,
            "base_rating": config.base_rating,
            "decay_step": config.decay_step,
            "max_rating": config.max_rating,
        },
        doit=doit,
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        total_size_bytes=total_size,
        total_size_human=format_size(total_size),
        series_candidates=series_dicts,
        series_stats=series_stats or {},
        series_total_size_human=format_size(series_total_size),
        movie_near_miss=movie_near_miss_dicts,
        movie_near_miss_size_human=format_size(movie_near_miss_size),
        series_near_miss=series_near_miss_dicts,
        series_near_miss_size_human=format_size(series_near_miss_size),
    )


def _save_cleanup_html_report(html_content: str, no_open: bool = False) -> str:
    """Save HTML report to a temp file and copy CSS alongside it.

    Follows the same pattern as reports/html.py → generate_html_report()
    (DA fix #14): saves to system temp dir, copies report.css from static/
    so the browser can find it via relative path.

    Args:
        html_content: Rendered HTML string to save.
        no_open: If True, do not open the file in a browser.

    Returns:
        Absolute path of the saved HTML file.
    """
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, f"emby_cleanup_report_{int(time.time())}.html")

    # Copy CSS alongside HTML so the relative <link href="report.css"> resolves
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css_src = os.path.join(pkg_dir, "static", "css", "report.css")
    if os.path.exists(css_src):
        shutil.copy2(css_src, os.path.join(temp_dir, "report.css"))
    else:
        logger.warning(f"CSS file not found at {css_src}; report may be unstyled.")

    with open(temp_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    logger.info(f"Cleanup HTML report saved to: {temp_path}")

    if not no_open:
        webbrowser.open(f"file://{temp_path}")

    return temp_path
