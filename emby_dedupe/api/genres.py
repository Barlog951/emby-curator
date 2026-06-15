"""
Emby genre management API functions.

Provides CRUD operations for genres in Emby media server:
- Fetching all genres and items with genre metadata
- Normalizing genre names to TMDB-standard canonical forms
- Updating item genres with atomic full-object POST
- Building audit reports for genre health analysis
"""

import copy
import difflib
from typing import Optional

import httpx

from emby_dedupe.api.pagination import paginate_emby_items
from emby_dedupe.utils.constants import GENRE_NORMALIZATION_MAP, PAGE_SIZE, TMDB_CANONICAL_GENRES
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger

# Full metadata fields for library-wide genre audit/reports.
# SeriesId/ParentIndexNumber/IndexNumber are episode-specific but harmless
# to request on movies/series — they simply come back as None.
_GENRE_FIELDS = (
    "Genres,GenreItems,ProviderIds,LockedFields,Overview,Tags,Studios,"
    "OfficialRating,CommunityRating,CriticRating,SortName,Taglines,"
    "DateCreated,PremiereDate,ProductionYear,EndDate,Status,AirDays,"
    "SeriesId,ParentIndexNumber,IndexNumber"
)

# Minimal fields for targeted batch fetch (webhook/item-ids mode)
_GENRE_FIELDS_BATCH = "Genres,GenreItems,ProviderIds,LockedFields"


def get_user_id(client: httpx.Client, base_url: str) -> str:
    """Fetch the first user's ID from the Emby server.

    Args:
        client: The httpx client configured with auth headers.
        base_url: Base URL of the Emby server.

    Returns:
        The ID string of the first user.

    Raises:
        EmbyServerConnectionError: If the request fails or no users are found.
    """
    try:
        response = make_http_request(client, "GET", f"{base_url}/Users")
        users = response.json()
        if not users:
            raise EmbyServerConnectionError("No users found on Emby server.")
        return users[0]["Id"]
    except httpx.HTTPStatusError as e:
        raise EmbyServerConnectionError(
            f"Failed to fetch users from Emby server: {e.response.content.decode('utf-8')}"
        )
    except httpx.RequestError as e:
        raise EmbyServerConnectionError(
            f"An error occurred while fetching users from Emby server: {str(e)}"
        )


def fetch_all_genres(client: httpx.Client, base_url: str) -> list[dict]:
    """Fetch all genres from the Emby server.

    Args:
        client: The httpx client configured with auth headers.
        base_url: Base URL of the Emby server.

    Returns:
        List of genre objects, each with at minimum "Name" and "Id" keys.
        Returns empty list on failure.
    """
    try:
        response = make_http_request(client, "GET", f"{base_url}/Genres", params={"Recursive": "true"})
        data = response.json()
        return data.get("Items", [])
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to fetch genres from Emby server: {e}")
        return []


def fetch_items_with_genres(
    client: httpx.Client,
    base_url: str,
    library_ids: list[str],
    user_id: str = "",
    item_types: str = "Movie,Series",
) -> list[dict]:
    """Fetch all media items with full metadata, paginated.

    Uses the user-scoped endpoint (/Users/{user_id}/Items) to return all fields
    in a single batch request — the same payload as fetch_full_item. This avoids
    a second per-item GET during normalization, halving the number of requests.

    Args:
        client: The httpx client configured with auth headers.
        base_url: Base URL of the Emby server.
        library_ids: List of library IDs to filter by. If empty, fetches all items.
        user_id: Emby user ID for the user-scoped endpoint. Falls back to /Items if empty.
        item_types: Comma-separated Emby item types to include.  Default is
            ``"Movie,Series"`` to match the genre-normalize workflow; the
            descriptions CLI passes ``"Movie,Series,Episode"`` to also fetch
            episodes for per-episode overview localization.

    Returns:
        Flat list of all media items with all metadata fields.
    """
    all_items: list[dict] = []
    base_params = {
        "Recursive": "true",
        "IncludeItemTypes": item_types,
        "Fields": _GENRE_FIELDS,
    }

    target_ids: list[Optional[str]] = list(library_ids) if library_ids else [None]

    for lib_id in target_ids:
        all_items.extend(_fetch_library_items(client, base_url, user_id, lib_id, base_params))

    return all_items


def fetch_items_by_ids(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    item_ids: list[str],
    chunk_size: int = 100,
    fields: Optional[str] = None,
) -> list[dict]:
    """Fetch specific items by ID using the batch endpoint.

    Uses the ``Ids`` query parameter on ``/Users/{user_id}/Items`` to retrieve
    multiple items per request.  Non-existent IDs are silently ignored by Emby
    (no 404 errors), so transient/deleted items simply don't appear in the result.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL with port.
        user_id: Emby user ID for the user-scoped endpoint.
        item_ids: List of Emby item IDs to fetch.
        chunk_size: Max IDs per request (default 100).
        fields: Optional comma-separated Fields query value.  Defaults to the
            minimal genre-only set; callers that need Overview/Taglines/Name
            (e.g. the descriptions CLI) should pass an explicit field list.

    Returns:
        List of full item dicts.  Items that no longer exist are omitted.
    """
    if not item_ids:
        return []

    fields_value = fields or _GENRE_FIELDS_BATCH
    all_items: list[dict] = []

    for i in range(0, len(item_ids), chunk_size):
        chunk = item_ids[i : i + chunk_size]
        endpoint = f"{base_url}/Users/{user_id}/Items"
        params = {
            "Ids": ",".join(chunk),
            "Fields": fields_value,
        }
        try:
            response = make_http_request(client, "GET", endpoint, params=params)
            data = response.json()
            all_items.extend(data.get("Items", []))
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"Failed to batch-fetch {len(chunk)} items: {e}")

    if len(all_items) < len(item_ids):
        logger.info(
            f"Batch fetch: {len(all_items)} of {len(item_ids)} items found "
            f"({len(item_ids) - len(all_items)} no longer exist)"
        )

    return all_items


def _fetch_library_items(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    lib_id: Optional[str],
    base_params: dict,
) -> list[dict]:
    """Fetch all pages of items for a single library (or all libraries if lib_id is None)."""
    items: list[dict] = []
    endpoint = f"{base_url}/Users/{user_id}/Items" if user_id else f"{base_url}/Items"
    params = {**base_params, "Limit": str(PAGE_SIZE)}
    if lib_id is not None:
        params["ParentId"] = lib_id

    for page_items, _total in paginate_emby_items(
        client, endpoint, params, error_context=f"fetch items for library {lib_id}"
    ):
        items.extend(page_items)

    return items


def fetch_full_item(
    client: httpx.Client, base_url: str, user_id: str, item_id: str
) -> dict:
    """Fetch the full item object (~52 fields) for a given item ID.

    Uses the user-scoped endpoint which returns the full item metadata.
    Note: /Items/{item_id} returns 404; /Users/{user_id}/Items/{item_id} is required.

    Args:
        client: The httpx client configured with auth headers.
        base_url: Base URL of the Emby server.
        user_id: The Emby user ID (from get_user_id).
        item_id: The item ID to fetch.

    Returns:
        Full item dict.

    Raises:
        EmbyServerConnectionError: On failure or non-2xx response.
    """
    url = f"{base_url}/Users/{user_id}/Items/{item_id}"
    try:
        response = make_http_request(client, "GET", url)
        return response.json()
    except httpx.HTTPStatusError as e:
        raise EmbyServerConnectionError(
            f"Failed to fetch item {item_id}: HTTP {e.response.status_code} "
            f"{e.response.content.decode('utf-8')}"
        )
    except httpx.RequestError as e:
        raise EmbyServerConnectionError(
            f"Failed to fetch item {item_id}: {str(e)}"
        )


def update_item_genres(
    client: httpx.Client,
    base_url: str,
    item_id: str,
    full_item: dict,
    new_genres: list[str],
    lock: bool = True,
) -> bool:
    """Update genres for a single Emby item using atomic full-object POST.

    Performs a no-op check first: if genres are already correct, returns False.
    Sends the full item payload (including read-only fields) back to Emby.
    This is confirmed safe on Emby v4.9.3.0 by smoke testing (21/21 tests passed).

    Args:
        client: The httpx client configured with auth headers.
        base_url: Base URL of the Emby server.
        item_id: The item ID to update.
        full_item: The full item dict (from fetch_full_item).
        new_genres: The new list of genre names to set.
        lock: If True, adds "Genres" to LockedFields to prevent automatic updates.

    Returns:
        True if genres were updated, False if no change was needed or on failure.
    """
    old_genres = full_item.get("Genres", [])

    # No-op check: skip if genres are already correct
    if sorted(new_genres) == sorted(old_genres):
        return False

    payload = copy.deepcopy(full_item)
    payload["Genres"] = new_genres
    # Emby auto-assigns numeric IDs; we set Id to "" and let Emby resolve
    payload["GenreItems"] = [{"Name": g, "Id": ""} for g in new_genres]

    if lock:
        locked_fields = payload.setdefault("LockedFields", [])
        if "Genres" not in locked_fields:
            locked_fields.append("Genres")

    try:
        resp = client.post(f"{base_url}/Items/{item_id}", json=payload)
        if resp.is_success:
            logger.info(
                f"Updated genres for {full_item.get('Name', item_id)}: "
                f"{old_genres} \u2192 {new_genres}"
            )
            return True
        logger.error(
            f"Failed to update genres for {full_item.get('Name', item_id)}: "
            f"HTTP {resp.status_code}"
        )
        return False
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(
            f"Failed to update genres for {full_item.get('Name', item_id)}: {e}"
        )
        return False


def normalize_genre_name(name: str, normalization_map: dict) -> str:
    """Return the canonical genre name for a given input.

    Looks up the lowercase version of name in normalization_map. If found,
    returns the canonical form; otherwise returns name unchanged (preserving
    original casing).

    Args:
        name: The genre name to normalize.
        normalization_map: Dict mapping lowercase variant names to canonical names.

    Returns:
        Canonical genre name if found in map, otherwise name unchanged.
    """
    return normalization_map.get(name.lower(), name)


def _check_normalization_candidate(
    item: dict, genres: list, variant_groups: dict
) -> Optional[dict]:
    """Check if an item needs genre normalization and update variant_groups in place.

    Returns a candidate dict if the item needs normalization, else None.
    """
    suggested_genres = [normalize_genre_name(g, GENRE_NORMALIZATION_MAP) for g in genres]
    if not any(s != g for g, s in zip(genres, suggested_genres)):
        return None

    for genre, canonical in zip(genres, suggested_genres):
        if canonical != genre:
            variant_groups.setdefault(canonical, set()).add(genre)

    return {
        "item_id": item.get("Id", ""),
        "item_name": item.get("Name", ""),
        "current_genres": genres,
        "suggested_genres": suggested_genres,
    }


def build_genre_audit(items: list[dict]) -> dict:
    """Analyze all items and build a genre health audit report.

    Args:
        items: List of media item dicts, each expected to have a "Genres" key.

    Returns:
        Dict with keys:
            genre_counts: {genre_name: count} — items per genre (not sorted)
            items_without_genres: list of items with Genres == []
            normalization_candidates: list of dicts for items needing normalization
                {"item_id", "item_name", "current_genres", "suggested_genres"}
            variant_groups: {canonical: list_of_variants_found}
            total_items: int
            total_without_genres: int
    """
    genre_counts: dict[str, int] = {}
    items_without_genres: list[dict] = []
    normalization_candidates: list[dict] = []
    variant_groups: dict[str, set] = {}

    for item in items:
        genres = item.get("Genres", [])

        if not genres:
            items_without_genres.append(item)

        for genre in genres:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1

        candidate = _check_normalization_candidate(item, genres, variant_groups)
        if candidate:
            normalization_candidates.append(candidate)

    # Convert sets to lists for JSON serialisation
    variant_groups_serialisable = {k: list(v) for k, v in variant_groups.items()}

    return {
        "genre_counts": genre_counts,
        "items_without_genres": items_without_genres,
        "normalization_candidates": normalization_candidates,
        "variant_groups": variant_groups_serialisable,
        "total_items": len(items),
        "total_without_genres": len(items_without_genres),
    }


def suggest_genre_mappings(genre_counts: dict) -> list[dict]:
    """Identify genres not in the TMDB canonical list and suggest possible mappings.

    Compares all genres found in the library against TMDB_CANONICAL_GENRES.
    Genres already handled by GENRE_NORMALIZATION_MAP are excluded (they are
    shown as normalization candidates, not unknown genres).
    Uses difflib fuzzy matching to suggest likely canonical equivalents.

    Args:
        genre_counts: {genre_name: item_count} from build_genre_audit.

    Returns:
        List of dicts sorted by item count (descending):
            {"genre": str, "count": int, "suggestions": list[str]}
        Empty suggestions list means no close match was found.
    """
    unknown = []
    for genre, count in genre_counts.items():
        if genre in TMDB_CANONICAL_GENRES:
            continue
        if genre.lower() in GENRE_NORMALIZATION_MAP:
            continue  # Already handled by normalization
        suggestions = difflib.get_close_matches(
            genre, TMDB_CANONICAL_GENRES, n=3, cutoff=0.6
        )
        unknown.append({"genre": genre, "count": count, "suggestions": suggestions})

    return sorted(unknown, key=lambda x: x["count"], reverse=True)
