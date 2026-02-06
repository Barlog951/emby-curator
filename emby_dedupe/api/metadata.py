"""
Metadata processing utilities for Emby media items.
"""

import os
import time
from typing import Any, Dict, List, Optional

from emby_dedupe.api.quality_compare import detect_ai_upscale, detect_source_quality
from emby_dedupe.utils.logging import logger


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
    languages = set()
    for stream in audio_streams:
        lang = stream.get("Language", "unknown")
        if lang and lang != "unknown":
            languages.add(lang)

    # Format file size in human-readable format (KB, MB, GB)
    size_bytes = item.get("Size", 0)
    size_formatted = "unknown"
    if size_bytes:
        if size_bytes >= 1073741824:  # 1 GB
            size_formatted = f"{size_bytes / 1073741824:.2f} GB"
        elif size_bytes >= 1048576:  # 1 MB
            size_formatted = f"{size_bytes / 1048576:.2f} MB"
        elif size_bytes >= 1024:  # 1 KB
            size_formatted = f"{size_bytes / 1024:.2f} KB"
        else:
            size_formatted = f"{size_bytes} bytes"

    # Format date added from Emby's API
    date_added = "unknown"

    # First choice: DateCreated - the most accurate field for when the item was added to Emby
    if "DateCreated" in item and item["DateCreated"]:
        try:
            date_str = item["DateCreated"]
            if isinstance(date_str, str) and 'T' in date_str:
                # Parse ISO 8601 datetime format
                date_parts = date_str.split('T')[0].split('-')
                time_parts = date_str.split('T')[1].split(':')
                if len(date_parts) == 3:
                    date_added = f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]} {time_parts[0]}:{time_parts[1]}"
                    logger.debug(f"Found DateCreated: {date_added}")
            else:
                date_added = str(date_str)
                logger.debug(f"Found DateCreated (non-ISO): {date_added}")
        except Exception as e:
            logger.warning(f"Error parsing DateCreated: {e}")

    # Second choice: DateModified - when the item was last modified in Emby
    if date_added == "unknown" and "DateModified" in item and item["DateModified"]:
        try:
            date_str = item["DateModified"]
            if isinstance(date_str, str) and 'T' in date_str:
                date_parts = date_str.split('T')[0].split('-')
                time_parts = date_str.split('T')[1].split(':')
                if len(date_parts) == 3:
                    date_added = f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]} {time_parts[0]}:{time_parts[1]}"
                    logger.debug(f"Found DateModified: {date_added}")
            else:
                date_added = str(date_str)
                logger.debug(f"Found DateModified (non-ISO): {date_added}")
        except Exception as e:
            logger.warning(f"Error parsing DateModified: {e}")

    # Fallback to other date fields
    date_fields = ["PremiereDate", "EndDate", "ProductionYear"]
    if date_added == "unknown":
        for field in date_fields:
            if field in item and item[field]:
                try:
                    date_str = item[field]
                    if field == "ProductionYear":
                        # If it's just a year, format it as a full date
                        date_added = f"{date_str}-01-01 (year only)"
                        logger.debug(f"Using ProductionYear as date: {date_added}")
                        break

                    # If format is ISO 8601
                    if isinstance(date_str, str) and 'T' in date_str:
                        date_parts = date_str.split('T')[0].split('-')
                        if len(date_parts) == 3:
                            date_added = f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]}"
                            logger.debug(f"Found date in {field}: {date_added}")
                            break
                    else:
                        date_added = str(date_str)
                        logger.debug(f"Found date in {field} (non-ISO): {date_added}")
                        break
                except Exception as e:
                    logger.warning(f"Error parsing date from {field}: {e}")

    # Last resort: file system modification time
    if date_added == "unknown" and "Path" in item and item["Path"]:
        try:
            file_path = item["Path"]
            if os.path.exists(file_path):
                file_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(file_path)))
                date_added = f"{file_time} (file modified time)"
                logger.debug(f"Using file modification time: {date_added}")
        except Exception as e:
            logger.warning(f"Error getting file modification time: {e}")

    # If we STILL don't have a date (very unlikely by this point)
    if date_added == "unknown":
        # Last desperate attempt - use any field with "date" in its name
        for key in item.keys():
            if "date" in key.lower() and key not in ["DateCreated", "DateModified", "PremiereDate"]:
                try:
                    date_added = f"{str(item[key])} (from {key})"
                    logger.debug(f"Using alternative date field {key}: {date_added}")
                    break
                except Exception as e:
                    logger.debug(f"Could not use date from field {key}: {e}")

    # Get premiere date (original release date)
    premiere_date = "unknown"
    if "PremiereDate" in item:
        try:
            date_str = item["PremiereDate"]
            if 'T' in date_str:
                date_parts = date_str.split('T')[0].split('-')
                if len(date_parts) == 3:
                    premiere_date = f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]}"
            else:
                premiere_date = date_str
        except Exception:
            premiere_date = item.get("PremiereDate", "unknown")

    # Construct the quality description safely
    quality_description = {
        "video": {
            "codec": video_stream.get("Codec", "unknown")
            if video_stream
            else "unknown",
            "resolution": video_stream.get("DisplayTitle", "unknown")
            if video_stream
            else "unknown",
            "bitrate": video_stream.get("BitRate", "unknown")
            if video_stream
            else "unknown",
            "bitdepth": video_stream.get("BitDepth", "unknown")
            if video_stream
            else "unknown",
            "interlaced": video_stream.get("IsInterlaced", "unknown")
            if video_stream
            else "unknown",
        },
        "audio": {
            "codec": audio_stream.get("Codec", "unknown")
            if audio_stream
            else "unknown",
            "channels": audio_stream.get("Channels", "unknown")
            if audio_stream
            else "unknown",
            "bitrate": audio_stream.get("BitRate", "unknown")
            if audio_stream
            else "unknown",
            "languages": list(languages) if languages else ["unknown"],
        },
        "size": size_bytes,  # Raw size for sorting
        "size_formatted": size_formatted,  # Human-readable size
        "date_added": date_added,
        "premiere_date": premiere_date,
        "year": item.get("ProductionYear", "unknown"),
        "rating": item.get("OfficialRating", "unknown"),
        "overview": item.get("Overview", ""),
        "path": item.get("Path", "unknown"),
    }

    # Add TV series metadata if available
    if item.get("SeriesName"):
        quality_description["is_episode"] = True
        quality_description["series_name"] = item.get("SeriesName", "unknown")
        # Emby API uses ParentIndexNumber for season, IndexNumber for episode
        quality_description["season_number"] = item.get("ParentIndexNumber", "unknown")
        quality_description["episode_number"] = item.get("IndexNumber", "unknown")

        # Enhance the display info
        if quality_description["season_number"] != "unknown" and quality_description["episode_number"] != "unknown":
            quality_description["episode_info"] = f"S{quality_description['season_number']}E{quality_description['episode_number']}"
        else:
            quality_description["episode_info"] = "Unknown episode"
    else:
        quality_description["is_episode"] = False

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

        # Parse date added to get timestamp for comparison (newer = higher rating)
        date_rating = 0
        try:
            if "DateCreated" in item:
                date_str = item["DateCreated"]
                if isinstance(date_str, str) and 'T' in date_str:
                    date_obj = time.strptime(date_str.split('T')[0], "%Y-%m-%d")
                    # Convert to timestamp for comparison (int for type correctness)
                    date_rating = int(time.mktime(date_obj))
        except Exception as e:
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
            "date_added": (date_rating, 0.8),  # Higher weight for date added - prefer newer files
        }

        # Calculate the base weighted quality rating
        base_quality_rating = sum(
            value * weight for value, weight in quality_factors.values()
        )

        # Apply source quality and AI upscale multipliers
        item_path = item.get("Path")
        item_name = item.get("Name", "")

        # Detect source quality multiplier
        source_multiplier = detect_source_quality(item_path, item_name)

        # Detect AI upscale and apply penalty (0.7x if detected)
        is_ai_upscale = detect_ai_upscale(item_path, item_name)
        ai_upscale_multiplier = 0.7 if is_ai_upscale else 1.0

        # Apply multipliers to get final quality rating
        quality_rating = base_quality_rating * source_multiplier * ai_upscale_multiplier

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
