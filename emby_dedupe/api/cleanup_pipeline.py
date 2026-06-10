"""
Cleanup pipeline — domain logic and Emby data fetching for library cleanup.

Implements the movie and series filter pipelines (age/staleness, exclusion,
play/interest, actors, franchise, path, rating decay) plus all the Emby API
helpers they need (paginated fetching, user resolution, play-status batch
checks, episode staleness maps, size calculation and library probing).

Consumed by the CLI entry point in emby_dedupe.cli.cleanup.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Optional

import httpx
from tqdm import tqdm

from emby_dedupe.models.cleanup import (
    CleanupCandidate,
    CleanupConfig,
    SeriesCleanupCandidate,
)
from emby_dedupe.utils.constants import PAGE_SIZE
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CRITIC_RATING_DIVISOR: float = 10.0  # CriticRating is 0-100 (RT %); divide to get 0-10

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
    if community_rating is not None and critic_rating is not None:
        return (community_rating + critic_rating / _CRITIC_RATING_DIVISOR) / 2.0
    if community_rating is not None:
        return community_rating
    if critic_rating is not None:
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
        return [], stats, []

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
