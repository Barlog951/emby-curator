"""CLI for description (Overview) fill from TMDB with language fallback chain."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import httpx
from tqdm import tqdm

from emby_dedupe.api.client import check_emby_connection, handle_host_and_port
from emby_dedupe.api.description_cache import (
    DEFAULT_TTL_SECONDS,
    load_cache,
    save_cache,
)
from emby_dedupe.api.descriptions import (
    LANG_CHAIN_DEFAULT,
    build_series_tmdb_map,
    collect_overview_candidates,
    fetch_tmdb_episode_localized,
    fetch_tmdb_localized,
    pick_overview_with_fallback,
    pick_tagline_with_fallback,
    pick_title_from_localized,
    update_item_metadata,
)
from emby_dedupe.api.genre_providers import RateLimiter
from emby_dedupe.api.genres import (
    fetch_full_item,
    fetch_items_by_ids,
    fetch_items_with_genres,
    get_user_id,
)
from emby_dedupe.cli.arguments import get_env_variable
from emby_dedupe.cli.genres import _resolve_library_ids
from emby_dedupe.utils.constants import ENV_DEDUPE_TMDB_API_KEY
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.logging import logger, set_logging_level


def _parse_lang_chain(raw: Optional[str]) -> tuple[str, ...]:
    """Parse --overview-langs flag: comma-separated BCP47 codes."""
    if not raw:
        return LANG_CHAIN_DEFAULT
    chain = tuple(p.strip() for p in raw.split(",") if p.strip())
    return chain or LANG_CHAIN_DEFAULT


def _preview_change(
    item: dict,
    new_overview: Optional[str],
    overview_lang: Optional[str],
    new_title: Optional[str],
    new_tagline: Optional[str] = None,
    tagline_lang: Optional[str] = None,
) -> None:
    """Print a side-by-side dry-run preview for one item."""
    name = item.get("Name", item.get("Id", "?"))
    year = item.get("ProductionYear") or ""
    header = f"{name} ({year})" if year else name
    tag = overview_lang or "title/tagline-only"
    print(f"\n━━━ {header}  [{tag}] ━━━")
    if new_title is not None:
        print(f"  [Title, current ]: {name}")
        print(f"  [Title, new (EN)]: {new_title}")
    if new_tagline is not None and tagline_lang is not None:
        cur_tag = (item.get("Taglines") or [""])[0]
        print(f"  [Tagline, current  ]: {cur_tag}")
        print(f"  [Tagline, new ({tagline_lang})]: {new_tagline}")
    if new_overview is not None and overview_lang is not None:
        cur = (item.get("Overview") or "").strip()
        print(f"  [Overview, current     ]: {cur[:240]}{'…' if len(cur) > 240 else ''}")
        print(
            f"  [Overview, new ({overview_lang})]: "
            f"{new_overview[:240]}{'…' if len(new_overview) > 240 else ''}"
        )


# Fields needed when fetching by item IDs.  The default batch field-set is
# genre-only; --item-ids mode for descriptions needs Overview/Taglines/Name etc.
_DESC_FETCH_FIELDS = (
    "Genres,GenreItems,ProviderIds,LockedFields,Overview,Taglines,"
    "Name,ProductionYear,Type,SeriesId,ParentIndexNumber,IndexNumber"
)


# Counters used inside _run_fill — kept as a tiny dataclass-ish dict so the
# per-item helpers can mutate a shared accumulator instead of taking 6 args.
def _new_run_stats() -> dict:
    return {
        "found_overview": 0, "found_tagline": 0, "found_title": 0,
        "updated": 0, "skipped_no_data": 0, "errors": 0, "cache_hits": 0,
    }


def _fetch_input_items(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    item_ids_raw: Optional[str],
) -> list[dict]:
    """Resolve the candidate input set: either explicit item IDs or a full sweep."""
    if item_ids_raw:
        item_ids = [i.strip() for i in item_ids_raw.split(",") if i.strip()]
        return fetch_items_by_ids(
            client, base_url, user_id, item_ids, fields=_DESC_FETCH_FIELDS,
        )
    logger.info("Fetching items from Emby...")
    # IMPORTANT: do NOT pass user_id here.  The user-scoped /Users/{uid}/Items
    # endpoint returns LockedFields=None even when requested, which would
    # cause re-processing of items we've already locked.
    # IncludeItemTypes also pulls Episodes so per-episode overviews can be
    # localized via the parent series's TMDB ID.
    return fetch_items_with_genres(
        client, base_url, library_ids, item_types="Movie,Series,Episode"
    )


def _resolve_episode_series_tmdb(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    series_id: Optional[str],
    series_tmdb_map: dict,
) -> Optional[str]:
    """Return the parent series's TMDB ID, fetching it on miss (--item-ids mode)."""
    if not series_id:
        return None
    series_tmdb_id = series_tmdb_map.get(series_id)
    if series_tmdb_id:
        return series_tmdb_id
    # Fallback: parent wasn't in the fetched batch — fetch it once and cache.
    try:
        parent = fetch_full_item(client, base_url, user_id, series_id)
    except Exception:  # noqa: BLE001
        return None
    series_tmdb_id = (parent.get("ProviderIds") or {}).get("Tmdb")
    if series_tmdb_id:
        series_tmdb_map[series_id] = series_tmdb_id
    return series_tmdb_id


_MEDIA_TYPE_BY_ITEM_TYPE = {"Series": "tv", "BoxSet": "collection"}


def _fetch_localized_for_episode(
    item: dict,
    series_tmdb_id: str,
    tmdb_client: httpx.Client,
    tmdb_limiter: RateLimiter,
    cache: Optional[dict],
    cache_ttl_seconds: int,
    stats: dict,
) -> Optional[dict]:
    """Fetch localized text for an episode and track cache hits."""
    season = item["ParentIndexNumber"]
    episode = item["IndexNumber"]
    pre_hit = cache is not None and f"ep:{series_tmdb_id}:s{season}e{episode}" in cache
    localized = fetch_tmdb_episode_localized(
        tmdb_client, tmdb_limiter, series_tmdb_id, season, episode,
        cache=cache, cache_ttl=cache_ttl_seconds,
    )
    if pre_hit:
        stats["cache_hits"] += 1
    return localized


def _fetch_localized_for_movie_or_series(
    item: dict,
    tmdb_client: httpx.Client,
    tmdb_limiter: RateLimiter,
    cache: Optional[dict],
    cache_ttl_seconds: int,
    stats: dict,
) -> Optional[dict]:
    """Fetch localized text for a movie/series/collection and track cache hits."""
    tmdb_id = item["ProviderIds"]["Tmdb"]
    media = _MEDIA_TYPE_BY_ITEM_TYPE.get(item.get("Type"), "movie")
    pre_hit = cache is not None and f"{media}:{tmdb_id}" in cache
    localized = fetch_tmdb_localized(
        tmdb_client, tmdb_limiter, tmdb_id, media,
        cache=cache, cache_ttl=cache_ttl_seconds,
    )
    if pre_hit:
        stats["cache_hits"] += 1
    return localized


def _fetch_localized_for_item(
    item: dict,
    client: httpx.Client,
    base_url: str,
    user_id: str,
    tmdb_client: httpx.Client,
    tmdb_limiter: RateLimiter,
    series_tmdb_map: dict,
    cache: Optional[dict],
    cache_ttl_seconds: int,
    stats: dict,
) -> Optional[dict]:
    """Return localized TMDB data for an item (episode or movie/series).

    Returns None when no TMDB ID is resolvable; the caller treats that as
    ``skipped_no_data`` rather than an error.
    """
    if item.get("Type") == "Episode":
        series_tmdb_id = _resolve_episode_series_tmdb(
            client, base_url, user_id, item.get("SeriesId"), series_tmdb_map,
        )
        if not series_tmdb_id:
            return None
        return _fetch_localized_for_episode(
            item, series_tmdb_id, tmdb_client, tmdb_limiter,
            cache, cache_ttl_seconds, stats,
        )
    return _fetch_localized_for_movie_or_series(
        item, tmdb_client, tmdb_limiter, cache, cache_ttl_seconds, stats,
    )


def _pick_updates(
    item: dict,
    localized: dict,
    lang_chain: tuple[str, ...],
    update_title: bool,
) -> tuple[Optional[tuple[str, str]], Optional[tuple[str, str]], Optional[tuple[str, str]]]:
    """Pick (overview, tagline, title) candidates for an item — any may be None."""
    cur_ov = (item.get("Overview") or "").strip()
    cur_tags = item.get("Taglines") or []
    cur_tag = (cur_tags[0] if cur_tags else "").strip()
    overview_pick = pick_overview_with_fallback(localized, cur_ov, lang_chain)
    tagline_pick = pick_tagline_with_fallback(localized, cur_tag, lang_chain)
    title_pick = (
        pick_title_from_localized(item.get("Name") or "", localized)
        if update_title
        else None
    )
    return overview_pick, tagline_pick, title_pick


def _apply_or_preview(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    item: dict,
    new_overview: Optional[str],
    overview_lang: Optional[str],
    new_tagline: Optional[str],
    tagline_lang: Optional[str],
    new_title: Optional[str],
    args: argparse.Namespace,
    stats: dict,
) -> None:
    """Either print a dry-run preview or POST the metadata update."""
    if not args.doit:
        _preview_change(
            item, new_overview, overview_lang, new_title,
            new_tagline=new_tagline, tagline_lang=tagline_lang,
        )
        return
    try:
        full_item = fetch_full_item(client, base_url, user_id, item["Id"])
        if update_item_metadata(
            client, base_url, item["Id"], full_item,
            new_overview=new_overview, new_title=new_title,
            new_tagline=new_tagline, lock=args.lock,
        ):
            stats["updated"] += 1
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to update {item.get('Name','?')}: {e}")
        stats["errors"] += 1


def _process_item(
    item: dict,
    client: httpx.Client,
    base_url: str,
    user_id: str,
    tmdb_client: httpx.Client,
    tmdb_limiter: RateLimiter,
    series_tmdb_map: dict,
    cache: Optional[dict],
    cache_ttl_seconds: int,
    lang_chain: tuple[str, ...],
    update_title: bool,
    args: argparse.Namespace,
    stats: dict,
) -> None:
    """Process a single candidate item end-to-end (fetch + pick + apply)."""
    try:
        localized = _fetch_localized_for_item(
            item, client, base_url, user_id,
            tmdb_client, tmdb_limiter, series_tmdb_map,
            cache, cache_ttl_seconds, stats,
        )
    except Exception as e:  # noqa: BLE001 - external API surface
        logger.error(f"TMDB fetch failed for {item.get('Name','?')}: {e}")
        stats["errors"] += 1
        return

    if localized is None:
        stats["skipped_no_data"] += 1
        return

    overview_pick, tagline_pick, title_pick = _pick_updates(
        item, localized, lang_chain, update_title,
    )
    if overview_pick is None and tagline_pick is None and title_pick is None:
        stats["skipped_no_data"] += 1
        return

    new_overview, overview_lang = overview_pick or (None, None)
    new_tagline, tagline_lang = tagline_pick or (None, None)
    new_title = title_pick[0] if title_pick else None

    if new_overview is not None:
        stats["found_overview"] += 1
    if new_tagline is not None:
        stats["found_tagline"] += 1
    if new_title is not None:
        stats["found_title"] += 1

    _apply_or_preview(
        client, base_url, user_id, item,
        new_overview, overview_lang, new_tagline, tagline_lang, new_title,
        args, stats,
    )


def _print_fill_summary(stats: dict, doit: bool) -> None:
    print()
    print(
        f"Fill summary: {stats['found_overview']} overview translations, "
        f"{stats['found_tagline']} tagline translations, "
        f"{stats['found_title']} title replacements proposed, "
        f"{stats['updated']} items updated, "
        f"{stats['skipped_no_data']} skipped (no TMDB data), "
        f"{stats['errors']} errors"
    )
    if not doit and (stats["found_overview"] or stats["found_tagline"] or stats["found_title"]):
        print("Dry-run — re-run with --doit to apply.")


def _resolve_tmdb_key(args: argparse.Namespace) -> str:
    """Return the TMDB API key from args or env, or exit with an error."""
    tmdb_key = getattr(args, "tmdb_api_key", None) or get_env_variable(ENV_DEDUPE_TMDB_API_KEY)
    if not tmdb_key:
        logger.error("TMDB key required. Pass --tmdb-api-key or set DEDUPE_TMDB_API_KEY.")
        sys.exit(1)
    return tmdb_key


def _resolve_cache(args: argparse.Namespace) -> tuple[Optional[dict], int]:
    """Load the on-disk cache (or None when --no-cache) and resolve TTL seconds."""
    use_cache = not bool(getattr(args, "no_cache", False))
    cache_ttl_days = getattr(args, "cache_ttl_days", None)
    cache_ttl_seconds = (
        cache_ttl_days * 86400 if cache_ttl_days is not None else DEFAULT_TTL_SECONDS
    )
    cache: Optional[dict] = load_cache() if use_cache else None
    if cache is not None:
        print(f"Cache loaded: {len(cache)} entries (TTL {cache_ttl_seconds // 86400}d)")
    return cache, cache_ttl_seconds


def _collect_candidates(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    args: argparse.Namespace,
) -> tuple[list[dict], list[dict]]:
    """Fetch all items and reduce to localization candidates (possibly --limit-capped)."""
    items = _fetch_input_items(
        client, base_url, user_id, library_ids,
        getattr(args, "item_ids", None),
    )
    candidates = collect_overview_candidates(items)
    print(
        f"Total items: {len(items)} | candidates (English overview + TMDB ID + unlocked): "
        f"{len(candidates)}"
    )
    limit = getattr(args, "limit", None)
    if limit is not None and limit > 0:
        candidates = candidates[:limit]
        print(f"Capped to first {len(candidates)} item(s) via --limit.")
    return items, candidates


def _run_fill(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    args: argparse.Namespace,
) -> None:
    """Fill English Overviews from TMDB using the language fallback chain."""
    tmdb_key = _resolve_tmdb_key(args)
    lang_chain = _parse_lang_chain(getattr(args, "overview_langs", None))
    items, candidates = _collect_candidates(client, base_url, user_id, library_ids, args)

    if not candidates:
        print("Nothing to do.")
        return

    print(f"Language chain: {' -> '.join(lang_chain)} -> EN (keep)")

    tmdb_client = httpx.Client(headers={"Authorization": f"Bearer {tmdb_key}"})
    tmdb_limiter = RateLimiter(35.0)
    update_title = bool(getattr(args, "update_title", False))
    series_tmdb_map = build_series_tmdb_map(items)
    cache, cache_ttl_seconds = _resolve_cache(args)
    stats = _new_run_stats()

    try:
        for item in tqdm(
            candidates, desc="Fetching from TMDB", unit="item", disable=args.doit is False
        ):
            _process_item(
                item, client, base_url, user_id,
                tmdb_client, tmdb_limiter, series_tmdb_map,
                cache, cache_ttl_seconds, lang_chain, update_title,
                args, stats,
            )
    finally:
        tmdb_client.close()
        if cache is not None:
            save_cache(cache)
            print(f"Cache saved: {len(cache)} entries (this run: {stats['cache_hits']} cache hits)")

    _print_fill_summary(stats, args.doit)


def _validate_args(
    host: Optional[str],
    api_key: Optional[str],
    libraries: list[str],
    all_libraries: bool,
    item_ids: Optional[str],
) -> None:
    missing = []
    if not host:
        missing.append("host (--host / DEDUPE_EMBY_HOST)")
    if not api_key:
        missing.append("api-key (-a / DEDUPE_EMBY_API_KEY)")
    if not libraries and not all_libraries and not item_ids:
        missing.append("library (-l / --all-libraries / --item-ids)")
    if missing:
        logger.error(f"Missing required arguments: {', '.join(missing)}")
        sys.exit(1)


def run_descriptions_command(args: argparse.Namespace) -> None:
    """Entry point for `descriptions fill`."""
    set_logging_level(getattr(args, "verbosity", 0), get_env_variable("DEDUPE_LOGGING"))

    libraries = args.library or []
    all_libraries = getattr(args, "all_libraries", False)
    item_ids = getattr(args, "item_ids", None)
    _validate_args(args.host, args.api_key, libraries, all_libraries, item_ids)

    port = int(args.port) if isinstance(args.port, str) else args.port
    validated_host, validated_port = handle_host_and_port(args.host, port)
    base_url = f"{validated_host}:{validated_port}"

    try:
        client = httpx.Client(headers={"X-Emby-Token": args.api_key})
        if not check_emby_connection(client, f"{base_url}/System/Info"):
            logger.error(f"Unable to connect to Emby at {base_url}.")
            sys.exit(1)

        user_id = get_user_id(client, base_url)
        library_ids = _resolve_library_ids(
            client, base_url, args.api_key, libraries, all_libraries
        )
        _run_fill(client, base_url, user_id, library_ids, args)
    except EmbyServerConnectionError as e:
        logger.error(str(e))
        sys.exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {e}")
        sys.exit(1)
