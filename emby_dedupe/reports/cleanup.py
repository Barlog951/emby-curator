"""
Report formatting for the library cleanup feature.

Renders cleanup pipeline results (movie and series candidates, protection
stats, near-miss lists) as console tables, JSON-serializable dicts and a
self-contained Jinja2 HTML report in the report directory.

Consumed by the CLI entry point in emby_dedupe.cli.cleanup.
"""

from __future__ import annotations

import os
import re
import webbrowser
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from emby_dedupe.api.cleanup_pipeline import _CRITIC_RATING_DIVISOR, _compute_effective_rating
from emby_dedupe.models.cleanup import (
    CleanupCandidate,
    CleanupConfig,
    SeriesCleanupCandidate,
)
from emby_dedupe.reports.common import format_size, report_dir, write_temp_report
from emby_dedupe.reports.images import inline_images_in_place, inline_poster_urls
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
    print(f"Path protected:      {protection_stats.get('path_protected', 0)}")
    print(f"Rating protected:    {protection_stats.get('rating_protected', 0)}")
    print(f"Collection kept:     {protection_stats.get('collection_protected', 0)}"
          " (a collection goes whole or not at all; another part stays)")
    print(f"Collections removed: {protection_stats.get('collections_removed', 0)} (every owned part goes)")
    print(f"Final candidates:    {protection_stats.get('final_candidates', 0)}")


def _format_rating_str(
    community_rating: float | None,
    critic_rating: float | None,
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


def _print_table(rows: list, columns: list) -> None:
    """Print a fixed-width console table.

    Args:
        rows: Row objects to render (one line each, numbered from 1).
        columns: List of ``(header, width, value_fn)`` tuples. ``value_fn(row, index)``
            returns the already-formatted cell text; the table left-justifies it to
            ``width`` (callers truncate long text cells themselves).
    """
    header = " ".join(f"{name:<{width}}" for name, width, _ in columns)
    print(header)
    print("-" * len(header))
    for i, row in enumerate(rows, 1):
        print(" ".join(f"{value_fn(row, i):<{width}}" for _, width, value_fn in columns))


def _print_movie_table(candidates: list[CleanupCandidate]) -> None:
    """Print the movie candidates table to stdout.

    Args:
        candidates: List of CleanupCandidate objects.
    """
    total_size = sum(c.size_bytes for c in candidates)
    print(f"\nTotal movie space to free: {format_size(total_size)}\n")
    _print_table(candidates, [
        ("#", 4, lambda c, i: str(i)),
        ("Name", 40, lambda c, i: c.name[:40]),
        ("Year", 6, lambda c, i: str(c.year or "")),
        ("Rating", 8, lambda c, i: _format_rating_str(c.rating, c.critic_rating)),
        ("Required", 10, lambda c, i: f"{c.threshold:.1f}"),
        ("Age", 6, lambda c, i: f"{c.age_years:.1f}"),
        ("Library", 20, lambda c, i: c.library[:20]),
        ("Size", 10, lambda c, i: format_size(c.size_bytes)),
        ("Collection (whole)", 32, lambda c, i: _collection_label(c)[:32]),
    ])


def _collection_label(candidate: CleanupCandidate) -> str:
    """``"Name (n parts)"`` for a collection candidate, empty otherwise."""
    if not candidate.collection:
        return ""
    return f"{candidate.collection} ({candidate.collection_size} parts)"


def _print_series_table(series_candidates: list[SeriesCleanupCandidate]) -> None:
    """Print a series table to stdout.

    Args:
        series_candidates: List of SeriesCleanupCandidate objects.
    """
    total_size = sum(c.size_bytes for c in series_candidates)
    print(f"Total: {format_size(total_size)}\n")
    _print_table(series_candidates, [
        ("#", 4, lambda c, i: str(i)),
        ("Name", 40, lambda c, i: c.name[:40]),
        ("Year", 6, lambda c, i: str(c.year or "")),
        ("Rating", 8, lambda c, i: _format_rating_str(c.rating, c.critic_rating)),
        ("Required", 10, lambda c, i: f"{c.threshold:.1f}"),
        ("Stale", 8, lambda c, i: f"{c.stale_years:.1f}"),
        ("Eps", 6, lambda c, i: str(c.episode_count)),
        ("Library", 20, lambda c, i: c.library[:20]),
        ("Size", 10, lambda c, i: format_size(c.size_bytes)),
    ])


def _format_days_left(days: int | None) -> str:
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
    _print_table(near_miss, [
        ("#", 4, lambda c, i: str(i)),
        ("Name", 40, lambda c, i: c.name[:40]),
        ("Year", 6, lambda c, i: str(c.year or "")),
        ("Rating", 8, lambda c, i: _format_rating_str(c.rating, c.critic_rating)),
        ("Required", 10, lambda c, i: f"{c.threshold:.1f}"),
        ("Days Left", 10, lambda c, i: _format_days_left(c.days_left)),
        ("Age", 6, lambda c, i: f"{c.age_years:.1f}"),
        ("Library", 20, lambda c, i: c.library[:20]),
        ("Size", 10, lambda c, i: format_size(c.size_bytes)),
    ])


def _print_near_miss_series_table(near_miss: list[SeriesCleanupCandidate]) -> None:
    """Print near-miss series table with days_left column."""
    _print_table(near_miss, [
        ("#", 4, lambda c, i: str(i)),
        ("Name", 40, lambda c, i: c.name[:40]),
        ("Year", 6, lambda c, i: str(c.year or "")),
        ("Rating", 8, lambda c, i: _format_rating_str(c.rating, c.critic_rating)),
        ("Required", 10, lambda c, i: f"{c.threshold:.1f}"),
        ("Days Left", 10, lambda c, i: _format_days_left(c.days_left)),
        ("Stale", 8, lambda c, i: f"{c.stale_years:.1f}"),
        ("Eps", 6, lambda c, i: str(c.episode_count)),
        ("Library", 20, lambda c, i: c.library[:20]),
        ("Size", 10, lambda c, i: format_size(c.size_bytes)),
    ])


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
    series_candidates: list[SeriesCleanupCandidate] | None = None,
    series_stats: dict | None = None,
    movie_near_miss: list[CleanupCandidate] | None = None,
    series_near_miss: list[SeriesCleanupCandidate] | None = None,
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
    series_candidates: list[SeriesCleanupCandidate] | None = None,
    series_stats: dict | None = None,
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
                "collection": c.collection,
                "collection_size": c.collection_size,
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
        "collection": c.collection,
        "collection_size": c.collection_size,
        "deletion_result": c.deletion_result,
        "provider_ids": dict(c.provider_ids),
        "image_url": poster_url(base_url, c.item_id, api_key),
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
        "provider_ids": dict(c.provider_ids),
        "image_url": poster_url(base_url, c.item_id, api_key),
    }


# ---------------------------------------------------------------------------
# HTML report: rows, plain reasons and the verdict
# ---------------------------------------------------------------------------

# Deletion status -> report outcome on a --doit run. A dry run has one outcome only.
_DOIT_OUTCOME = {"success": "removed", "failed": "failed", "skipped_unsafe": "blocked"}

# Outcome sections in page order: legend label, swatch class, lead sentence.
_SECTIONS = (
    ("removed", "Removed", "seg-removed", ""),
    ("would_remove", "Would be removed", "seg-would",
     Markup('Nothing is deleted yet. To keep a title, add its id to <code class="flag">--exclude-ids</code> '
            'before <code class="flag">--doit</code>.')),
    ("blocked", "Blocked", "seg-blocked", "Still on disk. Each row says why and what to do."),
    ("failed", "Failed", "seg-failed", "Still on disk: Emby did not delete them. Each row says what to do."),
    ("other", "Not attempted", "seg-other", "Still on disk: the run did not try to delete them."),
)


# Section headings longer than their legend label.
_SECTION_TITLE = {"blocked": "Blocked by the safety guard"}


def _outcome(candidate: dict, doit: bool) -> str:
    """The report outcome of one candidate, from its deletion result."""
    if not doit:
        return "would_remove"
    status = str((candidate.get("deletion_result") or {}).get("status") or "")
    return _DOIT_OUTCOME.get(status, "other")


def _plural(count: int, word: str, plural: str | None = None) -> str:
    """'1 film', '2 films'; series and other invariant nouns pass ``plural``."""
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _titles_phrase(films: int, series: int) -> str:
    """'2 films and 1 series', '1 film', '3 series'."""
    parts = [p for n, p in ((films, _plural(films, "film")), (series, _plural(series, "series", "series"))) if n]
    return " and ".join(parts) or "0 titles"


def _number(value: float) -> str:
    """A rating or threshold with one decimal, like the ratings it is compared with."""
    return f"{value:.1f}"


def _rating_text(rating: float | None, critic: float | None, threshold: float) -> str:
    """The comparison the rule made: 'rated 4.1 (users 5.1, critics 31%), needs 7.5'."""
    if rating is None and critic is None:
        return f"no rating, needs {_number(threshold)}"
    effective = _compute_effective_rating(rating, critic)
    parts = [f"users {rating:.1f}"] if rating is not None and critic is not None else []
    if critic is not None:
        parts.append(f"critics {critic / _CRITIC_RATING_DIVISOR:.1f}")
    detail = f" ({', '.join(parts)})" if parts else ""
    shown, needed = f"{effective:.1f}", _number(threshold)
    if shown != needed:
        return f"rated {shown}{detail}, needs {needed}"
    # Equal after rounding: the fewest decimals that tell the two apart.
    digits = next((d for d in (2, 3) if f"{effective:.{d}f}" != f"{threshold:.{d}f}"), 3)
    shown, needed = f"{effective:.{digits}f}", f"{threshold:.{digits}f}"
    if shown == needed:
        return f"rated {shown}{detail}, exactly the {needed} it needs"
    return f"rated {shown}{detail}, needs {needed}"


def _when_text(days: int | None) -> str:
    """When a next-in-line title becomes a candidate: 'in 12 days', 'on the next run'."""
    if days is None:
        return ""
    if days == 0:
        return "on the next run"
    if days < 30:
        return f"in {_plural(days, 'day')}"
    if days < 365:
        return f"in about {_plural(max(1, round(days / 30.44)), 'month')}"
    return f"in about {days / 365.25:.1f} years"


def _exclude_id(provider_ids: dict | None) -> str:
    """The id to pass to --exclude-ids: IMDb first, then TMDb, then TVDB."""
    ids = {str(k).lower(): str(v) for k, v in (provider_ids or {}).items() if v}
    return next((ids[k] for k in ("imdb", "tmdb", "tvdb") if k in ids), "")


def _date_text(iso: str | None) -> str:
    """'2023-02-09' from an Emby timestamp, as the dedupe report prints dates; the input when it does not parse."""
    if not iso:
        return ""
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00")[:19])
    except ValueError:
        return iso
    return f"{stamp:%Y-%m-%d}"


def _reason_lines(outcome: str, error: str | None) -> tuple[str, str]:
    """Plain (why, next step) for a title that is still on disk after a --doit run."""
    text = (error or "").lower()
    if outcome == "failed":
        why = f"Emby did not delete it ({error})." if error else "Emby did not delete it."
        return why, "Run again, or delete it in Emby."
    if outcome == "other":
        return "The run did not try to delete it.", "Run again."
    if text.startswith("collection kept"):
        sibling = re.search(r"'([^']+)'|\"([^\"]+)\"", error or "")
        name = next((g for g in sibling.groups() if g), "") if sibling else ""
        part = f"Another part of its collection ({name})" if name else "Another part of its collection"
        return (f"{part} could not be deleted, so the whole collection stays.",
                f"Sort out {name} first, then run again: a collection goes together." if name
                else "Sort out the other part first, then run again: a collection goes together.")
    if "path unknown" in text:
        return ("Emby gave no file path, so its folder could not be checked.",
                "Run again later: a missing path usually means Emby was scanning the item.")
    if "co-located" in text:
        return ("Its folder also holds media you keep, and Emby would delete the whole folder.",
                "Open it in Emby: give it its own folder or fix a wrong match, then run again.")
    return "The safety guard refused this delete.", "Open it in Emby and check its folder, then run again."


def _html_row(c: dict, outcome: str, kind: str) -> dict:
    """Template facts for one row: the closed line, the rule's comparison and the evidence."""
    rating = _rating_text(c["rating"], c["critic_rating"], c["threshold"])
    if kind == "series":
        episodes = _plural(c["episode_count"], "episode") if c["episode_count"] else "no episodes"
        age = f"no new episode for {c['stale_years']:.1f} years · {episodes}"
    else:
        age = f"{c['age_years']:.1f} years on the server"
    sub = " · ".join(str(p) for p in (c["year"], c["library"], c["size_human"]) if p)
    row = {**c, "kind": kind, "outcome": outcome, "sub": sub, "facts": f"{rating} · {age}",
           "exclude_id": _exclude_id(c.get("provider_ids")), "why": "", "next_step": "",
           "last_added": _date_text(c.get("last_episode_added")), "when": _when_text(c.get("days_left"))}
    if outcome in ("blocked", "failed", "other"):
        row["why"], row["next_step"] = _reason_lines(outcome, (c.get("deletion_result") or {}).get("error"))
    return row


def _funnel(stats: dict, steps: tuple[tuple[str, str], ...]) -> list[dict]:
    """One line per protection that kept at least one title."""
    return [{"label": label, "count": f"{stats.get(key, 0):,}"} for key, label in steps if stats.get(key, 0)]


def _rules(config: CleanupConfig, stats: dict, series_stats: dict | None) -> dict:
    """The folded "How titles were chosen" block: each count once, then the rating rule."""
    years = config.min_age_years
    film_steps = (
        ("age_filtered", f"added less than {_plural(years, 'year')} ago"),
        ("excluded_filtered", "excluded by id"),
        ("play_protected", "watched by someone"),
        ("interest_protected", "favourited or started"),
        ("actor_protected", "with a favourite actor"),
        ("path_protected", "in a protected folder"),
        ("rating_protected", "rated high enough for their age"),
        ("collection_protected", "in a collection with a part that stays"),
    )
    series_steps = (
        ("stale_filtered", f"got a new episode in the last {_plural(years, 'year')}"),
        ("excluded_filtered", "excluded by id"),
        ("play_protected", "watched by someone"),
        ("favorite_protected", "favourited"),
        ("path_protected", "in a protected folder"),
        ("rating_protected", "rated high enough for their age"),
    )
    funnels = [{"what": "Films", "checked": f"{stats.get('total_analyzed', 0):,}",
                "left": f"{stats.get('final_candidates', 0):,}", "steps": _funnel(stats, film_steps)}]
    if series_stats:
        funnels.append({"what": "Series", "checked": f"{series_stats.get('total_analyzed', 0):,}",
                        "left": f"{series_stats.get('final_candidates', 0):,}",
                        "steps": _funnel(series_stats, series_steps)})
    decay = []
    last = config.masterpiece_only_after_years
    for age in range(years, last):
        need = min(config.base_rating + config.decay_step * (age - years), config.max_rating)
        capped = need >= config.max_rating
        label = f"{age} years and more" if capped else f"at {_plural(age, 'year')}"
        decay.append({"age": label, "need": _number(need)})
        if capped:
            break
    decay.append({"age": f"films: {_plural(last, 'year')} and more", "need": _number(config.masterpiece_rating)})
    example_age = years + 0.8
    example = min(config.base_rating + config.decay_step * 0.8, config.max_rating)
    return {"funnels": funnels, "decay": decay, "collections_removed": stats.get("collections_removed", 0) or 0,
            "step": _number(config.decay_step), "example": f"{example_age:.1f} years needs {_number(example)}"}


def _next_up(near_rows: list[dict]) -> dict | None:
    """The next-in-line title that goes first (dated ones only)."""
    dated = [r for r in near_rows if r.get("days_left") is not None]
    return min(dated, key=lambda r: r["days_left"]) if dated else None


def _verdict(rows: list[dict], near_rows: list[dict], doit: bool) -> dict:
    """Headline, subline, warning, bar and legend, counted from each row's own outcome."""
    count: dict[str, dict[str, int]] = {}
    for r in rows:
        slot = count.setdefault(r["outcome"], {"film": 0, "series": 0, "bytes": 0})
        slot[r["kind"]] += 1
        slot["bytes"] += r["size_bytes"] or 0
    deleting = count.get("removed" if doit else "would_remove")
    if not rows:
        headline = "Nothing to clean up."
    elif not deleting:
        headline = "Nothing removed."
    elif doit:
        headline = f"{_titles_phrase(deleting['film'], deleting['series'])} removed, {format_size(deleting['bytes'])} freed."
    else:
        headline = (f"{_titles_phrase(deleting['film'], deleting['series'])} would be removed, "
                    f"{format_size(deleting['bytes'])} to free.")
    nxt = _next_up(near_rows)
    subline = f"Next to go: {nxt['name']} {nxt['when']}, {nxt['size_human']}." if nxt else ""
    held = [(key, word) for key, word in (("blocked", "blocked by the safety guard"), ("failed", "failed"),
                                          ("other", "not attempted")) if key in count]
    warning = ""
    if held:
        parts = [f"{count[k]['film'] + count[k]['series']} {w}" for k, w in held]
        warning = f"{', '.join(parts)}. Those titles are still on disk; each row says what to do."
    sections: list[dict[str, Any]] = []
    for key, title, css, lead in _SECTIONS:
        if key not in count:
            continue
        slot = count[key]
        n = slot["film"] + slot["series"]
        # The headline already gives the removed size; the heading keeps only the count.
        size = ""
        heading = f"{_SECTION_TITLE.get(key, title)} · {_plural(n, 'title')}{size}"
        sections.append({"key": key, "label": title, "title": heading, "css": css, "lead": lead, "count": n,
                         "rows": [r for r in rows if r["outcome"] == key]})
    aria = ", ".join(f"{s['count']} {s['key'].replace('_', ' ')}" for s in sections)
    return {"headline": headline, "subline": subline, "warning": warning, "sections": sections,
            "aria": f"Outcome of {len(rows)} titles: {aria}" if rows else "",
            # One outcome only: a single-colour bar says nothing the headline does not.
            "has_bar": len(sections) > 1}


def _decide(rows: list[dict], near_rows: list[dict], doit: bool) -> list[dict[str, str]]:
    """Copy-paste --exclude-ids values: titles the reader may still want to keep."""
    def ids(group: list[dict]) -> str:
        return ",".join(dict.fromkeys(r["exclude_id"] for r in group if r["exclude_id"]))

    def which(group: list[dict], what: str) -> str:
        return f"the title {what}" if len(group) == 1 else f"any of the {len(group)} titles {what}"

    lines = []
    pending = [r for r in rows if r["outcome"] == "would_remove"] if not doit else []
    if ids(pending):
        lines.append({"label": f"To keep {which(pending, 'that would be removed')}, add its id to --exclude-ids "
                               "before --doit", "command": ids(pending)})
    if ids(near_rows):
        lines.append({"label": f"To keep {which(near_rows, 'next in line')} for good, add its id to --exclude-ids",
                      "command": ids(near_rows)})
    return lines


def _link_collection_siblings(rows: list[dict]) -> None:
    """Point a held collection part at the part it waits for, when that part is in the report."""
    by_name = {r["name"]: r["item_id"] for r in rows}
    for r in rows:
        error = (r.get("deletion_result") or {}).get("error") or ""
        match = re.search(r"'([^']+)'", error) if error.lower().startswith("collection kept") else None
        r["sibling_name"] = match.group(1) if match else ""
        r["sibling_id"] = by_name.get(r["sibling_name"], "") if r["sibling_name"] != r["name"] else ""


def _mark_collection_heads(rows: list[dict]) -> None:
    """Flag the first row of each collection run so the template prints one header for it."""
    previous = None
    for r in rows:
        coll = r.get("collection")
        r["collection_head"] = bool(coll) and coll != previous
        previous = coll


def poster_url(base_url: str, item_id: str, api_key: str) -> str:
    """The report's poster URL for an item ('' without a key). Carries no credential:
    the poster is fetched with the key in a header and embedded before rendering."""
    if not api_key:
        return ""
    return f"{base_url}/Items/{item_id}/Images/Primary?maxWidth=200"


def prefetch_cleanup_posters(
    base_url: str, candidates: Iterable[CleanupCandidate | SeriesCleanupCandidate], api_key: str,
) -> dict[str, str]:
    """Fetch the posters of the items about to be deleted, while Emby still has them.

    The report is rendered after the deletions, when a deleted item's image is already
    gone. Returns ``{poster_url: data URI}`` for the ones fetched.
    """
    urls = [u for u in (poster_url(base_url, c.item_id, api_key) for c in candidates) if u]
    fetched = inline_poster_urls(urls, api_key, emby_url=base_url)
    return {u: v for u, v in fetched.items() if v.startswith("data:")}


def _generate_cleanup_html_report(
    base_url: str,
    candidates: list[CleanupCandidate],
    protection_stats: dict,
    config: CleanupConfig,
    doit: bool,
    server_id: str = "",
    api_key: str = "",
    series_candidates: list[SeriesCleanupCandidate] | None = None,
    series_stats: dict | None = None,
    movie_near_miss: list[CleanupCandidate] | None = None,
    series_near_miss: list[SeriesCleanupCandidate] | None = None,
    prefetched_posters: dict[str, str] | None = None,
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

    from emby_dedupe import __version__
    from emby_dedupe.reports.html import _read_report_css

    films = [_movie_candidate_to_dict(c, base_url, api_key) for c in candidates]
    shows = [_series_candidate_to_dict(c, base_url, api_key) for c in (series_candidates or [])]
    rows = [_html_row(d, _outcome(d, doit), kind) for kind, dicts in (("film", films), ("series", shows)) for d in dicts]
    near_rows = [_html_row({**_movie_candidate_to_dict(c, base_url, api_key), "days_left": c.days_left}, "next", "film")
                 for c in (movie_near_miss or [])]
    near_rows += [_html_row({**_series_candidate_to_dict(c, base_url, api_key), "days_left": c.days_left}, "next",
                            "series") for c in (series_near_miss or [])]
    near_rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] or 0))
    verdict = _verdict(rows, near_rows, doit)
    for section in verdict["sections"]:
        _mark_collection_heads(section["rows"])
    _link_collection_siblings(rows)
    verdict["decide"] = _decide(rows, near_rows, doit)
    near_films = sum(1 for r in near_rows if r["kind"] == "film")
    now = datetime.now()

    context = {
        "base_url": base_url,
        "server_id": server_id,
        "doit": doit,
        "verdict": verdict,
        "near_rows": near_rows,
        "near_title": f"Next in line · {_titles_phrase(near_films, len(near_rows) - near_films)}",
        "rules": _rules(config, protection_stats, series_stats),
        "mode": "Real run (--doit)" if doit else "Dry run · nothing deleted",
        "mode_short": "Real run" if doit else "Dry run",
        "version": __version__,
        "generated_at": f"{now:%Y-%m-%d %H:%M:%S}",
        "generated_human": f"{now.day} {now:%b %Y %H:%M}",
        "report_css": _read_report_css(),
    }

    # Embed the posters so the API key never reaches the report file. This walks the
    # whole context rather than a list of sections, so adding a section to the template
    # cannot reintroduce the leak (see reports.images.inline_images_in_place).
    inline_images_in_place(context, api_key, prefetched=prefetched_posters, emby_url=base_url)

    return template.render(**context)


def _save_cleanup_html_report(html_content: str, no_open: bool = False) -> str:
    """Save the HTML report to the report directory.

    The report directory is :func:`reports.common.report_dir`. The stylesheet is embedded
    in the page, so the file stands alone when moved or attached.

    Args:
        html_content: Rendered HTML string to save.
        no_open: If True, do not open the file in a browser.

    Returns:
        Absolute path of the saved HTML file.
    """
    temp_dir = report_dir()
    temp_path = write_temp_report(html_content, "emby_cleanup_report_", temp_dir)

    logger.info(f"Cleanup HTML report saved to: {temp_path}")

    if not no_open:
        webbrowser.open(f"file://{temp_path}")

    return temp_path
