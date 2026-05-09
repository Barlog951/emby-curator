"""
Metadata processing utilities for Emby media items.
"""

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from emby_dedupe.api.quality_compare import detect_ai_upscale, detect_source_quality
from emby_dedupe.utils.logging import logger


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format (KB, MB, GB).

    Args:
        size_bytes: File size in bytes.

    Returns:
        Formatted string with size and unit.
    """
    if not size_bytes:
        return "unknown"

    if size_bytes >= 1073741824:  # 1 GB
        return f"{size_bytes / 1073741824:.2f} GB"
    elif size_bytes >= 1048576:  # 1 MB
        return f"{size_bytes / 1048576:.2f} MB"
    elif size_bytes >= 1024:  # 1 KB
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} bytes"


def _parse_iso_date(date_str: str, include_time: bool = True) -> Optional[str]:
    """Parse ISO 8601 date string to formatted date.

    Args:
        date_str: ISO 8601 formatted date string.
        include_time: Whether to include time in output.

    Returns:
        Formatted date string or None if parsing fails.
    """
    if not isinstance(date_str, str) or 'T' not in date_str:
        return None

    try:
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%Y-%m-%d %H:%M") if include_time else dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _try_parse_date_field(item: Dict[str, Any], field_name: str, include_time: bool = True) -> Optional[str]:
    """Try to parse a date from a specific field.

    Args:
        item: Media item dict.
        field_name: Name of the field to parse.
        include_time: Whether to include time in output.

    Returns:
        Formatted date string or None if field missing or parsing fails.
    """
    if field_name not in item or not item[field_name]:
        return None

    date_str = item[field_name]
    try:
        parsed = _parse_iso_date(date_str, include_time=include_time)
        if parsed:
            logger.debug(f"Found {field_name}: {parsed}")
            return parsed
        else:
            # Non-ISO format
            result = str(date_str)
            logger.debug(f"Found {field_name} (non-ISO): {result}")
            return result
    except (ValueError, TypeError) as e:
        logger.warning(f"Error parsing {field_name}: {e}")
        return None


def _try_fallback_date_fields(item: Dict[str, Any]) -> Optional[str]:
    """Try to get date from fallback fields (PremiereDate, EndDate, ProductionYear).

    Args:
        item: Media item dict.

    Returns:
        Formatted date string or None if not found.
    """
    date_fields = ["PremiereDate", "EndDate", "ProductionYear"]
    for field in date_fields:
        if field not in item or not item[field]:
            continue

        date_str = item[field]
        if field == "ProductionYear":
            result = f"{date_str}-01-01 (year only)"
            logger.debug(f"Using ProductionYear as date: {result}")
            return result

        try:
            # Try ISO 8601 format
            parsed = _parse_iso_date(date_str, include_time=False)
            if parsed:
                logger.debug(f"Found date in {field}: {parsed}")
                return parsed
            else:
                result = str(date_str)
                logger.debug(f"Found date in {field} (non-ISO): {result}")
                return result
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing date from {field}: {e}")

    return None


def _try_filesystem_date(item: Dict[str, Any]) -> Optional[str]:
    """Try to get date from filesystem modification time.

    Args:
        item: Media item dict.

    Returns:
        Formatted date string or None if file doesn't exist or error occurs.
    """
    if "Path" not in item or not item["Path"]:
        return None

    try:
        file_path = item["Path"]
        if os.path.exists(file_path):
            file_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(file_path)))
            result = f"{file_time} (file modified time)"
            logger.debug(f"Using file modification time: {result}")
            return result
    except OSError as e:
        logger.warning(f"Error getting file modification time: {e}")

    return None


def _try_any_date_field(item: Dict[str, Any]) -> Optional[str]:
    """Last resort: try any field with 'date' in name.

    Args:
        item: Media item dict.

    Returns:
        Formatted date string or None if not found.
    """
    for key in item.keys():
        if "date" in key.lower() and key not in ["DateCreated", "DateModified", "PremiereDate"]:
            try:
                result = f"{str(item[key])} (from {key})"
                logger.debug(f"Using alternative date field {key}: {result}")
                return result
            except (ValueError, TypeError, OSError) as e:
                logger.debug(f"Could not use date from field {key}: {e}")

    return None


def _resolve_date_added(item: Dict[str, Any]) -> str:
    """Resolve date added from multiple possible sources with fallback chain.

    Tries in priority order:
    1. DateCreated (when item added to Emby)
    2. DateModified (when item last modified)
    3. PremiereDate, EndDate, ProductionYear
    4. File system modification time
    5. Any field with "date" in name

    Args:
        item: Media item dict.

    Returns:
        Formatted date string or "unknown" if not found.
    """
    # Try primary date fields first
    date = _try_parse_date_field(item, "DateCreated", include_time=True)
    if date:
        return date

    date = _try_parse_date_field(item, "DateModified", include_time=True)
    if date:
        return date

    # Try fallback date fields
    date = _try_fallback_date_fields(item)
    if date:
        return date

    # Try filesystem modification time
    date = _try_filesystem_date(item)
    if date:
        return date

    # Last resort: any field with 'date' in name
    date = _try_any_date_field(item)
    if date:
        return date

    return "unknown"


def _extract_premiere_date(item: Dict[str, Any]) -> str:
    """Extract premiere date (original release date) from item.

    Args:
        item: Media item dict.

    Returns:
        Formatted premiere date or "unknown" if not found.
    """
    if "PremiereDate" not in item:
        return "unknown"

    date_str = item["PremiereDate"]
    parsed = _parse_iso_date(date_str, include_time=False)
    if parsed:
        return parsed
    return date_str


def _build_tv_metadata(item: Dict[str, Any], quality_desc: Dict[str, Any]) -> None:
    """Add TV series metadata to quality description dict (in-place).

    Args:
        item: Media item dict.
        quality_desc: Quality description dict to modify.
    """
    if not item.get("SeriesName"):
        quality_desc["is_episode"] = False
        return

    quality_desc["is_episode"] = True
    quality_desc["series_name"] = item.get("SeriesName", "unknown")
    quality_desc["season_number"] = item.get("ParentIndexNumber", "unknown")
    quality_desc["episode_number"] = item.get("IndexNumber", "unknown")

    # Enhance the display info
    if quality_desc["season_number"] != "unknown" and quality_desc["episode_number"] != "unknown":
        quality_desc["episode_info"] = f"S{quality_desc['season_number']}E{quality_desc['episode_number']}"
    else:
        quality_desc["episode_info"] = "Unknown episode"


def _extract_video_quality(video_stream: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract video quality information from a video stream."""
    if not video_stream:
        return {
            "codec": "unknown",
            "resolution": "unknown",
            "bitrate": "unknown",
            "bitdepth": "unknown",
            "interlaced": "unknown",
        }
    return {
        "codec": video_stream.get("Codec", "unknown"),
        "resolution": video_stream.get("DisplayTitle", "unknown"),
        "bitrate": video_stream.get("BitRate", "unknown"),
        "bitdepth": video_stream.get("BitDepth", "unknown"),
        "interlaced": video_stream.get("IsInterlaced", "unknown"),
    }


def _extract_audio_quality(audio_stream: Optional[Dict[str, Any]], languages: List[str]) -> Dict[str, Any]:
    """Extract audio quality information from an audio stream and language list."""
    if not audio_stream:
        return {
            "codec": "unknown",
            "channels": "unknown",
            "bitrate": "unknown",
            "languages": languages if languages else ["unknown"],
        }
    return {
        "codec": audio_stream.get("Codec", "unknown"),
        "channels": audio_stream.get("Channels", "unknown"),
        "bitrate": audio_stream.get("BitRate", "unknown"),
        "languages": languages if languages else ["unknown"],
    }


def get_quality_description(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get the quality description from the media item.

    Args:
        item (dict): A media item containing MediaStreams.

    Returns:
        dict: A description of the quality of the given media item,
              or empty if critical details are missing.
    """
    # Check for 'MediaStreams' presence before continuing
    if "MediaStreams" not in item:
        logger.warning(
            f"Item ID {item.get('Id', 'unknown')} does not have 'MediaStreams'."
        )
        return {}

    # Safe extraction of streams
    video_stream = next((s for s in item["MediaStreams"] if s["Type"] == "Video"), None)
    audio_stream = next((s for s in item["MediaStreams"] if s["Type"] == "Audio"), None)

    # Find all audio languages
    audio_streams = [s for s in item.get("MediaStreams", []) if s["Type"] == "Audio"]
    languages = []
    for stream in audio_streams:
        lang = stream.get("Language", "unknown")
        if lang and lang != "unknown" and lang not in languages:
            languages.append(lang)

    # Construct the quality description safely
    size_bytes = item.get("Size", 0)
    quality_description = {
        "video": _extract_video_quality(video_stream),
        "audio": _extract_audio_quality(audio_stream, languages),
        "size": size_bytes,  # Raw size for sorting
        "size_formatted": _format_file_size(size_bytes),  # Human-readable size
        "date_added": _resolve_date_added(item),
        "premiere_date": _extract_premiere_date(item),
        "year": item.get("ProductionYear", "unknown"),
        "rating": item.get("OfficialRating", "unknown"),
        "overview": item.get("Overview", ""),
        "path": item.get("Path", "unknown"),
    }

    # Add TV series metadata if available
    _build_tv_metadata(item, quality_description)

    return quality_description


def get_image_url(base_url: str, item_id: str, item_image_tags: dict, server_id: str, api_key: Optional[str] = None) -> str:
    """
    Generates a URL for the primary image (poster/thumbnail) of a media item.

    Args:
        base_url (str): The base URL of the Emby server.
        item_id (str): ID of the media item.
        item_image_tags (dict): Dictionary containing image tag information.
        server_id (str): Server ID for the item.
        api_key (Optional[str]): API key to include in the URL for authentication.

    Returns:
        str: URL to the primary image, or a placeholder if no image is available.
    """
    # Check if the item has a primary image
    if not item_image_tags or "Primary" not in item_image_tags:
        # Return a placeholder image if no primary image is available
        logger.debug(f"No primary image found for item {item_id}. Available tags: {item_image_tags}")
        return f"{base_url}/web/assets/img/media.png"

    # Get the primary image tag
    primary_tag = item_image_tags["Primary"]

    # Debugging information
    logger.debug(f"Image tags for item {item_id}: {item_image_tags}")

    # Construct the image URL - using the Emby Images API to get the primary image
    # Add timestamp to prevent caching issues and ensure the latest image
    image_url = f"{base_url}/Items/{item_id}/Images/Primary?tag={primary_tag}&quality=90&maxHeight=300&ts={int(time.time())}"

    # Add API key if provided - this is needed for direct image access
    if api_key:
        image_url += f"&api_key={api_key}"
    else:
        # If no API key provided but we have X-Emby-Token in headers, add it to the URL
        logger.warning(f"No API key provided for image URL {image_url}. Images may not display correctly.")

    logger.debug(f"Generated image URL: {image_url}")
    return image_url


def _calculate_quality_rating(
    item: Dict[str, Any],
    video_stream: Optional[Dict],
    audio_stream: Optional[Dict],
) -> float:
    """Calculate quality rating for a media item.

    Args:
        item: Media item dict.
        video_stream: Video stream info.
        audio_stream: Audio stream info.

    Returns:
        Quality rating score.
    """
    # Parse date added to get timestamp for comparison
    date_rating = 0
    date_str = item.get("DateCreated", "")
    if isinstance(date_str, str) and 'T' in date_str:
        try:
            date_rating = int(datetime.fromisoformat(date_str).timestamp())
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing DateCreated for rating: {e}")

    # Define quality factors and their corresponding weights
    quality_factors = {
        "resolution": (
            video_stream.get("Height", 0) * video_stream.get("Width", 0)
            if video_stream
            else 0,
            1,
        ),
        "audio_channels": (
            audio_stream.get("Channels", 0) if audio_stream else 0,
            0.5,
        ),
        "bitrate": (item.get("Bitrate", 0), 0.2),
        "file_size": (item.get("Size", 0), 0.3),
        "date_added": (date_rating, 0.8),
    }

    # Calculate the base weighted quality rating
    base_quality_rating = sum(
        value * weight for value, weight in quality_factors.values()
    )

    # Apply source quality and AI upscale multipliers
    item_path = item.get("Path")
    item_name = item.get("Name", "")

    source_multiplier = detect_source_quality(item_path, item_name)
    is_ai_upscale = detect_ai_upscale(item_path, item_name)
    ai_upscale_multiplier = 0.7 if is_ai_upscale else 1.0

    # Apply multipliers to get final quality rating
    return base_quality_rating * source_multiplier * ai_upscale_multiplier


def rate_media_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Assigns a quality rating to each media item based on its attributes.

    Args:
        items (list): List of media items.

    Returns:
        list: Rated media items, each with a 'rating' key indicating its quality score.
    """
    rated_items = []
    for item in items:
        # Skip items with no 'MediaStreams' key
        if "MediaStreams" not in item:
            logger.debug(
                f"Media item {item.get('Id', 'unknown')} has no 'MediaStreams' entry; skipping."
            )
            continue

        video_stream = next(
            (s for s in item["MediaStreams"] if s["Type"] == "Video"), None
        )
        audio_stream = next(
            (s for s in item["MediaStreams"] if s["Type"] == "Audio"), None
        )

        # Calculate quality rating using helper
        quality_rating = _calculate_quality_rating(item, video_stream, audio_stream)

        # Detect source and AI upscale for quality description
        item_path = item.get("Path")
        item_name = item.get("Name", "")
        source_multiplier = detect_source_quality(item_path, item_name)
        is_ai_upscale = detect_ai_upscale(item_path, item_name)

        # Get detailed quality description
        quality_description = get_quality_description(item) if video_stream and audio_stream else {}

        # Add source quality info to quality description
        if quality_description:
            quality_description["source_quality_multiplier"] = source_multiplier
            quality_description["is_ai_upscale"] = is_ai_upscale

        # For TV episodes, add the series/season/episode info to the name for better display
        item_name = item["Name"]
        if item.get("SeriesName") and "episode_info" in quality_description:
            item_name = f"{item.get('SeriesName')} - {quality_description['episode_info']} - {item_name}"

        # Include the quality rating and relevant details in the result
        rated_items.append(
            {
                "id": item["Id"],
                "name": item_name,
                "path": item.get("Path"),
                "serverid": item.get("ServerId"),
                "library_name": item.get("LibraryName", "Unknown"),
                "is_episode": "SeriesName" in item,
                "series_name": item.get("SeriesName", ""),
                # Emby API uses ParentIndexNumber for season, IndexNumber for episode
                "season_number": item.get("ParentIndexNumber", ""),
                "episode_number": item.get("IndexNumber", ""),
                "rating": quality_rating,
                "quality_description": quality_description
            }
        )

    return rated_items
