"""
Decide which movies and TV series are Czech/Slovak, for the ``genres origin`` command.

Signals, in order of trust:

1. TMDb ``original_language`` ``cs``/``sk``. A non-English film co-produced by
   Czechia, Slovakia or Czechoslovakia also counts (European co-productions such
   as *A Royal Affair*). An English-language one does not: TMDb lists Czechia for
   Hollywood films merely shot in Prague (*The Bourne Identity*, *Van Helsing*).
2. ČSFD origin, for titles TMDb doesn't know: the FIRST country ČSFD lists.
   (TMDb sorts countries alphabetically, so "first" means nothing there.)
3. Emby's own ``ProductionLocations``, when every one of them is CZ/SK.

A film whose Emby metadata names only other countries while TMDb says ``cs`` is
refused: Emby holds the wrong TMDb id (seen live: *The Color of Magic*, a UK film,
filed under a Czech TMDb entry).
"""

import httpx

from emby_dedupe.api.genre_providers import TMDB_BASE, RateLimiter
from emby_dedupe.utils.logging import logger

_LANGUAGES = frozenset({"cs", "sk"})
_TMDB_COUNTRIES = frozenset({"CZ", "SK", "XC"})  # XC = Czechoslovakia on TMDb
_EMBY_COUNTRIES = frozenset({"Czech Republic", "Czechia", "Slovakia", "Czechoslovakia"})
_CSFD_COUNTRIES = frozenset({"Česko", "Slovensko", "Československo"})

# Separate prefix from fetch_tmdb_genres' "tmdb_<id>_<type>" keys, whose values are lists.
# The media type is part of the key: TMDb movie and TV ids overlap.
_CACHE_PREFIX = "origin_tmdb_"


def fetch_tmdb_origin(
    client: httpx.Client, limiter: RateLimiter, tmdb_id: str, cache: dict, media_type: str = "movie"
) -> dict | None:
    """Original language and production countries of a TMDb movie or TV series.

    Args:
        client: httpx client with the TMDb bearer header set.
        limiter: Rate limiter shared with the other TMDb calls.
        tmdb_id: TMDb id.
        cache: Genre cache dict, mutated in place. Network errors are not cached.
        media_type: "movie" or "tv".

    Returns:
        ``{"lang": str, "countries": [iso, ...]}``; ``{"missing": True}`` when TMDb
        has no such title; None when the request failed.
    """
    key = f"{_CACHE_PREFIX}{media_type}_{tmdb_id}"
    if key in cache:
        return cache[key]
    limiter.acquire()
    try:
        response = client.get(f"{TMDB_BASE}/{media_type}/{tmdb_id}")
        if response.status_code == 404:
            cache[key] = {"missing": True}
            return cache[key]
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"TMDB origin lookup failed for {media_type}/{tmdb_id}: {e}")
        return None
    countries = [c.get("iso_3166_1", "") for c in data.get("production_countries") or []]
    countries += [c for c in data.get("origin_country") or [] if c not in countries]
    cache[key] = {"lang": data.get("original_language") or "", "countries": countries}
    return cache[key]


def csfd_countries_by_id(csfd_cache: dict) -> dict[str, list[str]]:
    """Index the ČSFD page cache's film entries by ČSFD id → origin countries."""
    index: dict[str, list[str]] = {}
    for key, film in csfd_cache.items():
        if key.startswith("film:") and isinstance(film, dict) and film.get("csfd_id"):
            # pages cached before the series-origin parser fix hold "Česko (" for a series
            countries = (str(c).split("(")[0].strip() for c in film.get("countries") or [])
            index[str(film["csfd_id"])] = [c for c in countries if c]
    return index


def _classify_tmdb(locations: set[str], tmdb: dict) -> tuple[bool, str]:
    lang = tmdb.get("lang") or ""
    if locations and not locations & _EMBY_COUNTRIES:
        return False, f"Emby lists only {', '.join(sorted(locations))}"
    if lang in _LANGUAGES:
        return True, f"TMDb original language {lang}"
    if set(tmdb.get("countries") or []) & _TMDB_COUNTRIES and lang != "en":
        return True, f"CZ/SK co-production in {lang or 'no language'}"
    return False, f"TMDb original language {lang or 'unknown'}"


def classify_origin(
    item: dict, tmdb: dict | None, csfd_countries: list[str] | None
) -> tuple[bool, str]:
    """Is this Emby movie or series Czech/Slovak?

    Args:
        item: Emby item with ``ProductionLocations``.
        tmdb: Result of :func:`fetch_tmdb_origin`, or None when there is no TMDb data.
        csfd_countries: ČSFD origin countries in ČSFD's order, or None when unknown.

    Returns:
        (verdict, reason) — the reason is shown in dry runs.
    """
    locations = set(item.get("ProductionLocations") or [])
    if tmdb and not tmdb.get("missing"):
        return _classify_tmdb(locations, tmdb)
    if csfd_countries:
        origin = " / ".join(csfd_countries)
        return csfd_countries[0] in _CSFD_COUNTRIES, f"ČSFD origin {origin}"
    if locations and locations <= _EMBY_COUNTRIES:
        return True, f"Emby origin {', '.join(sorted(locations))}"
    return False, "no origin data"
