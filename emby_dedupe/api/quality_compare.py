"""
Quality comparison module for comparing proposed media with existing items.

The quality scoring here is now UNIFIED with the deduplication path: both
``ExistingQuality.calculate_score`` (this module) and
``metadata._calculate_quality_rating`` delegate to the single normalised model in
``emby_dedupe.api.scoring`` (megapixels + HDR + Mbps + channels, with a
codec-adjusted starved-bitrate demotion and the Dolby Vision Profile-5 penalty). A
``check`` recommendation and a dedupe keep/delete decision therefore agree on the
ordering of the same pair of files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

# rank-torrent-name is a hard runtime dependency (pyproject); a missing install must
# fail loudly, not silently switch detection behavior. Runtime parse failures on odd
# filenames are still caught around each rtn_parse() CALL.
from RTN import parse as rtn_parse

from emby_dedupe.api.scoring import (
    _is_dovi_profile5,
    codec_efficiency_ratio,
    compute_quality_breakdown,
    duration_minutes_from_ticks,
    hdr_bonus_from_string,
)
from emby_dedupe.utils.constants import LANGUAGE_NORMALIZATION_MAP, should_quality_override_language
from emby_dedupe.utils.logging import logger


def _round_breakdown(breakdown: dict[str, Any], ndigits: int = 2) -> dict[str, Any]:
    """Round the numeric values of a score breakdown for compact JSON output.

    Rounds top-level floats and the nested ``multipliers`` floats; leaves booleans
    (e.g. ``starved_demotion``) and other types untouched.
    """
    rounded: dict[str, Any] = {}
    for key, value in breakdown.items():
        if isinstance(value, bool):
            rounded[key] = value
        elif isinstance(value, (int, float)):
            rounded[key] = round(value, ndigits)
        elif isinstance(value, dict):
            rounded[key] = {
                k: (round(v, ndigits) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                for k, v in value.items()
            }
        else:
            rounded[key] = value
    return rounded


class SourceQualityTier(TypedDict, total=False):
    """Type definition for source quality tier data."""
    bonus: float
    patterns: list[str]


# Resolution mapping from string to pixels (width x height)
RESOLUTION_MAP = {
    "2160p": (3840, 2160),
    "4k": (3840, 2160),
    "uhd": (3840, 2160),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
    "sd": (720, 480),
}

# Audio channel mapping
AUDIO_CHANNEL_MAP = {
    "atmos": 8,
    "7.1": 8,
    "dts-hd": 8,
    "truehd": 8,
    "5.1": 6,
    "ac3": 6,
    "dts": 6,
    "stereo": 2,
    "2.0": 2,
    "mono": 1,
}

# Quality factor weights (UPDATED: research-based comprehensive quality scoring)
# NOTE: Bitrate weight increased from 0.2 to 0.8 based on industry research
# NOTE: File size weight reduced from 0.3 to 0.1 to prevent dominance over resolution
# NOTE: date_added weight at 0.00001 to prevent timestamp dominance
QUALITY_WEIGHTS = {
    "resolution": 1.0,      # Baseline (pixels)
    "audio_channels": 0.5,  # Audio quality
    "bitrate": 0.8,         # INCREASED: Critical for quality assessment
    "file_size": 0.1,       # REDUCED: Prevent dominance
    "date_added": 0.00001,  # Minimal influence
}

# Source quality tiers and multipliers (35% weight in final score)
SOURCE_QUALITY_TIERS: dict[str, SourceQualityTier] = {
    "bluray_remux": {
        "bonus": 1.3,  # Lossless, 50-90 Mbps for 4K
        "patterns": ["REMUX", "BDREMUX", "BluRay.Remux", "Blu-ray.Remux", "BD.REMUX"]
    },
    "bluray": {
        "bonus": 1.15,  # High-quality encode, 20-40 Mbps for 4K
        "patterns": ["BluRay", "Blu-Ray", "Blu-ray", "BRRip", "BDRip", "BD.Rip"]
    },
    "webdl": {
        "bonus": 1.0,  # Baseline, 15-25 Mbps for 4K
        "patterns": ["WEB-DL", "WEBDL", "WEB.DL", "WEBRip", "WEB-Rip", "WEB.Rip"]
    },
    "hdtv": {
        "bonus": 0.9,  # Lower quality, 5-12 Mbps for 4K
        "patterns": ["HDTV", "DVB", "PDTV"]
    },
    "unknown": {
        "bonus": 0.95
    }
}

# Minimum acceptable bitrates by resolution (in bps) - RED FLAG thresholds
# Based on Netflix, scene release standards, and private tracker minimums
MIN_BITRATE_THRESHOLDS = {
    "4k": 15_000_000,     # 15 Mbps minimum for 4K (2160p)
    "1080p": 5_000_000,   # 5 Mbps minimum for 1080p
    "720p": 3_000_000,    # 3 Mbps minimum for 720p
}

# Bits per pixel quality bands (30% weight in final score)
# Research-backed thresholds from video encoding standards
BPP_QUALITY_BANDS = {
    "excellent": 0.3,      # >0.3 bpp - Excellent quality
    "good": 0.15,          # 0.15-0.3 bpp - Good quality
    "acceptable": 0.08,    # 0.08-0.15 bpp - Acceptable quality
    "poor": 0.05,          # 0.05-0.08 bpp - Poor quality
    "critical": 0.0        # <0.05 bpp - CRITICAL (RED FLAG)
}

# Codec efficiency multipliers (15% weight in final score)
# Based on compression efficiency research (AV1 > HEVC > H.264)
CODEC_EFFICIENCY = {
    "av1": 1.15,    # 50% more efficient than x264
    "hevc": 1.1,    # 35-50% more efficient than x264
    "x265": 1.1,    # H.265 variant
    "h265": 1.1,    # H.265 variant
    "avc": 1.0,     # H.264 baseline
    "x264": 1.0,    # H.264 variant
    "h264": 1.0,    # H.264 variant
}

# AI upscale detection patterns
AI_UPSCALE_PATTERNS = [
    "AI.UPSCALE",
    "AI-UPSCALE",
    "AI_UPSCALE",
    "Ai.Upscale",
    "Ai-Upscale",
    "Ai_Upscale",
    "UPSCALED",
    "Upscaled",
    "AI.Enhanced",
    "AI-Enhanced",
    "AI_Enhanced",
]


def detect_source_quality(path: str | None, name: str | None) -> float:
    """
    Detect source quality from path/name and return multiplier.

    Checks both Path and Name fields for source quality indicators.
    Prioritizes Path over Name if both are available.

    Args:
        path: File path from item.get("Path")
        name: Item name from item.get("Name")

    Returns:
        Float multiplier (0.9-1.3) based on detected source quality
    """
    search_text = ""
    if path:
        search_text = path
    elif name:
        search_text = name
    else:
        return SOURCE_QUALITY_TIERS["unknown"]["bonus"]

    search_text_upper = search_text.upper()

    # Check in priority order (highest quality first)
    for tier_name, tier_info in SOURCE_QUALITY_TIERS.items():
        if tier_name == "unknown":
            continue
        for pattern in tier_info["patterns"]:
            if pattern.upper() in search_text_upper:
                logger.debug(
                    f"Detected source quality '{tier_name}' from pattern '{pattern}' "
                    f"in: {search_text[:100]}"
                )
                return tier_info["bonus"]

    return SOURCE_QUALITY_TIERS["unknown"]["bonus"]


def detect_ai_upscale(path: str | None, name: str | None) -> bool:
    """
    Detect if content is AI upscaled.

    Args:
        path: File path from item.get("Path")
        name: Item name from item.get("Name")

    Returns:
        True if AI upscale detected, False otherwise
    """
    search_text = ""
    if path:
        search_text = path
    elif name:
        search_text = name
    else:
        return False

    search_text_upper = search_text.upper()

    for pattern in AI_UPSCALE_PATTERNS:
        if pattern.upper() in search_text_upper:
            logger.debug(
                f"Detected AI upscale from pattern '{pattern}' in: {search_text[:100]}"
            )
            return True

    return False


def estimate_bitrate_from_size(
    size_bytes: int,
    duration_minutes: int = 120
) -> int:
    """Estimate video bitrate from file size.

    Used as fallback when actual bitrate is not available.
    Assumes typical movie duration and 90% video content (10% audio/subs).

    Args:
        size_bytes: Total file size in bytes
        duration_minutes: Estimated duration in minutes (default 120 = 2 hours)

    Returns:
        Estimated bitrate in bps
    """
    if size_bytes == 0 or duration_minutes == 0:
        return 0

    # Assume 90% of file is video, 10% is audio/subtitles
    video_bytes = size_bytes * 0.9
    duration_seconds = duration_minutes * 60
    bitrate_bps = int((video_bytes * 8) / duration_seconds)

    logger.debug(
        f"Estimated bitrate from file size: {size_bytes / (1024**3):.1f} GB "
        f"→ ~{bitrate_bps / 1_000_000:.1f} Mbps (assuming {duration_minutes} min duration)"
    )

    return bitrate_bps


def calculate_bpp(
    bitrate: int,
    width: int,
    height: int,
    fps: int = 24
) -> float:
    """Calculate bits per pixel.

    Args:
        bitrate: Video bitrate in bps
        width: Video width in pixels
        height: Video height in pixels
        fps: Frame rate (default 24)

    Returns:
        Bits per pixel value
    """
    if width == 0 or height == 0 or fps == 0:
        return 0.0

    total_pixels_per_second = width * height * fps
    return bitrate / total_pixels_per_second


def _get_codec_efficiency_ratio(codec: str | None) -> float:
    """Return the codec compression efficiency ratio vs H.264 baseline.

    Thin wrapper over ``scoring.codec_efficiency_ratio`` (the single source of the
    HEVC=0.65 / AV1=0.5 ratios), kept as a local name for the BPP/bitrate call sites.
    """
    return codec_efficiency_ratio(codec)


def get_bpp_multiplier(bpp: float, codec: str | None = None) -> float:
    """Get quality multiplier based on bits per pixel.

    Adjusts for codec efficiency: HEVC/AV1 achieve the same visual quality
    with fewer bits per pixel than H.264. A 4K HEVC file at 0.07 bpp is
    roughly equivalent to an H.264 file at 0.11 bpp.

    Args:
        bpp: Bits per pixel value.
        codec: Video codec name for efficiency adjustment.

    Returns:
        Multiplier (0.5-1.2)
    """
    # Adjust BPP for codec efficiency before comparing to bands
    effective_bpp = bpp / _get_codec_efficiency_ratio(codec)

    if effective_bpp >= BPP_QUALITY_BANDS["excellent"]:
        return 1.1  # Excellent quality (tightened from 1.2 to prevent
                     # 1080p files with naturally high BPP from dominating
                     # 4K files with lower but codec-efficient BPP)
    elif effective_bpp >= BPP_QUALITY_BANDS["good"]:
        return 1.05  # Good quality
    elif effective_bpp >= BPP_QUALITY_BANDS["acceptable"]:
        return 1.0  # Acceptable quality
    elif effective_bpp >= BPP_QUALITY_BANDS["poor"]:
        return 0.85  # Poor quality
    else:
        return 0.5  # Critical - severe penalty


def has_quality_red_flags(
    resolution_height: int,
    bitrate: int,
    bpp: float,
    codec: str | None = None
) -> tuple[bool, str]:
    """Detect severe quality issues that should auto-reject.

    Args:
        resolution_height: Video height (720, 1080, 2160)
        bitrate: Video bitrate in bps
        bpp: Bits per pixel value
        codec: Video codec name (e.g., "hevc", "x265", "h264") for efficiency adjustment

    Returns:
        (has_red_flag, reason)
    """
    # Apply codec efficiency adjustment to thresholds
    codec_efficiency = _get_codec_efficiency_ratio(codec)

    # Check minimum bitrate by resolution
    if resolution_height >= 2000:  # 4K
        threshold = MIN_BITRATE_THRESHOLDS["4k"] * codec_efficiency
        if bitrate < threshold:
            return (True, f"4K under-bitrate: {bitrate/1_000_000:.1f} Mbps < {threshold/1_000_000:.1f} Mbps minimum")
    elif resolution_height >= 1000:  # 1080p
        threshold = MIN_BITRATE_THRESHOLDS["1080p"] * codec_efficiency
        if bitrate < threshold:
            return (True, f"1080p under-bitrate: {bitrate/1_000_000:.1f} Mbps < {threshold/1_000_000:.1f} Mbps minimum")
    elif resolution_height >= 700:  # 720p
        threshold = MIN_BITRATE_THRESHOLDS["720p"] * codec_efficiency
        if bitrate < threshold:
            return (True, f"720p under-bitrate: {bitrate/1_000_000:.1f} Mbps < {threshold/1_000_000:.1f} Mbps minimum")

    # Check bits per pixel - critical threshold is < 0.05 bpp
    if 0 < bpp < BPP_QUALITY_BANDS["poor"]:  # BPP between 0 and 0.05 is critical
        return (True, f"Critical BPP: {bpp:.4f} < 0.05 minimum")

    return (False, "")


def _check_quality_type_from_rtn(quality_lower: str, filename: str) -> float | None:
    """Check quality type and return appropriate multiplier.

    Args:
        quality_lower: Lowercased quality string from RTN
        filename: Filename for logging (truncated)

    Returns:
        Quality multiplier if matched, None otherwise
    """
    # Check for REMUX (highest quality)
    if "remux" in quality_lower:
        logger.debug(f"RTN detected REMUX quality in: {filename[:100]}")
        return SOURCE_QUALITY_TIERS["bluray_remux"]["bonus"]

    # Check for BluRay
    if "bluray" in quality_lower or "blu-ray" in quality_lower:
        logger.debug(f"RTN detected BluRay quality in: {filename[:100]}")
        return SOURCE_QUALITY_TIERS["bluray"]["bonus"]

    # Check for WEB-DL
    if "web-dl" in quality_lower or "webdl" in quality_lower:
        logger.debug(f"RTN detected WEB-DL quality in: {filename[:100]}")
        return SOURCE_QUALITY_TIERS["webdl"]["bonus"]

    # Check for WEBRip
    if "webrip" in quality_lower:
        logger.debug(f"RTN detected WEBRip quality in: {filename[:100]}")
        return SOURCE_QUALITY_TIERS["webdl"]["bonus"]

    # Check for HDTV
    if "hdtv" in quality_lower:
        logger.debug(f"RTN detected HDTV quality in: {filename[:100]}")
        return SOURCE_QUALITY_TIERS["hdtv"]["bonus"]

    return None


def detect_source_quality_with_rtn(path: str | None, name: str | None) -> float:
    """Detect source quality using RTN library for better accuracy.

    Args:
        path: File path
        name: File or item name

    Returns:
        Quality multiplier (0.9-1.3)
    """
    # Try to parse with RTN first
    try:
        filename = path if path else name
        if not filename:
            return detect_source_quality(path, name)

        parsed = rtn_parse(filename)

        # Check quality attribute for source quality
        if parsed.quality:
            quality_lower = str(parsed.quality).lower()
            multiplier = _check_quality_type_from_rtn(quality_lower, filename)
            if multiplier is not None:
                return multiplier
    except Exception as e:
        logger.debug(f"RTN parsing failed, falling back to regex: {e}")

    # Fallback to existing regex-based detection
    return detect_source_quality(path, name)


def _try_rtn_codec_detection(path: str) -> float | None:
    """Try to detect codec using RTN parsing.

    Args:
        path: File path for RTN parsing.

    Returns:
        Multiplier if detected, None otherwise.
    """
    try:
        parsed = rtn_parse(path)
        if parsed.codec:
            codec_str = str(parsed.codec).lower()
            for codec_name, multiplier in CODEC_EFFICIENCY.items():
                if codec_name in codec_str:
                    logger.debug(f"RTN detected codec {codec_name} in: {path[:100]}")
                    return multiplier
    except Exception as e:
        logger.debug(f"RTN codec parsing failed: {e}")

    return None


def get_codec_multiplier_with_rtn(codec: str | None, path: str | None = None) -> float:
    """Get efficiency multiplier for video codec using RTN.

    Args:
        codec: Codec name (e.g., "hevc", "x265", "h264")
        path: Optional file path for RTN parsing

    Returns:
        Efficiency multiplier (1.0-1.15)
    """
    # Try RTN parsing first if we have a path
    if path:
        rtn_multiplier = _try_rtn_codec_detection(path)
        if rtn_multiplier is not None:
            return rtn_multiplier

    # Fallback to direct codec check
    if codec:
        codec_lower = codec.lower()
        for codec_name, multiplier in CODEC_EFFICIENCY.items():
            if codec_name in codec_lower:
                return multiplier

    return 1.0  # Unknown codec, neutral


@dataclass
class MediaQualityFields:
    """Media-quality descriptor fields shared by ProposedQuality and CheckConfig.

    These attributes describe the technical quality of a media item and evolve
    together; both the proposed-item quality model and the check-input config
    carry the identical set, so they are declared here once. All fields are
    keyword-only in practice (every construction site uses keywords), so the
    inherited __init__ ordering is not relied upon.
    """

    resolution: str | None = None
    codec: str | None = None
    hdr: str | None = None
    audio: str | None = None
    audio_languages: list[str] | None = None
    size_mb: int | None = None
    bitrate_kbps: int | None = None
    path: str | None = None
    source_quality_tier: str | None = None
    # Runtime in minutes — lets the shared scorer estimate bitrate from size when the
    # bitrate is unknown. Defaults to None and is keyword-safe (the external torrents
    # consumer never passes it); when unknown the bitrate-adequacy check stays neutral.
    duration_minutes: float | None = None


@dataclass
class ProposedQuality(MediaQualityFields):
    """Quality information for a proposed (torrent) item."""

    name: str | None = None
    is_ai_upscale: bool = False

    def get_resolution_pixels(self) -> int:
        """Get resolution as total pixels."""
        if not self.resolution:
            return 0
        resolution_lower = self.resolution.lower()
        if resolution_lower in RESOLUTION_MAP:
            w, h = RESOLUTION_MAP[resolution_lower]
            return w * h
        return 0

    def get_resolution_pixels_tuple(self) -> tuple[int, int]:
        """Get resolution as (width, height) tuple."""
        if not self.resolution:
            return (0, 0)
        resolution_lower = self.resolution.lower()
        if resolution_lower in RESOLUTION_MAP:
            return RESOLUTION_MAP[resolution_lower]
        return (0, 0)

    def get_audio_channels(self) -> int:
        """Get audio channel count."""
        if not self.audio:
            return 0
        audio_lower = self.audio.lower()
        for key, channels in AUDIO_CHANNEL_MAP.items():
            if key in audio_lower:
                return channels
        return 2  # Default to stereo

    def get_size_bytes(self) -> int:
        """Get file size in bytes."""
        if self.size_mb:
            return self.size_mb * 1024 * 1024
        return 0

    def get_bitrate(self) -> int:
        """Get bitrate in bps."""
        if self.bitrate_kbps:
            return self.bitrate_kbps * 1000
        return 0

    def _cross_check_source_quality(self, provided_multiplier: float) -> None:
        """Cross-check provided source quality tier against auto-detection.

        Args:
            provided_multiplier: Multiplier from provided tier.
        """
        if not (self.path or self.name):
            return

        auto_multiplier = detect_source_quality(self.path, self.name)

        # Find tier name for auto-detected multiplier
        auto_tier = "unknown"
        for tier_name, tier_info in SOURCE_QUALITY_TIERS.items():
            if tier_info["bonus"] == auto_multiplier:
                auto_tier = tier_name
                break

        # Warn if mismatch
        if abs(provided_multiplier - auto_multiplier) > 0.01:
            logger.warning(
                f"Source quality mismatch! Provided: {self.source_quality_tier} "
                f"({provided_multiplier}x), Auto-detected: {auto_tier} ({auto_multiplier}x) "
                f"from '{self.path or self.name}'. Using provided value."
            )
        else:
            logger.debug(
                f"Source quality cross-check OK: {self.source_quality_tier} matches auto-detection"
            )

    def get_source_quality_multiplier(self) -> float:
        """Get source quality multiplier based on path/name or provided tier.

        If source_quality_tier is provided, uses that directly.
        If both tier and path/name are provided, cross-checks and warns on mismatch.
        """
        # If user provided source_quality_tier directly, use it
        if self.source_quality_tier:
            # Get the multiplier for the provided tier
            provided_multiplier = SOURCE_QUALITY_TIERS.get(
                self.source_quality_tier, SOURCE_QUALITY_TIERS["unknown"]
            )["bonus"]

            # Cross-check if path/name also provided
            self._cross_check_source_quality(provided_multiplier)

            return provided_multiplier

        # No tier provided, auto-detect from path/name
        return detect_source_quality(self.path, self.name)

    def is_ai_upscaled(self) -> bool:
        """Check if content is AI upscaled."""
        return detect_ai_upscale(self.path, self.name)

    def calculate_score(self) -> float:
        """Calculate the proposed item's quality score (unified model).

        Delegates to the shared normalised scorer via ``_create_proposed_as_existing``
        so a proposed (torrent) item is scored exactly like an existing Emby item —
        there is only one formula. HDR is keyed off the ``hdr`` string; a proposed
        item is never penalised as Dolby Vision Profile 5 (the string cannot
        distinguish a defective P5 from a harmless P7/P8).
        """
        return _create_proposed_as_existing(self).calculate_score()

    def score_breakdown(self) -> dict[str, Any]:
        """Return the per-factor breakdown behind ``calculate_score`` (explainability)."""
        return _create_proposed_as_existing(self).score_breakdown()


def _detect_resolution_from_dimensions(width: int, height: int) -> str | None:
    """Detect resolution string from width/height dimensions.

    Uses OR logic for aspect ratio compatibility - movies with non-standard
    aspect ratios (1.85:1, 2.39:1) have height < 1080 even when they're
    "1080p" content (e.g., 1920x1040, 1920x800).

    Args:
        width: Video width in pixels.
        height: Video height in pixels.

    Returns:
        Resolution string (2160p, 1080p, 720p, 480p) or None.
    """
    if width >= 3840 or height >= 2160:
        return "2160p"
    elif width >= 1920 or height >= 1080:
        return "1080p"
    elif width >= 1280 or height >= 720:
        return "720p"
    elif width >= 854 or height >= 480:
        return "480p"
    return None


def _calculate_date_rating_from_item(item: dict[str, Any]) -> int:
    """Calculate date rating from item DateCreated field.

    Args:
        item: Media item dict.

    Returns:
        Unix timestamp of creation date, capped at current time.
    """
    date_rating = 0
    try:
        if "DateCreated" in item:
            date_str = item["DateCreated"]
            if isinstance(date_str, str) and 'T' in date_str:
                date_obj = time.strptime(date_str.split('T')[0], "%Y-%m-%d")
                date_timestamp = int(time.mktime(date_obj))
                # Cap at current time to prevent future dates from giving unfair bonuses
                current_timestamp = int(time.time())
                date_rating = min(date_timestamp, current_timestamp)
    except Exception:
        pass

    return date_rating


@dataclass
class ExistingQuality:
    """Quality information for an existing Emby item."""

    id: str
    name: str
    resolution: str | None = None
    width: int = 0
    height: int = 0
    codec: str | None = None
    hdr: str | None = None
    audio_channels: int = 0
    audio_languages: list[str] | None = None
    size_bytes: int = 0
    bitrate: int = 0
    path: str | None = None
    provider_ids: dict[str, str] | None = None
    season: int | None = None
    episode: int | None = None
    date_rating: int = 0
    raw_score: float = 0.0
    source_quality_tier: str | None = None
    is_ai_upscale: bool = False
    # Video range + DV-P5 flag drive the HDR bonus and the DV Profile-5 penalty in the
    # unified scorer; duration lets it estimate bitrate from size when bitrate is 0.
    video_range: str | None = None
    is_dovi_p5: bool = False
    duration_minutes: float | None = None
    # Lazily-computed per-factor breakdown (explainability), cached on first
    # calculate_score/score_breakdown so sorting doesn't re-run the RTN-parsing source
    # detection. Excluded from equality/repr — it's a derived cache, not identity.
    _breakdown_cache: dict[str, Any] | None = field(
        default=None, compare=False, repr=False
    )

    @staticmethod
    def _extract_streams(item: dict[str, Any]) -> tuple[dict | None, dict | None, list[str]]:
        """Extract video, audio streams and languages from item.

        Args:
            item: Emby item dict.

        Returns:
            Tuple of (video_stream, audio_stream, audio_languages).
        """
        video_stream = None
        audio_stream = None
        audio_languages = []

        for stream in item.get("MediaStreams", []):
            if stream.get("Type") == "Video" and not video_stream:
                video_stream = stream
            elif stream.get("Type") == "Audio":
                if not audio_stream:
                    audio_stream = stream
                lang = stream.get("Language", "").lower()
                if lang and lang not in audio_languages:
                    audio_languages.append(lang)

        return video_stream, audio_stream, audio_languages

    @staticmethod
    def _detect_source_quality_tier(item_path: str | None, item_name: str) -> str | None:
        """Detect source quality tier from path/name.

        Args:
            item_path: Item path.
            item_name: Item name.

        Returns:
            Source quality tier name (falls back to ``"unknown"`` when no tier matches).
        """
        source_multiplier = detect_source_quality(item_path, item_name)
        for tier_name, tier_info in SOURCE_QUALITY_TIERS.items():
            if tier_info["bonus"] == source_multiplier:
                return tier_name
        return "unknown"

    @staticmethod
    def _classify_audio_codec(audio_codec: str | None) -> tuple[bool, bool]:
        """Classify audio codec as lossless or webdl-indicating.

        Args:
            audio_codec: Audio codec string from Emby stream.

        Returns:
            Tuple of (is_lossless, is_webdl_audio).
        """
        audio_lower = (audio_codec or "").lower()
        lossless = audio_lower in (
            "truehd", "dts-hd ma", "dts-hd", "dtshd", "flac", "pcm", "lpcm",
        )
        webdl = audio_lower in ("eac3", "aac", "he-aac", "opus")
        return lossless, webdl

    @staticmethod
    def _infer_by_resolution(
        bitrate: int, lossless_audio: bool, webdl_audio: bool,
        remux_threshold: int, bluray_threshold: int, webdl_threshold: int,
    ) -> str:
        """Infer source quality for a given resolution using bitrate thresholds.

        Args:
            bitrate: Item bitrate in bps.
            lossless_audio: Whether audio codec indicates lossless source.
            webdl_audio: Whether audio codec indicates streaming source.
            remux_threshold: Bitrate above which lossless audio indicates remux.
            bluray_threshold: Bitrate above which source is bluray.
            webdl_threshold: Bitrate above which source is webdl.

        Returns:
            Inferred source quality tier name.
        """
        if lossless_audio and bitrate > remux_threshold:
            return "bluray_remux"
        if lossless_audio or bitrate > bluray_threshold:
            return "bluray"
        if webdl_audio or bitrate > webdl_threshold:
            return "webdl"
        return "hdtv"

    @staticmethod
    def _infer_source_quality_from_streams(
        bitrate: int, height: int, audio_codec: str | None
    ) -> str | None:
        """Infer source quality tier from stream metadata when path/name gives no signal.

        Uses bitrate ranges per resolution and audio codec as heuristics:
        - Lossless audio (TrueHD, DTS-HD MA) indicates BluRay/REMUX
        - DDP/EAC3/AAC indicates WEB-DL (streaming services)
        - Bitrate thresholds vary by resolution

        Args:
            bitrate: Item bitrate in bps.
            height: Video height in pixels.
            audio_codec: Audio codec string from Emby stream (e.g. "eac3", "truehd").

        Returns:
            Inferred source quality tier name, or None if insufficient data.
        """
        if bitrate <= 0 and not audio_codec:
            return None

        lossless_audio, webdl_audio = ExistingQuality._classify_audio_codec(audio_codec)

        # Resolution-specific bitrate thresholds: (remux, bluray, webdl)
        if height >= 2000:  # 4K
            return ExistingQuality._infer_by_resolution(
                bitrate, lossless_audio, webdl_audio, 40_000_000, 25_000_000, 8_000_000
            )
        if height >= 1000:  # 1080p
            return ExistingQuality._infer_by_resolution(
                bitrate, lossless_audio, webdl_audio, 20_000_000, 12_000_000, 3_000_000
            )
        if height >= 700:  # 720p — no remux distinction
            if lossless_audio or bitrate > 8_000_000:
                return "bluray"
            if webdl_audio or bitrate > 2_000_000:
                return "webdl"
            return "hdtv"

        # SD content — audio codec is best signal
        if lossless_audio:
            return "bluray"
        if webdl_audio:
            return "webdl"
        return None

    @classmethod
    def from_emby_item(cls, item: dict[str, Any]) -> ExistingQuality:
        """Create ExistingQuality from an Emby item dict."""
        # Extract streams
        video_stream, audio_stream, audio_languages = cls._extract_streams(item)

        width = video_stream.get("Width", 0) if video_stream else 0
        height = video_stream.get("Height", 0) if video_stream else 0

        # Determine resolution string using helper
        resolution = _detect_resolution_from_dimensions(width, height)

        # Calculate date rating using helper
        date_rating = _calculate_date_rating_from_item(item)

        # Detect source quality and AI upscale
        item_path = item.get("Path")
        item_name = item.get("Name", "")
        source_quality_tier = cls._detect_source_quality_tier(item_path, item_name)

        # Fallback: infer from stream metadata when path/name gives unknown
        if source_quality_tier == "unknown":
            audio_codec = audio_stream.get("Codec") if audio_stream else None
            item_bitrate = item.get("Bitrate", 0)
            inferred = cls._infer_source_quality_from_streams(item_bitrate, height, audio_codec)
            if inferred:
                logger.debug(
                    f"Source quality inferred from streams: {inferred} "
                    f"(bitrate={item_bitrate}, audio={audio_codec}, height={height})"
                )
                source_quality_tier = inferred

        is_ai_upscale = detect_ai_upscale(item_path, item_name)

        return cls(
            id=item.get("Id", ""),
            name=item.get("Name", ""),
            resolution=resolution,
            width=width,
            height=height,
            codec=video_stream.get("Codec", "") if video_stream else None,
            audio_channels=audio_stream.get("Channels", 0) if audio_stream else 0,
            audio_languages=audio_languages or None,
            size_bytes=item.get("Size", 0),
            bitrate=item.get("Bitrate", 0),
            path=item.get("Path"),
            provider_ids=item.get("ProviderIds"),
            season=item.get("ParentIndexNumber"),
            episode=item.get("IndexNumber"),
            date_rating=date_rating,
            source_quality_tier=source_quality_tier,
            is_ai_upscale=is_ai_upscale,
            video_range=video_stream.get("VideoRange") if video_stream else None,
            is_dovi_p5=_is_dovi_profile5(video_stream),
            duration_minutes=duration_minutes_from_ticks(item.get("RunTimeTicks")),
        )

    def _score_inputs(self) -> dict[str, Any]:
        """Assemble the normalised scoring inputs for this item.

        Shared by ``calculate_score`` and ``score_breakdown`` so the (potentially
        RTN-parsing) source detection and factor extraction happen in one place.
        """
        # Use stored tier if available (e.g. inferred from streams),
        # otherwise fall back to path/name detection.
        if self.source_quality_tier and self.source_quality_tier != "unknown":
            source_multiplier = SOURCE_QUALITY_TIERS.get(
                self.source_quality_tier, SOURCE_QUALITY_TIERS["unknown"]
            )["bonus"]
        else:
            source_multiplier = detect_source_quality_with_rtn(self.path, self.name)

        return {
            "res_megapixels": (self.width * self.height) / 1_000_000.0,
            "height": self.height,
            "hdr_bonus": hdr_bonus_from_string(self.video_range),
            "video_bitrate_mbps": self.bitrate / 1_000_000.0 if self.bitrate else 0.0,
            "audio_channels": self.audio_channels,
            "date_rating": self.date_rating,
            "codec": self.codec,
            "source_multiplier": source_multiplier,
            "ai_multiplier": 0.7 if self.is_ai_upscale else 1.0,
            "is_dovi_p5": self.is_dovi_p5,
            "size_bytes": self.size_bytes,
            "duration_minutes": self.duration_minutes,
        }

    def calculate_score(self) -> float:
        """Calculate the item's quality score using the unified normalised model.

        Delegates to ``scoring.compute_quality_score`` (shared with the dedupe path).
        Resolution dominates; HDR (keyed off ``video_range``) is a bonus; video
        bitrate is a within-resolution tiebreaker; a bitrate below the codec-adjusted
        adequacy floor demotes the resolution credit proportionally; a Dolby Vision
        Profile-5 file is collapsed by the DV-P5 penalty. When bitrate is unknown it
        is estimated from size + duration, or left neutral if neither is available.

        The per-factor breakdown is computed once and cached (``score_breakdown``):
        sorting (``apply_language_priority`` / smart-override) re-scores the same
        instances many times and the source detection can run RTN parsing, so this
        avoids repeating it.
        """
        return float(self.score_breakdown()["total"])

    def score_breakdown(self) -> dict[str, Any]:
        """Return (and lazily cache) the per-factor breakdown behind the score."""
        if self._breakdown_cache is None:
            self._breakdown_cache = compute_quality_breakdown(**self._score_inputs())
            # Keep the legacy ``raw_score`` slot populated for any external reader.
            self.raw_score = self._breakdown_cache["total"]
        return self._breakdown_cache

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "id": self.id,
            "name": self.name,
            "quality": {
                "resolution": self.resolution,
                "codec": self.codec,
                "audio_channels": self.audio_channels,
                "bitrate_kbps": self.bitrate // 1000 if self.bitrate else 0,
                "size_mb": self.size_bytes // (1024 * 1024) if self.size_bytes else 0,
                "source_quality_tier": self.source_quality_tier,
                "is_ai_upscale": self.is_ai_upscale,
            },
            "audio_languages": self.audio_languages,
            "provider_ids": self.provider_ids,
            "path": self.path,
            "season": self.season,
            "episode": self.episode,
            # Per-factor breakdown behind this item's score (explainability).
            "score_breakdown": _round_breakdown(self.score_breakdown()),
        }


@dataclass
class ComparisonResult:
    """Result of comparing proposed vs existing quality.

    This is a PUBLIC, stable contract (the torrents repo consumes it): the field set,
    the Literal value sets below, and the ExistingQuality fields (including
    ``source_quality_tier`` and ``is_ai_upscale``) may grow but will not change
    meaning or disappear without a deprecation path.

    Value sets:
        recommendation: "download" | "skip"
        status: "found" | "not_found" | "excluded" (the item's provider ID was in the
            checker's exclude list — flows through ``status != "not_found"`` branches)
        reason: "not_found" | "better_quality" | "same_or_worse" | "excluded_id"
    """

    recommendation: Literal["download", "skip"]
    reason: Literal["not_found", "better_quality", "same_or_worse", "excluded_id"]
    status: Literal["found", "not_found", "excluded"]
    existing: ExistingQuality | None = None
    proposed: ProposedQuality | None = None
    existing_score: float = 0.0
    proposed_score: float = 0.0
    score_diff: float = 0.0

    @property
    def should_download(self) -> bool:
        """Return True if the recommendation is to download."""
        return self.recommendation == "download"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        result: dict[str, Any] = {
            "status": self.status,
            "recommendation": self.recommendation,
            "reason": self.reason,
        }

        if self.existing:
            result["existing"] = self.existing.to_dict()

        if self.proposed:
            result["proposed"] = {
                "resolution": self.proposed.resolution,
                "codec": self.proposed.codec,
                "size_mb": self.proposed.size_mb,
                "audio_languages": self.proposed.audio_languages,
            }

        if self.status == "found":
            result["quality_comparison"] = {
                "existing_score": round(self.existing_score, 2),
                "proposed_score": round(self.proposed_score, 2),
                "score_diff": round(self.score_diff, 2),
                "winner": "proposed" if self.proposed_score > self.existing_score else "existing",
                # Per-factor breakdown of each score (explainability): which factor
                # (resolution/hdr/bitrate/audio) and which multiplier drove the result.
                # Rounded to 2 decimals for compact JSON.
                "existing_breakdown": _round_breakdown(self.existing.score_breakdown()) if self.existing else None,
                "proposed_breakdown": _round_breakdown(self.proposed.score_breakdown()) if self.proposed else None,
            }

        return result


def _get_best_lang_priority(
    item_languages: list[str] | None,
    normalized_priorities: list[str],
    lang_mapping: dict[str, str],
) -> int:
    """Find best (lowest index) language priority for an item.

    Args:
        item_languages: Item's audio languages.
        normalized_priorities: Normalized priority list.
        lang_mapping: Language normalization mapping.

    Returns:
        Best priority index (9999 if no match).
    """
    if not item_languages:
        return 9999

    # Normalize item languages
    normalized_langs = [lang_mapping.get(lang.lower(), lang.lower()) for lang in item_languages]

    # Find best priority
    best_priority = 9999
    for lang in normalized_langs:
        if lang in normalized_priorities:
            priority = normalized_priorities.index(lang)
            if priority < best_priority:
                best_priority = priority

    return best_priority


def apply_language_priority(
    items: list[ExistingQuality],
    lang_priorities: list[str] | None = None,
) -> list[ExistingQuality]:
    """Sort items by language priority, then by quality score.

    Args:
        items: List of existing quality items.
        lang_priorities: Language priority list (e.g., ['sk', 'cs', 'en']).

    Returns:
        Sorted list of items.
    """
    if not lang_priorities:
        return sorted(items, key=lambda x: x.calculate_score(), reverse=True)

    # Use shared language normalization mapping
    lang_mapping = LANGUAGE_NORMALIZATION_MAP

    # Normalize the priority list
    normalized_priorities = [lang_mapping.get(lang.lower(), lang.lower()) for lang in lang_priorities]

    def get_lang_priority(item: ExistingQuality) -> tuple[bool, int, float]:
        """Get language priority score for sorting."""
        best_priority = _get_best_lang_priority(
            item.audio_languages, normalized_priorities, lang_mapping
        )
        has_priority = best_priority < 9999
        return (not has_priority, best_priority, -item.calculate_score())

    return sorted(items, key=get_lang_priority)


def _create_proposed_as_existing(proposed: ProposedQuality) -> ExistingQuality:
    """Convert ProposedQuality to ExistingQuality format for comparison.

    Args:
        proposed: Proposed torrent quality information.

    Returns:
        ExistingQuality object representing the proposed item.
    """
    # Extract width/height from resolution
    width, height = 0, 0
    if proposed.resolution:
        res_lower = proposed.resolution.lower()
        if res_lower in RESOLUTION_MAP:
            width, height = RESOLUTION_MAP[res_lower]

    # Detect source quality and AI upscale for proposed item
    proposed_source_tier = ExistingQuality._detect_source_quality_tier(
        proposed.path, proposed.name or ""
    )
    proposed_is_ai_upscale = detect_ai_upscale(proposed.path, proposed.name)

    return ExistingQuality(
        id="proposed",
        name="Proposed",
        width=width,
        height=height,
        codec=proposed.codec,
        audio_channels=proposed.get_audio_channels(),
        audio_languages=proposed.audio_languages,
        size_bytes=proposed.get_size_bytes(),
        bitrate=proposed.get_bitrate(),
        date_rating=int(time.time()),  # Current time (newest)
        path=proposed.path,
        source_quality_tier=proposed_source_tier,
        is_ai_upscale=proposed_is_ai_upscale,
        # HDR bonus is keyed off the free-form ``hdr`` string for proposed items.
        # A proposed item is NEVER penalised as DV Profile 5: the string ("DV") can't
        # tell a defective P5 from a harmless P7/P8, so is_dovi_p5 stays False.
        video_range=proposed.hdr,
        is_dovi_p5=False,
        duration_minutes=proposed.duration_minutes,
    )


def _apply_smart_override_if_needed(
    all_items: list[ExistingQuality],
    sorted_items: list[ExistingQuality],
    lang_priorities: list[str] | None,
) -> list[ExistingQuality]:
    """Apply smart override logic if quality significantly better than language priority.

    Args:
        all_items: All items including proposed.
        sorted_items: Items sorted by language priority.
        lang_priorities: Language priority list.

    Returns:
        Updated sorted items (either original or re-sorted by quality).
    """
    if not lang_priorities or len(sorted_items) < 2:
        return sorted_items

    best_by_lang = sorted_items[0]
    best_by_quality = max(all_items, key=lambda x: x.calculate_score())

    # If they're the same item, no override needed
    if best_by_lang.id == best_by_quality.id:
        return sorted_items

    lang_item_langs = best_by_lang.audio_languages or []
    quality_item_langs = best_by_quality.audio_languages or []

    # Calculate quality ratio
    lang_score = best_by_lang.calculate_score()
    quality_score = best_by_quality.calculate_score()
    quality_ratio = quality_score / lang_score if lang_score > 0 else float('inf')

    # Determine if items have priority languages
    lang_mapping = LANGUAGE_NORMALIZATION_MAP
    normalized_priorities = [lang_mapping.get(lang.lower(), lang.lower()) for lang in lang_priorities]

    def has_priority_lang(langs):
        """Check if any language matches priority list."""
        if not langs:
            return False
        normalized = [lang_mapping.get(lang.lower(), lang.lower()) for lang in langs]
        return any(lang in normalized_priorities for lang in normalized)

    lang_item_has_priority = has_priority_lang(lang_item_langs)
    quality_item_has_priority = has_priority_lang(quality_item_langs)
    is_single_lang_scenario = len(lang_item_langs) == 1 and len(quality_item_langs) >= 2

    # Use shared smart override logic
    if should_quality_override_language(
        quality_ratio=quality_ratio,
        lang_item_has_priority_lang=lang_item_has_priority,
        quality_item_has_priority_lang=quality_item_has_priority,
        is_single_lang_scenario=is_single_lang_scenario,
    ):
        # Log the override decision
        if is_single_lang_scenario:
            logger.info(f"Quality override (single-lang): {quality_item_langs} quality {quality_score:.1f} "
                      f"wins over {lang_item_langs} quality {lang_score:.1f} (ratio: {quality_ratio:.2f}x)")
        else:
            logger.info(f"Quality override (worse-priority): {quality_item_langs} quality {quality_score:.1f} "
                      f"wins over {lang_item_langs} quality {lang_score:.1f} (ratio: {quality_ratio:.2f}x)")

        # Use quality-based sorting
        return sorted(all_items, key=lambda x: x.calculate_score(), reverse=True)

    return sorted_items


def _apply_bluray_native_exception(
    proposed_as_existing: ExistingQuality,
    best_existing: ExistingQuality,
    current_recommendation: Literal["download", "skip"],
) -> Literal["download", "skip"]:
    """Apply BluRay native exception rule.

    If comparing native BluRay 1080p vs AI upscaled 4K, and native is 1.5x+ larger,
    prefer the native version.

    Args:
        proposed_as_existing: Proposed item as ExistingQuality.
        best_existing: Best existing item.
        current_recommendation: Current recommendation ("download" or "skip").

    Returns:
        Updated recommendation after applying exception rule.
    """
    # Only apply if one is AI upscale and one is not
    if proposed_as_existing.is_ai_upscale == best_existing.is_ai_upscale:
        return current_recommendation

    # Determine which is native and which is AI upscaled
    native = best_existing if not best_existing.is_ai_upscale else proposed_as_existing
    ai_item = proposed_as_existing if proposed_as_existing.is_ai_upscale else best_existing

    # Check if native is BluRay/REMUX source
    native_source = detect_source_quality(native.path, native.name)
    is_bluray = native_source >= SOURCE_QUALITY_TIERS["bluray"]["bonus"]

    # Check if native is 1080p and AI upscale is 4K
    native_is_1080p = (
        1920 * 1080 <= (native.width * native.height) < 3840 * 2160
    )
    ai_is_4k = (ai_item.width * ai_item.height) >= 3840 * 2160

    # Check size ratio
    if is_bluray and native_is_1080p and ai_is_4k and ai_item.size_bytes > 0:
        size_ratio = native.size_bytes / ai_item.size_bytes
        if size_ratio >= 1.5:
            logger.info(
                f"BluRay native exception: Native BluRay 1080p "
                f"({native.size_bytes // (1024*1024)}MB) preferred over AI upscaled 4K "
                f"({ai_item.size_bytes // (1024*1024)}MB) due to {size_ratio:.2f}x size ratio"
            )
            # Override recommendation to prefer native
            if native.id == "proposed":
                return "download"
            else:
                return "skip"

    return current_recommendation


def compare_quality(
    proposed: ProposedQuality,
    existing_items: list[dict[str, Any]],
    lang_priorities: list[str] | None = None,
) -> ComparisonResult:
    """Compare proposed quality against existing items.

    Uses the SAME logic as deduplication - treats proposed as another item
    and applies language priority to all items together.

    Args:
        proposed: Proposed (torrent) quality information.
        existing_items: List of existing Emby items.
        lang_priorities: Language priority list.

    Returns:
        ComparisonResult with recommendation.
    """
    # Handle not found case
    # Download even if quality isn't perfect - goal is to have content first,
    # then upgrade later when better quality becomes available
    if not existing_items:
        return ComparisonResult(
            recommendation="download",
            reason="not_found",
            status="not_found",
            proposed=proposed,
        )

    # Convert existing items to ExistingQuality objects
    existing = [ExistingQuality.from_emby_item(item) for item in existing_items]

    # Create a pseudo-ExistingQuality for the proposed item using helper
    proposed_as_existing = _create_proposed_as_existing(proposed)

    # Add proposed to the list for comparison
    all_items = existing + [proposed_as_existing]

    # Apply language priority and sort (same as deduplication)
    sorted_items = apply_language_priority(all_items, lang_priorities)

    # Apply "smart override" logic if quality significantly better than language priority
    sorted_items = _apply_smart_override_if_needed(all_items, sorted_items, lang_priorities)

    # The best item is at index 0
    best_item = sorted_items[0]

    # If the best item is the proposed one, recommend download
    recommendation: Literal["download", "skip"]
    reason: Literal["not_found", "better_quality", "same_or_worse", "excluded_id"]
    if best_item.id == "proposed":
        recommendation = "download"
        best_existing = sorted_items[1] if len(sorted_items) > 1 else existing[0]
    else:
        recommendation = "skip"
        best_existing = best_item

    # Apply BluRay native exception rule using helper. It can flip the recommendation
    # (e.g. a large native 1080p BluRay overriding an AI-upscaled 4K); keep ``reason``
    # consistent with the flipped recommendation so a "skip" never reports
    # "better_quality" (and vice-versa).
    recommendation = _apply_bluray_native_exception(
        proposed_as_existing, best_existing, recommendation
    )
    reason = "better_quality" if recommendation == "download" else "same_or_worse"

    # Resolution dominance is now handled by the unified normalised scoring model
    # (resolution-dominant, with a codec-adjusted starved-bitrate demotion and the
    # Dolby Vision Profile-5 penalty) — no manual override needed here.

    # Calculate scores for reporting
    best_existing_score = best_existing.calculate_score()
    proposed_score = proposed_as_existing.calculate_score()
    score_diff = proposed_score - best_existing_score

    return ComparisonResult(
        recommendation=recommendation,
        reason=reason,
        status="found",
        existing=best_existing,
        proposed=proposed,
        existing_score=best_existing_score,
        proposed_score=proposed_score,
        score_diff=score_diff,
    )
