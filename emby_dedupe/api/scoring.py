"""Shared, normalised media-quality scoring model.

The single quality-scoring model used by both paths:

* the deduplication path (``metadata._calculate_quality_rating``), and
* the check path (``quality_compare.ExistingQuality.calculate_score`` /
  ``ProposedQuality.calculate_score``).

Factors are normalised (megapixels, Mbps, channels) so the weights express intent
instead of raw magnitudes deciding the outcome. Resolution dominates; HDR is a real
bonus; video bitrate is a within-resolution tiebreaker; the added date only resolves
otherwise-identical items. A file whose bitrate is below the codec-adjusted adequacy
floor for its resolution has its resolution credit demoted proportionally, so a badly
under-bitrate high-res file can lose to a well-encoded lower-res one. This is a
demotion, never a 0.0 auto-reject, so the language-override ratio math stays intact.

This module imports nothing from ``quality_compare`` or ``metadata``; callers compute
their own ``source_multiplier`` / ``ai_multiplier`` and pass them in as floats.
"""

import math
import re
from typing import Any

# --- Weights (normalised factors) -----------------------------------------------
RES_WEIGHT = 10.0        # resolution (megapixels) — primary
HDR_WEIGHT = 5.0         # HDR (HDR10/HDR10+/Dolby Vision/HLG) over SDR
BITRATE_WEIGHT = 1.0     # video bitrate (Mbps) — tiebreaker within a resolution tier
AUDIO_WEIGHT = 0.5       # audio channels — minor
DATE_WEIGHT = 0.01       # added-date — only breaks exact ties (normalised tiny)

# Dolby Vision Profile 5 ("green/pink") quality penalty.
#
# DV Profile 5 is single-layer (BL+RPU), encoded in the IPT-PQ-C2 / ICtCp matrix
# with NO HDR10 fallback. On any Emby playback path that is not full-DV-P5 aware
# (most clients, browsers, transcodes) it renders with a green/magenta tint, i.e.
# it is effectively unwatchable. When a non-defective copy of the same item also
# exists, we must keep that one even though the DV P5 file is usually LARGER (and
# would otherwise win on raw size/bitrate). This multiplier collapses the P5
# file's quality rating so a non-P5 sibling always outranks it, while staying a
# positive multiplier (keeps the language-override ratio math intact).
DOVI_P5_QUALITY_PENALTY = 0.0001

# Emby runtime is expressed in ticks of 100 ns; 600,000,000 ticks == 1 minute.
_TICKS_PER_MINUTE = 600_000_000


def _is_dovi_profile5(video_stream: dict[str, Any] | None) -> bool:
    """Return True if a video stream is Dolby Vision Profile 5 (green/pink risk).

    Emby exposes the DV profile directly: a P5 file reports
    ``ExtendedVideoSubType == "DoviProfile50"`` (description ``"Profile 5.0"``,
    ``VideoRange == "DolbyVision"``). DV Profile 7/8 carry an HDR10 base layer and do
    not show the tint, so they are not flagged (subtype ``DoviProfile70``/``DoviProfile81``).

    Args:
        video_stream: An Emby video ``MediaStream`` dict, or None.

    Returns:
        True only for Dolby Vision Profile 5.
    """
    if not video_stream:
        return False
    subtype = str(video_stream.get("ExtendedVideoSubType") or "")
    if subtype.startswith("DoviProfile5"):
        return True
    # Fallback for builds that omit ExtendedVideoSubType: Dolby Vision range
    # plus a "Profile 5.x" description.
    video_range = str(video_stream.get("VideoRange") or "").replace(" ", "").lower()
    desc = str(video_stream.get("ExtendedVideoSubTypeDescription") or "").strip().lower()
    return video_range == "dolbyvision" and desc.startswith("profile 5")


def is_neutral_multiplier(value: float) -> bool:
    """True when a quality multiplier is the neutral 1.0 (tolerant float compare).

    Multipliers are built from float constants (0.7 AI, 0.9 HDTV, 1.15 BluRay, …), so
    they are compared with a tolerance rather than ``== 1.0``.
    """
    return math.isclose(value, 1.0, rel_tol=1e-9, abs_tol=1e-12)


def hdr_bonus_from_string(value: str | None) -> float:
    """Return the HDR bonus (1.0) for any real HDR marker, 0.0 for SDR/unknown.

    Accepts both Emby ``VideoRange`` values ("DolbyVision", "HDR 10", "HLG") and
    the free-form ``hdr`` strings the check path carries for proposed items
    ("DV", "HDR10", "HDR10+"). The Dolby Vision Profile-5 penalty is applied
    separately and never through this bonus.
    """
    normalized = str(value or "").replace(" ", "").lower()
    if not normalized or normalized in ("sdr", "unknown", "none"):
        return 0.0
    return 1.0


# Explicit HDR markers as they appear in release filenames. Not a scoring input: the
# HDR bonus comes from the video stream's VideoRange only (metadata._hdr_bonus), because
# release names tagged "HDR" are regularly 8-bit SDR. Kept as a public helper. Word
# boundaries keep "HDR" from matching inside other tokens; bare "DV" is excluded because
# it collides with "DVDRip". Character ranges are lower-case only; IGNORECASE covers the rest.
_HDR_FILENAME_RE = re.compile(
    r"(?<![a-z0-9])(?:hdr10\+?|hdr|dolby[ ._-]?vision|dovi|hlg)(?![a-z0-9])",
    re.IGNORECASE,
)


def hdr_marker_in_text(text: str | None) -> bool:
    """Return True if a filename/title carries an explicit HDR marker.

    Matches ``HDR``, ``HDR10``/``HDR10+``, ``Dolby Vision``/``DoVi``, and ``HLG``.
    Deliberately does NOT match a bare ``DV`` (false-positives on ``DVDRip``).
    Never used for scoring: the HDR bonus and the Dolby Vision Profile-5 penalty
    are both strictly stream-metadata-driven.
    """
    if not text:
        return False
    return bool(_HDR_FILENAME_RE.search(str(text)))


def codec_efficiency_ratio(codec: str | None) -> float:
    """Return the codec's bitrate-adequacy ratio vs the H.264 baseline.

    HEVC achieves comparable quality at ~65% of H.264's bitrate, AV1 at ~50%.
    Used to lower the starved-bitrate floor for efficient codecs so an efficient
    encode is not falsely demoted for a bitrate that is perfectly adequate for it.
    """
    if not codec:
        return 1.0
    codec_lower = codec.lower()
    if any(x in codec_lower for x in ("hevc", "x265", "h265")):
        return 0.65
    if "av1" in codec_lower:
        return 0.5
    return 1.0


def frame_size(width: int | None, height: int | None) -> tuple[float, float]:
    """The 16:9 frame that encloses a ``width`` x ``height`` picture; (0, 0) when either is unknown.

    Resolution is scored on this frame, not on the stored pixels, so black bars earn nothing: a 2.39:1
    episode stored cropped (3832x1600) and the same episode stored letterboxed (3840x2160, picture
    3840x1608) both count as a 3840x2160 frame; otherwise a letterboxed SDR copy outranks its cropped
    HDR twin. A 4:3 picture (1440x1080) likewise counts as its pillarboxed 1920x1080 frame.

    Args:
        width: Stored video width in pixels (None or 0 when unknown).
        height: Stored video height in pixels (None or 0 when unknown).

    Returns:
        (frame width, frame height) in pixels.
    """
    width, height = width or 0, height or 0
    if not (width and height):
        return 0.0, 0.0
    frame_width = max(float(width), height * 16 / 9)
    return frame_width, frame_width * 9 / 16


def frame_megapixels(width: int | None, height: int | None) -> float:
    """Megapixels of the enclosing 16:9 frame (``frame_size``): the resolution factor of the score."""
    frame_width, frame_height = frame_size(width, height)
    return frame_width * frame_height / 1_000_000.0


def frame_height(width: int | None, height: int | None) -> int:
    """Height of the enclosing 16:9 frame (the stored height when the width is unknown).

    Selects the bitrate-adequacy floor, so a cropped 4K film (3832x1600) is held to the 4K floor
    like its letterboxed twin, not to the 1080p one.
    """
    return max(height or 0, round(frame_size(width, height)[1]))


def bitrate_floor_mbps(height: int) -> float:
    """Bitrate (Mbps) below which a file is 'starved' for its resolution.

    These are the (H.264-baseline) adequacy floors; ``compute_quality_score``
    multiplies them by ``codec_efficiency_ratio`` before use. Tunable defaults.
    """
    if height >= 2000:   # 2160p / 4K
        return 6.0
    if height >= 1000:   # 1080p
        return 2.5
    if height >= 700:    # 720p
        return 1.2
    return 0.0           # SD: no floor


def estimate_bitrate_mbps_from_size(
    size_bytes: int, duration_minutes: float
) -> float:
    """Estimate video bitrate (Mbps) from file size and duration.

    Assumes ~90% of the file is video (10% audio/subtitles). Returns 0.0 when
    inputs are unusable.
    """
    if size_bytes <= 0 or duration_minutes <= 0:
        return 0.0
    video_bytes = size_bytes * 0.9
    duration_seconds = duration_minutes * 60.0
    return (video_bytes * 8.0) / duration_seconds / 1_000_000.0


def duration_minutes_from_ticks(runtime_ticks: int | None) -> float | None:
    """Convert Emby ``RunTimeTicks`` to minutes, or None when unknown."""
    if not runtime_ticks:
        return None
    try:
        minutes = float(runtime_ticks) / _TICKS_PER_MINUTE
    except (TypeError, ValueError):
        return None
    return minutes if minutes > 0 else None


def _resolve_adequacy_bitrate(
    video_bitrate_mbps: float,
    size_bytes: int,
    duration_minutes: float | None,
) -> tuple[float, bool]:
    """Resolve the bitrate used for the adequacy/starvation check.

    Order: real video bitrate → estimate from size + duration → NEUTRAL. When the
    bitrate is unknown AND it cannot be estimated (size or duration missing), the
    adequacy check is skipped entirely (no demotion, never treated as defective).

    Returns:
        (adequacy_mbps, known) — ``known`` is False in the neutral case.
    """
    if video_bitrate_mbps > 0:
        return video_bitrate_mbps, True
    if duration_minutes and duration_minutes > 0 and size_bytes > 0:
        estimated = estimate_bitrate_mbps_from_size(size_bytes, duration_minutes)
        if estimated > 0:
            return estimated, True
    return 0.0, False


def compute_quality_breakdown(
    *,
    res_megapixels: float,
    height: int,
    hdr_bonus: float,
    video_bitrate_mbps: float,
    audio_channels: int,
    date_rating: int,
    codec: str | None,
    source_multiplier: float,
    ai_multiplier: float,
    is_dovi_p5: bool,
    size_bytes: int = 0,
    duration_minutes: float | None = None,
) -> dict[str, Any]:
    """Compute the quality score AND its per-factor breakdown (explainability).

    Same inputs and arithmetic as ``compute_quality_score`` (which delegates here for
    its ``"total"``). The returned dict makes the score auditable::

        {
          "resolution_credit": 82.9,   # weighted resolution term (after any demotion)
          "resolution_raw": 8.29,      # raw megapixels (before weight/demotion)
          "starved_demotion": False,   # True if bitrate was below the codec floor
          "hdr": 5.0, "bitrate": 25.0, "audio": 3.0, "date": 0.02,
          "multipliers": {"source": 1.15, "ai": 1.0, "dovi_p5": 1.0},
          "total": 109.4,
        }

    so ``(resolution_credit+hdr+bitrate+audio+date) * source * ai * dovi_p5 == total``.
    See ``compute_quality_score`` for the argument semantics.
    """
    adequacy_mbps, adequacy_known = _resolve_adequacy_bitrate(
        video_bitrate_mbps, size_bytes, duration_minutes
    )

    # Starved-bitrate demotion: if the (known) bitrate is below the codec-adjusted
    # adequacy floor for this resolution, reduce the resolution credit proportionally.
    # At/above the floor — or when the bitrate is unknown and cannot be estimated —
    # the file keeps full resolution credit (neutral, never "defective").
    floor = bitrate_floor_mbps(height) * codec_efficiency_ratio(codec)
    res_credit = res_megapixels
    starved_demotion = False
    if adequacy_known and floor and 0 < adequacy_mbps < floor:
        res_credit = res_megapixels * (adequacy_mbps / floor)
        starved_demotion = True

    resolution_credit = res_credit * RES_WEIGHT
    hdr = hdr_bonus * HDR_WEIGHT
    bitrate = video_bitrate_mbps * BITRATE_WEIGHT
    audio = audio_channels * AUDIO_WEIGHT
    date = (date_rating / 1_000_000_000.0) * DATE_WEIGHT

    base_rating = resolution_credit + hdr + bitrate + audio + date
    dovi_p5_multiplier = DOVI_P5_QUALITY_PENALTY if is_dovi_p5 else 1.0
    total = base_rating * source_multiplier * ai_multiplier * dovi_p5_multiplier

    return {
        "resolution_credit": resolution_credit,
        "resolution_raw": res_megapixels,
        "starved_demotion": starved_demotion,
        "hdr": hdr,
        "bitrate": bitrate,
        "audio": audio,
        "date": date,
        "multipliers": {
            "source": source_multiplier,
            "ai": ai_multiplier,
            "dovi_p5": dovi_p5_multiplier,
        },
        "total": total,
    }


def _source_tier_label(source_multiplier: float) -> str:
    """Human label for a source-quality multiplier (REMUX/BluRay/WEB-DL/HDTV)."""
    if source_multiplier >= 1.3:
        return "BluRay REMUX"
    if source_multiplier >= 1.15:
        return "BluRay"
    if source_multiplier >= 1.0:
        return "WEB-DL"
    return "HDTV"


def format_score_summary(breakdown: dict[str, Any]) -> str:
    """One-line human explanation of a score (built from ``compute_quality_breakdown``).

    Example: ``"score 109.4 = res 82.9 + HDR 5.0 + bitrate 25.0 + audio 3.0,
    ×1.15 BluRay"``.
    """
    parts = [f"res {breakdown['resolution_credit']:.1f}"]
    if breakdown["hdr"]:
        parts.append(f"HDR {breakdown['hdr']:.1f}")
    parts.append(f"bitrate {breakdown['bitrate']:.1f}")
    parts.append(f"audio {breakdown['audio']:.1f}")
    summary = f"score {breakdown['total']:.1f} = " + " + ".join(parts)

    m = breakdown["multipliers"]
    notes = []
    if not is_neutral_multiplier(m["source"]):
        notes.append(f"×{m['source']:.2f} {_source_tier_label(m['source'])}")
    if not is_neutral_multiplier(m["ai"]):
        notes.append(f"×{m['ai']:.2f} AI-upscale")
    if not is_neutral_multiplier(m["dovi_p5"]):
        notes.append(f"×{m['dovi_p5']:.4f} DV-P5")
    if notes:
        summary += ", " + ", ".join(notes)
    if breakdown["starved_demotion"]:
        summary += " (bitrate-starved)"
    return summary


def compute_quality_score(
    *,
    res_megapixels: float,
    height: int,
    hdr_bonus: float,
    video_bitrate_mbps: float,
    audio_channels: int,
    date_rating: int,
    codec: str | None,
    source_multiplier: float,
    ai_multiplier: float,
    is_dovi_p5: bool,
    size_bytes: int = 0,
    duration_minutes: float | None = None,
) -> float:
    """Compute the unified, normalised quality score.

    Args:
        res_megapixels: megapixels of the enclosing 16:9 frame (``frame_megapixels``).
        height: height of that frame in pixels (``frame_height``; selects the bitrate-adequacy floor).
        hdr_bonus: 1.0 for real HDR, 0.0 otherwise (see ``hdr_bonus_from_string``).
        video_bitrate_mbps: real video bitrate in Mbps, 0.0 if unknown. Used as the
            within-resolution tiebreaker term (real-only — an estimated bitrate is
            used solely for the adequacy check, never as the tiebreaker).
        audio_channels: number of audio channels.
        date_rating: unix timestamp (0 if unknown) — a tiny tiebreaker only.
        codec: video codec name, for the codec-adjusted adequacy floor.
        source_multiplier: pre-computed source-quality multiplier (REMUX/BluRay/…).
        ai_multiplier: pre-computed AI-upscale multiplier (0.7 if upscaled else 1.0).
        is_dovi_p5: True to apply the Dolby Vision Profile-5 collapse penalty.
        size_bytes: file size, used only to estimate bitrate when it is unknown.
        duration_minutes: runtime in minutes, used only to estimate bitrate.

    Returns:
        The quality score (higher is better). For the per-factor breakdown behind this
        number, call ``compute_quality_breakdown`` with the same arguments.
    """
    return float(compute_quality_breakdown(
        res_megapixels=res_megapixels,
        height=height,
        hdr_bonus=hdr_bonus,
        video_bitrate_mbps=video_bitrate_mbps,
        audio_channels=audio_channels,
        date_rating=date_rating,
        codec=codec,
        source_multiplier=source_multiplier,
        ai_multiplier=ai_multiplier,
        is_dovi_p5=is_dovi_p5,
        size_bytes=size_bytes,
        duration_minutes=duration_minutes,
    )["total"])
