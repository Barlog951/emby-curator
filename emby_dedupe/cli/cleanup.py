"""
Library cleanup command — identify and optionally remove unwatched stale media.

Handles both movies and TV series. Libraries are auto-probed for content type
and the appropriate pipeline is run for each.

Movies pass through a 7-layer filter pipeline (age, exclusion, play/interest,
actors, franchise, path, rating decay).

Series pass through a 5-layer filter pipeline (staleness, exclusion,
play/favorites, path, rating decay). Staleness is measured as years since
the last episode was added to Emby.

Both use a dynamic rating decay model: older items must have a higher
community rating to be protected.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import webbrowser
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import httpx
from jinja2 import Environment, FileSystemLoader
from tqdm import tqdm

import emby_dedupe.api.client as _client_mod
from emby_dedupe.api.client import (
    check_emby_connection,
    delete_item,
    handle_host_and_port,
    logout,
)
from emby_dedupe.api.search import get_all_library_ids, get_library_ids_by_name
from emby_dedupe.reports.common import format_size
from emby_dedupe.utils.constants import PAGE_SIZE
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PROTECT_PATH = "/Dokumenty/"
_CRITIC_RATING_DIVISOR: float = 10.0  # CriticRating is 0-100 (RT %); divide to get 0-10

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CleanupConfig:
    """Configuration for the library cleanup pipeline.

    Args:
        min_age_years: Movie must be at least this old (by DateCreated) to be eligible.
        protect_paths: Path substrings that protect a movie (e.g., "/Dokumenty/").
        base_rating: Minimum required CommunityRating at min_age_years.
        decay_step: Rating requirement increase per year over min_age_years.
        max_rating: Cap on required rating (reached after enough years).
        top_actors: Number of top actors from primary user's watch history to protect.
        excluded_provider_ids: Set of provider ID values (IMDB/TMDB/TVDB) to skip.
    """

    min_age_years: int = 3
    protect_paths: list[str] = field(default_factory=lambda: [_DEFAULT_PROTECT_PATH])
    base_rating: float = 6.0
    decay_step: float = 0.5
    max_rating: float = 8.0
    no_actor_protection_after_years: int = 10
    masterpiece_only_after_years: int = 12
    masterpiece_rating: float = 9.0
    excluded_provider_ids: set[str] = field(default_factory=set)
    near_miss_count: int = 5


@dataclass
class CleanupCandidate:
    """A movie identified as a cleanup candidate after passing all filter layers.

    Args:
        item_id: Emby item ID.
        name: Movie title.
        year: Production year (None if unknown).
        rating: CommunityRating from Emby (None = unrated, distinct from 0.0).
        critic_rating: CriticRating from Emby on 0-100 scale (None if absent).
        threshold: Computed age-decay rating threshold.
        age_years: Age in years since DateCreated.
        library: Library name.
        size_bytes: File size in bytes (0 if Size is None/missing).
        path: File system path on the Emby server.
        deletion_result: Result dict from delete_item() if --doit was used.
    """

    item_id: str
    name: str
    year: Optional[int]
    rating: Optional[float]
    critic_rating: Optional[float]
    threshold: float
    age_years: float
    library: str
    size_bytes: int
    path: str
    deletion_result: Optional[dict] = None
    days_left: Optional[int] = None


@dataclass
class SeriesCleanupCandidate:
    """A TV series identified as a cleanup candidate after passing all filter layers.

    Uses staleness (years since last episode added) instead of movie age.

    Args:
        item_id: Emby series item ID.
        name: Series title.
        year: Production year (None if unknown).
        rating: CommunityRating from Emby (None = unrated).
        critic_rating: CriticRating from Emby on 0-100 scale (None if absent).
        threshold: Computed staleness-decay rating threshold.
        stale_years: Years since the last episode was added to Emby.
        last_episode_added: ISO date string of the most recently added episode.
        episode_count: Total episode count (RecursiveItemCount).
        library: Library name.
        size_bytes: Total size of all episodes in bytes.
        path: Series root path on the Emby server.
        deletion_result: Result dict from delete_item() if --doit was used.
    """

    item_id: str
    name: str
    year: Optional[int]
    rating: Optional[float]
    critic_rating: Optional[float]
    threshold: float
    stale_years: float
    last_episode_added: Optional[str]
    episode_count: int
    library: str
    size_bytes: int
    path: str
    deletion_result: Optional[dict] = None
    days_left: Optional[int] = None


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _compute_age_years(date_created_str: Optional[str]) -> float:
    """Compute age in years from a DateCreated string.

    Uses only the date portion (first 10 chars) so timezone precision is
    irrelevant — a ±1 day error doesn't matter for a 3-year threshold.

    Args:
        date_created_str: ISO 8601 date string from Emby (e.g. "2020-01-15T...").

    Returns:
        Age in years as float, or 0.0 if missing/unparseable (safe: won't flag for deletion).
    """
    if not date_created_str:
        return 0.0
    try:
        added_date = date.fromisoformat(date_created_str[:10])
        return (date.today() - added_date).days / 365.25
    except (ValueError, TypeError):
        return 0.0


def _compute_rating_threshold(age_years: float, config: CleanupConfig) -> float:
    """Compute the age-adjusted required rating threshold.

    Formula: base_rating + (age_years - min_age_years) * decay_step, capped at max_rating.

    Examples with defaults (base=6.0, step=0.5, max=8.0):
        3yr → 6.0, 4yr → 6.5, 5yr → 7.0, 6yr → 7.5, 7yr+ → 8.0

    Args:
        age_years: Movie age in years.
        config: Cleanup configuration.

    Returns:
        Required community rating as float.
    """
    raw = config.base_rating + ((age_years - config.min_age_years) * config.decay_step)
    return min(raw, config.max_rating)


def _compute_effective_rating(
    community_rating: Optional[float],
    critic_rating: Optional[float],
) -> float:
    """Compute effective rating from CommunityRating and CriticRating.

    CriticRating is on a 0-100 scale (Rotten Tomatoes); normalised to 0-10.
    When both are available, returns their average. When only one is available,
    returns that source alone (no penalty for missing data).

    Args:
        community_rating: Emby CommunityRating (0-10 scale). None if absent.
        critic_rating: Emby CriticRating (0-100 scale). None if absent.

    Returns:
        Effective rating on 0-10 scale for threshold comparison.
    """
    has_community = community_rating is not None
    has_critic = critic_rating is not None
    if has_community and has_critic:
        return (community_rating + critic_rating / _CRITIC_RATING_DIVISOR) / 2.0
    if has_community:
        return community_rating
    if has_critic:
        return critic_rating / _CRITIC_RATING_DIVISOR
    return 0.0


def _compute_days_until_candidate(
    effective_rating: float,
    current_age_years: float,
    config: CleanupConfig,
) -> Optional[int]:
    """Compute how many days until a near-miss item becomes a cleanup candidate.

    Considers two thresholds:
      1. Normal decay: threshold rises with age until it exceeds effective_rating.
      2. Masterpiece gate: at masterpiece_only_after_years (12yr), only 9.0+ survives.

    Args:
        effective_rating: Combined rating on 0-10 scale.
        current_age_years: Current age/staleness in years.
        config: Cleanup configuration with decay parameters.

    Returns:
        Days until the item becomes a candidate, or None if it will never become one
        (effective_rating >= masterpiece_rating).
    """
    if effective_rating >= config.masterpiece_rating:
        return None  # Protected forever

    # Age at which normal decay threshold crosses effective_rating
    if effective_rating <= config.max_rating:
        crossing_age = (
            (effective_rating - config.base_rating) / config.decay_step
            + config.min_age_years
        )
    else:
        crossing_age = float("inf")  # Threshold caps at max_rating

    # Age at which masterpiece gate applies (if rating < 9.0)
    masterpiece_age = config.masterpiece_only_after_years

    target_age = min(crossing_age, masterpiece_age)
    days_left = (target_age - current_age_years) * 365.25
    return max(0, int(days_left))


def _is_franchise_protected(provider_ids: dict) -> bool:
    """Check if a movie belongs to a TMDB collection (franchise).

    Args:
        provider_ids: ProviderIds dict from Emby item.

    Returns:
        True if TmdbCollection key is present (case-insensitive).
    """
    return "tmdbcollection" in {k.lower() for k in provider_ids}


def _is_path_protected(path: str, protect_paths: list[str]) -> bool:
    """Check if a movie's path matches any protection pattern.

    Empty strings in protect_paths are intentionally skipped to avoid
    matching every path (DA fix #7).

    Args:
        path: File system path of the movie.
        protect_paths: List of path substrings to protect.

    Returns:
        True if path contains any non-empty protection substring.
    """
    return any(p and p in path for p in protect_paths)


def _is_excluded_by_provider_id(provider_ids: dict, excluded_set: set[str]) -> bool:
    """Check if any of the movie's provider IDs appear in the exclusion set.

    Args:
        provider_ids: ProviderIds dict from Emby item.
        excluded_set: Set of provider ID values to exclude (e.g. IMDB IDs).

    Returns:
        True if any provider ID value matches the exclusion set.
    """
    return any(v in excluded_set for v in provider_ids.values())


def _get_movie_actor_names(people: list[dict]) -> set[str]:
    """Extract actor names from an Emby People list.

    Args:
        people: List of person dicts from Emby (each has "Name", "Type", etc.).

    Returns:
        Set of actor names (only Type == "Actor").
    """
    return {p["Name"] for p in people if p.get("Type") == "Actor"}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _paginated_fetch_library(
    client: httpx.Client,
    endpoint: str,
    base_params: dict,
    lib_id: str,
    lib_name: str,
    progress_label: str,
    progress_unit: str,
) -> list[dict]:
    """Paginate through a single library and return tagged items.

    Args:
        client: Configured httpx client with auth headers.
        endpoint: API endpoint URL.
        base_params: Base query parameters (without ParentId/StartIndex).
        lib_id: Library ID to fetch from.
        lib_name: Display name for progress bar and item tags.
        progress_label: Label for the tqdm progress bar.
        progress_unit: Unit name for the tqdm progress bar.

    Returns:
        List of items tagged with _library_name and _library_id.
    """
    items: list[dict] = []
    params = {**base_params, "ParentId": lib_id}
    start_index = 0
    page = 0

    progress: tqdm | None = None
    try:
        while True:
            params["StartIndex"] = str(start_index)
            try:
                response = make_http_request(client, "GET", endpoint, params=params)
                data = response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.error(f"Failed to fetch from library {lib_id} page {page}: {e}")
                break

            page_items = data.get("Items", [])
            total = data.get("TotalRecordCount", 0)
            count = len(page_items)

            if progress is None:
                progress = tqdm(total=total, desc=f"{progress_label} '{lib_name}'", unit=progress_unit)

            for item in page_items:
                item["_library_name"] = lib_name
                item["_library_id"] = lib_id

            items.extend(page_items)
            progress.update(count)
            logger.debug(f"Library {lib_id} page {page}: {count}/{total}")

            start_index += count
            page += 1

            if start_index >= total or count == 0:
                break
    finally:
        if progress is not None:
            progress.close()

    return items


def _paginated_fetch(
    client: httpx.Client,
    endpoint: str,
    base_params: dict,
    library_ids: list[str],
    lib_id_to_name: Optional[dict[str, str]] = None,
    progress_label: str = "Fetching",
    progress_unit: str = "item",
) -> list[dict]:
    """Generic paginated fetch across libraries with progress tracking.

    Handles pagination, error recovery, tqdm progress bars, and library-name
    tagging. Used by both movie and series fetch functions.

    Args:
        client: Configured httpx client with auth headers.
        endpoint: API endpoint URL.
        base_params: Base query parameters (without ParentId/StartIndex).
        library_ids: List of library IDs to scan.
        lib_id_to_name: Optional mapping of library ID to display name.
        progress_label: Label for the tqdm progress bar.
        progress_unit: Unit name for the tqdm progress bar.

    Returns:
        Flat list of all items with _library_name and _library_id tags.
    """
    all_items: list[dict] = []
    name_map = lib_id_to_name or {}

    for lib_id in library_ids:
        lib_name = name_map.get(lib_id, lib_id)
        lib_items = _paginated_fetch_library(
            client, endpoint, base_params, lib_id, lib_name,
            progress_label, progress_unit,
        )
        all_items.extend(lib_items)

    return all_items


def _resolve_primary_user_id(
    client: httpx.Client,
    base_url: str,
    username: Optional[str] = None,
) -> str:
    """Resolve primary user ID by username, with fallback to first user.

    DA fix #11: Always look up user by name rather than blindly using first user,
    so favorite-actor protection is scoped to the correct person.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        username: Username to look up (case-insensitive). Warns and falls back
            to first user if not found or not provided.

    Returns:
        Emby user ID string.
    """
    try:
        response = make_http_request(client, "GET", f"{base_url}/Users")
        users = response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to fetch users: {e}")
        return ""

    if not users:
        logger.error("No users found on Emby server.")
        return ""

    if username:
        for u in users:
            if u.get("Name", "").lower() == username.lower():
                logger.info(f"Primary user resolved: {u['Name']} ({u['Id']})")
                return u["Id"]
        logger.warning(
            f"User '{username}' not found in Emby, falling back to first user "
            f"({users[0].get('Name', '?')}) for favorite-actor protection."
        )
    else:
        logger.warning(
            "No --username provided; using first user for favorite actors "
            "(recommend: --username Barlog for accurate actor protection)."
        )
    return users[0]["Id"]


def _fetch_all_users(client: httpx.Client, base_url: str) -> list[dict]:
    """Fetch all user accounts from the Emby server.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.

    Returns:
        List of user dicts; empty list on error.
    """
    try:
        response = make_http_request(client, "GET", f"{base_url}/Users")
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to fetch users: {e}")
        return []


def _fetch_all_library_movies(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    lib_id_to_name: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Fetch all movies from specified libraries with full metadata.

    Uses the user-scoped endpoint so Size and People fields are returned.
    Applies IncludeItemTypes=Movie to avoid fetching TV series/episodes
    when --all-libraries is used (DA fix #12).

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        user_id: Emby user ID for the user-scoped endpoint.
        library_ids: List of library IDs to scan.
        lib_id_to_name: Optional mapping of library ID to display name.

    Returns:
        Flat list of all movie items with full metadata.
    """
    return _paginated_fetch(
        client,
        f"{base_url}/Users/{user_id}/Items",
        {
            "Recursive": "true",
            "IncludeItemTypes": "Movie",
            "Fields": "Path,ProviderIds,DateCreated,ProductionYear,CommunityRating,CriticRating,People,Size,Overview",
            "Limit": str(PAGE_SIZE),
        },
        library_ids,
        lib_id_to_name=lib_id_to_name,
        progress_label="Fetching",
        progress_unit="movie",
    )


_FALLBACK_TOP_N = 50  # used only when no Emby favorites exist at all


def _collect_community_favorite_people(
    client: httpx.Client,
    base_url: str,
    users: list[dict],
) -> set[str]:
    """Collect favorited People names from ALL users (union).

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        users: All user dicts from /Users.

    Returns:
        Set of actor names favorited by at least one user.
    """
    all_names: set[str] = set()
    users_with_fav_people = 0

    with tqdm(users, desc="Fetching favorite people", unit="user") as progress:
        for user in progress:
            uid = user.get("Id", "")
            if not uid:
                continue
            try:
                response = make_http_request(
                    client,
                    "GET",
                    f"{base_url}/Users/{uid}/Items",
                    params={
                        "IncludeItemTypes": "Person",
                        "Filters": "IsFavorite",
                        "Recursive": "true",
                    },
                )
                people = response.json().get("Items", [])
                if people:
                    names = {p["Name"] for p in people if p.get("Name")}
                    all_names |= names
                    users_with_fav_people += 1
                    progress.set_postfix_str(user.get("Name", uid))
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(f"Could not fetch favorite people for user {uid}: {e}")

    if all_names:
        print(f"Protecting movies with {len(all_names)} favorited people ({users_with_fav_people} users contributed).")
        logger.info(f"Community favorite people: {sorted(all_names)}")
    return all_names


def _count_actors_in_items(page_items: list[dict], actor_counter: Counter) -> None:
    """Count actor appearances in a page of movie items.

    Args:
        page_items: List of movie dicts with People field.
        actor_counter: Mutable Counter updated in place.
    """
    for item in page_items:
        for person in item.get("People", []):
            if person.get("Type") == "Actor":
                actor_counter[person["Name"]] += 1


def _build_top_actors_from_watch_history(
    client: httpx.Client,
    base_url: str,
    primary_user_id: str,
) -> set[str]:
    """Count actor appearances across the primary user's played movies.

    Falls back to this when no users have favorited any People.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        primary_user_id: Emby ID of the primary user.

    Returns:
        Set of top-N actor names by play frequency.
    """
    endpoint = f"{base_url}/Users/{primary_user_id}/Items"
    params: dict = {
        "Filters": "IsPlayed",
        "IncludeItemTypes": "Movie",
        "Fields": "People",
        "Recursive": "true",
        "Limit": str(PAGE_SIZE),
    }

    actor_counter: Counter = Counter()
    start_index = 0
    page = 0

    progress: tqdm | None = None
    try:
        while True:
            params["StartIndex"] = str(start_index)
            try:
                response = make_http_request(client, "GET", endpoint, params=params)
                data = response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.error(f"Failed to fetch played movies for actor counting (page {page}): {e}")
                break

            page_items = data.get("Items", [])
            total = data.get("TotalRecordCount", 0)
            count = len(page_items)

            if progress is None:
                progress = tqdm(total=total, desc=f"Building top-{_FALLBACK_TOP_N} actors from watch history", unit="movie")

            _count_actors_in_items(page_items, actor_counter)
            progress.update(count)
            start_index += count
            page += 1

            if start_index >= total or count == 0:
                break
    finally:
        if progress is not None:
            progress.close()

    top_actors = {name for name, _ in actor_counter.most_common(_FALLBACK_TOP_N)}
    logger.info(f"Favorite actors (watch history): {len(top_actors)} actors from {sum(actor_counter.values())} appearances")
    return top_actors


def _build_favorite_actors_set(
    client: httpx.Client,
    base_url: str,
    primary_user_id: str,
    all_users: Optional[list[dict]] = None,
) -> set[str]:
    """Build a set of actors to protect, using Emby native favorites first.

    Strategy (community then fallback):
    1. Fetch favorited People from ALL users (union of everyone's favorites).
    2. If nobody has any favorited people, fall back to top-50-by-watch-frequency
       for the primary user.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        primary_user_id: Emby ID of the primary user (used for fallback).
        all_users: All user dicts from /Users. If None, fetches them.

    Returns:
        Set of actor names to protect.
    """
    users = all_users or []
    if not users and primary_user_id:
        users = _fetch_all_users(client, base_url)

    community_names = _collect_community_favorite_people(client, base_url, users)
    if community_names:
        return community_names

    print("No users have favorited any people — falling back to watch-history top-N.")
    if not primary_user_id:
        logger.warning("No primary user ID; skipping favorite-actor protection.")
        return set()

    return _build_top_actors_from_watch_history(client, base_url, primary_user_id)


def _classify_movie_user_data(
    items: list[dict],
    played_ids: set[str],
    interested_ids: set[str],
) -> None:
    """Classify movie items as played or of interest based on UserData.

    Updates both sets in place.

    Args:
        items: List of item dicts with UserData from Emby API.
        played_ids: Mutable set of played item IDs (updated in place).
        interested_ids: Mutable set of interested item IDs (updated in place).
    """
    for item in items:
        item_id = item.get("Id", "")
        user_data = item.get("UserData", {})
        if user_data.get("Played"):
            played_ids.add(item_id)
        if user_data.get("IsFavorite") or (user_data.get("PlaybackPositionTicks", 0) > 0):
            interested_ids.add(item_id)


def _check_user_movie_status(
    client: httpx.Client,
    endpoint: str,
    uid: str,
    candidate_ids: list[str],
    chunk_size: int,
    played_ids: set[str],
    interested_ids: set[str],
) -> None:
    """Check play/interest status for all candidate movies for a single user.

    Args:
        client: Configured httpx client with auth headers.
        endpoint: User-specific Items endpoint URL.
        uid: User ID (for error logging).
        candidate_ids: Movie IDs to check.
        chunk_size: Number of IDs per batch request.
        played_ids: Mutable set updated in place.
        interested_ids: Mutable set updated in place.
    """
    for i in range(0, len(candidate_ids), chunk_size):
        chunk = candidate_ids[i : i + chunk_size]
        params = {
            "Ids": ",".join(chunk),
            "IncludeItemTypes": "Movie",
            "Fields": "UserData",
        }
        try:
            response = make_http_request(client, "GET", endpoint, params=params)
            items = response.json().get("Items", [])
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Failed to batch-check UserData for user {uid}: {e}")
            continue

        _classify_movie_user_data(items, played_ids, interested_ids)


def _check_play_and_interest_batch(
    client: httpx.Client,
    base_url: str,
    users: list[dict],
    candidate_ids: list[str],
) -> tuple[set[str], set[str]]:
    """Batch-check play status and interest across ALL users for candidate movies.

    DA fix #13: Single batch per user using the user-scoped endpoint which returns
    UserData (Played, IsFavorite, PlaybackPositionTicks) by default. Much more
    efficient than two separate passes.

    For each user, candidates are chunked into 100-item groups to avoid URL
    length limits (100 is the safe limit; PAGE_SIZE=1000 would be too long).

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        users: List of all user dicts from /Users.
        candidate_ids: List of Emby item IDs to check.

    Returns:
        Tuple of (played_ids, interested_ids) — sets of item IDs protected by
        any user having played or shown interest (favorite/in-progress).
    """
    if not candidate_ids or not users:
        return set(), set()

    played_ids: set[str] = set()
    interested_ids: set[str] = set()
    chunk_size = 100

    with tqdm(total=len(users), desc="Checking play status", unit="user") as progress:
        for user in users:
            uid = user.get("Id", "")
            uname = user.get("Name", uid)
            if not uid:
                progress.update(1)
                continue

            endpoint = f"{base_url}/Users/{uid}/Items"
            _check_user_movie_status(
                client, endpoint, uid, candidate_ids, chunk_size,
                played_ids, interested_ids,
            )

            progress.set_postfix_str(uname)
            progress.update(1)

    logger.info(
        f"Play/interest check: {len(played_ids)} played, {len(interested_ids)} interested "
        f"across {len(users)} users"
    )
    return played_ids, interested_ids


# ---------------------------------------------------------------------------
# Series-specific API helpers
# ---------------------------------------------------------------------------


def _fetch_all_library_series(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    lib_id_to_name: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Fetch all TV series from specified libraries with metadata.

    Uses the user-scoped endpoint so RecursiveItemCount is populated.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        user_id: Emby user ID for the user-scoped endpoint.
        library_ids: List of library IDs to scan.
        lib_id_to_name: Optional mapping of library ID to display name.

    Returns:
        Flat list of all series items with metadata.
    """
    return _paginated_fetch(
        client,
        f"{base_url}/Users/{user_id}/Items",
        {
            "Recursive": "true",
            "IncludeItemTypes": "Series",
            "Fields": "DateCreated,ProviderIds,Path,CommunityRating,CriticRating,ProductionYear,RecursiveItemCount",
            "Limit": str(PAGE_SIZE),
        },
        library_ids,
        lib_id_to_name=lib_id_to_name,
        progress_label="Fetching series",
        progress_unit="series",
    )


def _update_episode_map(episode_map: dict[str, str], page_items: list[dict]) -> None:
    """Update episode_map with the latest DateCreated per SeriesId from a page of episodes.

    Args:
        episode_map: Mutable mapping of SeriesId to latest DateCreated (updated in place).
        page_items: List of episode dicts from Emby API response.
    """
    for ep in page_items:
        series_id = ep.get("SeriesId", "")
        date_created = ep.get("DateCreated", "")
        if series_id and date_created:
            if date_created > episode_map.get(series_id, ""):
                episode_map[series_id] = date_created


def _fetch_library_episodes(
    client: httpx.Client,
    endpoint: str,
    base_params: dict,
    lib_id: str,
    episode_map: dict[str, str],
) -> None:
    """Paginate through all episodes in a single library and update the episode map.

    Args:
        client: Configured httpx client with auth headers.
        endpoint: Emby Items endpoint URL.
        base_params: Base query parameters for episode fetch.
        lib_id: Library ID to scan.
        episode_map: Mutable mapping updated in place with latest DateCreated per SeriesId.
    """
    params = {**base_params, "ParentId": lib_id}
    start_index = 0
    page = 0
    progress: tqdm | None = None

    try:
        while True:
            params["StartIndex"] = str(start_index)
            try:
                response = make_http_request(client, "GET", endpoint, params=params)
                data = response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.error(f"Failed to fetch episodes from library {lib_id} page {page}: {e}")
                break

            page_items = data.get("Items", [])
            total = data.get("TotalRecordCount", 0)
            count = len(page_items)

            if progress is None:
                progress = tqdm(total=total, desc="Fetching episodes for staleness", unit="ep")

            _update_episode_map(episode_map, page_items)
            progress.update(count)
            start_index += count
            page += 1

            if start_index >= total or count == 0:
                break
    finally:
        if progress is not None:
            progress.close()


def _build_last_episode_added_map(
    client: httpx.Client,
    base_url: str,
    library_ids: list[str],
) -> dict[str, str]:
    """Build a mapping of SeriesId to the most recent episode DateCreated.

    Emby's DateLastMediaAdded on Series objects is always null, so we fetch
    all episodes and compute the max DateCreated per SeriesId ourselves.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        library_ids: Library IDs to scan for episodes.

    Returns:
        Dict mapping SeriesId to the latest episode DateCreated ISO string.
    """
    episode_map: dict[str, str] = {}
    endpoint = f"{base_url}/Items"
    base_params = {
        "Recursive": "true",
        "IncludeItemTypes": "Episode",
        "Fields": "DateCreated,SeriesId",
        "Limit": str(PAGE_SIZE),
    }

    for lib_id in library_ids:
        _fetch_library_episodes(client, endpoint, base_params, lib_id, episode_map)

    logger.info(f"Episode staleness map: {len(episode_map)} series with episodes")
    return episode_map


def _classify_series_items(
    items: list[dict],
    played_ids: set[str],
    favorited_ids: set[str],
) -> None:
    """Classify series items as played or favorited based on UserData.

    A series is played if Played=True OR UnplayedItemCount < RecursiveItemCount
    (partial watch). Updates both sets in place.

    Args:
        items: List of series item dicts with UserData from Emby API.
        played_ids: Mutable set of played series IDs (updated in place).
        favorited_ids: Mutable set of favorited series IDs (updated in place).
    """
    for item in items:
        item_id = item.get("Id", "")
        user_data = item.get("UserData", {})
        total_eps = item.get("RecursiveItemCount", 0) or 0
        unplayed = user_data.get("UnplayedItemCount", total_eps)

        if user_data.get("Played") or (total_eps > 0 and unplayed < total_eps):
            played_ids.add(item_id)
        if user_data.get("IsFavorite"):
            favorited_ids.add(item_id)


def _check_user_series_status(
    client: httpx.Client,
    endpoint: str,
    uid: str,
    candidate_ids: list[str],
    chunk_size: int,
    played_ids: set[str],
    favorited_ids: set[str],
) -> None:
    """Check play/favorite status for all candidate series for a single user.

    Args:
        client: Configured httpx client with auth headers.
        endpoint: User-specific Items endpoint URL.
        uid: User ID (for error logging).
        candidate_ids: Series IDs to check.
        chunk_size: Number of IDs per batch request.
        played_ids: Mutable set updated in place.
        favorited_ids: Mutable set updated in place.
    """
    for i in range(0, len(candidate_ids), chunk_size):
        chunk = candidate_ids[i : i + chunk_size]
        params = {
            "Ids": ",".join(chunk),
            "IncludeItemTypes": "Series",
            "Fields": "UserData,RecursiveItemCount",
        }
        try:
            response = make_http_request(client, "GET", endpoint, params=params)
            items = response.json().get("Items", [])
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Failed to batch-check series UserData for user {uid}: {e}")
            continue

        _classify_series_items(items, played_ids, favorited_ids)


def _check_series_play_and_favorites(
    client: httpx.Client,
    base_url: str,
    users: list[dict],
    candidate_ids: list[str],
) -> tuple[set[str], set[str]]:
    """Batch-check play status and favorites for TV series across all users.

    A series is considered played if Played=True OR UnplayedItemCount < RecursiveItemCount
    for any user (partial watch counts).

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        users: List of all user dicts from /Users.
        candidate_ids: List of series item IDs to check.

    Returns:
        Tuple of (played_ids, favorited_ids).
    """
    if not candidate_ids or not users:
        return set(), set()

    played_ids: set[str] = set()
    favorited_ids: set[str] = set()
    chunk_size = 50

    with tqdm(total=len(users), desc="Checking series play status", unit="user") as progress:
        for user in users:
            uid = user.get("Id", "")
            uname = user.get("Name", uid)
            if not uid:
                progress.update(1)
                continue

            endpoint = f"{base_url}/Users/{uid}/Items"
            _check_user_series_status(
                client, endpoint, uid, candidate_ids, chunk_size, played_ids, favorited_ids
            )

            progress.set_postfix_str(uname)
            progress.update(1)

    logger.info(
        f"Series play/favorite check: {len(played_ids)} played, {len(favorited_ids)} favorited "
        f"across {len(users)} users"
    )
    return played_ids, favorited_ids


def _calculate_series_sizes(
    client: httpx.Client,
    base_url: str,
    series_ids: list[str],
) -> dict[str, int]:
    """Calculate total file size for each series by summing episode sizes.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        series_ids: List of series item IDs.

    Returns:
        Dict mapping series_id to total_bytes.
    """
    size_map: dict[str, int] = {}
    endpoint = f"{base_url}/Items"

    for series_id in series_ids:
        params = {
            "ParentId": series_id,
            "Recursive": "true",
            "IncludeItemTypes": "Episode",
            "Fields": "Size",
            "Limit": str(PAGE_SIZE),
        }
        total_size = 0
        start_index = 0

        while True:
            params["StartIndex"] = str(start_index)
            try:
                response = make_http_request(client, "GET", endpoint, params=params)
                data = response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(f"Failed to fetch episode sizes for series {series_id}: {e}")
                break

            page_items = data.get("Items", [])
            total_count = data.get("TotalRecordCount", 0)

            for ep in page_items:
                total_size += ep.get("Size") or 0

            start_index += len(page_items)
            if start_index >= total_count or len(page_items) == 0:
                break

        size_map[series_id] = total_size

    return size_map


def _probe_library_content(
    client: httpx.Client,
    base_url: str,
    library_id: str,
) -> tuple[int, int]:
    """Count Movies and Series in a library to determine content type.

    Uses Limit=0 queries to get TotalRecordCount without fetching items.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        library_id: Library ID to probe.

    Returns:
        Tuple of (movie_count, series_count).
    """
    endpoint = f"{base_url}/Items"
    counts: list[int] = []

    for item_type in ("Movie", "Series"):
        params = {
            "ParentId": library_id,
            "Recursive": "true",
            "IncludeItemTypes": item_type,
            "Limit": "0",
        }
        try:
            response = make_http_request(client, "GET", endpoint, params=params)
            counts.append(response.json().get("TotalRecordCount", 0))
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Failed to probe {item_type} count for library {library_id}: {e}")
            counts.append(0)

    return counts[0], counts[1]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_cleanup_args(
    host: Optional[str],
    api_key: Optional[str],
    libraries: list[str],
    all_libraries: bool,
    doit: bool,
    username: Optional[str],
    password: Optional[str],
) -> None:
    """Validate cleanup command arguments.

    Custom validation (not validate_required_arguments) to support --all-libraries
    and the nuanced username requirement (DA fix #4, #11).

    Args:
        host: Emby server URL.
        api_key: Emby API key.
        libraries: List of library names.
        all_libraries: If True, libraries list may be empty.
        doit: If True, deletions will be performed.
        username: Emby username (required for --doit, recommended always).
        password: Emby password (required for --doit).

    Raises:
        SystemExit: If required arguments are missing.
    """
    import sys

    errors = []
    if not host:
        errors.append("--host / DEDUPE_EMBY_HOST is required.")
    if not api_key:
        errors.append("--api-key / DEDUPE_EMBY_API_KEY is required.")
    if not libraries and not all_libraries:
        errors.append("Specify at least one --library / -l or use --all-libraries.")
    if doit and not username:
        errors.append("--username is required when --doit is set (needed for DELETE auth).")
    if doit and not password:
        errors.append("--password is required when --doit is set (needed for DELETE auth).")

    if errors:
        for err in errors:
            logger.error(err)
        sys.exit(1)

    if not doit and not username:
        logger.warning(
            "No --username provided; favorite-actor protection will use first Emby user. "
            "Recommend: --username Barlog for accurate results."
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _classify_movie_protection(
    movie: dict,
    age: float,
    effective_rating: float,
    threshold: float,
    played_ids: set[str],
    interested_ids: set[str],
    favorite_actors: set[str],
    config: CleanupConfig,
) -> Optional[str]:
    """Determine which protection layer (if any) shields a movie from cleanup.

    Returns the stats key to increment (e.g. "play_protected"), or None if the
    movie is a cleanup candidate.
    """
    item_id = movie.get("Id", "")
    if item_id in played_ids:
        return "play_protected"
    if item_id in interested_ids:
        return "interest_protected"
    if age >= config.masterpiece_only_after_years:
        return "rating_protected" if effective_rating >= config.masterpiece_rating else None
    if age < config.no_actor_protection_after_years and _get_movie_actor_names(movie.get("People", [])) & favorite_actors:
        return "actor_protected"
    if _is_franchise_protected(movie.get("ProviderIds", {})):
        return "franchise_protected"
    if _is_path_protected(movie.get("Path", ""), config.protect_paths):
        return "path_protected"
    if effective_rating >= threshold:
        return "rating_protected"
    return None


def _apply_movie_filters(
    movies: list[dict],
    played_ids: set[str],
    interested_ids: set[str],
    favorite_actors: set[str],
    config: CleanupConfig,
    stats: dict,
) -> tuple[list[CleanupCandidate], list[CleanupCandidate]]:
    """Apply per-movie protection filters and build CleanupCandidate objects.

    Filter layers applied: play, interest, masterpiece, actor, franchise, path,
    rating decay.  Updates stats counters in place.

    Also collects "near miss" movies — those protected only by rating (the last
    filter layer). These are the closest to becoming cleanup candidates.

    Args:
        movies: Movie dicts that passed age and exclusion filters (with _age_years set).
        played_ids: Set of item IDs played by any user.
        interested_ids: Set of item IDs favorited or in-progress by any user.
        favorite_actors: Set of protected actor names.
        config: Cleanup configuration.
        stats: Stats dict with filter counters (updated in place).

    Returns:
        Tuple of (candidates, near_miss) both sorted by age descending.
    """
    candidates: list[CleanupCandidate] = []
    near_miss: list[CleanupCandidate] = []

    for movie in movies:
        age = movie["_age_years"]
        threshold = _compute_rating_threshold(age, config)
        community_rating = movie.get("CommunityRating")
        critic_rating = movie.get("CriticRating")
        effective_rating = _compute_effective_rating(community_rating, critic_rating)

        protection = _classify_movie_protection(
            movie, age, effective_rating, threshold,
            played_ids, interested_ids, favorite_actors, config,
        )
        if protection:
            stats[protection] += 1
            # Collect near-miss: protected ONLY by rating (last filter layer)
            if protection == "rating_protected":
                near_miss.append(
                    CleanupCandidate(
                        item_id=movie.get("Id", ""),
                        name=movie.get("Name", "Unknown"),
                        year=movie.get("ProductionYear"),
                        rating=community_rating,
                        critic_rating=critic_rating,
                        threshold=threshold,
                        age_years=age,
                        library=movie.get("_library_name", "Unknown"),
                        size_bytes=movie.get("Size") or 0,
                        path=movie.get("Path", ""),
                    )
                )
            continue

        # 12+ year masterpiece path overrides the normal decay threshold
        if age >= config.masterpiece_only_after_years:
            threshold = config.masterpiece_rating

        candidates.append(
            CleanupCandidate(
                item_id=movie.get("Id", ""),
                name=movie.get("Name", "Unknown"),
                year=movie.get("ProductionYear"),
                rating=community_rating,
                critic_rating=critic_rating,
                threshold=threshold,
                age_years=age,
                library=movie.get("_library_name", "Unknown"),
                size_bytes=movie.get("Size") or 0,
                path=movie.get("Path", ""),
            )
        )

    candidates.sort(key=lambda c: c.age_years, reverse=True)
    # Sort near-miss by days_left (soonest removal first), then slice to configured limit
    for nm in near_miss:
        eff = _compute_effective_rating(nm.rating, nm.critic_rating)
        nm.days_left = _compute_days_until_candidate(eff, nm.age_years, config)
    near_miss.sort(key=lambda c: c.days_left if c.days_left is not None else float("inf"))
    if config.near_miss_count > 0:
        near_miss = near_miss[:config.near_miss_count]
    return candidates, near_miss


def _run_cleanup_pipeline(
    client: httpx.Client,
    base_url: str,
    config: CleanupConfig,
    library_ids: list[str],
    primary_user_id: str,
    lib_id_to_name: Optional[dict[str, str]] = None,
) -> tuple[list[CleanupCandidate], dict, list[CleanupCandidate]]:
    """Orchestrate the 7-layer filter pipeline.

    Filter order:
        1. Age filter — only movies older than min_age_years
        2. Exclusion filter — skip movies with excluded provider IDs
        3. Play + interest filter (batch across all users)
        4. Favorite-actor filter (primary user only)
        5. Franchise filter (TmdbCollection in ProviderIds)
        6. Path filter (protect_paths)
        7. Rating decay filter (CommunityRating vs age-adjusted threshold)

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        config: Cleanup configuration.
        library_ids: Library IDs to scan.
        primary_user_id: Emby ID of primary user for actor protection.
        lib_id_to_name: Optional mapping of library ID -> display name (Bug fix #1).

    Returns:
        Tuple of (candidates, protection_stats, near_miss) where near_miss
        contains movies protected only by rating.
    """
    stats: dict = {
        "total_analyzed": 0,
        "age_filtered": 0,
        "excluded_filtered": 0,
        "play_protected": 0,
        "interest_protected": 0,
        "actor_protected": 0,
        "franchise_protected": 0,
        "path_protected": 0,
        "rating_protected": 0,
        "final_candidates": 0,
    }

    # 1. Fetch all movies
    print(f"Scanning {len(library_ids)} library/libraries for movies...")
    all_movies = _fetch_all_library_movies(
        client, base_url, primary_user_id, library_ids, lib_id_to_name=lib_id_to_name
    )
    stats["total_analyzed"] = len(all_movies)
    logger.info(f"Total movies fetched: {len(all_movies)}")

    # 2. Age filter
    age_eligible = []
    for movie in all_movies:
        age = _compute_age_years(movie.get("DateCreated"))
        if age >= config.min_age_years:
            movie["_age_years"] = age
            age_eligible.append(movie)
        else:
            stats["age_filtered"] += 1

    logger.info(f"After age filter (>={config.min_age_years}yr): {len(age_eligible)} movies")

    # 3. Exclusion filter
    not_excluded = []
    for movie in age_eligible:
        if _is_excluded_by_provider_id(movie.get("ProviderIds", {}), config.excluded_provider_ids):
            stats["excluded_filtered"] += 1
        else:
            not_excluded.append(movie)

    logger.info(f"After exclusion filter: {len(not_excluded)} movies")

    # 4. Fetch all users (needed for both favorite-people and play/interest checks)
    all_users = _fetch_all_users(client, base_url)
    print(f"Fetching favorite people across {len(all_users)} users...")
    favorite_actors = _build_favorite_actors_set(
        client, base_url, primary_user_id, all_users=all_users
    )

    # 5. Check play + interest across all users (batch)
    candidate_ids = [m["Id"] for m in not_excluded]
    print(f"Checking play/interest status for {len(candidate_ids)} movies across all users...")
    played_ids, interested_ids = _check_play_and_interest_batch(
        client, base_url, all_users, candidate_ids
    )

    # 6. Apply per-movie filters (play, interest, actor, franchise, path, rating)
    candidates, near_miss = _apply_movie_filters(
        not_excluded, played_ids, interested_ids, favorite_actors, config, stats
    )
    stats["final_candidates"] = len(candidates)

    logger.info(f"Cleanup candidates: {len(candidates)}, near-miss: {len(near_miss)}")
    return candidates, stats, near_miss


def _apply_series_filters(
    series_list: list[dict],
    played_ids: set[str],
    favorited_ids: set[str],
    config: CleanupConfig,
    stats: dict,
) -> tuple[list[dict], list[dict]]:
    """Apply per-series protection filters (play, favorite, path, rating).

    Returns the series that survived all filters and near-miss series (protected
    only by rating). Updates stats counters in place.

    Args:
        series_list: Series dicts with _stale_years already set.
        played_ids: Set of series IDs that have been played by any user.
        favorited_ids: Set of series IDs favorited by any user.
        config: Cleanup configuration.
        stats: Stats dict with filter counters (updated in place).

    Returns:
        Tuple of (survivors, rating_protected) dicts.
    """
    survivors: list[dict] = []
    rating_protected: list[dict] = []
    for series in series_list:
        item_id = series.get("Id", "")
        path = series.get("Path", "")
        stale_years = series["_stale_years"]
        threshold = _compute_rating_threshold(stale_years, config)
        community_rating = series.get("CommunityRating")
        critic_rating = series.get("CriticRating")
        effective_rating = _compute_effective_rating(community_rating, critic_rating)

        if item_id in played_ids:
            stats["play_protected"] += 1
        elif item_id in favorited_ids:
            stats["favorite_protected"] += 1
        elif _is_path_protected(path, config.protect_paths):
            stats["path_protected"] += 1
        elif effective_rating >= threshold:
            stats["rating_protected"] += 1
            rating_protected.append(series)
        else:
            survivors.append(series)

    return survivors, rating_protected


def _build_series_candidates(
    pre_size_candidates: list[dict],
    size_map: dict[str, int],
    config: CleanupConfig,
) -> list[SeriesCleanupCandidate]:
    """Convert raw series dicts into SeriesCleanupCandidate objects.

    Args:
        pre_size_candidates: Series dicts that passed all filters.
        size_map: Mapping of series ID to total size in bytes.
        config: Cleanup configuration.

    Returns:
        List of SeriesCleanupCandidate sorted by staleness descending.
    """
    candidates: list[SeriesCleanupCandidate] = []
    for series in pre_size_candidates:
        item_id = series.get("Id", "")
        stale_years = series["_stale_years"]
        threshold = _compute_rating_threshold(stale_years, config)

        candidates.append(
            SeriesCleanupCandidate(
                item_id=item_id,
                name=series.get("Name", "Unknown"),
                year=series.get("ProductionYear"),
                rating=series.get("CommunityRating"),
                critic_rating=series.get("CriticRating"),
                threshold=threshold,
                stale_years=stale_years,
                last_episode_added=series.get("_last_episode_added"),
                episode_count=series.get("RecursiveItemCount") or 0,
                library=series.get("_library_name", "Unknown"),
                size_bytes=size_map.get(item_id, 0),
                path=series.get("Path", ""),
            )
        )

    candidates.sort(key=lambda c: c.stale_years, reverse=True)
    return candidates


def _run_series_cleanup_pipeline(
    client: httpx.Client,
    base_url: str,
    config: CleanupConfig,
    library_ids: list[str],
    primary_user_id: str,
    lib_id_to_name: Optional[dict[str, str]] = None,
) -> tuple[list[SeriesCleanupCandidate], dict, list[SeriesCleanupCandidate]]:
    """Orchestrate the series cleanup filter pipeline.

    Filter order:
        1. Staleness filter — skip series with recent episodes (< min_age_years)
        2. Exclusion filter — skip series with excluded provider IDs
        3. Play + favorite check across all users (batch)
        4. Path filter (protect_paths)
        5. Rating decay filter (CommunityRating vs staleness-adjusted threshold)

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        config: Cleanup configuration.
        library_ids: Library IDs to scan.
        primary_user_id: Emby ID of primary user.
        lib_id_to_name: Optional mapping of library ID to display name.

    Returns:
        Tuple of (candidates, stats, near_miss) where near_miss contains
        series protected only by rating.
    """
    stats: dict = {
        "total_analyzed": 0,
        "stale_filtered": 0,
        "excluded_filtered": 0,
        "play_protected": 0,
        "favorite_protected": 0,
        "path_protected": 0,
        "rating_protected": 0,
        "final_candidates": 0,
    }

    # 1. Fetch all series
    print(f"Scanning {len(library_ids)} library/libraries for series...")
    all_series = _fetch_all_library_series(
        client, base_url, primary_user_id, library_ids, lib_id_to_name=lib_id_to_name
    )
    stats["total_analyzed"] = len(all_series)
    logger.info(f"Total series fetched: {len(all_series)}")

    if not all_series:
        return [], stats

    # 2. Build last-episode-added map from episodes
    print("Building episode staleness map...")
    episode_map = _build_last_episode_added_map(client, base_url, library_ids)

    # 3. Staleness filter — skip series where last episode was added < min_age_years ago
    stale_eligible = []
    for series in all_series:
        series_id = series.get("Id", "")
        last_ep_date = episode_map.get(series_id)
        stale_years = _compute_age_years(last_ep_date)

        if stale_years >= config.min_age_years:
            series["_stale_years"] = stale_years
            series["_last_episode_added"] = last_ep_date
            stale_eligible.append(series)
        else:
            stats["stale_filtered"] += 1

    logger.info(f"After staleness filter (>={config.min_age_years}yr): {len(stale_eligible)} series")

    # 4. Exclusion filter
    not_excluded = []
    for series in stale_eligible:
        provider_ids = series.get("ProviderIds", {})
        if _is_excluded_by_provider_id(provider_ids, config.excluded_provider_ids):
            stats["excluded_filtered"] += 1
        else:
            not_excluded.append(series)

    logger.info(f"After exclusion filter: {len(not_excluded)} series")

    # 5. Fetch all users and check play + favorites across all users
    all_users = _fetch_all_users(client, base_url)
    candidate_ids = [s["Id"] for s in not_excluded]
    print(f"Checking play/favorite status for {len(candidate_ids)} series across all users...")
    played_ids, favorited_ids = _check_series_play_and_favorites(
        client, base_url, all_users, candidate_ids
    )

    # 6. Apply per-series filters (play, favorite, path, rating)
    pre_size_candidates, pre_near_miss = _apply_series_filters(
        not_excluded, played_ids, favorited_ids, config, stats
    )

    # 7. Calculate sizes for remaining candidates + near-miss
    all_need_sizes = pre_size_candidates + pre_near_miss
    size_map: dict[str, int] = {}
    if all_need_sizes:
        print(f"Calculating sizes for {len(all_need_sizes)} series (candidates + near-miss)...")
        size_map = _calculate_series_sizes(
            client, base_url, [s["Id"] for s in all_need_sizes]
        )

    # 8. Build final candidate objects
    candidates = _build_series_candidates(pre_size_candidates, size_map, config)
    near_miss = _build_series_candidates(pre_near_miss, size_map, config)
    # Sort near-miss by days_left (soonest removal first), then slice
    for nm in near_miss:
        eff = _compute_effective_rating(nm.rating, nm.critic_rating)
        nm.days_left = _compute_days_until_candidate(eff, nm.stale_years, config)
    near_miss.sort(key=lambda c: c.days_left if c.days_left is not None else float("inf"))
    if config.near_miss_count > 0:
        near_miss = near_miss[:config.near_miss_count]
    stats["final_candidates"] = len(candidates)

    logger.info(f"Series cleanup candidates: {len(candidates)}, near-miss: {len(near_miss)}")
    return candidates, stats, near_miss


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
        "threshold_str": f"{c.threshold:.1f}",
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
        "threshold_str": f"{c.threshold:.1f}",
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


def _process_near_miss_candidates(
    near_miss: Optional[list],
    is_series: bool,
    base_url: str,
    api_key: str,
) -> tuple[list[dict], int]:
    """Process near-miss candidates into template-friendly dicts."""
    dicts = []
    total_size = 0
    for c in (near_miss or []):
        if is_series and isinstance(c, SeriesCleanupCandidate):
            d = _series_candidate_to_dict(c, base_url, api_key)
        elif not is_series and isinstance(c, CleanupCandidate):
            d = _movie_candidate_to_dict(c, base_url, api_key)
        else:
            continue
        d["days_left"] = c.days_left
        d["days_left_str"] = _format_days_left(c.days_left)
        dicts.append(d)
        total_size += c.size_bytes
    return dicts, total_size


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

    series_dicts = []
    series_total_size = 0
    if series_candidates:
        series_dicts = [_series_candidate_to_dict(c, base_url, api_key) for c in series_candidates]
        series_total_size = sum(c.size_bytes for c in series_candidates)

    movie_nm_dicts, movie_nm_size = _process_near_miss_candidates(movie_near_miss, False, base_url, api_key)
    series_nm_dicts, series_nm_size = _process_near_miss_candidates(series_near_miss, True, base_url, api_key)

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
        movie_near_miss=movie_nm_dicts,
        movie_near_miss_size_human=format_size(movie_nm_size),
        series_near_miss=series_nm_dicts,
        series_near_miss_size_human=format_size(series_nm_size),
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_excluded_ids(raw: str) -> set[str]:
    """Parse comma-separated provider IDs into a set.

    Args:
        raw: Comma-separated string of provider IDs.

    Returns:
        Set of stripped, non-empty provider ID strings.
    """
    return {s.strip() for s in raw.split(",") if s.strip()} if raw else set()


def _normalize_protect_paths(raw: str | list | tuple) -> list[str]:
    """Normalize protect_paths input to a list of non-empty path strings.

    Args:
        raw: Protect paths as a comma-string, list, or tuple.

    Returns:
        List of non-empty path strings, defaulting to [_DEFAULT_PROTECT_PATH].
    """
    if isinstance(raw, str):
        paths = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        paths = list(raw)
    else:
        paths = []
    return paths or [_DEFAULT_PROTECT_PATH]


def _probe_and_split_libraries(
    client: httpx.Client,
    base_url: str,
    library_ids: list[str],
    lib_id_to_name: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Probe libraries to split into movie-containing and series-containing lists.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        library_ids: Library IDs to probe.
        lib_id_to_name: Mapping of library ID to display name.

    Returns:
        Tuple of (movie_lib_ids, series_lib_ids).
    """
    movie_lib_ids: list[str] = []
    series_lib_ids: list[str] = []

    for lib_id in library_ids:
        movie_count, series_count = _probe_library_content(client, base_url, lib_id)
        lib_name = lib_id_to_name.get(lib_id, lib_id)
        if movie_count > 0:
            movie_lib_ids.append(lib_id)
        if series_count > 0:
            series_lib_ids.append(lib_id)
        logger.info(f"Library '{lib_name}': {movie_count} movies, {series_count} series")

    return movie_lib_ids, series_lib_ids


def _perform_deletions(
    client: httpx.Client,
    base_url: str,
    candidates: list,
    username: Optional[str],
    password: Optional[str],
    api_key: Optional[str],
    label: str = "movies",
) -> None:
    """Delete cleanup candidates from Emby and record results.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        candidates: List of CleanupCandidate or SeriesCleanupCandidate objects.
        username: Emby username for DELETE auth.
        password: Emby password for DELETE auth.
        api_key: Emby API key.
        label: Label for progress bar (e.g. "movies" or "series").
    """
    logger.info(f"Deleting {len(candidates)} {label} candidates...")
    with tqdm(candidates, desc=f"Deleting {label}", unit=label.rstrip("s")) as progress:
        for candidate in progress:
            progress.set_postfix_str(candidate.name[:40])
            result = delete_item(
                client, base_url, candidate.item_id,
                doit=True, username=username, password=password, api_key=api_key,
            )
            candidate.deletion_result = result
            logger.info(f"Deleted {candidate.name}: {result.get('status', 'unknown')}")


def _resolve_library_ids(
    client: httpx.Client,
    base_url: str,
    api_key: Optional[str],
    libraries: list[str],
    all_libraries: bool,
) -> tuple[list[str], dict[str, str]]:
    """Resolve library IDs and build a name mapping.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        api_key: Emby API key.
        libraries: List of library names (empty if all_libraries is True).
        all_libraries: If True, use all libraries.

    Returns:
        Tuple of (library_ids, lib_id_to_name).
    """
    all_lib_infos = get_all_library_ids(client, base_url, api_key)
    lib_id_to_name: dict[str, str] = {
        lib["id"]: lib["name"] for lib in all_lib_infos if lib.get("id") and lib.get("name")
    }

    if all_libraries:
        library_ids = list(lib_id_to_name.keys())
    else:
        library_ids = get_library_ids_by_name(client, base_url, api_key, libraries)

    return library_ids, lib_id_to_name


_EMPTY_MOVIE_STATS: dict = {
    "total_analyzed": 0, "age_filtered": 0, "excluded_filtered": 0,
    "play_protected": 0, "interest_protected": 0, "actor_protected": 0,
    "franchise_protected": 0, "path_protected": 0, "rating_protected": 0,
    "final_candidates": 0,
}


def _output_report(
    output_format: str,
    candidates: list[CleanupCandidate],
    protection_stats: dict,
    config: CleanupConfig,
    series_candidates: Optional[list[SeriesCleanupCandidate]],
    series_stats: Optional[dict],
    movie_near_miss: Optional[list[CleanupCandidate]] = None,
    series_near_miss: Optional[list[SeriesCleanupCandidate]] = None,
) -> None:
    """Output cleanup report in the requested format (console or JSON).

    Args:
        output_format: "json" or "console".
        candidates: Movie candidates.
        protection_stats: Movie filter stage counts.
        config: Cleanup configuration.
        series_candidates: Series candidates (None if no series scanned).
        series_stats: Series filter stage counts.
        movie_near_miss: Movies protected only by rating.
        series_near_miss: Series protected only by rating.
    """
    series_for_report = series_candidates if series_stats else None
    if output_format == "json":
        report_data = _format_cleanup_report_json(
            candidates, protection_stats, config,
            series_candidates=series_for_report, series_stats=series_stats,
        )
        print(json.dumps(report_data, indent=2, default=str))
    else:
        _format_cleanup_report_console(
            candidates, protection_stats, config,
            series_candidates=series_for_report, series_stats=series_stats,
            movie_near_miss=movie_near_miss, series_near_miss=series_near_miss,
        )


def _execute_cleanup(
    client: httpx.Client,
    base_url: str,
    config: CleanupConfig,
    api_key: Optional[str],
    libraries: list[str],
    all_libraries: bool,
    username: Optional[str],
    password: Optional[str],
    output_format: str,
    html_report: bool,
    html_only: bool,
    no_open: bool,
    doit: bool,
) -> None:
    """Execute the full cleanup workflow after connection is established.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        config: Cleanup configuration.
        api_key: Emby API key.
        libraries: Library names to scan.
        all_libraries: If True, scan all libraries.
        username: Emby username.
        password: Emby password.
        output_format: "json" or "console".
        html_report: Whether to generate HTML report.
        html_only: HTML-only mode (no console, no browser).
        no_open: Whether to suppress browser auto-open.
        doit: Whether to perform actual deletions.
    """
    server_id = make_http_request(client, "GET", f"{base_url}/System/Info").json().get("Id", "")

    primary_user_id = _resolve_primary_user_id(client, base_url, username)
    if not primary_user_id:
        logger.error("Cannot resolve a valid Emby user ID; aborting cleanup.")
        return

    library_ids, lib_id_to_name = _resolve_library_ids(
        client, base_url, api_key, libraries, all_libraries
    )
    if not library_ids:
        logger.error("No library IDs resolved. Check library names and permissions.")
        return

    movie_lib_ids, series_lib_ids = _probe_and_split_libraries(
        client, base_url, library_ids, lib_id_to_name
    )

    # Run pipelines
    candidates, protection_stats, movie_near_miss = _run_cleanup_pipeline(
        client, base_url, config, movie_lib_ids, primary_user_id,
        lib_id_to_name=lib_id_to_name,
    ) if movie_lib_ids else ([], dict(_EMPTY_MOVIE_STATS), [])

    series_candidates, series_stats, series_near_miss = _run_series_cleanup_pipeline(
        client, base_url, config, series_lib_ids, primary_user_id,
        lib_id_to_name=lib_id_to_name,
    ) if series_lib_ids else ([], None, [])

    _output_report(
        output_format, candidates, protection_stats, config,
        series_candidates, series_stats,
        movie_near_miss=movie_near_miss, series_near_miss=series_near_miss,
    )

    if doit and candidates:
        _perform_deletions(client, base_url, candidates, username, password, api_key, "movies")
    if doit and series_candidates:
        _perform_deletions(client, base_url, series_candidates, username, password, api_key, "series")

    if html_report or html_only:
        series_for_report = series_candidates if series_stats else None
        html_content = _generate_cleanup_html_report(
            base_url, candidates, protection_stats, config, doit,
            server_id=server_id, api_key=api_key,
            series_candidates=series_for_report, series_stats=series_stats,
            movie_near_miss=movie_near_miss, series_near_miss=series_near_miss,
        )
        report_path = _save_cleanup_html_report(html_content, no_open=no_open)
        print(f"\nHTML report: {report_path}")


def run_cleanup_command(args) -> None:
    """Entry point for the cleanup subcommand.

    Orchestrates: validation -> connection -> pipeline -> report -> optional
    deletion -> HTML report -> logout.

    Args:
        args: Namespace with cleanup arguments (from argparse.Namespace or typer ctx).
    """
    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    api_key = getattr(args, "api_key", None)
    libraries = getattr(args, "library", []) or []
    all_libraries = getattr(args, "all_libraries", False)
    doit = getattr(args, "doit", False)
    username = getattr(args, "username", None)
    password = getattr(args, "password", None)

    excluded_provider_ids = _parse_excluded_ids(getattr(args, "exclude_ids", "") or "")
    protect_paths = _normalize_protect_paths(
        getattr(args, "protect_paths", [_DEFAULT_PROTECT_PATH])
    )

    _validate_cleanup_args(host, api_key, libraries, all_libraries, doit, username, password)

    base_url, resolved_port = handle_host_and_port(host, port)
    if resolved_port not in (80, 443):
        base_url = f"{base_url}:{resolved_port}"

    config = CleanupConfig(
        min_age_years=getattr(args, "min_age_years", 3),
        protect_paths=protect_paths,
        base_rating=getattr(args, "base_rating", 6.0),
        decay_step=getattr(args, "decay_step", 0.5),
        max_rating=getattr(args, "max_rating", 8.0),
        excluded_provider_ids=excluded_provider_ids,
        near_miss_count=getattr(args, "near_miss_count", 5),
    )

    client = httpx.Client(headers={"X-Emby-Token": api_key}, timeout=120)

    try:
        check_emby_connection(client, f"{base_url}/System/Info")
        _execute_cleanup(
            client, base_url, config, api_key, libraries, all_libraries,
            username, password,
            output_format=getattr(args, "format", "console"),
            html_report=getattr(args, "html_report", False),
            html_only=getattr(args, "html_only", False),
            no_open=getattr(args, "no_open", False),
            doit=doit,
        )
    except Exception as e:
        logger.error(f"Cleanup command failed: {e}")
        raise
    finally:
        if _client_mod.auth_state.token_for_delete and doit:
            logout(client, base_url, _client_mod.auth_state.token_for_delete)
        client.close()
