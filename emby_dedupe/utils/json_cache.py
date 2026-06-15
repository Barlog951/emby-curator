"""Atomic JSON file-cache primitives shared by the genre and description caches.

Both caches persist a single dict to a JSON file with identical requirements:
crash-safe writes (write a sibling ``.tmp`` then rename over the target),
tolerance of a missing or corrupt file (return ``{}``), and never letting a disk
error reach the caller. This module is the one implementation; the genre and
description cache modules are thin wrappers that supply their own path and label.
"""

from __future__ import annotations

import json
from pathlib import Path

from emby_dedupe.utils.logging import logger


def load_json_cache(path: Path, *, label: str = "cache") -> dict:
    """Load a JSON dict from ``path``. Returns ``{}`` if missing or corrupt.

    Args:
        path: Cache file location.
        label: Noun used in warning logs, e.g. "genre cache".

    Returns:
        The parsed dict, or an empty dict on any read/parse error.
    """
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load {label}: {e}")
    return {}


def save_json_cache(path: Path, data: dict, *, label: str = "cache") -> None:
    """Write ``data`` to ``path`` atomically via a sibling ``.tmp`` file.

    A disk error is logged and swallowed — caching is best-effort and must never
    crash the caller.

    Args:
        path: Cache file location.
        data: Dict to serialise.
        label: Noun used in warning logs, e.g. "description cache".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"Could not save {label}: {e}")
