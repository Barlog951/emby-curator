# Missing Episodes Export — Integration with Czech Tracker Scraper

## Overview

The `emby-dedupe` dashboard (`dashboards/emby_missing.py`) already detects missing episodes via Emby's `/Shows/Missing` endpoint. This document describes how to export that data as a JSON file that the Czech Tracker Scraper (`/Users/dodko/DEV/torrents`) can consume to automatically find and download missing content.

## Output File

**Path:** `/Users/dodko/DEV/torrents/data/missing_episodes.json`

The scraper reads this file via: `python src/main.py --search-missing data/missing_episodes.json`

## JSON Format

```json
[
  {
    "show": "Breaking Bad",
    "emby_id": "12345",
    "imdb_id": "tt0903747",
    "year": 2008,
    "episodes": [
      {"season": 2, "episode": 12},
      {"season": 2, "episode": 13},
      {"season": 3, "episode": 5}
    ]
  },
  {
    "show": "NCIS",
    "emby_id": "67890",
    "episodes": [
      {"season": 21, "episode": 5}
    ]
  }
]
```

### Field Reference

| Field | Type | Required | Source in Emby | Notes |
|-------|------|----------|----------------|-------|
| `show` | string | **YES** | `OriginalSeriesName` or `SeriesName` | Clean English name preferred. No year suffix, no "Season X" |
| `episodes` | array | **YES** | `ParentIndexNumber` + `IndexNumber` from `/Shows/Missing` | Grouped by show, not repeated per episode |
| `emby_id` | string | optional | `SeriesId` | For tracking which Emby item was resolved |
| `imdb_id` | string | optional | `ProviderIds.Imdb` from series metadata | Helps tracker search accuracy |
| `year` | int | optional | `ProductionYear` | For disambiguation (e.g., "Scrubs 2001" vs "Scrubs 2026") |

### Rules

1. **Group episodes by show** — one entry per show, all missing episodes in the `episodes` array
2. **Use `OriginalSeriesName`** (English) over `SeriesName` (may be Czech/Slovak translated) — the scraper generates Czech search terms itself via BAML
3. **Skip Season 0** (specials) — already filtered by `IncludeSpecials=false`
4. **Skip unaired** — already filtered by `IncludeUnaired=false`
5. **Limit batch size** — max 5-10 shows per file (each show = 1 tracker login + search + detail page checks)

## Where to Add the Export

### Option A: New cell in `dashboards/emby_missing.py` (recommended)

Add an "Export for Scraper" button in the Missing Episodes tab. The data is already available in the `analyze_missing_episodes` cell — specifically in `_results` which contains `(series_id, missing_items, n_missing)` tuples.

```python
@app.cell
def export_for_scraper(mo, raw_series, _results, jsonlib, Path):
    """Export missing episodes as JSON for Czech Tracker Scraper."""

    def _build_export(max_shows=10, min_missing=1, max_missing=50):
        """Build the export JSON from dashboard data."""
        series_map = {s["Id"]: s for s in raw_series}
        export = []

        for sid, missing_items, n_missing in _results:
            if n_missing < min_missing or n_missing > max_missing:
                continue

            s = series_map.get(sid, {})
            pids = s.get("ProviderIds", {})

            # Prefer OriginalTitle (English) over Name (may be Czech)
            show_name = (
                s.get("OriginalTitle")
                or s.get("SortName")
                or s.get("Name", "Unknown")
            )

            # Group episodes
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

            # Add optional fields if available
            imdb = pids.get("Imdb") or pids.get("imdb")
            if imdb:
                entry["imdb_id"] = imdb
            year = s.get("ProductionYear")
            if year:
                entry["year"] = year

            export.append(entry)

        # Sort by fewest missing first (easiest to complete)
        export.sort(key=lambda e: len(e["episodes"]))
        return export[:max_shows]

    # UI controls
    max_shows_slider = mo.ui.slider(start=1, stop=20, value=5, label="Max shows to export")
    export_btn = mo.ui.button(label="Export for Scraper", kind="success")

    if export_btn.value:
        data = _build_export(max_shows=max_shows_slider.value)
        out_path = Path("/Users/dodko/DEV/torrents/data/missing_episodes.json")
        out_path.write_text(jsonlib.dumps(data, indent=2, ensure_ascii=False))
        mo.callout(
            f"Exported {len(data)} shows ({sum(len(e['episodes']) for e in data)} episodes) "
            f"to {out_path}",
            kind="success",
        )
    else:
        mo.hstack([max_shows_slider, export_btn], gap="1rem")

    return (export_btn, max_shows_slider)
```

### Option B: Standalone script

If you prefer a non-marimo script:

```python
#!/usr/bin/env python3
"""Export missing episodes from Emby for Czech Tracker Scraper."""

import json
import httpx

EMBY_HOST = "https://emby.in.fukiyato.com"
EMBY_API_KEY = "36825b1ab6394b8daee5bc1c2186bd90"
OUTPUT = "/Users/dodko/DEV/torrents/data/missing_episodes.json"
MAX_SHOWS = 5


def main():
    client = httpx.Client(timeout=120, verify=False)

    # 1. Get all series
    series_list = []
    offset = 0
    while True:
        r = client.get(f"{EMBY_HOST}/emby/Items", params={
            "api_key": EMBY_API_KEY,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": "ProviderIds,ProductionYear,OriginalTitle,SortName",
            "StartIndex": str(offset),
            "Limit": "500",
        }).json()
        items = r.get("Items", [])
        if not items:
            break
        series_list.extend(items)
        if offset + len(items) >= r.get("TotalRecordCount", 0):
            break
        offset += 500

    print(f"Found {len(series_list)} series")

    # 2. Get missing episodes per series
    export = []
    for s in series_list:
        sid = s["Id"]
        r = client.get(f"{EMBY_HOST}/emby/Shows/Missing", params={
            "api_key": EMBY_API_KEY,
            "ParentId": sid,
            "IncludeSpecials": "false",
            "IncludeUnaired": "false",
            "Fields": "ParentIndexNumber,IndexNumber",
        }).json()

        missing_items = r.get("Items", []) if isinstance(r, dict) else (r if isinstance(r, list) else [])
        if not missing_items:
            continue

        show_name = s.get("OriginalTitle") or s.get("SortName") or s.get("Name", "Unknown")
        pids = s.get("ProviderIds", {})

        episodes = []
        for ep in missing_items:
            sn, en = ep.get("ParentIndexNumber"), ep.get("IndexNumber")
            if sn and en and sn > 0:
                episodes.append({"season": sn, "episode": en})

        if not episodes:
            continue

        entry = {"show": show_name, "emby_id": sid, "episodes": sorted(episodes, key=lambda e: (e["season"], e["episode"]))}
        imdb = pids.get("Imdb") or pids.get("imdb")
        if imdb:
            entry["imdb_id"] = imdb
        year = s.get("ProductionYear")
        if year:
            entry["year"] = year

        export.append(entry)

    # Sort by fewest missing (easiest to complete first)
    export.sort(key=lambda e: len(e["episodes"]))
    export = export[:MAX_SHOWS]

    with open(OUTPUT, "w") as f:
        json.dump(export, f, indent=2, ensure_ascii=False)

    total_eps = sum(len(e["episodes"]) for e in export)
    print(f"Exported {len(export)} shows ({total_eps} episodes) to {OUTPUT}")
    for e in export:
        print(f"  {e['show']}: {len(e['episodes'])} missing")


if __name__ == "__main__":
    main()
```

## What the Scraper Does With This File

```bash
# Dry run (default) — shows what it would download
python src/main.py --search-missing data/missing_episodes.json

# Auto-download to Transmission
python src/main.py --search-missing data/missing_episodes.json --auto-approve
```

Per show in the file:
1. **Search tracker ONCE** for show name (cached for 1 hour)
2. **Smart classify** — skip wrong seasons (Season 5 pack irrelevant when looking for S02E12)
3. **Extract file lists** from relevant packs (cached permanently — torrent contents never change)
4. **Batch verify** — one BAML call per pack checks ALL missing episodes at once
5. **Select optimal downloads** — minimum set of packs covering all missing episodes
6. **Download** — fetch .torrent file, add to Transmission with correct path

## Cost Estimate

| Shows | Episodes | First Run | Cached Run |
|-------|----------|-----------|------------|
| 5 | ~15 total | ~$0.40 (80 Haiku calls) | ~$0.05 (10 Haiku calls) |
| 10 | ~30 total | ~$0.80 | ~$0.10 |

Most cost is `ExtractTorrentData` on first run — cached permanently after that.

## Priority / Sorting Suggestions

When deciding which shows to export, consider:

1. **Fewest missing episodes first** — shows with 1-3 missing are easiest to complete
2. **Most-watched shows** — prioritize shows the user actively watches
3. **Recently active** — shows with episodes added in last 30 days (user is collecting)
4. **High completion %** — shows at 90%+ are almost done, worth completing
5. **Skip dead shows** — shows with 100+ missing episodes are likely not worth searching per-episode

The dashboard already has `% Complete`, `Last Added`, and `Missing Episodes` columns — use these for filtering.
