"""
External genre providers: TMDB and OMDb API clients with rate limiting and caching.
Used by `genres fix` to fill missing genres and cross-validate existing ones.
"""

import json
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from emby_dedupe.api.genres import normalize_genre_name
from emby_dedupe.utils.constants import GENRE_NORMALIZATION_MAP
from emby_dedupe.utils.logging import logger

TMDB_BASE = "https://api.themoviedb.org/3"
OMDB_BASE = "https://www.omdbapi.com"
CACHE_PATH = Path.home() / ".cache" / "emby-dedupe" / "genre-cache.json"

# TMDB uses compound genre names for TV that don't exist in the movie genre taxonomy.
# Expand them into their constituent canonical genres.
_TMDB_TV_GENRE_EXPANSIONS: dict[str, list[str]] = {
    "Action & Adventure": ["Action", "Adventure"],
    "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
    "War & Politics": ["War"],
}

# Genre values that should never be stored — media type labels or missing-data sentinels.
_GENRES_TO_SKIP: frozenset[str] = frozenset({"TV Movie", "TV Film", "N/A", ""})


class RateLimiter:
    """Throttles requests to stay within API rate limits."""

    def __init__(self, calls_per_second: float) -> None:
        self._interval = 1.0 / calls_per_second
        self._last: float = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            self._last = now + max(wait, 0)
        if wait > 0:
            time.sleep(wait)


def load_genre_cache() -> dict:
    """Load genre cache from disk. Returns {} on missing or corrupt file."""
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load genre cache: {e}")
    return {}


def save_genre_cache(cache: dict) -> None:
    """Save genre cache to disk atomically."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except OSError as e:
        logger.warning(f"Could not save genre cache: {e}")


def fetch_tmdb_genres(
    client: httpx.Client,
    limiter: RateLimiter,
    tmdb_id: str,
    media_type: str = "movie",
    cache: Optional[dict] = None,
) -> list[str]:
    """Fetch and normalize genres for a TMDB item.

    Args:
        client: httpx client with Authorization header set.
        limiter: RateLimiter instance for TMDB API.
        tmdb_id: TMDB item ID.
        media_type: "movie" or "tv".
        cache: Optional shared cache dict (mutated in place).

    Returns:
        Normalized, deduplicated list of genre names. Empty on failure or 404.
    """
    cache_key = f"tmdb_{tmdb_id}_{media_type}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    limiter.acquire()
    try:
        response = client.get(f"{TMDB_BASE}/{media_type}/{tmdb_id}")
        if response.status_code == 404:
            if cache is not None:
                cache[cache_key] = []
            return []
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.warning(f"TMDB request failed for {media_type}/{tmdb_id}: {e}")
        return []

    raw_genres = data.get("genres", [])
    expanded: list[str] = []
    for g in raw_genres:
        name = g["name"]
        if name in _TMDB_TV_GENRE_EXPANSIONS:
            expanded.extend(_TMDB_TV_GENRE_EXPANSIONS[name])
        elif name not in _GENRES_TO_SKIP:
            expanded.append(normalize_genre_name(name, GENRE_NORMALIZATION_MAP))
    deduped = list(dict.fromkeys(expanded))

    if cache is not None:
        cache[cache_key] = deduped
    return deduped


def fetch_omdb_genres(
    client: httpx.Client,
    limiter: RateLimiter,
    imdb_id: str,
    api_keys: list[str],
    cache: Optional[dict] = None,
) -> list[str]:
    """Fetch and normalize genres for an item from OMDb, trying each API key in order.

    Args:
        client: httpx client for OMDb requests.
        limiter: RateLimiter instance for OMDb API.
        imdb_id: IMDb ID (e.g. "tt0111161").
        api_keys: List of OMDb API keys to try in order.
        cache: Optional shared cache dict (mutated in place).

    Returns:
        Normalized, deduplicated list of genre names. Empty if all keys exhausted or on failure.
    """
    cache_key = f"imdb_{imdb_id}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    for key in api_keys:
        limiter.acquire()
        try:
            response = client.get(f"{OMDB_BASE}/", params={"i": imdb_id, "apikey": key})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"OMDb request failed for {imdb_id}: {e}")
            return []

        # OMDb rate limit returns HTTP 200 with a JSON error body
        if data.get("Response") == "False":
            error_msg = data.get("Error", "")
            if "limit" in error_msg.lower():
                logger.warning(f"OMDb key {key[:4]}... exhausted, trying next")
                continue
            # Other error (e.g. invalid ID)
            if cache is not None:
                cache[cache_key] = []
            return []

        # Success: parse comma-separated genre string
        genre_str = data.get("Genre", "")
        raw = [g.strip() for g in genre_str.split(",") if g.strip()]
        normalized = [
            normalize_genre_name(g, GENRE_NORMALIZATION_MAP)
            for g in raw
            if g not in _GENRES_TO_SKIP
        ]
        deduped = list(dict.fromkeys(normalized))

        if cache is not None:
            cache[cache_key] = deduped
        return deduped

    logger.warning(f"All OMDb API keys exhausted for {imdb_id}")
    return []


def fetch_genres_for_item(
    item: dict,
    tmdb_client: Optional[httpx.Client],
    tmdb_limiter: Optional[RateLimiter],
    omdb_client: Optional[httpx.Client],
    omdb_limiter: Optional[RateLimiter],
    omdb_keys: list[str],
    cache: dict,
) -> list[str]:
    """Fetch genres for an Emby item from TMDB (primary) or OMDb (fallback).

    Args:
        item: Emby item dict with at least "ProviderIds" and "Type" keys.
        tmdb_client: httpx client configured for TMDB. None to skip TMDB.
        tmdb_limiter: RateLimiter for TMDB. None to skip TMDB.
        omdb_client: httpx client for OMDb. None to skip OMDb.
        omdb_limiter: RateLimiter for OMDb. None to skip OMDb.
        omdb_keys: List of OMDb API keys.
        cache: Shared cache dict (mutated in place).

    Returns:
        Normalized genre list from TMDB or OMDb. Empty if neither provider returns results.
    """
    provider_ids = item.get("ProviderIds") or {}
    tmdb_id = provider_ids.get("Tmdb")
    imdb_id = provider_ids.get("Imdb")
    media_type = "tv" if item.get("Type") == "Series" else "movie"

    # Try TMDB first
    if tmdb_client is not None and tmdb_limiter is not None and tmdb_id:
        genres = fetch_tmdb_genres(tmdb_client, tmdb_limiter, tmdb_id, media_type, cache)
        if genres:
            return genres

    # OMDb fallback
    if omdb_client is not None and omdb_limiter is not None and imdb_id:
        return fetch_omdb_genres(omdb_client, omdb_limiter, imdb_id, omdb_keys, cache)

    return []


def compare_genres(emby_genres: list[str], external_genres: list[str]) -> dict:
    """Compare Emby genres vs external (TMDB/OMDb) genres after normalization.

    Args:
        emby_genres: Genres currently set on the Emby item.
        external_genres: Genres returned by TMDB or OMDb.

    Returns:
        Dict with keys:
            missing_from_emby: genres in external but not in Emby (sorted)
            extra_in_emby: genres in Emby but not in external (sorted)
            merged: union of both sets (sorted)
            has_diff: True only when external has genres Emby is missing (additive only)
    """
    emby_norm = {normalize_genre_name(g, GENRE_NORMALIZATION_MAP) for g in emby_genres}
    ext_norm = {normalize_genre_name(g, GENRE_NORMALIZATION_MAP) for g in external_genres}
    missing = sorted(ext_norm - emby_norm)
    extra = sorted(emby_norm - ext_norm)
    merged = sorted(emby_norm | ext_norm)
    return {
        "missing_from_emby": missing,
        "extra_in_emby": extra,
        "merged": merged,
        "has_diff": bool(missing),  # only flag if TMDB has genres Emby is missing
    }
