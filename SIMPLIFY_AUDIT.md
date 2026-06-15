# "Reinventing the wheel?" audit — emby-dedupe

Date: 2026-06-16. Read-only review of `emby_dedupe/` (~8.7k NCLOC).
Verdict per item: **DRY** = duplication to consolidate (no new dep), **LIB** = a
proper library genuinely fits, **KEEP** = looks reinvented but is fine as-is.

Ranked by value (impact ÷ risk).

---

## 1. ⭐ Pagination loops — 5 hand-rolled copies → 1 generator  [DRY]

Five near-identical `while True:` StartIndex paginators:
- `api/cleanup_pipeline.py:235, 512, 777, 982`
- `api/genres.py:198`

Each does: set `StartIndex`, GET, read `Items` + `TotalRecordCount`, extend,
advance, stop when `start_index >= total or count == 0`. ~30 lines × 5.

**Fix (no new dependency):** one generator in `utils/`:
```python
def paginate_items(client, endpoint, params, page_size=PAGE_SIZE):
    start = 0
    while True:
        page = {**params, "StartIndex": str(start), "Limit": str(page_size)}
        data = make_http_request(client, "GET", endpoint, params=page).json()
        items = data.get("Items", [])
        yield from items
        start += len(items)
        if start >= data.get("TotalRecordCount", 0) or not items:
            break
```
Callers that need a tqdm bar or per-item tagging wrap the generator. Removes
~120 lines. **Highest-value, lowest-risk change. No new dep.**

No mature official Emby Python SDK exists, so a library can't replace this — the
right answer is one internal helper, not a package.

---

## 2. ⭐ Two JSON disk caches — duplicated → `diskcache`  [LIB]

`api/genre_providers.py` (load/save_genre_cache) and `api/description_cache.py`
(load/save_cache + is_fresh + make_entry/read_entry + `_ts` TTL wrapping) are
copy-paste — the description cache's docstring literally says *"Mirrors the
design of genre_providers.py cache."* Both reimplement: atomic `.tmp`+rename,
corrupt-file tolerance, and (description side) manual TTL via an `_ts` field.

**This is the strongest "use a real library" case.** [`diskcache`](https://grantjenks.com/docs/diskcache/)
gives all of it natively:
```python
from diskcache import Cache
cache = Cache(Path.home() / ".cache" / "emby-dedupe")
cache.set(key, value, expire=30*24*3600)   # TTL built in
value = cache.get(key)                       # None on miss/expired
```
- atomic + process/thread-safe writes (SQLite-backed) — better than the current
  whole-file rewrite, which loses data if two runs race
- TTL replaces the entire `is_fresh`/`make_entry`/`read_entry`/`_ts` machinery
- negative caching (remember "TMDB had no data") works the same — store `None`

Tradeoff: switches storage from a human-readable JSON blob to a SQLite dir, and
needs a one-time migration of the live cache on emby-gpu (3,225 entries).
**If you'd rather not add a dep:** consolidate both into one shared
`utils/json_cache.py` (TTL-aware) — kills the duplication, keeps JSON. Either
way the duplication goes.

---

## 3. File-size formatting — 2 functions + inline copies → 1  [DRY, optionally LIB]

- `api/deduplication.py:113` `_format_file_size` (B/KB/MB/GB, "Unknown" on 0)
- `api/metadata.py:15` `_format_file_size` (KB/MB/GB)
- inline `/ (1024**3)` math in `api/quality_compare.py:230, 924`

**Fix:** one `format_size()` in `utils/`. Optionally `humanize.naturalsize()`,
BUT caveat: humanize binary mode prints "GiB" not "GB", which would change
report text and break snapshot assertions. Honest call: **consolidate to one
internal helper** (zero risk) rather than pulling humanize just for this.

---

## 4. Date-parsing ladder in metadata.py → `python-dateutil`  [LIB, medium]

`api/metadata.py:36-160` hand-rolls a chain: `_parse_iso_date` (manual
`'T' in date_str` sniff + `fromisoformat` + try/except), `_try_parse_date_field`,
`_try_fallback_date_fields`, `_try_filesystem_date` — verbose fallback ladder
with repeated try/except.

`dateutil.parser.parse()` (already transitively installed; would need adding to
pyproject deps) parses ISO + most real-world formats in one call, collapsing the
ladder. Medium value — the current code's *behavior* is partly intentional
(passes through non-ISO strings verbatim, special-cases `ProductionYear`), so
this is a careful simplification, not a blind swap.

---

## 5. KEEP as-is (looks reinvented, but isn't overcomplicated)

- **`RateLimiter`** (`genre_providers.py:34`) — 15 lines, thread-safe, correct.
  `pyrate-limiter`/`aiolimiter` exist but are heavier; replacing a minimal correct
  class with a dependency is not a win. **Leave it.**
- **`_format_days_left`** (`reports/cleanup.py:157`) — compact table format
  ("20d", "1mo", "1.1yr"). `humanize.naturaldelta` gives prose ("3 weeks"),
  wrong for a fixed-width column. **Leave it.**
- **Atomic file write** (`utils/file_ops.py`) — standard tmp+rename, fine.
- **Retry/backoff** — already uses the `backoff` library correctly. No reinvention.
- **`config.py`** env>file>default merge — `pydantic-settings` is the modern
  idiom, but this works and a swap is a bigger lift with real regression risk.
  Optional, low priority.

---

## Recommended order
1. Pagination generator (#1) — biggest cleanup, no dep, low risk
2. Cache consolidation (#2) — `diskcache` or shared util (your call on the dep)
3. File-size helper (#3) — quick DRY win
4. Date parsing (#4) — careful, medium value
(skip #5)

Each is independent and test-backed (1,046 tests). Do one, run pytest, commit.
