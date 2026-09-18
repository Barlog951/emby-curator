"""``csfd fill`` — complete metadata and posters from ČSFD.

Targets items that TMDb/TVDb could not identify (no provider id) or that still
lack a poster, overview or genres. Each item is looked up on ČSFD by title and
year; only an unambiguous match is used, and only EMPTY fields are filled, so a
run never overwrites metadata that is already there. Dry-run by default.

A manual mapping file (``--map``, ``<emby_id><TAB><csfd_url>`` per line) settles
the titles the search cannot resolve by itself.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tqdm import tqdm

from emby_dedupe.api.client import check_emby_connection, handle_host_and_port
from emby_dedupe.api.csfd import (
    KIND_FILM,
    KIND_SERIES,
    CsfdClient,
    CsfdError,
    CsfdFilm,
    candidate_hits,
    load_csfd_cache,
    pick_match,
    save_csfd_cache,
    verify_match,
)
from emby_dedupe.api.descriptions import post_item_update
from emby_dedupe.api.genres import fetch_items_by_ids, fetch_items_with_genres, get_user_id
from emby_dedupe.api.item_images import upload_primary_image
from emby_dedupe.cli.arguments import get_env_variable
from emby_dedupe.cli.genres import _resolve_library_ids
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.logging import logger, set_logging_level

PROVIDER_KEYS = ("Tmdb", "Imdb", "Tvdb")
CSFD_PROVIDER_KEY = "Csfd"
CACHE_SAVE_EVERY = 10
VERIFY_FETCH_LIMIT = 2  # film pages fetched per query when no title matched outright
_FOLDER_TITLE_RE = re.compile(r"^(.*?)\s*\((?:19|20)\d{2}(?:-\d{4})?\)")


@dataclass
class ItemPlan:
    """What ``csfd fill`` would change on one Emby item."""

    item_id: str
    name: str
    film: CsfdFilm | None = None
    reason: str = ""
    fields: dict[str, object] = field(default_factory=dict)
    poster: bool = False

    @property
    def has_changes(self) -> bool:
        """True when something is written: a field, a poster, or just the Csfd id stamp."""
        return bool(self.fields) or self.poster or self.film is not None


# ---------------------------------------------------------------------------
# candidate selection
# ---------------------------------------------------------------------------

def _has_provider(item: dict) -> bool:
    ids = item.get("ProviderIds") or {}
    return any(ids.get(k) for k in PROVIDER_KEYS)


def missing_fields(item: dict) -> list[str]:
    """Names of the metadata pieces this item lacks."""
    gaps = []
    if "Primary" not in (item.get("ImageTags") or {}):
        gaps.append("poster")
    if not (item.get("Overview") or "").strip():
        gaps.append("overview")
    if not item.get("Genres"):
        gaps.append("genres")
    if not item.get("ProductionYear"):
        gaps.append("year")
    return gaps


def is_candidate(item: dict, only_unmatched: bool) -> bool:
    """True when the item is worth a ČSFD lookup."""
    if item.get("Type") not in ("Movie", "Series"):
        return False
    if (item.get("ProviderIds") or {}).get(CSFD_PROVIDER_KEY):
        return False  # already resolved by an earlier run
    if only_unmatched:
        return not _has_provider(item)
    return not _has_provider(item) or bool(missing_fields(item))


def title_queries(item: dict) -> list[str]:
    """Distinct search strings for an item: its Name, folder title, original title."""
    queries: list[str] = []
    folder = Path(item.get("Path") or "").name
    if item.get("Type") == "Movie":
        folder = Path(item.get("Path") or "").parent.name
    folder_match = _FOLDER_TITLE_RE.match(folder)
    for candidate in (item.get("Name"), folder_match.group(1) if folder_match else None,
                      item.get("OriginalTitle")):
        text = (candidate or "").strip()
        if text and text not in queries:
            queries.append(text)
    return queries


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def resolve_film(
    csfd: CsfdClient, item: dict, manual: dict[str, str]
) -> tuple[CsfdFilm | None, str]:
    """Find the ČSFD page for an item: manual map first, then strict search."""
    if item["Id"] in manual:
        return csfd.film(manual[item["Id"]]), "manual map"
    kind = KIND_SERIES if item.get("Type") == "Series" else KIND_FILM
    year = item.get("ProductionYear")
    queries = title_queries(item)
    searches = {query: csfd.search(query) for query in queries}
    for query, hits in searches.items():
        hit = pick_match(hits, query, year, kind)
        if hit:
            return csfd.film(hit.url), f"matched '{query}'"
    # Second pass: same-year hits whose page lists one of our titles as an
    # original/alternative name (ČSFD displays the localized title in search).
    for query, hits in searches.items():
        for hit in candidate_hits(hits, year, kind)[:VERIFY_FETCH_LIMIT]:
            film = csfd.film(hit.url)
            if verify_match(film, queries):
                return film, f"verified '{query}' via original title"
    return None, "no unambiguous ČSFD match"


def plan_item(item: dict, film: CsfdFilm, overwrite_poster: bool = False) -> ItemPlan:
    """Decide which EMPTY fields the ČSFD data can fill.

    ``overwrite_poster`` uploads ČSFD artwork even when a Primary image exists —
    used to replace fallback frame posters with real art on hand-mapped items.
    """
    plan = ItemPlan(item_id=item["Id"], name=item.get("Name", item["Id"]), film=film)
    gaps = missing_fields(item)
    if "overview" in gaps and film.plot:
        plan.fields["Overview"] = film.plot
    if "genres" in gaps and film.genres_en:
        plan.fields["Genres"] = film.genres_en
    if "year" in gaps and film.year:
        plan.fields["ProductionYear"] = film.year
    if item.get("CommunityRating") is None and film.rating_10 is not None:
        plan.fields["CommunityRating"] = film.rating_10
    plan.poster = ("poster" in gaps or overwrite_poster) and film.poster_url is not None
    return plan


def build_payload(item: dict, plan: ItemPlan) -> dict:
    """Full-object POST body: the item plus the planned fields and locks."""
    payload = copy.deepcopy(item)
    locked = payload.setdefault("LockedFields", [])
    for key, value in plan.fields.items():
        payload[key] = value
        if key in ("Overview", "Genres") and key not in locked:
            locked.append(key)
    genres = plan.fields.get("Genres")
    if isinstance(genres, list):
        payload["GenreItems"] = [{"Name": g, "Id": ""} for g in genres]
    provider_ids = payload.setdefault("ProviderIds", {})
    if plan.film is not None:
        provider_ids[CSFD_PROVIDER_KEY] = plan.film.csfd_id
    return payload


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def load_manual_map(path: str | None) -> dict[str, str]:
    """Read ``<emby_id>\\t<csfd_url>`` lines; blank lines and ``#`` comments ignored."""
    if not path:
        return {}
    mapping: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item_id, _, url = line.partition("\t")
        if item_id and url.startswith("http"):
            mapping[item_id.strip()] = url.strip()
    return mapping


def _describe(plan: ItemPlan) -> str:
    parts = [f"{k}={'…' if k == 'Overview' else v}" for k, v in plan.fields.items()]
    if plan.poster:
        parts.append("poster")
    if not parts:
        return "csfd id only" if plan.film else "nothing to fill"
    return ", ".join(parts)


def _apply(client: httpx.Client, base_url: str, csfd: CsfdClient, item: dict,
           plan: ItemPlan) -> bool:
    ok = post_item_update(client, base_url, plan.item_id, build_payload(item, plan))
    if ok and plan.poster and plan.film and plan.film.poster_url:
        try:
            data, content_type = csfd.fetch_poster(plan.film.poster_url)
        except CsfdError as exc:
            logger.warning(f"{plan.name}: {exc}")
            return ok
        ok = upload_primary_image(client, base_url, plan.item_id, data, content_type) and ok
    return ok


def _write_report(path: str, plans: list[ItemPlan]) -> None:
    lines = ["emby_id\tname\tresult\tcsfd_url\tplanned"]
    for p in plans:
        lines.append(
            f"{p.item_id}\t{p.name}\t{p.reason}\t{p.film.url if p.film else ''}\t{_describe(p)}"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Report written: {path}")


def _fetch_candidates(client: httpx.Client, base_url: str, user_id: str,
                      library_ids: list[str], args: argparse.Namespace) -> list[dict]:
    item_ids = getattr(args, "item_ids", None)
    if item_ids:
        items = fetch_items_by_ids(client, base_url, user_id, item_ids.split(","))
    else:
        items = fetch_items_with_genres(client, base_url, library_ids, user_id)
    only_unmatched = getattr(args, "only_unmatched", False)
    candidates = [i for i in items if is_candidate(i, only_unmatched)]
    limit = getattr(args, "limit", None)
    return candidates[:limit] if limit else candidates


def _lookup(csfd: CsfdClient, item: dict, manual: dict[str, str], stats: dict[str, int],
            overwrite_poster: bool = False) -> ItemPlan:
    """Resolve one item on ČSFD and plan its fills; errors and misses become empty plans."""
    name = item.get("Name", "")
    try:
        film, reason = resolve_film(csfd, item, manual)
    except CsfdError as exc:
        stats["errors"] += 1
        logger.warning(f"{name}: {exc}")
        return ItemPlan(item["Id"], name, reason=f"error: {exc}")
    if film is None:
        stats["unmatched"] += 1
        logger.info(f"UNMATCHED  {name} ({item.get('ProductionYear')})")
        return ItemPlan(item["Id"], name, reason=reason)
    stats["matched"] += 1
    plan = plan_item(item, film, overwrite_poster)
    plan.reason = reason
    logger.info(f"MATCH      {name} -> {film.url}  [{_describe(plan)}]")
    return plan


def _run_fill(client: httpx.Client, base_url: str, user_id: str,
              library_ids: list[str], args: argparse.Namespace) -> None:
    cache = None if getattr(args, "no_cache", False) else load_csfd_cache()
    csfd = CsfdClient(httpx.Client(), args.flaresolverr_url, cache)
    manual = load_manual_map(getattr(args, "map_file", None))
    candidates = _fetch_candidates(client, base_url, user_id, library_ids, args)
    logger.info(f"{len(candidates)} candidate item(s) for ČSFD lookup")

    plans: list[ItemPlan] = []
    stats = {"matched": 0, "unmatched": 0, "updated": 0, "failed": 0, "errors": 0}
    try:
        for index, item in enumerate(tqdm(candidates, desc="ČSFD", unit="item"), start=1):
            if cache is not None and index % CACHE_SAVE_EVERY == 0:
                save_csfd_cache(cache)  # a killed run keeps its lookups
            plan = _lookup(csfd, item, manual, stats, getattr(args, "overwrite_poster", False))
            plans.append(plan)
            if args.doit and plan.has_changes:  # a bare match still stamps the Csfd id
                stats["updated" if _apply(client, base_url, csfd, item, plan) else "failed"] += 1
    finally:
        if cache is not None:
            save_csfd_cache(cache)
    if getattr(args, "report", None):
        _write_report(args.report, plans)
    mode = "applied" if args.doit else "dry run — re-run with --doit to apply"
    logger.info(
        f"ČSFD fill ({mode}): matched {stats['matched']}, unmatched {stats['unmatched']}, "
        f"errors {stats['errors']}, updated {stats['updated']}, failed {stats['failed']}"
    )


def run_csfd_command(args: argparse.Namespace) -> None:
    """Entry point for ``csfd fill``."""
    set_logging_level(getattr(args, "verbosity", 0), get_env_variable("DEDUPE_LOGGING"))
    libraries = args.library or []
    all_libraries = getattr(args, "all_libraries", False)
    if not args.host or not args.api_key:
        logger.error("Missing host (--host / DEDUPE_EMBY_HOST) or api-key (-a / DEDUPE_EMBY_API_KEY)")
        sys.exit(1)
    if not libraries and not all_libraries and not getattr(args, "item_ids", None):
        logger.error("Missing library (-l / --all-libraries / --item-ids)")
        sys.exit(1)
    port = int(args.port) if isinstance(args.port, str) else args.port
    validated_host, validated_port = handle_host_and_port(args.host, port)
    base_url = f"{validated_host}:{validated_port}"
    try:
        client = httpx.Client(headers={"X-Emby-Token": args.api_key})
        if not check_emby_connection(client, f"{base_url}/System/Info"):
            logger.error(f"Unable to connect to Emby at {base_url}.")
            sys.exit(1)
        user_id = get_user_id(client, base_url)
        library_ids = _resolve_library_ids(client, base_url, args.api_key, libraries, all_libraries)
        _run_fill(client, base_url, user_id, library_ids, args)
    except EmbyServerConnectionError as e:
        logger.error(str(e))
        sys.exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {e}")
        sys.exit(1)
