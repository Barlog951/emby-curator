"""
Media search functions for finding items in Emby libraries.

Provides search capabilities for:
- Searching by name (fuzzy and exact)
- Searching by provider IDs (IMDB, TMDB, TVDB)
- Searching for TV episodes by series/season/episode
"""

import re
from typing import Any, Optional

import httpx

from emby_dedupe.utils.logging import logger


def normalize_title(title: str) -> str:
    """Normalize a title for comparison.

    Args:
        title: The title to normalize.

    Returns:
        Normalized title (lowercase, no special chars, single spaces).
    """
    # Convert to lowercase
    normalized = title.lower()
    # Remove special characters except spaces
    normalized = re.sub(r'[^\w\s]', '', normalized)
    # Replace multiple spaces with single space
    normalized = re.sub(r'\s+', ' ', normalized)
    # Strip leading/trailing whitespace
    normalized = normalized.strip()
    return normalized


def titles_match(title1: str, title2: str, fuzzy: bool = True) -> bool:
    """Check if two titles match.

    Args:
        title1: First title.
        title2: Second title.
        fuzzy: If True, use fuzzy matching. If False, require exact match.

    Returns:
        True if titles match.
    """
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)

    if norm1 == norm2:
        return True

    if not fuzzy:
        return False

    # Check if one title contains the other (for subtitle handling)
    if norm1 in norm2 or norm2 in norm1:
        return True

    return False


def search_by_name(
    client: httpx.Client,
    host: str,
    api_key: str,
    name: str,
    year: Optional[int] = None,
    media_type: Optional[str] = None,
    library_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Search for media items by name.

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.
        name: Media name to search for.
        year: Optional release year to filter by.
        media_type: Optional media type ('Movie', 'Series', 'Episode').
        library_ids: Optional list of library IDs to search in.

    Returns:
        List of matching media items.
    """
    # Build search query
    params = {
        "api_key": api_key,
        "SearchTerm": name,
        "Recursive": "true",
        "Fields": "ProviderIds,Path,MediaStreams,DateCreated,DateModified,PremiereDate,ProductionYear,Tags,Overview,ParentId,SeriesName,ParentIndexNumber,IndexNumber",
    }

    if media_type:
        params["IncludeItemTypes"] = media_type

    # Note: Emby API doesn't support comma-separated ParentId in search
    # For multiple libraries, we'll search all and filter results
    # Only use ParentId for single library searches
    if library_ids and len(library_ids) == 1:
        params["ParentId"] = library_ids[0]

    if year:
        params["Years"] = str(year)

    url = f"{host}/Items"
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        items = data.get("Items", [])

        # Filter by name similarity
        matching_items = []
        for item in items:
            item_name = item.get("Name", "")
            if titles_match(name, item_name):
                matching_items.append(item)

        logger.debug(f"Found {len(matching_items)} items matching '{name}'")
        return matching_items

    except httpx.HTTPError as e:
        logger.error(f"Error searching for '{name}': {e}")
        return []


def search_by_provider_id(
    client: httpx.Client,
    host: str,
    api_key: str,
    provider_id: str,
    provider_type: str = "Imdb",
    library_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Search for media items by provider ID.

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.
        provider_id: Provider ID (e.g., 'tt1375666' for IMDB).
        provider_type: Provider type ('Imdb', 'Tmdb', 'Tvdb').
        library_ids: Optional list of library IDs to search in.

    Returns:
        List of matching media items.
    """
    # Normalize provider type
    provider_type_map = {
        'imdb': 'Imdb',
        'tmdb': 'Tmdb',
        'tvdb': 'Tvdb',
    }
    provider_type = provider_type_map.get(provider_type.lower(), provider_type)

    params = {
        "api_key": api_key,
        f"Any{provider_type}Id": provider_id,
        "Recursive": "true",
        "Fields": "ProviderIds,Path,MediaStreams,DateCreated,DateModified,PremiereDate,ProductionYear,Tags,Overview,ParentId,SeriesName,ParentIndexNumber,IndexNumber",
    }

    # Note: Emby API doesn't support comma-separated ParentId in search
    # For multiple libraries, we'll search all and filter results
    # Only use ParentId for single library searches
    if library_ids and len(library_ids) == 1:
        params["ParentId"] = library_ids[0]

    url = f"{host}/Items"
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        items = data.get("Items", [])
        logger.debug(f"Found {len(items)} items with {provider_type} ID '{provider_id}'")
        return items

    except httpx.HTTPError as e:
        logger.error(f"Error searching for {provider_type} ID '{provider_id}': {e}")
        return []


def search_tv_episode(
    client: httpx.Client,
    host: str,
    api_key: str,
    series_name: str,
    season: int,
    episode: int,
    library_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Search for a TV episode by series, season, and episode number.

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.
        series_name: Name of the TV series.
        season: Season number.
        episode: Episode number.
        library_ids: Optional list of library IDs to search in.

    Returns:
        List of matching episodes.
    """
    # First, search for the series
    params = {
        "api_key": api_key,
        "SearchTerm": series_name,
        "IncludeItemTypes": "Series",
        "Recursive": "true",
        "Fields": "ProviderIds",
    }

    # Note: Emby API doesn't support comma-separated ParentId in search
    # For multiple libraries, we'll search all and filter results
    # Only use ParentId for single library searches
    if library_ids and len(library_ids) == 1:
        params["ParentId"] = library_ids[0]

    url = f"{host}/Items"
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        series_items = data.get("Items", [])

        # Find matching series
        matching_series = None
        for series in series_items:
            if titles_match(series_name, series.get("Name", "")):
                matching_series = series
                break

        if not matching_series:
            logger.debug(f"Series '{series_name}' not found")
            return []

        # Search for the episode within the series
        series_id = matching_series["Id"]
        episode_params = {
            "api_key": api_key,
            "ParentId": series_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "ProviderIds,Path,MediaStreams,DateCreated,DateModified,PremiereDate,ProductionYear,Tags,Overview,ParentId,SeriesName,ParentIndexNumber,IndexNumber",
        }

        response = client.get(url, params=episode_params)
        response.raise_for_status()
        data = response.json()
        episodes = data.get("Items", [])

        # Filter to specific season/episode
        matching_episodes = []
        for ep in episodes:
            ep_season = ep.get("ParentIndexNumber")
            ep_number = ep.get("IndexNumber")

            if ep_season == season and ep_number == episode:
                # Add series name for consistency
                ep["SeriesName"] = matching_series.get("Name")
                matching_episodes.append(ep)

        logger.debug(f"Found {len(matching_episodes)} episodes matching S{season:02d}E{episode:02d}")
        return matching_episodes

    except httpx.HTTPError as e:
        logger.error(f"Error searching for episode: {e}")
        return []


def get_all_library_ids(
    client: httpx.Client,
    host: str,
    api_key: str,
) -> list[dict[str, str]]:
    """Get all library IDs from the Emby server.

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.

    Returns:
        List of dicts with 'id' and 'name' keys.
    """
    url = f"{host}/Library/VirtualFolders"
    params = {"api_key": api_key}

    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        libraries = []
        for folder in data:
            libraries.append({
                "id": folder.get("ItemId"),
                "name": folder.get("Name"),
            })

        logger.debug(f"Found {len(libraries)} libraries")
        return libraries

    except httpx.HTTPError as e:
        logger.error(f"Error getting library IDs: {e}")
        return []


def get_library_ids_by_name(
    client: httpx.Client,
    host: str,
    api_key: str,
    library_names: list[str],
) -> list[str]:
    """Get library IDs by name.

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.
        library_names: List of library names to find.

    Returns:
        List of library IDs.
    """
    all_libraries = get_all_library_ids(client, host, api_key)

    library_ids = []
    for name in library_names:
        for lib in all_libraries:
            if lib["name"].lower() == name.lower():
                library_ids.append(lib["id"])
                break
        else:
            logger.warning(f"Library '{name}' not found")

    return library_ids


def _search_provider_id_across_libraries(
    client: httpx.Client,
    host: str,
    api_key: str,
    provider_id: str,
    provider_type: str,
    library_ids: Optional[list[str]],
) -> list[dict[str, Any]]:
    """Search for provider ID across multiple libraries (one at a time to avoid timeouts).

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.
        provider_id: Provider ID.
        provider_type: Provider type ('Imdb', 'Tmdb', 'Tvdb').
        library_ids: List of library IDs to search.

    Returns:
        Combined list of matching items from all libraries.
    """
    if not library_ids:
        # Search all libraries at once (might timeout but we'll try)
        try:
            return search_by_provider_id(client, host, api_key, provider_id, provider_type, None)
        except Exception as e:
            logger.debug(f"Provider ID search across all libraries failed: {e}")
            return []

    # Search each library individually (fast, no timeouts)
    all_results = []
    for lib_id in library_ids:
        try:
            results = search_by_provider_id(client, host, api_key, provider_id, provider_type, [lib_id])
            all_results.extend(results)
        except Exception as e:
            logger.debug(f"Provider ID search in library {lib_id} failed: {e}")
            continue

    return all_results


def search_media(
    client: httpx.Client,
    host: str,
    api_key: str,
    name: Optional[str] = None,
    year: Optional[int] = None,
    imdb: Optional[str] = None,
    tmdb: Optional[str] = None,
    tvdb: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    library_names: Optional[list[str]] = None,
    skip_provider_search: bool = False,
) -> list[dict[str, Any]]:
    """Search for media using various criteria.

    Args:
        client: HTTP client.
        host: Emby server URL.
        api_key: Emby API key.
        name: Media name.
        year: Release year.
        imdb: IMDB ID.
        tmdb: TMDB ID.
        tvdb: TVDB ID.
        season: Season number (for TV episodes).
        episode: Episode number (for TV episodes).
        library_names: Library names to search in. None = all libraries.

    Returns:
        List of matching media items.
    """
    # Get library IDs if specified
    library_ids = None
    if library_names:
        library_ids = get_library_ids_by_name(client, host, api_key, library_names)
        if not library_ids:
            logger.warning("No valid library IDs found")
            return []

    # Search by provider ID first (most accurate)
    # For multiple libraries, search each individually to avoid timeouts
    if not skip_provider_search:
        if imdb:
            results = _search_provider_id_across_libraries(
                client, host, api_key, imdb, "Imdb", library_ids
            )
            if results:
                return results

        if tmdb:
            results = _search_provider_id_across_libraries(
                client, host, api_key, tmdb, "Tmdb", library_ids
            )
            if results:
                return results

        if tvdb:
            results = _search_provider_id_across_libraries(
                client, host, api_key, tvdb, "Tvdb", library_ids
            )
            if results:
                return results

    # Search by name
    if name:
        # Determine media type
        if season is not None and episode is not None:
            # TV episode search
            return search_tv_episode(client, host, api_key, name, season, episode, library_ids)
        elif season is not None:
            # Search for series, then filter by season
            results = search_by_name(client, host, api_key, name, year, "Series", library_ids)
            return results
        else:
            # Movie or general search
            return search_by_name(client, host, api_key, name, year, None, library_ids)

    return []
