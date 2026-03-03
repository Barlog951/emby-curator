#!/usr/bin/env python3
"""
Export missing episodes from Emby for Czech Tracker Scraper.

Usage:
    .dashboard-venv/bin/python scripts/export-missing-episodes.py [options]

Options:
    --min-missing N     Minimum missing episodes (default: 1)
    --max-missing N     Maximum missing episodes (default: 20)
    --min-complete N    Minimum % complete (default: 50)
    --max-shows N       Max shows to export (default: 10)
    --output PATH       Output file (default: /Users/dodko/DEV/emby-dedupe/dashboards/exports/missing_episodes.json)
    --dry-run           Show what would be exported without writing
"""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

# Thread-local storage for httpx clients
_tls = threading.local()

EMBY_HOST = "https://emby.in.fukiyato.com"
EMBY_API_KEY = "***EMBY_KEY_REDACTED***"


def main():
    parser = argparse.ArgumentParser(description="Export missing episodes for Czech Tracker")
    parser.add_argument("--min-missing", type=int, default=1, help="Min missing episodes (default: 1)")
    parser.add_argument("--max-missing", type=int, default=20, help="Max missing episodes (default: 20)")
    parser.add_argument("--min-complete", type=float, default=50, help="Min %% complete (default: 50)")
    parser.add_argument("--max-shows", type=int, default=10, help="Max shows to export (default: 10)")
    parser.add_argument("--output", default="/Users/dodko/DEV/emby-dedupe/dashboards/exports/missing_episodes.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't write file")
    args = parser.parse_args()

    client = httpx.Client(timeout=120, verify=False)

    def emby_get(path, **params):
        params["api_key"] = EMBY_API_KEY
        return client.get(f"{EMBY_HOST}/emby/{path}", params=params).json()

    # 1. Get all series
    print("Loading series...")
    all_series = []
    off = 0
    while True:
        d = emby_get("Items", Recursive="true", IncludeItemTypes="Series",
                      Fields="ProviderIds,ProductionYear,OriginalTitle,SortName",
                      StartIndex=str(off), Limit="500")
        items = d.get("Items", [])
        if not items:
            break
        all_series.extend(items)
        if off + len(items) >= d.get("TotalRecordCount", 0):
            break
        off += 500

    # 2. Count episodes per series
    print("Loading episode counts...")
    ep_count = {}
    off = 0
    while True:
        d = emby_get("Items", Recursive="true", IncludeItemTypes="Episode",
                      Fields="SeriesId", EnableImages="false", EnableUserData="false",
                      StartIndex=str(off), Limit="500")
        items = d.get("Items", [])
        if not items:
            break
        for ep in items:
            sid = ep.get("SeriesId", "")
            ep_count[sid] = ep_count.get(sid, 0) + 1
        if off + len(items) >= d.get("TotalRecordCount", 0):
            break
        off += 500

    # 3. Get missing episodes per series (concurrent)
    print(f"Checking {len(all_series)} series for missing episodes (15 threads)...")

    def fetch_missing(series):
        sid = series.get("Id", "")
        try:
            if not hasattr(_tls, "client"):
                _tls.client = httpx.Client(timeout=30, verify=False)
            r = _tls.client.get(f"{EMBY_HOST}/emby/Shows/Missing", params={
                "api_key": EMBY_API_KEY, "ParentId": sid,
                "IncludeSpecials": "false", "IncludeUnaired": "false",
                "Fields": "ParentIndexNumber,IndexNumber",
            })
            d = r.json()
            if isinstance(d, dict):
                return (sid, d.get("Items", []), d.get("TotalRecordCount", 0))
            return (sid, [], 0)
        except Exception:
            return (sid, [], 0)

    with ThreadPoolExecutor(max_workers=15) as pool:
        results = list(pool.map(fetch_missing, all_series))

    # 4. Build export
    series_map = {s["Id"]: s for s in all_series}
    seen_tmdb = set()
    export = []

    for sid, missing_items, n_missing in results:
        if n_missing < args.min_missing or n_missing > args.max_missing:
            continue

        s = series_map.get(sid, {})
        pids = s.get("ProviderIds", {})
        tmdb_id = pids.get("Tmdb") or pids.get("tmdb") or sid
        if tmdb_id in seen_tmdb:
            continue

        n_have = ep_count.get(sid, 0)
        n_total = n_have + n_missing
        pct = round(n_have / n_total * 100, 1) if n_total > 0 else 0
        if pct < args.min_complete:
            continue

        show_name = s.get("OriginalTitle") or s.get("SortName") or s.get("Name", "Unknown")

        episodes = []
        for ep in missing_items:
            sn = ep.get("ParentIndexNumber")
            en = ep.get("IndexNumber")
            if sn is not None and en is not None and sn > 0:
                episodes.append({"season": sn, "episode": en})

        if not episodes:
            continue

        entry = {
            "show": show_name,
            "emby_id": sid,
            "episodes": sorted(episodes, key=lambda e: (e["season"], e["episode"])),
        }
        imdb = pids.get("Imdb") or pids.get("IMDB") or pids.get("imdb")
        if imdb:
            entry["imdb_id"] = imdb
        year = s.get("ProductionYear")
        if year:
            entry["year"] = year

        export.append(entry)
        seen_tmdb.add(tmdb_id)

    export.sort(key=lambda e: len(e["episodes"]))
    export = export[:args.max_shows]

    # 5. Output
    total_eps = sum(len(e["episodes"]) for e in export)

    print(f"\n{'='*60}")
    print(f"  {len(export)} shows, {total_eps} total missing episodes")
    print(f"  Filters: {args.min_missing}-{args.max_missing} missing, >= {args.min_complete}% complete")
    print(f"{'='*60}")
    for e in export:
        eps_str = ", ".join(f"S{ep['season']:02d}E{ep['episode']:02d}" for ep in e["episodes"][:8])
        if len(e["episodes"]) > 8:
            eps_str += f" (+{len(e['episodes'])-8} more)"
        print(f"  {e['show']:40s} {len(e['episodes']):3d} eps  {eps_str}")

    if args.dry_run:
        print(f"\n  [DRY RUN] Would write to: {args.output}")
    else:
        _now = datetime.now()
        wrapper = {
            "exported_at": _now.isoformat(),
            "exported_at_unix": int(_now.timestamp()),
            "total_shows": len(export),
            "total_episodes": total_eps,
            "shows": export,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(wrapper, indent=2, ensure_ascii=False))
        print(f"\n  Written to: {args.output}")


if __name__ == "__main__":
    main()
