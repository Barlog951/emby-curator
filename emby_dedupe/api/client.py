"""
Emby API client for interacting with the Emby server.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx
from httpx import URL
from tqdm import tqdm

from emby_dedupe.utils.constants import (
    DEFAULT_PORT_EMBY,
    DEFAULT_PORT_HTTP,
    DEFAULT_PORT_HTTPS,
    PAGE_SIZE,
)
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger


@dataclass
class AuthState:
    token_for_delete: Optional[str] = None
    user_id: Optional[str] = None


auth_state = AuthState()


def get_auth_token(
    client: httpx.Client, base_url: str, username: str, password: str
) -> Tuple[str, str]:
    """
    Retrieves the authentication token for a given username and password pair.

    Args:
        client (httpx.Client): The httpx client object.
        base_url (str): The base URL of the Emby server.
        username (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        Tuple[str, str]: The authentication token and user's GUID received from Emby server.

    Raises:
        EmbyServerConnectionError: If an error occurs while authenticating.
    """
    # Emby API requires SHA1 for password hash (explicitly not for security)
    sha1_password = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest()
    auth_url = f"{base_url}/Users/AuthenticateByName"
    data = {"Username": username, "Pw": password, "Password": sha1_password}
    headers = {
        "X-Emby-Authorization": 'MediaBrowser Client="media_cleaner", Device="Scripted Client", DeviceId="scripted_client", Version="0.1", Token=""',
        "Content-Type": "application/json",
    }

    try:
        response = client.post(auth_url, headers=headers, json=data)
        response.raise_for_status()
        response_data = response.json()
        access_token = response_data.get("AccessToken")
        user_id = response_data.get("User", {}).get("Id")
        if not access_token or not user_id:
            raise EmbyServerConnectionError(
                "Failed to retrieve access token or user ID from Emby server."
            )
        logger.info("Successfully authenticated with Emby server.")
        return access_token, user_id
    except httpx.HTTPStatusError as e:
        raise EmbyServerConnectionError(
            f"HTTP status error during authentication: {e.response.content.decode('utf-8')}"
        )
    except httpx.RequestError as e:
        raise EmbyServerConnectionError(
            f"HTTP request error during authentication: {e}"
        )


def logout(client: httpx.Client, base_url: str, auth_token: str) -> None:
    """
    Logs out from the Emby server to invalidate the authentication token.

    Args:
        client (httpx.Client): The httpx client object.
        base_url (str): The base URL of the Emby server.
        auth_token (str): The authentication token to be invalidated.
    """
    logout_url = f"{base_url}/Sessions/Logout"
    headers = {
        "X-Emby-Token": auth_token,
    }

    try:
        response = client.post(logout_url, headers=headers)
        response.raise_for_status()  # This will raise an HTTPError if the logout was unsuccessful.
        logger.info("Successfully logged out from Emby server.")
    except httpx.HTTPStatusError as e:
        # Handle cases where the HTTP response status indicates an error
        logger.error(f"Failed to log out due to an HTTP error: {str(e)}")
    except httpx.RequestError as e:
        # Handle cases where the HTTP request itself failed
        logger.error(f"Failed to log out due to a network error: {str(e)}")
    except httpx.TimeoutException as e:
        # Handle cases where the request timed out
        logger.error(f"Failed to log out due to a timeout: {str(e)}")
    except Exception as ex:
        # This is a catch-all for any other exceptions, which are not expected, but
        # provides a fail-safe to ensure the application does not crash.
        logger.error(f"An unexpected error occurred during logout: {str(ex)}")


def create_http_client(base_url: str, username: str, password: str) -> Tuple[httpx.Client, str, str]:
    """
    Create an httpx.Client instance and authenticate with the Emby server to receive
    an access token for subsequent API calls.

    Args:
        base_url (str): The base URL of the Emby server.
        username (str): The username for the Emby server.
        password (str): The password for the Emby server.

    Returns:
        Tuple[httpx.Client, str, str]: A client instance configured for communication with the Emby server,
            the authentication token, and the user ID.

    Raises:
        EmbyServerConnectionError: If an error occurs while authenticating.
    """
    client = httpx.Client()
    auth_token, user_id = get_auth_token(client, base_url, username, password)
    client.headers.update(
        {
            "X-Emby-Token": auth_token,
            # 'X-Emby-Authorization' header can be constructed here if required for all requests
        }
    )
    return client, auth_token, user_id


def check_emby_connection(client: httpx.Client, url: str) -> bool:
    """
    Check the connection to the Emby server by making a simple API request using the provided session.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        url (str): The URL to make the GET request to.

    Returns:
        bool: True if the server is reachable and the API key is valid, False otherwise.

    Raises:
        EmbyServerConnectionError: If there's an issue with connecting to the Emby server.
    """
    logger.debug(f"Checking connection to Emby server at {url}")
    try:
        make_http_request(client, "GET", url)
        logger.info("Successfully connected to the Emby server.")
        return True
    except httpx.HTTPStatusError as e:
        raise EmbyServerConnectionError(
            f"Failed to connect to Emby server: {e.response.content.decode('utf-8')}"
        )
    except httpx.RequestError as e:
        raise EmbyServerConnectionError(
            f"An error occurred while communicating with Emby server: {str(e)}"
        )


def get_library_id(
    client: httpx.Client, base_url: str, library_name: str
) -> Optional[str]:
    """
    Retrieves the ID of the specified library by name using a provided HTTP session.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        library_name (str): The name of the library to retrieve the ID for.

    Returns:
        Optional[str]: The ID of the library if found, else None.
    """
    url = f"{base_url}/Library/VirtualFolders"
    try:
        response = make_http_request(client, "GET", url)
        virtual_folders = response.json()

        for folder in virtual_folders:
            if folder.get("Name") == library_name:
                return folder.get("Id")

        logger.error(f"Library '{library_name}' not found.")
        return None  # Return None if library is not found

    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP error occurred while retrieving library ID: {e.response.content.decode('utf-8')}"
        )
    except httpx.RequestError as e:
        logger.error(f"HTTP request to Emby server failed: {str(e)}")

    return None  # Return None if any exception occurred


def handle_host_and_port(host: str, arg_port: Optional[int]) -> Tuple[str, int]:
    """
    Validate and handle the combination of host and port information.

    Args:
        host (str): The input host which may include protocol and port.
        arg_port (Optional[int]): The input port.

    Returns:
        Tuple[str, int]: The validated host and port.
    """
    url = URL(host)
    scheme = url.scheme or "http"
    final_host = (
        url.host or host
    )  # Default to using the original host if no scheme is provided.
    final_port = url.port

    # Determine default ports if not provided based on the scheme.
    if not final_port:
        if scheme == "https":
            final_port = DEFAULT_PORT_HTTPS
        elif scheme == "http":
            final_port = DEFAULT_PORT_HTTP
        else:
            final_port = DEFAULT_PORT_EMBY  # Fallback to default Emby port.

    if arg_port is not None and final_port != arg_port:
        logger.warning(
            f"The port number from the URL '{final_port}' is overridden by the command-line argument port '{arg_port}'."
        )
        final_port = arg_port

    return f"{scheme}://{final_host}", final_port


def ensure_authenticated_for_delete(
    client: httpx.Client, base_url: str, username: str, password: str
) -> Tuple[Optional[str], Optional[str]]:
    """
    Ensures the user is authenticated for DELETE operations. Will authenticate the user if it's the
    first time a DELETE operation is being attempted and will cache the token for future calls.

    Args:
        client (httpx.Client): The httpx client for making requests.
        base_url (str): The base URL of the Emby server.
        username (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        Tuple[Optional[str], Optional[str]]: The authentication token and user_id if authenticated, None otherwise.
    """
    if auth_state.token_for_delete is not None:
        return auth_state.token_for_delete, auth_state.user_id

    try:
        # Authenticate and save the token for future DELETE operations
        auth_state.token_for_delete, auth_state.user_id = get_auth_token(
            client, base_url, username, password
        )
        logger.info("Authenticated for DELETE operations.")
    except Exception as e:
        logger.error(f"Failed to authenticate for DELETE operations: {str(e)}")
        auth_state.token_for_delete = None
        auth_state.user_id = None

    return auth_state.token_for_delete, auth_state.user_id


def _process_item_ids_and_libraries(item_ids: list) -> tuple:
    """Process item IDs which may be dicts or strings, extract library names."""
    processed_ids = []
    library_names = {}  # Map item IDs to library names

    for item in item_ids:
        if isinstance(item, dict):
            processed_ids.append(item["id"])
            library_names[item["id"]] = item.get("library_name", "Unknown")
        else:
            processed_ids.append(item)
            library_names[item] = "Unknown"

    return processed_ids, library_names


def _add_library_names_to_items(items: list, library_names: dict) -> None:
    """Add library name to each item from the library names mapping."""
    for item in items:
        item_id = item.get("Id")
        if item_id and item_id in library_names:
            item["LibraryName"] = library_names[item_id]


def fetch_items_details(client: httpx.Client, base_url: str, item_ids: list) -> list:
    """
    Fetches the details for a list of media items by their IDs in one API request.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        item_ids (list): List of item IDs or objects with ID and library name to fetch details for.

    Returns:
        list: A list of media items with detailed information.
    """
    # Process item IDs which may be dicts or strings
    processed_ids, library_names = _process_item_ids_and_libraries(item_ids)

    # Comma-separated item IDs for the query parameter
    ids_param = ",".join(processed_ids)
    # Request ALL available fields to ensure we get date information
    # Add TV series specific fields (SeriesName, SeasonNumber, IndexNumber)
    url = f"{base_url}/Items"
    params = {
        "Fields": "MediaStreams,Path,ProviderIds,DateCreated,DateModified,PremiereDate,ProductionYear,Tags,Overview,ParentId,SeriesName,SeasonNumber,IndexNumber",
        "Ids": ids_param,
    }

    try:
        response = make_http_request(client, "GET", url, params=params)
        items = response.json().get("Items", [])

        # Debug log the fields in the first item to understand what's available
        if items and logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Item fields available: {list(items[0].keys())}")
            if 'DateCreated' in items[0]:
                logger.debug(f"DateCreated example: {items[0]['DateCreated']}")

        # Add the library name to each item
        _add_library_names_to_items(items, library_names)

        return items
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to fetch details for items: {e}")
        return []


def _fetch_total_item_count(
    client: httpx.Client, base_url: str, library_id: str
) -> int:
    """Fetch total number of items in a library.

    Args:
        client: The httpx client.
        base_url: Base URL of the Emby server.
        library_id: The library ID to query.

    Returns:
        Total number of items.

    Raises:
        httpx.HTTPStatusError, httpx.RequestError: If fetch fails.
    """
    url = f"{base_url}/Items"
    params = {
        "StartIndex": "0",
        "Limit": "0",
        "Recursive": "True",
        "ParentId": library_id,
        "Fields": "ProviderIds",
        "Is3D": "False",
        "IsFolder": "False",
    }
    response = make_http_request(client, "GET", url, params=params)
    total_items = response.json().get("TotalRecordCount", 0)
    logger.debug(f"Total media items to fetch: {total_items}")
    return total_items


def _fetch_paginated_items(
    client: httpx.Client,
    base_url: str,
    library_id: str,
    total_items: int,
    provider_tables: dict,
) -> None:
    """Fetch items in paginated manner and build provider tables.

    Args:
        client: The httpx client.
        base_url: Base URL of the Emby server.
        library_id: The library ID.
        total_items: Total number of items to fetch.
        provider_tables: Provider tables dict to populate (modified in-place).

    Raises:
        httpx.HTTPStatusError, httpx.RequestError: If fetch fails.
    """
    start_index = 0
    progress_bar = tqdm(total=total_items, desc="Fetching media items", unit="item")

    try:
        while start_index < total_items:
            url = f"{base_url}/Items"
            params = {
                "StartIndex": str(start_index),
                "Limit": str(PAGE_SIZE),
                "Recursive": "True",
                "ParentId": library_id,
                "Fields": "ProviderIds,SeriesName,ParentIndexNumber,IndexNumber",
                "Is3D": "False",
                "IsFolder": "False",
            }
            response = make_http_request(client, "GET", url, params=params)
            media_items = response.json().get("Items", [])
            build_provider_id_tables(media_items, provider_tables)
            processed_items = len(media_items)
            start_index += processed_items
            progress_bar.update(processed_items)
    finally:
        progress_bar.close()


def delete_item(
    client: httpx.Client,
    base_url: str,
    item_id: str,
    doit: bool,
    username: str,
    password: str,
    api_key: str,
) -> dict:
    """
    Attempts to delete a media item by its ID if the 'doit' flag is True.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): The base URL of the Emby server.
        item_id (str): The ID of the media item to be deleted.
        doit (bool): If True, actually performs the delete action, otherwise just simulates it.
        username (str): The username for authentication.
        password (str): The password for authentication.
        api_key (str): The API key for non-DELETE requests.

    Returns:
        dict: The deletion status and any error message if the deletion failed.
    """
    deletion_status = {"id": item_id, "status": "not_attempted", "error": None}
    if doit:
        # Ensure authentication is in place for DELETE operations
        auth_token, _ = ensure_authenticated_for_delete(
            client, base_url, username, password
        )
        if auth_token is None:
            deletion_status["status"] = "failed"
            deletion_status[
                "error"
            ] = "Authentication failed; cannot perform delete operations."
            return deletion_status

        client.headers.update({"X-Emby-Token": auth_token})
        url = f"{base_url}/Items/{item_id}"
        try:
            response = make_http_request(client, "DELETE", url)
            if response.is_success:
                deletion_status["status"] = "success"
            else:
                deletion_status.update(
                    {
                        "status": "failed",
                        "error": f"Status code: {response.status_code}, Response: {response.text}",
                    }
                )
                logger.error(
                    f"Deletion failed for item {item_id}, "
                    f"{url} [{response.status_code}] Response: {response.text}"
                )
        except Exception as e:
            deletion_status.update({"status": "failed", "error": str(e)})
            logger.error(f"Exception occurred during deletion of item {item_id}: {e}")
    else:
        deletion_status["status"] = "skipped"

    client.headers.update({"X-Emby-Token": api_key})

    return deletion_status


def fetch_and_process_media_items(
    client: httpx.Client, base_url: str, library_id: str, library_name: str = "Unknown"
) -> dict:
    """
    Fetches media items in a paginated manner and builds the provider ID tables.

    Args:
        client (httpx.Client): The httpx client configured for the Emby server communication.
        base_url (str): Base URL of the Emby server.
        library_id (str): The ID of the library/virtual folder to fetch media items from.
        library_name (str, optional): The name of the library for reporting. Defaults to "Unknown".

    Returns:
        dict: A dictionary with keys 'imdb', 'tvdb', 'tmdb', and 'library_name' containing provider ID mappings.
    """
    provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}, "series_episode": {}, "library_name": library_name}

    # Fetch total item count
    total_items = _fetch_total_item_count(client, base_url, library_id)

    # Fetch all items in pages and build provider tables
    _fetch_paginated_items(client, base_url, library_id, total_items, provider_tables)

    return provider_tables


def _create_item_info(item: dict, library_name: str, provider_id: str) -> dict:
    """Create item info dict with TV series metadata if applicable.

    Args:
        item: Media item from Emby.
        library_name: Name of the library.
        provider_id: Provider ID value for this item.

    Returns:
        Dict with id, library_name, and TV metadata if applicable.
    """
    item_info = {
        "id": item["Id"],
        "library_name": library_name,
        "provider_id": provider_id,
    }

    # Add TV series metadata if available
    if item.get("SeriesName"):
        item_info["is_episode"] = True
        item_info["series_name"] = item.get("SeriesName")
        item_info["season_number"] = item.get("ParentIndexNumber")
        item_info["episode_number"] = item.get("IndexNumber")
    else:
        item_info["is_episode"] = False

    return item_info


def _process_provider_id(provider, table_name, id_value, provider_tables, item, library_name, ignored_imdb_id):
    """
    Process a single provider ID and add to tables.

    Args:
        provider: Provider name (imdb, tvdb, tmdb).
        table_name: Table name in provider_tables.
        id_value: Provider ID value.
        provider_tables: Dictionary of provider tables.
        item: Media item.
        library_name: Library name.
        ignored_imdb_id: IMDb ID to ignore.
    """
    # Skip the IMDb ID if it is the one we're ignoring
    if provider == "imdb" and id_value == ignored_imdb_id:
        return

    if id_value:
        if id_value not in provider_tables[table_name]:
            provider_tables[table_name][id_value] = []

        item_info = _create_item_info(item, library_name, id_value)
        provider_tables[table_name][id_value].append(item_info)


def build_provider_id_tables(media_items: list, provider_tables: dict):
    """
    Builds tables that map provider IDs (Imdb, Tvdb, Tmdb) to lists of media item IDs,
    ignoring items with specific IMDb values.

    Args:
        media_items (list): A list of media items fetched from the Emby server.
        provider_tables (dict): A dictionary with keys 'imdb', 'tvdb', 'tmdb', and 'library_name' to store the mappings.
    """
    IGNORED_IMDB_ID = "tt0000000"  # IMDb ID to ignore
    library_name = provider_tables.get("library_name", "Unknown")

    for item in media_items:
        # Check the item is not a folder
        if item.get("IsFolder", False):
            continue
        provider_ids = item.get("ProviderIds", {})

        # Create case-insensitive lookup for provider IDs
        # Emby API returns inconsistent casing (e.g., "Imdb" vs "IMDB")
        provider_ids_lower = {k.lower(): v for k, v in provider_ids.items()}

        for provider, table_name in [
            ("imdb", "imdb"),
            ("tvdb", "tvdb"),
            ("tmdb", "tmdb"),
        ]:
            id_value = provider_ids_lower.get(provider)
            _process_provider_id(provider, table_name, id_value, provider_tables, item, library_name, IGNORED_IMDB_ID)

        # Also index episodes by SeriesName + Season + Episode for fallback grouping.
        # This catches duplicates where one copy has no provider IDs at all.
        series_name = item.get("SeriesName")
        season = item.get("ParentIndexNumber")
        episode_num = item.get("IndexNumber")
        if series_name and season is not None and episode_num is not None:
            se_key = f"{series_name}|S{season}E{episode_num}"
            if se_key not in provider_tables["series_episode"]:
                provider_tables["series_episode"][se_key] = []
            # Avoid adding the same item twice (already indexed via provider ID)
            existing_ids = {i["id"] for i in provider_tables["series_episode"][se_key]}
            if item["Id"] not in existing_ids:
                item_info = _create_item_info(item, library_name, se_key)
                provider_tables["series_episode"][se_key].append(item_info)
