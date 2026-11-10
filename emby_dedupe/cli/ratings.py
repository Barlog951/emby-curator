"""``ratings fill`` / ``ratings undo``: give items an age rating where Emby left it empty.

Only an empty ``OfficialRating`` is written, never a locked one, and only on items older than
``--min-age-hours`` (Emby's own fetch runs first). Each write is re-read to confirm it and
recorded in a journal, so a run can be reported and undone. The choice of rating lives in
:mod:`emby_dedupe.api.ratings`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from tqdm import tqdm

from emby_dedupe.api.client import check_emby_connection, get_library_id, handle_host_and_port
from emby_dedupe.api.descriptions import post_item_update
from emby_dedupe.api.genres import fetch_full_item, get_user_id
from emby_dedupe.api.pagination import paginate_emby_items
from emby_dedupe.api.ratings import CertificateSource, RatingChoice, choose_rating
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.json_cache import load_json_cache, save_json_cache
from emby_dedupe.utils.logging import logger
from emby_dedupe.utils.providers import normalize_provider_ids

ENV_JOURNAL = "DEDUPE_RATINGS_JOURNAL"
_DEFAULT_JOURNAL = Path.home() / ".emby-dedupe" / "ratings-journal.jsonl"
CACHE_PATH = Path.home() / ".cache" / "emby-dedupe" / "rating-cache.json"
CACHE_SAVE_EVERY = 100
_CACHE_LABEL = "rating cache"
_MEDIA_TYPE = {"Movie": "movie", "Series": "tv"}
_LIST_FIELDS = "OfficialRating,ProviderIds,DateCreated"
_PAGE_SIZE = 2000
ERROR_SHARE_LIMIT = 0.02  # TMDb lookup errors tolerated before a run counts as failed
ERROR_COUNT_LIMIT = 3


def journal_path() -> Path:
    """``$DEDUPE_RATINGS_JOURNAL`` or ``~/.emby-dedupe/ratings-journal.jsonl``."""
    return Path(os.environ.get(ENV_JOURNAL) or _DEFAULT_JOURNAL)


def append_journal(entry: dict) -> None:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_journal(run_id: str) -> list[dict]:
    """Journal lines written by one run."""
    try:
        lines = journal_path().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    entries = [json.loads(line) for line in lines if line.strip()]
    return [e for e in entries if e.get("run") == run_id]


def fetch_unrated(client: httpx.Client, base_url: str, library_ids: list[str], types: list[str],
                  min_age_hours: float) -> list[dict]:
    """Items of ``types`` with an empty OfficialRating, added at least ``min_age_hours`` ago."""
    params = {"Recursive": "true", "IncludeItemTypes": ",".join(types), "Fields": _LIST_FIELDS,
              "EnableImages": "false", "EnableUserData": "false", "Limit": str(_PAGE_SIZE)}
    cutoff = datetime.now(UTC) - timedelta(hours=min_age_hours)
    items: dict[str, dict] = {}
    parents: list[str | None] = list(library_ids) or [None]
    for parent_id in parents:
        page_params = {**params, "ParentId": parent_id} if parent_id else params
        for page, _total in paginate_emby_items(client, f"{base_url}/Items", page_params,
                                                error_context="fetch items", raise_on_error=True):
            items.update((item["Id"], item) for item in page if _is_unrated_and_settled(item, types, cutoff))
    return list(items.values())


def _is_unrated_and_settled(item: dict, types: list[str], cutoff: datetime) -> bool:
    """An item of the asked types, with no rating, added before ``cutoff``."""
    if (item.get("OfficialRating") or "").strip() or item.get("Type") not in types:
        return False
    created = _parse_date(item.get("DateCreated"))
    return created is None or created <= cutoff


def _parse_date(value: str | None) -> datetime | None:
    """Emby's UTC timestamp ("2026-10-30T12:34:56.0000000Z"), to the second; None when unreadable."""
    try:
        return datetime.strptime((value or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _resolve_choice(source: CertificateSource, item: dict) -> RatingChoice:
    """Look the item up on TMDb (by TMDb id, else IMDb id) and choose its rating."""
    media_type = _MEDIA_TYPE[item["Type"]]
    ids = normalize_provider_ids(item.get("ProviderIds"))
    tmdb_id = ids.get("tmdb")
    if not tmdb_id and ids.get("imdb"):
        tmdb_id = source.tmdb_id_for_imdb(str(ids["imdb"]), media_type)
    if not tmdb_id:
        return RatingChoice(None, "no tmdb id")
    certs = source.certificates(str(tmdb_id), media_type)
    if certs is None:
        return RatingChoice(None, "unknown to tmdb")
    return choose_rating(certs, media_type)


def _write(client: httpx.Client, base_url: str, user_id: str, item: dict, rating: str) -> str:
    """Write ``rating`` onto a fresh full copy of the item. Returns "written", "skipped_*" or "failed"."""
    full = fetch_full_item(client, base_url, user_id, item["Id"])
    if (full.get("OfficialRating") or "").strip():
        return "skipped_now_rated"
    if full.get("LockData") or "OfficialRating" in (full.get("LockedFields") or []):
        return "skipped_locked"
    payload = {**full, "OfficialRating": rating}
    if not post_item_update(client, base_url, item["Id"], payload):
        return "failed"
    after = fetch_full_item(client, base_url, user_id, item["Id"])
    return "written" if after.get("OfficialRating") == rating else "failed"


def run_fill(client: httpx.Client, base_url: str, user_id: str, library_ids: list[str],
             args: argparse.Namespace) -> Counter:
    """Choose and (with ``--doit``) write ratings for unrated items. Returns the run's counts."""
    types = [t.strip() for t in (args.types or "Movie").split(",") if t.strip() in _MEDIA_TYPE]
    items = fetch_unrated(client, base_url, library_ids, types, args.min_age_hours)
    limit = getattr(args, "limit", None)
    items = items[:limit] if limit else items
    cache = load_json_cache(CACHE_PATH, label=_CACHE_LABEL)
    source = CertificateSource(args.tmdb_api_key, cache)
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    counts: Counter = Counter()
    proposals: list[dict] = []
    print(f"{len(items)} unrated item(s) of type {', '.join(types)} older than {args.min_age_hours:g} h")
    try:
        for index, item in enumerate(tqdm(items, desc="Ratings", unit="item"), start=1):
            if index % CACHE_SAVE_EVERY == 0:
                save_json_cache(CACHE_PATH, cache, label=_CACHE_LABEL)
            try:
                choice = _resolve_choice(source, item)
            except httpx.HTTPError as e:
                counts["errors"] += 1
                logger.warning(f"TMDb lookup failed for {item.get('Name')} ({item['Id']}): {e}")
                continue
            if choice.rating is None:
                counts[f"empty: {choice.reason}"] += 1
                continue
            proposals.append({"id": item["Id"], "name": item.get("Name"), "type": item["Type"],
                              "rating": choice.rating, "reason": choice.reason, "certificates": choice.certificates})
            if not args.doit:
                counts[f"would write {choice.rating}"] += 1
                continue
            outcome = _write(client, base_url, user_id, item, choice.rating)
            counts[f"{outcome} {choice.rating}" if outcome == "written" else outcome] += 1
            if outcome == "written":
                append_journal({"run": run_id, "at": datetime.now().isoformat(timespec="seconds"),
                                "id": item["Id"], "name": item.get("Name"), "type": item["Type"], "old": "",
                                "new": choice.rating, "reason": choice.reason, "certificates": choice.certificates})
    finally:
        save_json_cache(CACHE_PATH, cache, label=_CACHE_LABEL)
        source.close()
    _report(counts, run_id, args, proposals)
    return counts


def _report(counts: Counter, run_id: str, args: argparse.Namespace, proposals: list[dict]) -> None:
    mode = f"run {run_id}" if args.doit else "dry run (add --doit to write)"
    print(f"\nRatings fill, {mode}, {datetime.now():%Y-%m-%d %H:%M}:")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")
    if getattr(args, "json_out", None):
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"run": run_id if args.doit else None, "doit": bool(args.doit),
                                    "generated": datetime.now().isoformat(timespec="seconds"),
                                    "counts": dict(counts), "items": proposals},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON: {path}")


def run_failed(counts: Counter) -> bool:
    """True when a write failed, or TMDb lookups failed beyond a small share of the run."""
    seen = sum(counts.values())
    return bool(counts.get("failed")) or counts.get("errors", 0) > max(ERROR_COUNT_LIMIT, ERROR_SHARE_LIMIT * seen)


def run_undo(client: httpx.Client, base_url: str, user_id: str, args: argparse.Namespace) -> Counter:
    """Clear the ratings one run wrote, where the item still holds that run's value."""
    counts: Counter = Counter()
    entries = read_journal(args.run)
    print(f"{len(entries)} write(s) recorded for run {args.run}")
    for entry in entries:
        full = fetch_full_item(client, base_url, user_id, entry["id"])
        if (full.get("OfficialRating") or "") != entry["new"]:
            counts["changed since, kept"] += 1
            continue
        if not args.doit:
            counts["would clear"] += 1
            continue
        ok = post_item_update(client, base_url, entry["id"], {**full, "OfficialRating": entry.get("old") or ""})
        counts["cleared" if ok else "failed"] += 1
        time.sleep(0.05)
    print(", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing to undo")
    return counts


def _library_ids(client: httpx.Client, base_url: str, names: list[str] | None) -> list[str]:
    """Ids of the named libraries (none = the whole server); exits on an unknown name."""
    ids = []
    for name in names or []:
        library_id = get_library_id(client, base_url, name)
        if not library_id:
            sys.exit(1)
        ids.append(library_id)
    return ids


def run_ratings_command(args: argparse.Namespace) -> None:
    """Connect to Emby and run ``ratings fill`` or ``ratings undo``."""
    if not args.host or not args.api_key:
        logger.error("ratings needs the Emby host and API key (DEDUPE_EMBY_HOST / DEDUPE_EMBY_API_KEY).")
        sys.exit(1)
    if args.action == "fill" and not getattr(args, "tmdb_api_key", None):
        logger.error("ratings fill needs a TMDb key: set DEDUPE_TMDB_API_KEY or pass --tmdb-api-key.")
        sys.exit(1)
    host, port = handle_host_and_port(args.host, int(args.port) if args.port else None)
    base_url = f"{host}:{port}"
    with httpx.Client(headers={"X-Emby-Token": args.api_key}, timeout=120) as client:
        try:
            if not check_emby_connection(client, f"{base_url}/System/Info"):
                sys.exit(1)
            user_id = get_user_id(client, base_url)
            if args.action == "undo":
                counts = run_undo(client, base_url, user_id, args)
            else:
                counts = run_fill(client, base_url, user_id, _library_ids(client, base_url, args.library), args)
        except (EmbyServerConnectionError, httpx.HTTPError) as e:
            logger.error(f"ratings failed: {e}")
            sys.exit(1)
    if run_failed(counts):
        sys.exit(1)
