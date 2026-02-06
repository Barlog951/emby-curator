"""
Missing episodes detection and management for Emby server.
This module provides functionality to identify and report missing episodes
without interfering with the existing deduplication functionality.
"""

from typing import Dict, List, Optional

import httpx
from tqdm import tqdm

from emby_dedupe.api.client import make_http_request
from emby_dedupe.utils.logging import logger

# Constants
UNKNOWN_SERIES_NAME = "Unknown Series"


def _get_user_id(client: httpx.Client, base_url: str) -> Optional[str]:
    """Get the first available user ID for API calls."""
    try:
        users_url = f"{base_url}/Users"
        users_response = make_http_request(client, "GET", users_url)
        users_data = users_response.json()
        if users_data and len(users_data) > 0:
            user_id = users_data[0].get("Id")
            logger.debug(f"Using User ID: {user_id}")
            return user_id
    except (httpx.HTTPError, ValueError):
        logger.debug("Could not get user ID, using direct Items endpoint")
    return None


def _fetch_series_metadata(client: httpx.Client, base_url: str, series_id: str, user_id: str | None) -> Dict:
    """Fetch metadata for a single series."""
    try:
        # Use user-specific endpoint if available, otherwise fallback to direct endpoint
        if user_id:
            url = f"{base_url}/Users/{user_id}/Items/{series_id}"
        else:
            url = f"{base_url}/Items/{series_id}"

        params = {
            "Fields": "ChannelMappingInfo,BasicSyncInfo,ProviderIds,OriginalTitle,SortName,ForcedSortName"
        }

        response = make_http_request(client, "GET", url, params=params)
        series_data = response.json()

        series_name = series_data.get("Name", UNKNOWN_SERIES_NAME)
        original_series_name = (
            series_data.get("OriginalTitle") or
            series_data.get("OriginalName") or
            series_data.get("SortName") or
            series_name
        )

        logger.debug(f"Series {series_id}: '{series_name}' -> '{original_series_name}'")

        return {
            "SeriesName": series_name,
            "OriginalSeriesName": original_series_name
        }

    except Exception as e:
        logger.debug(f"Error fetching metadata for series {series_id}: {e}")
        return {
            "SeriesName": UNKNOWN_SERIES_NAME,
            "OriginalSeriesName": UNKNOWN_SERIES_NAME
        }


def enrich_episodes_with_series_metadata(client: httpx.Client, base_url: str, episodes: List[Dict]) -> None:
    """
    Enrich missing episodes with proper series metadata including original titles.
    This is needed when the direct /Shows/Missing endpoint doesn't include series metadata.

    Mutates the episodes list in-place by adding SeriesName and OriginalSeriesName fields.
    """
    if not episodes:
        return

    # Get unique series IDs from episodes
    unique_series_ids = {episode.get("SeriesId") for episode in episodes if episode.get("SeriesId")}

    logger.debug(f"Fetching metadata for {len(unique_series_ids)} unique series")

    # Get user ID for API calls
    user_id = _get_user_id(client, base_url)

    # Fetch metadata for all unique series
    series_metadata = {}
    for series_id in unique_series_ids:
        if series_id:  # Type narrowing to satisfy mypy
            series_metadata[series_id] = _fetch_series_metadata(client, base_url, str(series_id), user_id)

    # Enrich episodes with series metadata
    for episode in episodes:
        series_id = episode.get("SeriesId")
        if series_id and series_id in series_metadata:
            episode["SeriesName"] = series_metadata[series_id]["SeriesName"]
            episode["OriginalSeriesName"] = series_metadata[series_id]["OriginalSeriesName"]
        elif not episode.get("SeriesName"):
            episode["SeriesName"] = UNKNOWN_SERIES_NAME
            episode["OriginalSeriesName"] = UNKNOWN_SERIES_NAME


def _parse_episodes_response(missing_data) -> List[Dict]:
    """Parse missing episodes response handling different formats."""
    if isinstance(missing_data, dict) and "Items" in missing_data:
        return missing_data["Items"]
    elif isinstance(missing_data, list):
        return missing_data
    else:
        logger.warning(f"Unexpected response format from missing episodes endpoint: {type(missing_data)}")
        return []


def _try_authenticated_missing_episodes(base_url: str, url: str, params: dict, username: str, password: str) -> Optional[List[Dict]]:
    """Try to fetch missing episodes with user authentication."""
    try:
        from emby_dedupe.api.client import create_http_client
        auth_client, _, user_id = create_http_client(base_url, username, password)
        logger.debug("Successfully authenticated for missing episodes")

        if user_id:
            params["UserId"] = user_id

        response = auth_client.get(url, params=params, timeout=60.0)
        logger.debug(f"Missing episodes response status with auth: {response.status_code}")

        if response.status_code == 200:
            missing_data = response.json()
            episodes = _parse_episodes_response(missing_data)
            logger.info(f"Found {len(episodes)} missing episodes via authenticated endpoint")
            enrich_episodes_with_series_metadata(auth_client, base_url, episodes)
            return episodes

    except Exception as e:
        logger.debug(f"User authentication failed for missing episodes: {e}")

    return None


def _try_api_key_missing_episodes(client: httpx.Client, base_url: str, url: str, params: dict) -> Optional[List[Dict]]:
    """Try to fetch missing episodes with API key authentication."""
    try:
        response = client.get(url, params=params, timeout=60.0)
        logger.debug(f"Missing episodes response status: {response.status_code}")

        if response.status_code == 200:
            missing_data = response.json()
            episodes = _parse_episodes_response(missing_data)
            logger.info(f"Found {len(episodes)} missing episodes via direct endpoint")
            enrich_episodes_with_series_metadata(client, base_url, episodes)
            return episodes

        elif response.status_code == 401:
            logger.warning("Authentication failed for missing episodes endpoint - may require user authentication")
        elif response.status_code == 403:
            logger.warning("Access forbidden for missing episodes endpoint")
        elif response.status_code == 404:
            logger.warning("Missing episodes endpoint not found - trying alternative method")
        else:
            logger.warning(f"Unexpected status code {response.status_code} from missing episodes endpoint")

    except httpx.TimeoutException:
        logger.warning("Missing episodes endpoint timed out - trying alternative method")
    except httpx.HTTPStatusError as e:
        logger.warning(f"HTTP error fetching missing episodes: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.warning(f"Request error fetching missing episodes: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error fetching missing episodes: {e}")

    return None


def get_missing_episodes(client: httpx.Client, base_url: str, library_id: Optional[str] = None, username: Optional[str] = None, password: Optional[str] = None) -> List[Dict]:
    """
    Retrieves the list of missing episodes from the Emby server.
    Falls back to alternative methods if the direct endpoint doesn't work.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        library_id (str, optional): The ID of the library to filter missing episodes.

    Returns:
        List[Dict]: A list of missing episodes with detailed information.
    """
    url = f"{base_url}/Shows/Missing"

    params = {
        "ImageTypeLimit": "1",
        "EnableImageTypes": "Primary,Backdrop,Thumb",
        "Fields": "Overview",
        "IncludeSpecials": "false",
        "IncludeUnaired": "false"
    }

    if library_id:
        params["ParentId"] = library_id

    logger.debug(f"Attempting to fetch missing episodes from: {url}")

    # Try user authentication first if available
    if username and password:
        logger.debug("Attempting with user authentication for missing episodes endpoint")
        episodes = _try_authenticated_missing_episodes(base_url, url, params, username, password)
        if episodes is not None:
            return episodes

    # Try with API key authentication
    episodes = _try_api_key_missing_episodes(client, base_url, url, params)
    if episodes is not None:
        return episodes

    # Fallback: Try alternative approach
    logger.info("Trying alternative approach to find missing episodes...")
    return get_missing_episodes_alternative(client, base_url, library_id)


def get_missing_episodes_alternative(client: httpx.Client, base_url: str, library_id: Optional[str] = None) -> List[Dict]:
    """
    Alternative method to find missing episodes by analyzing existing series.
    This is a fallback when the direct /Shows/Missing endpoint doesn't work.
    """
    try:
        # Get all TV series from the library
        url = f"{base_url}/Items"
        params = {
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": "BasicSyncInfo,ProviderIds,DateCreated,Overview,OriginalTitle,SortName,ForcedSortName"
        }

        if library_id:
            params["ParentId"] = library_id

        logger.debug(f"Fetching TV series from library {library_id}")

        # Build URL with params
        param_str = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{url}?{param_str}"

        response = make_http_request(client, "GET", full_url)
        series_data = response.json()

        series_list = series_data.get("Items", []) if isinstance(series_data, dict) else []
        logger.info(f"Found {len(series_list)} TV series in library")

        if not series_list:
            logger.info("No TV series found in the specified library")
            return []

        # Allow analyzing all series (remove the 50 series limit)
        # Note: This may take longer but will find more missing episodes
        missing_episodes = []

        logger.info(f"Analyzing all {len(series_list)} series for missing episodes...")

        with tqdm(total=len(series_list), desc="Analyzing series", unit="series") as progress:
            for series in series_list:
                series_id = series.get("Id")
                series_name = series.get("Name", UNKNOWN_SERIES_NAME)
                # Try multiple possible field names for original title
                original_series_name = (
                    series.get("OriginalTitle") or
                    series.get("OriginalName") or
                    series.get("SortName") or
                    series_name
                )

                if not series_id:
                    progress.update(1)
                    continue

                try:
                    # Use the correct Emby API endpoint to get missing episodes for this series
                    series_missing_episodes = get_missing_episodes_for_series(client, base_url, series_id, series_name, original_series_name)
                    missing_episodes.extend(series_missing_episodes)

                except Exception as e:
                    logger.debug(f"Error analyzing series {series_name}: {e}")

                progress.update(1)

        logger.info(f"Found {len(missing_episodes)} missing episodes using alternative method")
        return missing_episodes

    except Exception as e:
        logger.error(f"Error in alternative missing episodes method: {e}")
        return []


def get_missing_episodes_for_series(client: httpx.Client, base_url: str, series_id: str, series_name: str, original_series_name: str = None) -> List[Dict]:
    """
    Get missing episodes for a specific series using Emby's /Shows/Missing endpoint.
    This is the correct way to get missing episodes as determined by Emby's metadata.
    """
    try:
        url = f"{base_url}/Shows/Missing"
        params = {
            "ParentId": series_id,  # This is the key - filter by series ID
            "ImageTypeLimit": "1",
            "EnableImageTypes": "Primary,Backdrop,Thumb",
            "Fields": "Overview,ParentIndexNumber,IndexNumber",
            "IncludeSpecials": "false",  # Skip Season 0 specials
            "IncludeUnaired": "false"    # Skip future episodes
        }

        response = make_http_request(client, "GET", url, params=params)
        missing_data = response.json()

        # Handle response format
        if isinstance(missing_data, dict) and "Items" in missing_data:
            episodes = missing_data["Items"]
        elif isinstance(missing_data, list):
            episodes = missing_data
        else:
            logger.debug(f"Unexpected response format from missing episodes for series {series_id}: {type(missing_data)}")
            return []

        # Add series information to episodes
        for episode in episodes:
            episode["SeriesName"] = series_name
            episode["OriginalSeriesName"] = original_series_name or series_name
            episode["SeriesId"] = series_id
            episode["IsMissing"] = True

        logger.debug(f"Found {len(episodes)} missing episodes for series '{series_name}' (ID: {series_id})")
        return episodes

    except Exception as e:
        logger.debug(f"Error getting missing episodes for series {series_id}: {e}")
        return []


def get_expected_episodes_from_metadata(client: httpx.Client, base_url: str, series_id: str, season_number: int) -> List[Dict]:
    """
    Get expected episodes for a season based on metadata from external providers.
    This uses Emby's metadata to determine what episodes should exist, not gaps in numbering.
    """
    try:
        # Skip Season 0 (specials) as they often have inflated metadata
        if season_number == 0:
            logger.debug(f"Skipping Season 0 (specials) for series {series_id} - often has inflated metadata")
            return []

        # Try to get episode metadata from Emby's provider data
        url = f"{base_url}/Shows/{series_id}/Episodes"
        params = {
            "SeasonNumber": str(season_number),
            "Fields": "Overview,PremiereDate,ProviderIds,IsMissing",
        }

        response = make_http_request(client, "GET", url, params=params)
        episodes_data = response.json()

        expected_episodes = []
        if isinstance(episodes_data, dict) and "Items" in episodes_data:
            items = episodes_data["Items"]
        elif isinstance(episodes_data, list):
            items = episodes_data
        else:
            logger.debug(f"Unexpected response format for expected episodes: {type(episodes_data)}")
            return []

        # Only include episodes that are explicitly marked as missing by Emby
        for episode in items:
            if (episode.get("IndexNumber") and
                episode.get("ParentIndexNumber") == season_number and
                episode.get("IsMissing")):
                expected_episodes.append(episode)

        logger.debug(f"Found {len(expected_episodes)} expected missing episodes for series {series_id}, season {season_number}")
        return expected_episodes

    except Exception as e:
        logger.debug(f"Error getting expected episodes for series {series_id}, season {season_number}: {e}")
        # Fallback: if we can't get metadata, don't assume any episodes are missing
        return []


def get_season_episodes(client: httpx.Client, base_url: str, season_id: str) -> List[Dict]:
    """
    Get all episodes for a specific season.
    """
    try:
        url = f"{base_url}/Items"
        params = {
            "ParentId": season_id,
            "IncludeItemTypes": "Episode",
            "Fields": "BasicSyncInfo,IndexNumber,ParentIndexNumber"
        }

        param_str = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{url}?{param_str}"

        response = make_http_request(client, "GET", full_url)
        episodes_data = response.json()

        return episodes_data.get("Items", []) if isinstance(episodes_data, dict) else []

    except Exception as e:
        logger.debug(f"Error fetching episodes for season {season_id}: {e}")
        return []


def get_series_episodes(client: httpx.Client, base_url: str, series_id: str) -> List[Dict]:
    """
    Retrieves all episodes for a specific TV series.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        series_id (str): The ID of the TV series.

    Returns:
        List[Dict]: A list of episodes for the specified series.
    """
    url = f"{base_url}/Shows/{series_id}/Episodes"
    try:
        response = make_http_request(client, "GET", url)
        episodes_data = response.json()

        # Handle both direct Items list and paginated response
        if isinstance(episodes_data, dict) and "Items" in episodes_data:
            return episodes_data["Items"]
        elif isinstance(episodes_data, list):
            return episodes_data
        else:
            logger.warning(f"Unexpected response format from series episodes endpoint for series {series_id}")
            return []

    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to fetch episodes for series {series_id}: {e}")
        return []


def get_series_seasons(client: httpx.Client, base_url: str, series_id: str) -> List[Dict]:
    """
    Retrieves all seasons for a specific TV series.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        series_id (str): The ID of the TV series.

    Returns:
        List[Dict]: A list of seasons for the specified series.
    """
    url = f"{base_url}/Shows/{series_id}/Seasons"
    try:
        response = make_http_request(client, "GET", url)
        seasons_data = response.json()

        # Handle both direct Items list and paginated response
        if isinstance(seasons_data, dict) and "Items" in seasons_data:
            return seasons_data["Items"]
        elif isinstance(seasons_data, list):
            return seasons_data
        else:
            logger.warning(f"Unexpected response format from series seasons endpoint for series {series_id}")
            return []

    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to fetch seasons for series {series_id}: {e}")
        return []


def analyze_missing_episodes(missing_episodes: List[Dict]) -> Dict:
    """
    Analyzes missing episodes data to provide statistics and groupings.

    Args:
        missing_episodes (List[Dict]): List of missing episodes from Emby.

    Returns:
        Dict: Analysis results with statistics and grouped data.
    """
    if not missing_episodes:
        return {
            "total_missing": 0,
            "series_count": 0,
            "by_series": {},
            "by_season": {},
            "statistics": {}
        }

    # Deduplicate episodes first to avoid showing duplicates in report
    unique_episodes = {}
    for episode in missing_episodes:
        series_name = episode.get("SeriesName", UNKNOWN_SERIES_NAME)
        season_number = episode.get("ParentIndexNumber", 0)
        episode_number = episode.get("IndexNumber", 0)

        # Create unique key based on series, season, and episode number
        unique_key = f"{series_name}|S{season_number}E{episode_number}"

        # Keep the first occurrence of each unique episode
        if unique_key not in unique_episodes:
            unique_episodes[unique_key] = episode

    deduplicated_episodes = list(unique_episodes.values())

    # Log deduplication results
    if len(missing_episodes) != len(deduplicated_episodes):
        logger.info(f"Deduplicated {len(missing_episodes)} episodes down to {len(deduplicated_episodes)} unique episodes")

    # Debug: Log NCIS missing episodes specifically
    ncis_episodes = [ep for ep in deduplicated_episodes if ep.get("SeriesName") == "NCIS"]
    if ncis_episodes:
        logger.info(f"Found {len(ncis_episodes)} missing NCIS episodes")
        season_counts: dict[int, int] = {}
        for ep in ncis_episodes:
            season = ep.get("ParentIndexNumber", 0)
            season_counts[season] = season_counts.get(season, 0) + 1
        logger.info(f"NCIS missing episodes by season: {dict(sorted(season_counts.items()))}")

    by_series = {}
    by_season = {}

    logger.info(f"Analyzing {len(deduplicated_episodes)} missing episodes")

    with tqdm(total=len(deduplicated_episodes), desc="Analyzing missing episodes", unit="episode") as progress:
        for episode in deduplicated_episodes:
            series_name = episode.get("SeriesName", UNKNOWN_SERIES_NAME)
            series_id = episode.get("SeriesId", "")
            season_number = episode.get("ParentIndexNumber", 0)
            episode_number = episode.get("IndexNumber", 0)
            episode_name = episode.get("Name", f"Episode {episode_number}")

            # Group by series
            if series_name not in by_series:
                by_series[series_name] = {
                    "series_id": series_id,
                    "original_series_name": episode.get("OriginalSeriesName", episode.get("SeriesName", "")),
                    "episodes": [],
                    "total_missing": 0
                }

            episode_info = {
                "id": episode.get("Id", ""),
                "name": episode_name,
                "season": season_number,
                "episode": episode_number,
                "air_date": episode.get("PremiereDate", ""),
                "overview": episode.get("Overview", "")
            }

            by_series[series_name]["episodes"].append(episode_info)
            by_series[series_name]["total_missing"] += 1

            # Group by season
            season_key = f"{series_name} - Season {season_number}"
            if season_key not in by_season:
                by_season[season_key] = {
                    "series_name": series_name,
                    "series_id": series_id,
                    "season_number": season_number,
                    "episodes": [],
                    "total_missing": 0
                }

            by_season[season_key]["episodes"].append(episode_info)
            by_season[season_key]["total_missing"] += 1

            progress.update(1)

    # Calculate statistics using deduplicated count
    statistics = {
        "total_missing_episodes": len(deduplicated_episodes),
        "total_series_affected": len(by_series),
        "total_seasons_affected": len(by_season),
        "most_missing_series": max(by_series.items(), key=lambda x: x[1]["total_missing"])[0] if by_series else "None",
        "average_missing_per_series": len(deduplicated_episodes) / len(by_series) if by_series else 0
    }

    return {
        "total_missing": len(deduplicated_episodes),
        "series_count": len(by_series),
        "by_series": by_series,
        "by_season": by_season,
        "statistics": statistics
    }


def process_missing_episodes_for_libraries(
    client: httpx.Client,
    base_url: str,
    library_names: List[str],
    get_library_id_func,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict:
    """
    Process missing episodes for multiple libraries, reusing existing library ID resolution.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        library_names (List[str]): List of library names to process.
        get_library_id_func: Function to get library ID by name (reuses existing function).

    Returns:
        Dict: Combined analysis results for all libraries.
    """
    all_missing_episodes = []
    processed_libraries = []

    for library_name in library_names:
        logger.info(f"Processing missing episodes for library: {library_name}")

        library_id = get_library_id_func(client, base_url, library_name)
        if library_id is None:
            logger.error(f"Unable to find library '{library_name}'. Skipping.")
            continue

        missing_episodes = get_missing_episodes(client, base_url, library_id, username, password)
        all_missing_episodes.extend(missing_episodes)
        processed_libraries.append(library_name)

        logger.info(f"Found {len(missing_episodes)} missing episodes in library '{library_name}'")

    if not all_missing_episodes:
        logger.info("No missing episodes found in any library.")
        return analyze_missing_episodes([])

    logger.info(f"Total missing episodes across {len(processed_libraries)} libraries: {len(all_missing_episodes)}")

    analysis = analyze_missing_episodes(all_missing_episodes)
    analysis["processed_libraries"] = processed_libraries

    return analysis
