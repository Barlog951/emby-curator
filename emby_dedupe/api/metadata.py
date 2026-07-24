"""
Metadata processing utilities for Emby media items.
"""

import os
import time
from datetime import datetime
from typing import Any

from emby_dedupe.api.quality_compare import detect_ai_upscale, detect_source_quality

# ``DOVI_P5_QUALITY_PENALTY`` and ``_is_dovi_profile5`` now live in the shared
# ``scoring`` module and are re-exported here for backward compatibility — the DV-P5
# test suite imports ``_is_dovi_profile5`` from this module. (The redundant ``as``
# alias marks the penalty constant as an intentional re-export, not dead import.)
from emby_dedupe.api.scoring import DOVI_P5_QUALITY_PENALTY as DOVI_P5_QUALITY_PENALTY
from emby_dedupe.api.scoring import (
    _is_dovi_profile5,
    compute_quality_breakdown,
    duration_minutes_from_ticks,
    format_score_summary,
    hdr_bonus_from_string,
    hdr_marker_in_text,
    is_neutral_multiplier,
)
from emby_dedupe.utils.formatting import format_file_size
from emby_dedupe.utils.logging import logger


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format (unknown/zero → "unknown")."""
    return format_file_size(size_bytes, zero_label="unknown")


def _parse_iso_date(date_str: str, include_time: bool = True) -> str | None:
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


def _try_parse_date_field(item: dict[str, Any], field_name: str, include_time: bool = True) -> str | None:
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


def _try_fallback_date_fields(item: dict[str, Any]) -> str | None:
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


def _try_filesystem_date(item: dict[str, Any]) -> str | None:
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


def _try_any_date_field(item: dict[str, Any]) -> str | None:
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


def _resolve_date_added(item: dict[str, Any]) -> str:
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


def _extract_premiere_date(item: dict[str, Any]) -> str:
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


def _build_tv_metadata(item: dict[str, Any], quality_desc: dict[str, Any]) -> None:
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


def _extract_video_quality(video_stream: dict[str, Any] | None) -> dict[str, Any]:
    """Extract video quality information from a video stream."""
    if not video_stream:
        return {
            "codec": "unknown",
            "resolution": "unknown",
            "bitrate": "unknown",
            "bitdepth": "unknown",
            "interlaced": "unknown",
            "video_range": "unknown",
            "dv_profile": "",
            "dv_profile_desc": "",
            "is_dovi_p5": False,
        }
    return {
        "codec": video_stream.get("Codec", "unknown"),
        "resolution": video_stream.get("DisplayTitle", "unknown"),
        "bitrate": video_stream.get("BitRate", "unknown"),
        "bitdepth": video_stream.get("BitDepth", "unknown"),
        "interlaced": video_stream.get("IsInterlaced", "unknown"),
        "video_range": video_stream.get("VideoRange", "unknown"),
        "dv_profile": video_stream.get("ExtendedVideoSubType", ""),
        "dv_profile_desc": video_stream.get("ExtendedVideoSubTypeDescription", ""),
        "is_dovi_p5": _is_dovi_profile5(video_stream),
    }


def _extract_audio_quality(audio_stream: dict[str, Any] | None, languages: list[str]) -> dict[str, Any]:
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


def get_quality_description(item: dict[str, Any]) -> dict[str, Any]:
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


def get_image_url(base_url: str, item_id: str, item_image_tags: dict, server_id: str, api_key: str | None = None) -> str:
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


# --- Quality rating model --------------------------------------------------------
# The scoring model itself lives in the shared ``scoring`` module and is used by BOTH
# this dedupe path and the check path (quality_compare) — see scoring.py. This section
# only extracts the normalised factors from an Emby item dict and delegates.


def _resolution_megapixels(video_stream: dict[str, Any] | None) -> float:
    if not video_stream:
        return 0.0
    return ((video_stream.get("Height", 0) or 0) * (video_stream.get("Width", 0) or 0)) / 1_000_000.0


def _video_bitrate_mbps(item: dict[str, Any], video_stream: dict[str, Any] | None) -> float:
    """Video bitrate in Mbps — from the video stream (preferred) or the item total."""
    br = (video_stream or {}).get("BitRate") or item.get("Bitrate") or 0
    try:
        return max(0.0, float(br) / 1_000_000.0)
    except (TypeError, ValueError):
        return 0.0


def _hdr_bonus(
    video_stream: dict[str, Any] | None,
    item_name: str = "",
    item_path: str | None = None,
) -> float:
    """1.0 for any real HDR (HDR10/HDR10+/Dolby Vision/HLG), 0.0 for SDR/unknown.
    The Dolby Vision Profile-5 penalty is applied separately.

    Falls back to an explicit HDR marker in the filename/title when Emby exposes
    no HDR metadata — some WEB-DL rips carry real HDR the server misses, and
    without this an HDR copy ties with a genuinely SDR sibling and can lose the
    tie-break (as happened to The Agency S02E07, 2026-07-22)."""
    bonus = hdr_bonus_from_string((video_stream or {}).get("VideoRange"))
    if not bonus and (
        hdr_marker_in_text(os.path.basename(item_path or ""))
        or hdr_marker_in_text(item_name)
    ):
        return 1.0
    return bonus


def _rating_breakdown(
    item: dict[str, Any],
    video_stream: dict | None,
    audio_stream: dict | None,
) -> dict[str, Any]:
    """Extract the normalised factors from an Emby item and score them ONCE.

    Returns the full per-factor breakdown (``scoring.quality_score_breakdown``): the
    ``total`` is the rating, and ``multipliers`` carries the source/AI values so callers
    don't re-run ``detect_source_quality`` / ``detect_ai_upscale`` (they run once here).
    """
    # Parse date added to get timestamp for comparison
    date_rating = 0
    date_str = item.get("DateCreated", "")
    if isinstance(date_str, str) and 'T' in date_str:
        try:
            date_rating = int(datetime.fromisoformat(date_str).timestamp())
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing DateCreated for rating: {e}")

    height = (video_stream.get("Height", 0) or 0) if video_stream else 0
    channels = (audio_stream.get("Channels", 0) or 0) if audio_stream else 0

    item_path = item.get("Path")
    item_name = item.get("Name", "")

    return compute_quality_breakdown(
        res_megapixels=_resolution_megapixels(video_stream),
        height=height,
        hdr_bonus=_hdr_bonus(video_stream, item_name, item_path),
        video_bitrate_mbps=_video_bitrate_mbps(item, video_stream),
        audio_channels=channels,
        date_rating=date_rating,
        codec=(video_stream.get("Codec") if video_stream else None),
        source_multiplier=detect_source_quality(item_path, item_name),
        ai_multiplier=0.7 if detect_ai_upscale(item_path, item_name) else 1.0,
        is_dovi_p5=_is_dovi_profile5(video_stream),
        size_bytes=item.get("Size", 0) or 0,
        duration_minutes=duration_minutes_from_ticks(item.get("RunTimeTicks")),
    )


def _calculate_quality_rating(
    item: dict[str, Any],
    video_stream: dict | None,
    audio_stream: dict | None,
) -> float:
    """Calculate quality rating for a media item.

    Extracts the normalised factors from the Emby item and delegates to the shared
    ``scoring`` model (the single model shared with the check path).

    Args:
        item: Media item dict.
        video_stream: Video stream info.
        audio_stream: Audio stream info.

    Returns:
        Quality rating score.
    """
    return float(_rating_breakdown(item, video_stream, audio_stream)["total"])


def rate_media_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

        # Score the item ONCE — the breakdown's total is the rating, and its
        # multipliers give the source/AI values (no second detect_* pass).
        breakdown = _rating_breakdown(item, video_stream, audio_stream)
        quality_rating = float(breakdown["total"])
        source_multiplier = breakdown["multipliers"]["source"]
        is_ai_upscale = not is_neutral_multiplier(breakdown["multipliers"]["ai"])
        score_summary = format_score_summary(breakdown)

        # Get detailed quality description
        quality_description = get_quality_description(item) if video_stream and audio_stream else {}

        # Add source quality info + the one-line score summary (explainability). The
        # summary rides on quality_description so the HTML report renders it without
        # extra template-context plumbing.
        if quality_description:
            quality_description["source_quality_multiplier"] = source_multiplier
            quality_description["is_ai_upscale"] = is_ai_upscale
            quality_description["score_summary"] = score_summary

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
                # Emby runtime in ticks (10^7/s); the fold-safe delete pass uses it as a
                # same-content check between keeper and delete candidate.
                "runtime_ticks": item.get("RunTimeTicks"),
                "rating": quality_rating,
                # Per-factor breakdown + one-line summary behind ``rating``
                # (explainability); always present even when quality_description is
                # empty (missing video/audio streams).
                "score_breakdown": breakdown,
                "score_summary": score_summary,
                "quality_description": quality_description
            }
        )

    return rated_items
