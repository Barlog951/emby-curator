"""
``genres origin``: give Czech/Slovak movies and TV series one extra genre (default "Česko-slovenský").

Additive only. The English genres stay, and the genre is never removed, so a
genre added by hand survives every run. Titles that already have the genre are
re-checked, and the ones the rules no longer back are listed for a human to look at
(an item Emby re-identified, or a hand-added genre). The decision rules live in
:mod:`emby_dedupe.api.genre_origin`.
"""

import argparse
import sys

import httpx
from tqdm import tqdm

from emby_dedupe.api.csfd import CsfdClient, CsfdError, film_url, load_csfd_cache, save_csfd_cache
from emby_dedupe.api.genre_origin import classify_origin, csfd_countries_by_id, fetch_tmdb_origin
from emby_dedupe.api.genre_providers import RateLimiter, load_genre_cache, save_genre_cache
from emby_dedupe.api.genres import (
    fetch_full_item,
    fetch_items_by_ids,
    fetch_items_with_genres,
    update_item_genres,
)
from emby_dedupe.cli.arguments import get_env_variable
from emby_dedupe.utils.constants import ENV_DEDUPE_TMDB_API_KEY
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.logging import logger

_ORIGIN_FIELDS = "Genres,GenreItems,ProviderIds,LockedFields,ProductionLocations,ProductionYear"
_ORIGIN_TYPES = ("Movie", "Series")  # genres live on the Series, never on episodes
_CSFD_MAX_FAILURES = 3  # consecutive ČSFD failures before giving up for this run (FlareSolverr down)


class _OriginSources:
    """TMDb + ČSFD lookups for one run, with their caches."""

    def __init__(self, tmdb_key: str, flaresolverr_url: str) -> None:
        self.genre_cache = load_genre_cache()
        self.csfd_cache = load_csfd_cache()
        self.csfd_index = csfd_countries_by_id(self.csfd_cache)
        self.csfd_fetched = False
        self._csfd_failures = 0
        self._tmdb = httpx.Client(headers={"Authorization": f"Bearer {tmdb_key}"}, timeout=20)
        self._limiter = RateLimiter(35.0)
        self._csfd = CsfdClient(httpx.Client(), flaresolverr_url, cache=self.csfd_cache)

    def tmdb(self, item: dict) -> dict | None:
        tmdb_id = (item.get("ProviderIds") or {}).get("Tmdb")
        if not tmdb_id:
            return None
        media_type = "tv" if item.get("Type") == "Series" else "movie"
        return fetch_tmdb_origin(self._tmdb, self._limiter, tmdb_id, self.genre_cache, media_type)

    def classify(self, item: dict) -> tuple[bool, str]:
        tmdb = self.tmdb(item)
        csfd = None if tmdb and not tmdb.get("missing") else self.csfd(item)
        return classify_origin(item, tmdb, csfd)

    def csfd(self, item: dict) -> list[str] | None:
        csfd_id = (item.get("ProviderIds") or {}).get("Csfd")
        if not csfd_id:
            return None
        if csfd_id not in self.csfd_index and self._csfd_failures < _CSFD_MAX_FAILURES:
            try:
                self.csfd_index[csfd_id] = self._csfd.film(film_url(csfd_id)).countries
                self.csfd_fetched = True
                self._csfd_failures = 0
            except CsfdError as e:
                self._csfd_failures += 1
                logger.warning(f"ČSFD lookup failed for {item.get('Name')} ({csfd_id}): {e}")
        return self.csfd_index.get(csfd_id)

    def save(self) -> None:
        save_genre_cache(self.genre_cache)
        if self.csfd_fetched:
            save_csfd_cache(self.csfd_cache)


def _fetch_titles(
    client: httpx.Client, base_url: str, user_id: str, library_ids: list[str], item_ids: list[str] | None
) -> list[dict]:
    if item_ids:
        items = fetch_items_by_ids(client, base_url, user_id, item_ids, fields=_ORIGIN_FIELDS)
    else:
        items = fetch_items_with_genres(
            client, base_url, library_ids, item_types=",".join(_ORIGIN_TYPES), extra_fields="ProductionLocations"
        )
    # --all-libraries also walks the collections/playlists libraries, which repeat titles
    return list({i["Id"]: i for i in items if i.get("Type") in _ORIGIN_TYPES}.values())


def _label(item: dict, reason: str) -> str:
    kind = "series" if item.get("Type") == "Series" else "movie"
    return f"{item.get('Name')} ({item.get('ProductionYear') or '?'}, {kind}) [{reason}]"


def _add_genre(client: httpx.Client, base_url: str, user_id: str, item: dict, genre: str, lock: bool) -> str:
    """Add ``genre`` to the item's CURRENT genres (re-read in full, so nothing else is lost).

    Returns "added", "unchanged" (already there) or "errors" (Emby refused the update).
    """
    full_item = fetch_full_item(client, base_url, user_id, item["Id"])
    genres = list(full_item.get("Genres") or [])
    if genre in genres:
        return "unchanged"
    if update_item_genres(client, base_url, item["Id"], full_item, genres + [genre], lock=lock):
        return "added"
    return "errors"


def _tag_one(
    client: httpx.Client, base_url: str, user_id: str, movie: dict, sources: _OriginSources, args: argparse.Namespace
) -> str:
    """Classify one title and (with --doit) add the genre. Returns the counter to bump."""
    yes, reason = sources.classify(movie)
    if not yes:
        return "not_cz_sk"
    label = _label(movie, reason)
    if not args.doit:
        print(f"  would add: {label}")
        return "added"
    try:
        status = _add_genre(client, base_url, user_id, movie, args.genre_name, args.lock)
    except EmbyServerConnectionError as e:
        logger.error(f"Failed to tag {movie.get('Name')}: {e}")
        return "errors"
    if status == "added":
        print(f"  added: {label}")
    return status


def _recheck_tagged(tagged: list[dict], sources: _OriginSources) -> list[str]:
    """Labels of titles that have the genre although the rules say they aren't Czech/Slovak."""
    unbacked = []
    for title in tagged:
        yes, reason = sources.classify(title)
        if not yes:
            unbacked.append(_label(title, reason))
    return unbacked


def run_origin(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    args: argparse.Namespace,
    item_ids: list[str] | None = None,
) -> None:
    """Find Czech/Slovak movies and series, add the origin genre (dry run unless ``args.doit``),
    and list tagged titles the rules no longer back (never removing the genre).

    Args:
        client: Emby HTTP client.
        base_url: Emby server base URL with port.
        user_id: Emby user id for full-item fetches.
        library_ids: Libraries to scan (ignored with ``item_ids``).
        args: Uses ``doit``, ``lock``, ``genre_name``, ``tmdb_api_key``, ``flaresolverr_url``.
        item_ids: Optional explicit item ids.
    """
    tmdb_key = getattr(args, "tmdb_api_key", None) or get_env_variable(ENV_DEDUPE_TMDB_API_KEY)
    if not tmdb_key:
        logger.error("genres origin needs a TMDb key: set DEDUPE_TMDB_API_KEY or pass --tmdb-api-key.")
        sys.exit(1)
    genre = args.genre_name
    titles = _fetch_titles(client, base_url, user_id, library_ids, item_ids)
    tagged = [t for t in titles if genre in (t.get("Genres") or [])]
    todo = [t for t in titles if genre not in (t.get("Genres") or [])]
    print(f"Genre '{genre}': {len(tagged)} of {len(titles)} movies/series already have it")

    sources = _OriginSources(tmdb_key, args.flaresolverr_url)
    counts = {"added": 0, "not_cz_sk": 0, "errors": 0, "unchanged": 0}
    unbacked: list[str] = []
    try:
        for title in tqdm(todo, desc="Checking origin", unit="title"):
            counts[_tag_one(client, base_url, user_id, title, sources, args)] += 1
        unbacked = _recheck_tagged(tagged, sources)
    finally:
        sources.save()

    verb = "added" if args.doit else "would add"
    print(f"\nOrigin complete: {verb} {counts['added']}, not CZ/SK {counts['not_cz_sk']}, errors {counts['errors']}")
    if unbacked:
        print(f"{len(unbacked)} title(s) have '{genre}' but the rules don't back it (left as is; remove by hand if wrong):")
        for line in unbacked:
            print(f"  check: {line}")
    if not args.doit and counts["added"]:
        print("Run with --doit to apply changes")
