"""
Persistent cache for TMDB description lookups.

Mirrors the design of ``emby_dedupe/api/genre_providers.py`` cache:
- One JSON file on disk, atomic writes via ``.tmp`` + rename
- Keys identify the TMDB resource being queried (movie/tv/collection/episode)
- Negative results (empty data) are cached too — so re-runs skip items where
  TMDB has no Slavic translation, instead of re-querying every time
- Entries carry an ISO timestamp; ``is_fresh`` checks against a TTL

The cache lets us turn a multi-hour full sweep into a multi-minute incremental
re-run: only items missing from the cache or older than the TTL trigger
network calls.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from emby_dedupe.utils.json_cache import load_json_cache, save_json_cache

CACHE_PATH = Path.home() / ".cache" / "emby-dedupe" / "description-cache.json"

# Default freshness: 30 days.  TMDB community translations get added over time,
# so periodically re-checking items that previously had no SK/CZ data is worth
# the occasional API call.
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60


def build_movie_key(tmdb_id: str) -> str:
    """Cache key for a TMDB movie lookup."""
    return f"movie:{tmdb_id}"


def build_tv_key(tmdb_id: str) -> str:
    """Cache key for a TMDB tv series lookup."""
    return f"tv:{tmdb_id}"


def build_collection_key(tmdb_id: str) -> str:
    """Cache key for a TMDB collection/boxset lookup."""
    return f"collection:{tmdb_id}"


def build_episode_key(series_tmdb_id: str, season: int, episode: int) -> str:
    """Cache key for a TMDB episode lookup."""
    return f"ep:{series_tmdb_id}:s{season}e{episode}"


def load_cache() -> dict:
    """Load the cache from disk.  Returns {} on missing or corrupt file."""
    return load_json_cache(CACHE_PATH, label="description cache")


def save_cache(cache: dict) -> None:
    """Write the cache to disk atomically via a sibling ``.tmp`` file."""
    save_json_cache(CACHE_PATH, cache, label="description cache")


def is_fresh(entry: Optional[dict], ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    """Return True when entry exists and was written within ``ttl_seconds``."""
    if not entry:
        return False
    ts = entry.get("_ts")
    if not isinstance(ts, (int, float)):
        return False
    return (time.time() - ts) < ttl_seconds


def make_entry(localized: Optional[dict]) -> dict:
    """Wrap a TMDB localized dict (or None) with a timestamp for storage.

    Storing ``None`` results as ``{"_ts": ..., "data": None}`` is important: it
    lets us positively remember "TMDB returned 404 / no data" rather than
    treating every re-run as a cache miss.
    """
    return {"_ts": int(time.time()), "data": localized}


def read_entry(entry: Optional[dict]) -> Optional[dict]:
    """Unwrap a stored entry's ``data`` field.  Returns None if entry absent."""
    if not entry:
        return None
    return entry.get("data")
