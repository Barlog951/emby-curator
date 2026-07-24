"""
Command-line interface for genre management functionality.
Provides audit and normalization of Emby library genres.
"""

import argparse
import json
import sys
from dataclasses import dataclass

import httpx
from tqdm import tqdm

from emby_dedupe.api.client import (
    check_emby_connection,
    get_library_id,
    handle_host_and_port,
)
from emby_dedupe.api.genre_providers import RateLimiter, compare_genres, fetch_genres_for_item
from emby_dedupe.api.genres import (
    build_genre_audit,
    fetch_all_genres,
    fetch_full_item,
    fetch_items_by_ids,
    fetch_items_with_genres,
    get_user_id,
    normalize_genre_name,
    suggest_genre_mappings,
    update_item_genres,
)
from emby_dedupe.cli.arguments import get_env_variable, override_warning
from emby_dedupe.utils.constants import (
    ENV_DEDUPE_EMBY_API_KEY,
    ENV_DEDUPE_EMBY_HOST,
    ENV_DEDUPE_EMBY_LIBRARY,
    ENV_DEDUPE_EMBY_PORT,
    ENV_DEDUPE_LOGGING,
    ENV_DEDUPE_OMDB_API_KEY,
    ENV_DEDUPE_TMDB_API_KEY,
    GENRE_NORMALIZATION_MAP,
)
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.logging import logger, set_logging_level


def _validate_genres_args(
    host: str | None,
    api_key: str | None,
    library: list,
    all_libraries: bool,
    item_ids: list[str] | None = None,
) -> None:
    """Validate required arguments for genre commands.

    Genres only need host and API key (no username/password required).

    Args:
        host: Emby server host.
        api_key: Emby API key.
        library: List of library names.
        all_libraries: Whether to scan all libraries.
        item_ids: Explicit list of item IDs to process (skips library requirement).
    """
    missing = []
    if not host:
        missing.append("host (--host or DEDUPE_EMBY_HOST)")
    if not api_key:
        missing.append("api-key (-a or DEDUPE_EMBY_API_KEY)")
    if not library and not all_libraries and not item_ids:
        missing.append("library (-l or --all-libraries or --item-ids)")
    if missing:
        logger.error(f"Missing required arguments: {', '.join(missing)}")
        sys.exit(1)



def _print_genre_counts(genre_counts: dict) -> None:
    """Print genre frequency table with normalization hints."""
    if not genre_counts:
        return
    print("Genre counts (by frequency):")
    for genre, count in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True):
        canonical = normalize_genre_name(genre, GENRE_NORMALIZATION_MAP)
        if canonical != genre:
            print(f"  {genre}: {count} items  [← needs normalization → {canonical}]")
        else:
            print(f"  {genre}: {count} items")
    print()


def _print_normalization_candidates(normalization_candidates: list) -> None:
    """Print a summary of items that need genre normalization."""
    if not normalization_candidates:
        print("No normalization needed — all genres are already canonical.")
        return

    print("Normalization summary:")
    print(f"  {len(normalization_candidates)} items need normalization:")
    variant_item_counts: dict[str, dict[str, int]] = {}
    for candidate in normalization_candidates:
        for orig, suggested in zip(candidate["current_genres"], candidate["suggested_genres"]):
            if orig != suggested:
                inner = variant_item_counts.setdefault(suggested, {})
                inner[orig] = inner.get(orig, 0) + 1
    for canonical, variants in sorted(variant_item_counts.items()):
        for variant, item_count in sorted(variants.items(), key=lambda x: x[1], reverse=True):
            print(f"  {variant} → {canonical}: {item_count} items")


def _print_audit_report(audit: dict, all_genres: list) -> None:
    """Print a formatted genre audit report to the terminal.

    Args:
        audit: Audit dict from build_genre_audit.
        all_genres: Full list of genre objects from fetch_all_genres.
    """
    total_items = audit["total_items"]
    total_without = audit["total_without_genres"]
    pct = (total_without / total_items * 100) if total_items > 0 else 0.0

    print("=== Genre Audit Report ===")
    print(f"Total unique genres: {len(all_genres)}")
    print(f"Total items scanned: {total_items}")
    print(f"Items without genres: {total_without} ({pct:.1f}%)")
    print()

    _print_genre_counts(audit["genre_counts"])
    _print_normalization_candidates(audit.get("normalization_candidates", []))


def _run_audit(
    client: httpx.Client,
    base_url: str,
    library_ids: list[str],
    args: argparse.Namespace,
) -> None:
    """Run the genre audit action.

    Args:
        client: Configured httpx client.
        base_url: Emby server base URL with port.
        library_ids: List of library IDs to scan.
        args: Parsed CLI arguments.
    """
    all_genres = fetch_all_genres(client, base_url)
    logger.info("Fetching items with genres from Emby...")
    items = fetch_items_with_genres(client, base_url, library_ids)
    logger.info(f"Fetched {len(items)} items for audit")
    audit = build_genre_audit(items)
    _print_audit_report(audit, all_genres)

    if getattr(args, "suggest", False):
        suggestions = suggest_genre_mappings(audit["genre_counts"])
        print("\n=== Unknown Genres (not in TMDB canonical list) ===")
        if not suggestions:
            print("All genres are canonical — nothing to add to GENRE_NORMALIZATION_MAP.")
        else:
            print(f"Found {len(suggestions)} unknown genre(s).")
            print("Consider adding entries to GENRE_NORMALIZATION_MAP in constants.py:\n")
            for entry in suggestions:
                hint = (
                    f"  → possible match: {', '.join(entry['suggestions'])}"
                    if entry["suggestions"]
                    else "  → no close match found"
                )
                print(f"  \"{entry['genre']}\": {entry['count']} items{hint}")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            f.write(json.dumps(audit, indent=2, default=str))
        print(f"Audit results saved to: {args.output_json}")


def _run_repair_dupes(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    lock: bool,
) -> None:
    """Scan all items via single-item endpoint and fix duplicate genres.

    The batch fetch endpoint deduplicates Genres/GenreItems in its response,
    hiding real duplicates stored in Emby. This function uses the single-item
    endpoint to see the true stored values and repairs any duplicates found.

    Args:
        client: Configured httpx client.
        base_url: Emby server base URL with port.
        user_id: Emby user ID for single-item fetches.
        library_ids: List of library IDs to scan.
        lock: Whether to lock genres after repair.
    """
    items = fetch_items_with_genres(client, base_url, library_ids, user_id=user_id)
    repaired = 0
    clean = 0
    errors = 0

    for item in tqdm(items, desc="Scanning for duplicate genres", unit="item"):
        try:
            full_item = fetch_full_item(client, base_url, user_id, item["Id"])
            genres = full_item.get("Genres") or []
            deduped = list(dict.fromkeys(genres))
            if deduped == genres:
                clean += 1
                continue
            result = update_item_genres(client, base_url, item["Id"], full_item, deduped, lock=lock)
            if result:
                repaired += 1
                logger.info(
                    f"Repaired duplicates: {full_item.get('Name', item['Id'])}: "
                    f"{genres} → {deduped}"
                )
        except Exception as e:
            errors += 1
            logger.error(f"Failed to repair {item.get('Id', '?')}: {e}")

    print(f"Duplicate repair: {repaired} repaired, {clean} already clean, {errors} errors")


def _collect_normalization_candidates(items: list[dict]) -> list[tuple[dict, list[str]]]:
    """Return (item, new_genres) pairs for items that need normalization."""
    candidates = []
    for item in items:
        # Normalize and deduplicate (e.g. item had both "Suspense" and "Thriller"
        # — normalizing "Suspense"→"Thriller" would create a duplicate without dedup)
        new_genres = list(dict.fromkeys(
            normalize_genre_name(g, GENRE_NORMALIZATION_MAP)
            for g in (item.get("Genres") or [])
        ))
        # Emby deduplicates Genres in API responses but may keep duplicates in GenreItems.
        # Check GenreItems for duplicates too so we can repair items the previous run broke.
        genre_item_names = [gi["Name"] for gi in (item.get("GenreItems") or [])]
        genre_items_has_duplicates = len(genre_item_names) != len(set(genre_item_names))
        if new_genres != (item.get("Genres") or []) or genre_items_has_duplicates:
            candidates.append((item, new_genres))
    return candidates


def _preview_normalization_changes(items_to_update: list[tuple[dict, list[str]]]) -> None:
    """Print a dry-run preview of pending normalization changes."""
    print(f"Would update {len(items_to_update)} items:")
    mapping_counts: dict[tuple[str, str], int] = {}
    for item, _new_genres in items_to_update:
        for orig in item.get("Genres") or []:
            canonical = normalize_genre_name(orig, GENRE_NORMALIZATION_MAP)
            if canonical != orig:
                key = (orig, canonical)
                mapping_counts[key] = mapping_counts.get(key, 0) + 1
    for (variant, canonical), count in sorted(
        mapping_counts.items(), key=lambda x: x[1], reverse=True
    ):
        print(f"  {variant} → {canonical}: {count} items")
    print(f"Run with --doit to apply ({len(items_to_update)} items)")


def _apply_normalization_updates(
    items_to_update: list,
    client: httpx.Client,
    base_url: str,
    user_id: str,
    item_ids: list[str] | None,
    lock: bool,
) -> None:
    """Apply normalization updates for each (item, new_genres) pair and print summary."""
    updated = 0
    skipped = 0
    errors = 0
    for item, new_genres in tqdm(items_to_update, desc="Normalizing genres", unit="item"):
        try:
            # In --item-ids mode, item is already the full item from fetch_full_item.
            # In library scan mode, fetch the full item for safe POST-back.
            full_item = item if item_ids else fetch_full_item(client, base_url, user_id, item["Id"])
            result = update_item_genres(client, base_url, item["Id"], full_item, new_genres, lock=lock)
            if result:
                updated += 1
                logger.info(f"Updated: {item.get('Name', item['Id'])} ({item['Id']})")
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            logger.error(f"Failed to update {item['Id']}: {e}")
    print(
        f"Normalization complete: {updated} updated, "
        f"{skipped} skipped (already correct), {errors} errors"
    )


def _run_normalize(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    args: argparse.Namespace,
    item_ids: list[str] | None = None,
    prefetched_items: list[dict] | None = None,
) -> None:
    """Run the genre normalization action.

    Args:
        client: Configured httpx client.
        base_url: Emby server base URL with port.
        user_id: Emby user ID for full-item fetches.
        library_ids: List of library IDs to scan.
        args: Parsed CLI arguments.
        item_ids: If provided, only process these specific item IDs.
        prefetched_items: Pre-fetched items to use instead of fetching (avoids double-fetch).
    """
    if prefetched_items is not None:
        items = prefetched_items
    elif item_ids:
        # Targeted mode: fetch only specific items (from webhook)
        items = fetch_items_by_ids(client, base_url, user_id, item_ids)
    else:
        # Use the non-user-scoped endpoint for detection: it returns raw stored genre names
        # (e.g. "Suspense"). The user-scoped endpoint silently normalises some names in its
        # response, causing normalize to miss items that still need updating.
        items = list(
            tqdm(
                fetch_items_with_genres(client, base_url, library_ids),
                desc="Fetching items",
                unit="item",
                leave=False,
            )
        )

    items_to_update = _collect_normalization_candidates(items)
    repair_dupes = getattr(args, "repair_dupes", False)

    if not items_to_update:
        print("All genres are already normalized.")
        if repair_dupes and args.doit:
            print("\nScanning for duplicate genres (--repair-dupes)...")
            _run_repair_dupes(client, base_url, user_id, library_ids, lock=args.lock)
        return

    if not args.doit:
        _preview_normalization_changes(items_to_update)
        return

    _apply_normalization_updates(
        items_to_update, client, base_url, user_id, item_ids, args.lock
    )

    if repair_dupes:
        print("\nScanning for duplicate genres (--repair-dupes)...")
        _run_repair_dupes(client, base_url, user_id, library_ids, lock=args.lock)


@dataclass(frozen=True)
class GenreProviders:
    """Genre-provider clients, keys and cache bundled as one unit threaded through the
    genre-fill pipeline. ``cache`` is a mutable dict updated in place during a run.
    """
    tmdb_client: httpx.Client | None
    tmdb_limiter: RateLimiter | None
    omdb_client: httpx.Client | None
    omdb_limiter: RateLimiter | None
    omdb_keys: list[str]
    cache: dict


def _create_provider_clients(
    tmdb_key: str | None,
    omdb_keys: list[str],
    cache: dict,
) -> GenreProviders:
    """Create rate-limited HTTP clients for TMDB and OMDb, bundled with keys and cache.

    Args:
        tmdb_key: TMDB API bearer token, or None to skip TMDB.
        omdb_keys: List of OMDb API keys, or empty list to skip OMDb.
        cache: The loaded genre cache (mutated in place during the run).

    Returns:
        A GenreProviders bundle. The client/limiter for a disabled provider is None.
    """
    tmdb_client = (
        httpx.Client(headers={"Authorization": f"Bearer {tmdb_key}"}) if tmdb_key else None
    )
    tmdb_limiter = RateLimiter(35.0) if tmdb_key else None
    omdb_client = httpx.Client() if omdb_keys else None
    omdb_limiter = RateLimiter(10.0) if omdb_keys else None
    return GenreProviders(
        tmdb_client, tmdb_limiter, omdb_client, omdb_limiter, omdb_keys, cache
    )


def _process_single_item_genres(
    item: dict,
    providers: GenreProviders,
    client, base_url: str, user_id: str,
    item_ids: list | None, args,
) -> str:
    """Process one item's external genres. Returns status: no_data|no_diff|dry_run|updated|error."""
    try:
        external_genres = fetch_genres_for_item(
            item, providers.tmdb_client, providers.tmdb_limiter,
            providers.omdb_client, providers.omdb_limiter, providers.omdb_keys, providers.cache,
        )
        if not external_genres:
            return "no_data"

        comparison = compare_genres(item.get("Genres") or [], external_genres)
        if not comparison["has_diff"]:
            return "no_diff"

        item_name = item.get("Name", item["Id"])
        missing = comparison["missing_from_emby"]

        if not args.doit:
            print(f"  {item_name}: would add {missing}")
            return "dry_run"

        full_item = item if item_ids else fetch_full_item(client, base_url, user_id, item["Id"])
        result = update_item_genres(
            client, base_url, item["Id"], full_item, comparison["merged"], lock=args.lock
        )
        if result:
            logger.info(f"Updated {item_name}: added {missing}")
            return "updated"
        return "no_diff"
    except Exception as e:
        logger.error(f"Failed to process {item.get('Id', '?')}: {e}")
        return "error"


def _apply_genre_updates(
    items: list[dict],
    providers: GenreProviders,
    client: httpx.Client,
    base_url: str,
    user_id: str,
    item_ids: list[str] | None,
    args: argparse.Namespace,
) -> None:
    """Iterate items and apply external genre updates from TMDB/OMDb.

    Always additive — never removes existing genres.
    Dry-run when args.doit is False.

    Args:
        items: Items to process.
        providers: Bundle of TMDB/OMDb clients, keys and the mutable genre cache.
        client: Emby HTTP client.
        base_url: Emby server base URL with port.
        user_id: Emby user ID for full-item fetches.
        item_ids: If provided, items are already full items (no second fetch needed).
        args: Parsed CLI arguments (uses .doit and .lock).
    """
    found = 0
    updated = 0
    skipped_no_data = 0
    skipped_no_diff = 0
    errors = 0

    for item in tqdm(items, desc="Fetching external genres", unit="item"):
        status = _process_single_item_genres(
            item, providers, client, base_url, user_id, item_ids, args,
        )
        if status == "no_data":
            skipped_no_data += 1
        elif status == "no_diff":
            found += 1
            skipped_no_diff += 1
        elif status == "dry_run":
            found += 1
        elif status == "updated":
            found += 1
            updated += 1
        elif status == "error":
            errors += 1

    print(
        f"\nFix complete: {found} items had external data, "
        f"{updated} updated, {skipped_no_diff} already complete, "
        f"{skipped_no_data} no external data found, {errors} errors"
    )
    if not args.doit and found > 0:
        print("Run with --doit to apply changes")


def _run_fix(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    args: argparse.Namespace,
    item_ids: list[str] | None = None,
    prefetched_items: list[dict] | None = None,
) -> None:
    """Fetch genres from TMDB/OMDb and fill gaps or validate existing genres.

    --gaps-only (default): only process items with no genres.
    --validate: process all items, adding genres missing from Emby.
    --item-ids: process only these specific items (implies --validate).
    Always additive — never removes existing genres.
    """
    from emby_dedupe.api.genre_providers import load_genre_cache, save_genre_cache

    # Resolve API keys
    tmdb_key = getattr(args, "tmdb_api_key", None) or get_env_variable(ENV_DEDUPE_TMDB_API_KEY)
    omdb_keys_str = (
        get_env_variable("DEDUPE_OMDB_API_KEYS") or get_env_variable(ENV_DEDUPE_OMDB_API_KEY)
    )
    omdb_keys = [k.strip() for k in omdb_keys_str.split(",")] if omdb_keys_str else []

    if not tmdb_key and not omdb_keys:
        logger.error(
            "No API keys found. Set DEDUPE_TMDB_API_KEY and/or DEDUPE_OMDB_API_KEY env vars."
        )
        sys.exit(1)

    cache = load_genre_cache()
    providers = _create_provider_clients(tmdb_key, omdb_keys, cache)

    if prefetched_items is not None:
        items = prefetched_items
        print(f"Processing {len(items)} specific item(s) (normalize + validate)")
    elif item_ids:
        # Targeted mode: fetch only specific items, always validate (check + fill)
        items = fetch_items_by_ids(client, base_url, user_id, item_ids)
        print(f"Processing {len(items)} specific item(s) (normalize + validate)")
    else:
        # Determine mode: validate processes all items, gaps-only (default) processes only untagged
        validate = getattr(args, "validate", False)
        gaps_only = getattr(args, "gaps_only", False) or not validate

        logger.info("Fetching items from Emby...")
        all_items = fetch_items_with_genres(client, base_url, library_ids)

        if gaps_only:
            items = [i for i in all_items if not (i.get("Genres") or [])]
            print(f"Items with no genres: {len(items)} (of {len(all_items)} total)")
        else:
            items = all_items
            print(f"Validating genres for {len(items)} items")

    try:
        _apply_genre_updates(
            items, providers, client, base_url, user_id, item_ids, args,
        )
    finally:
        save_genre_cache(providers.cache)


def _run_process(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    library_ids: list[str],
    args: argparse.Namespace,
    item_ids: list[str],
) -> None:
    """Run normalize + fix in a single pass, fetching items only once.

    Used by the webhook listener to avoid double-fetching when both
    normalize and fix need to run on the same set of item IDs.

    Args:
        client: Configured httpx client.
        base_url: Emby server base URL with port.
        user_id: Emby user ID.
        library_ids: List of library IDs (unused in item-ids mode).
        args: Parsed CLI arguments.
        item_ids: List of Emby item IDs to process.
    """
    items = fetch_items_by_ids(client, base_url, user_id, item_ids)
    print(f"Processing {len(items)} item(s) (normalize + fix)")

    # Phase 1: Normalize
    items_to_update = _collect_normalization_candidates(items)
    if items_to_update and args.doit:
        _apply_normalization_updates(
            items_to_update, client, base_url, user_id, item_ids, args.lock
        )
        # Re-fetch only the modified items to get fresh genres from the server
        updated_ids = [item["Id"] for item, _ in items_to_update]
        refreshed = {
            i["Id"]: i
            for i in fetch_items_by_ids(client, base_url, user_id, updated_ids)
        }
        items = [refreshed.get(i["Id"], i) for i in items]
    elif not items_to_update:
        print("All genres are already normalized.")

    # Phase 2: Fix (reuse fetched items — no second fetch)
    _run_fix(
        client, base_url, user_id, library_ids, args,
        item_ids=item_ids, prefetched_items=items,
    )


def _resolve_library_ids(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    library: list[str],
    all_libraries: bool,
) -> list[str]:
    """Resolve library names or --all-libraries flag to a list of Emby library IDs."""
    if all_libraries:
        from emby_dedupe.api.search import get_all_library_ids

        lib_infos = get_all_library_ids(client, base_url, api_key)
        return [lib["id"] for lib in lib_infos]

    library_ids = []
    for lib_name in library:
        lib_id = get_library_id(client, base_url, lib_name)
        if lib_id:
            library_ids.append(lib_id)
        else:
            logger.warning(f"Library not found: {lib_name}")
    return library_ids


def _resolve_genres_config(args: argparse.Namespace) -> tuple:
    """Parse env vars, apply overrides, and return resolved (host, api_key, library, all_libraries, item_ids)."""
    env_host = get_env_variable(ENV_DEDUPE_EMBY_HOST)
    env_port = get_env_variable(ENV_DEDUPE_EMBY_PORT)
    env_api_key = get_env_variable(ENV_DEDUPE_EMBY_API_KEY)
    env_library_str = get_env_variable(ENV_DEDUPE_EMBY_LIBRARY)
    env_library = (
        [lib.strip() for lib in env_library_str.split(",")]
        if env_library_str else None
    )
    env_verbosity = get_env_variable(ENV_DEDUPE_LOGGING)

    set_logging_level(args.verbosity, env_verbosity)
    override_warning("--host", args.host, env_host or "")
    override_warning("--port", args.port and str(args.port) or "", env_port or "")
    override_warning("--api-key", args.api_key, env_api_key or "")
    override_warning("--library", ",".join(args.library) if args.library else "", env_library_str or "")

    host: str | None = args.host or env_host
    port = args.port or env_port or None
    api_key: str | None = args.api_key or env_api_key
    library = args.library or env_library or []
    all_libraries = getattr(args, "all_libraries", False)
    item_ids_raw = getattr(args, "item_ids", None)
    item_ids: list[str] | None = (
        [i.strip() for i in item_ids_raw.split(",") if i.strip()]
        if item_ids_raw else None
    )
    _validate_genres_args(host, api_key, library, all_libraries, item_ids)
    return host, port, api_key, library, all_libraries, item_ids


def run_genres_command(args: argparse.Namespace) -> None:
    """Main entry point for the genres command.

    Args:
        args: Parsed CLI arguments.
    """
    host, port, api_key, library, all_libraries, item_ids = _resolve_genres_config(args)

    # After _validate_genres_args, host and api_key are guaranteed non-None (or sys.exit was called)
    resolved_host: str = host  # type: ignore[assignment]
    resolved_api_key: str = api_key  # type: ignore[assignment]
    resolved_port: int | None = int(port) if isinstance(port, str) else port

    validated_host, validated_port = handle_host_and_port(resolved_host, resolved_port)
    base_url = f"{validated_host}:{validated_port}"

    try:
        client = httpx.Client(headers={"X-Emby-Token": resolved_api_key})

        if not check_emby_connection(client, f"{base_url}/System/Info"):
            logger.error(f"Unable to connect to the Emby server at {base_url}.")
            sys.exit(1)

        user_id = get_user_id(client, base_url)
        library_ids = _resolve_library_ids(
            client, base_url, resolved_api_key, library, all_libraries
        )

        if args.action == "audit":
            _run_audit(client, base_url, library_ids, args)
        elif args.action == "fix":
            _run_fix(client, base_url, user_id, library_ids, args, item_ids=item_ids)
        elif args.action == "process":
            _run_process(client, base_url, user_id, library_ids, args, item_ids=item_ids)
        else:
            _run_normalize(client, base_url, user_id, library_ids, args, item_ids=item_ids)

    except EmbyServerConnectionError as e:
        logger.error(str(e))
        sys.exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {str(e)}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred during genre management: {str(e)}")
        sys.exit(1)
