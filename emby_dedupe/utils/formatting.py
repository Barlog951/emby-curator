"""Shared human-readable formatting helpers.

Lives in ``utils`` (not ``reports``) so both the API layer and the report layer can
import it without an ``api -> reports`` import cycle.
"""

from __future__ import annotations

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_file_size(size_bytes: int | None, *, zero_label: str = "0 B") -> str:
    """Format a byte count as a human-readable string (e.g. ``"4.20 GB"``).

    The single file-size formatter for the whole package — previously three
    divergent copies rendered the same size differently ("512 B" vs "512.00 B" vs
    "500 bytes", .1f vs .2f). Uses two-decimal precision across all units.

    Args:
        size_bytes: Size in bytes. ``None`` or ``0`` yields ``zero_label`` — None-safe
            because Emby returns a null ``Size`` for some items (the old
            ``reports.common`` version crashed on None).
        zero_label: Label for an unknown/zero size (call sites differ: "unknown",
            "Unknown", or "0 B").

    Returns:
        Formatted size string.
    """
    if not size_bytes:  # None or 0
        return zero_label

    value = float(size_bytes)
    i = 0
    while value >= 1024 and i < len(_SIZE_UNITS) - 1:
        value /= 1024.0
        i += 1
    return f"{value:.2f} {_SIZE_UNITS[i]}"
