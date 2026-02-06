# Emby-Dedupe Remediation Plan

> **Generated:** 2026-02-06
> **Team:** 4-agent analysis team (SonarQube Analyst, Complexity Analyst, Test Strategist, Devil's Advocate)
> **Input:** HEALTH_REPORT.md (67/100 score), SonarQube analysis (48 issues), full codebase review
> **Purpose:** Ground-zero execution plan for the implementation team

---

## Executive Summary

| Metric | Current | After Plan |
|--------|---------|------------|
| **Health Score** | **67 / 100** | **85+ / 100** |
| SonarQube Issues | 48 (1 BLOCKER, 34 CRITICAL) | 0 |
| Grade-F Functions | 7 (CC 52-169) | 0 (all < 15) |
| Test Coverage | 56% (1,667/2,998 stmts) | 80%+ (2,398+ stmts) |
| Tests | 272 passing | ~420+ passing |
| Dead Functions | 2 confirmed | 0 |
| Broad Exception Handlers | 7 problematic (of 19 total) | 0 problematic |

**Total issues to fix:** 48 SonarQube issues + 15 health report recommendations (with 10 overlapping) = **53 unique action items**

**Estimated total effort:** 25-35 hours of AI agent time across 4 phases (revised upward per devil's advocate review)

**Risk level:** MEDIUM — core deduplication functions (CC 106-169) carry the highest risk; mitigated by behavioral tests first and incremental decomposition

### Critical Corrections from Devil's Advocate Review

These findings override the health report where they conflict:

1. **`create_http_client()` is NOT dead** — actively used by `api/missing_episodes.py:135`. Removing it breaks the missing episodes feature. **Only 2 dead functions remain.**
2. **12 of 19 broad exception handlers are intentional** — only 5 need fixing (2 in metadata.py, 2 false positives in html.py, 1 redundant in client.py)
3. **Complexity metrics differ** — SonarQube uses cognitive complexity (CC 169 max), health report used Radon cyclomatic complexity (CC 65 max). All targets in this plan use **SonarQube cognitive complexity < 15**.
4. **Effort estimates revised upward 50-100%** from health report originals based on actual code analysis
5. **Variable shadowing bug** found at `deduplication.py:1120` — inner loop variable `decision` shadows outer loop variable

---

## Phase 0: Immediate Blockers (Priority 0 — Unblock Quality Gate)

**Goal:** Fix the 1 BLOCKER and 2 type-mismatch bugs that block commits.
**Effort:** ~30 minutes | **Risk:** Low

| # | File:Line | Rule | Issue | Fix | Effort |
|---|-----------|------|-------|-----|--------|
| 0.1 | `api/missing_episodes.py:17` | S3516 (BLOCKER) | `enrich_episodes_with_series_metadata()` always returns same value | Investigate: if in-place mutation is intentional, change return type to `None`; if not, restructure to show enrichment | 15 min |
| 0.2 | `cli/main.py:94` | S5655 (CRITICAL) | `override_warning()` receives wrong type | Cast `logger.level` to `str()` or update `override_warning` signature | 5 min |
| 0.3 | `cli/missing_episodes.py:294` | S5655 (CRITICAL) | Same `override_warning()` type mismatch | Fix alongside 0.2 — same pattern | 5 min |
| 0.4 | `api/deduplication.py:1120` | — (Bug) | Variable shadowing: inner `for decision in decisions` shadows outer loop | Rename inner variable to `candidate` | 2 min |

**Verification:** `make test` → all 272 tests pass

---

## Phase 1: Quick Wins (Reduce SonarQube Noise)

**Goal:** Resolve 17 low-risk issues, remove debug code, extract shared constants.
**Effort:** ~2 hours | **Risk:** Minimal
**Resolves:** 17/48 SonarQube issues (35%)

### 1A: Zero-Risk Fixes (~15 minutes)

| # | File:Line | Rule | Fix | Effort |
|---|-----------|------|-----|--------|
| 1.1 | `api/deduplication.py:671` | S3457 | Remove `f` prefix from string with no interpolation | 1 min |
| 1.2 | `api/client.py:266` | S5713 | Remove redundant `Exception` from `except (EmbyServerConnectionError, Exception)` | 2 min |
| 1.3 | `api/client.py:372` | S1481 | Replace unused `user_id` with `_` | 2 min |
| 1.4 | `api/missing_episodes.py:136` | S1481 | Replace unused `auth_token` with `_` | 2 min |
| 1.5 | `api/deduplication.py:220` | S7504 | Remove unnecessary `list()` (verify no dict mutation during iteration first) | 3 min |
| 1.6 | `models/disjoint_set.py:18,36` | S6546 (x3) | Change `Union[str, dict]` to `str \| dict` (Python 3.12 syntax) | 3 min |

### 1B: Constant Extraction (~15 minutes)

| # | File:Line | Rule | Duplicated Value | Fix | Effort |
|---|-----------|------|-----------------|-----|--------|
| 1.7 | `api/missing_episodes.py:65` | S1192 | `"Unknown Series"` (8x) | Extract to `UNKNOWN_SERIES` constant | 5 min |
| 1.8 | `api/search.py:93` | S1192 | Long Fields string (3x) | Extract to `SEARCH_FIELDS` constant | 5 min |
| 1.9 | `reports/markdown.py:199` | S1192 | `"Items to Delete"` (4x) | Extract to `ITEMS_TO_DELETE_HEADER` constant | 5 min |

### 1C: Type Annotation Fixes (~15 minutes)

| # | File:Line | Rule | Fix | Effort |
|---|-----------|------|-----|--------|
| 1.10 | `api/deduplication.py:1038` | S5886 | Fix return type annotation to `tuple[list, dict]` | 5 min |
| 1.11 | `api/client.py:462` | S5886 | Fix return type annotation to match actual return | 5 min |
| 1.12 | `api/missing_episodes.py:46` | S5754 | Change bare `except:` to `except (httpx.HTTPError, ValueError):` | 3 min |

### 1D: Dead Code Removal (~10 minutes)

| # | File:Line | What | Notes | Effort |
|---|-----------|------|-------|--------|
| 1.13 | `reports/html.py:16` | `compare_dates()` function | Confirmed dead in production. Has 7 tests — remove function AND tests | 5 min |
| 1.14 | `utils/file_ops.py:82` | `read_json_file()` function | Not called in production. Has 3 tests — remove function AND tests | 5 min |
| — | ~~`api/client.py:109`~~ | ~~`create_http_client()`~~ | **NOT DEAD** — used by `api/missing_episodes.py:135`. **DO NOT REMOVE.** | — |

### 1E: Debug Code Removal (~30 minutes, HIGH VALUE)

| # | Files | What | CC Reduction | Effort |
|---|-------|------|-------------|--------|
| 1.15 | `api/deduplication.py` (lines 153-169, 191-212, 305-320, etc.) | Remove all hardcoded references to item IDs `"99424"` and `"20131603"` | **~30-40 CC points** across 3-4 functions | 30 min |

> **Why this matters:** Removing ~100 lines of debug logging reduces cognitive complexity of `build_disjoint_set` from CC=169 to ~130-140, and `rationalize_duplicates` from CC=162 to ~135-145, BEFORE any structural refactoring. This is the highest-value low-risk change in the entire plan.

### 1F: Parameter Bundling (~25 minutes)

| # | File:Line | Rule | Fix | Effort |
|---|-----------|------|-----|--------|
| 1.16 | `api/checker.py:366-382` | S107 | `check()` has 16 params — create `CheckConfig` dataclass to bundle | 15 min |
| 1.17 | `api/checker.py:497-511` | S107 | `should_download()` has 14 params — reuse `CheckConfig` | 10 min |

### 1G: Cross-Cutting DRY Extractions (~30 minutes)

These shared utilities reduce complexity in MULTIPLE functions simultaneously:

| # | What | Used In | Fix | CC Impact | Effort |
|---|------|---------|-----|----------|--------|
| 1.18 | Episode regex patterns (S01E01, 1x01, etc.) | `rationalize_duplicates`, `determine_items_to_delete` | Extract `_extract_episode_key_from_path()` to module level | -10 CC in 2 functions | 10 min |
| 1.19 | Language normalization mapping (slo→sk, cze→cs) | `determine_items_to_delete`, `main()`, `compare_quality`, `apply_language_priority` | Extract `LANGUAGE_NORMALIZATION_MAP` to `utils/constants.py` | -5 CC in 4 functions | 10 min |
| 1.20 | Smart language override algorithm | `determine_items_to_delete`, `compare_quality` | Extract shared `apply_smart_language_override()` | -15 CC in 2 functions | 10 min |

**Phase 1 Verification:** `make test` → all tests pass. `make sonar` → issue count drops from 48 to ~26.

---

## Phase 2: Test Safety Net (Must Complete Before Phase 3)

**Goal:** Write tests for 4 untested modules + strengthen safety net for Grade-F functions.
**Effort:** ~8-10 hours | **Risk:** Low (adding tests never breaks things)
**Target:** Coverage 56% → 70%+

### 2A: New Test Files for Untested Modules

#### `tests/unit/api/test_checker.py` — 28-32 tests
**Module:** `api/checker.py` (577 lines, 228 statements)
**Target coverage:** 80% (182 statements)

**Mock Strategy:**
- `httpx.Client` for all HTTP calls
- `emby_dedupe.api.client.fetch_and_process_media_items` for library fetches
- `emby_dedupe.api.search.search_media` for name searches
- `emby_dedupe.api.quality_compare.compare_quality` for quality comparison
- File I/O via `tmp_path` fixture for cache tests
- `time.time` for TTL tests

**Key Test Categories:**
- Initialization: direct params, Config object, from_config classmethod (3 tests)
- Cache operations: hit, miss, expired, disabled (6 tests)
- Provider table management: build, load, save, memory cache (5 tests)
- Provider ID lookup: found, not found, case-insensitive (3 tests)
- Check flow: excluded ID, IMDB lookup, name search fallback, not found (5 tests)
- should_download / check_batch / context manager (4 tests)
- Error handling: validation, connection failures (3 tests)

#### `tests/unit/api/test_missing_episodes.py` — 25-28 tests
**Module:** `api/missing_episodes.py` (629 lines, 218 statements)
**Target coverage:** 80% (174 statements)

**Mock Strategy:**
- `emby_dedupe.api.client.make_http_request` for all API calls
- `tqdm.tqdm` suppressed (or mock)
- Sample episode/series dicts as fixtures

**Key Test Categories:**
- `analyze_missing_episodes()`: pure function, test directly (5 tests — empty, single, multi, dedup, stats)
- `get_missing_episodes()`: auth success, API key fallback, alternative fallback, pagination (5 tests)
- `get_missing_episodes_alternative()`: success, empty library, error handling (3 tests)
- Per-series/season/episode fetching (5 tests)
- `enrich_episodes_with_series_metadata()`: success, empty list (2 tests)
- `process_missing_episodes_for_libraries()`: orchestration (3 tests)
- Error paths: timeout, 404, auth failure (3 tests)

#### `tests/unit/cli/test_check.py` — 15-18 tests
**Module:** `cli/check.py` (286 lines, 98 statements)
**Target coverage:** 80% (78 statements)

**Mock Strategy:**
- `EmbyChecker` class fully mocked
- `Config.from_cli_args` mocked
- `capsys` for output capture
- `argparse.Namespace` for mock args

**Key Test Categories:**
- Parameter extraction: search params, quality params (6 tests)
- Output formatting: json, simple, exit_code (3 tests)
- run_check: download recommendation, skip, validation error, exception (5 tests)
- Resource cleanup: checker closed on success/error (2 tests)

#### `tests/unit/cli/test_missing_episodes_cli.py` — 22-25 tests
**Module:** `cli/missing_episodes.py` (391 lines, 176 statements)
**Target coverage:** 80% (141 statements)

**Mock Strategy:**
- Mock `check_emby_connection`, `get_library_id`, `process_missing_episodes_for_libraries`
- `tmp_path` for file output tests
- `datetime.now` mocked for filename tests
- `capsys` for console output

**Key Test Categories:**
- Pure functions: `generate_default_filename()`, `format_structured_json_report()`, `format_missing_episodes_report()` (10 tests)
- File I/O: `write_to_file()` success, creates dir, error (3 tests)
- CLI entry: console/json/structured_json output, connection error, no missing (5 tests)
- Argument parsing: `add_missing_episodes_args()` (2 tests)

### 2B: Safety Net Tests for Grade-F Functions

These are **behavioral tests** (input → output verification) that survive refactoring. They validate WHAT the function does, not HOW it does it internally.

| Function | File | Current Coverage | Additional Tests | Key Edge Cases |
|----------|------|-----------------|-----------------|----------------|
| `process_duplicate_groups` | deduplication.py | 60% | 6-8 | Empty groups, single-item groups, all equal quality, language priority override, provider ID exclusion |
| `rationalize_duplicates` | deduplication.py | 60% | 5-6 | Empty provider tables, single provider type, cross-provider dedup, items with no provider IDs |
| `build_disjoint_set` | deduplication.py | 60% | 4-5 | Direct unit test, chain merges, self-references, empty input |
| `determine_items_to_delete` | deduplication.py | 60% | 5-7 | Language priority, exclude IDs, equal quality tie-breaking, single item group |
| `get_quality_description` | metadata.py | 80% | 5-7 | Multiple video streams, HDR metadata, unusual codecs, zero bitrate, 8K |
| `main` | cli/main.py | 54% | 8-10 | Full execution flow (mocked), multiple libraries, HTML-only mode, error paths |
| `format_html_report` | reports/html.py | 66% | 4-5 | Missing quality_description, callable languages field, empty decisions, unicode |

**Total safety net tests: 37-48**

### 2C: Coverage Math Verification

| Source | Statements Covered | Running Total |
|--------|-------------------|---------------|
| Current baseline | 1,667 | 56% |
| + 4 untested modules at 80% | +576 | 74.8% |
| + deduplication.py safety net (60%→80%) | +107 | 78.3% |
| + cli/main.py (54%→75%) | +27 | 79.2% |
| + reports/html.py (66%→80%) | +26 | 80.1% |
| **Projected total** | **2,403 / 2,998** | **80.1%** |

**Phase 2 Verification:** `make coverage` → 70%+ overall. `make test` → ~400+ tests passing.

---

## Phase 3: Complexity Reduction (The Big Refactoring)

**Goal:** Reduce all functions to SonarQube cognitive complexity < 15.
**Effort:** ~8-12 hours | **Risk:** Medium-High (mitigated by Phase 2 safety net)
**Prerequisites:** Phase 2 MUST be complete. All behavioral tests passing.

### Execution Rules:
1. **One function at a time** — refactor, run `make test`, verify all pass, then proceed
2. **Behavioral equivalence** — input/output must not change
3. **Add unit tests** for each extracted helper immediately after extraction
4. **Run `make sonar`** after completing each file to catch new issues early
5. **If > 3 tests break** from a single change → STOP and reassess

### Step 3.1: Independent Functions (Can Run in Parallel)

These functions have no dependencies on each other:

#### 3.1A: `get_quality_description` — `api/metadata.py:13` (CC: 100 → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_format_file_size(size_bytes)` | Convert bytes to human-readable string | 4 |
| `_parse_iso_date(date_str)` | Parse ISO 8601 date to display format | 4 |
| `_resolve_date_added(item)` | Try all date sources in priority order | 12 |
| `_extract_premiere_date(item)` | Extract and format premiere date | 4 |
| `_build_tv_metadata(item, quality_desc)` | Add TV series metadata | 4 |
| `get_quality_description` (refactored) | Extract streams, delegate to helpers | 10 |

**Risk:** Safe — pure data extraction, no side effects
**Effort:** 45-60 min

#### 3.1B: `format_html_report` — `reports/html.py:68` (CC: 93 → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_validate_decisions(decisions)` | Filter decisions with valid keep/delete items | 4 |
| `_detect_language_priority_usage(decisions)` | Check if language priority was used | 6 |
| `_ensure_quality_fields(quality_desc)` | Validate and default audio/video fields (DRY — used for keep AND delete) | 6 |
| `_process_delete_item(item, base_url, keep_serverid)` | Create processed delete item dict | 8 |
| `_create_language_priority_message(keep_item)` | Generate human-readable explanation | 5 |
| `_process_decision_group(decision, base_url)` | Process one decision into template format | 10 |
| `format_html_report` (refactored) | Validate, detect, process groups, render | 10 |

**Risk:** Safe — pure data transformation
**Effort:** 45-60 min

#### 3.1C: `main` — `cli/main.py:63` (CC: 71 → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_resolve_configuration(args)` | Resolve args + env vars into final config | 10 |
| `_parse_language_priorities(lang_prio_str)` | Parse and normalize language priority string (uses shared `LANGUAGE_NORMALIZATION_MAP`) | 7 |
| `_connect_and_fetch_libraries(client, base_url, libraries)` | Connect, iterate libraries, build provider tables | 8 |
| `_run_deduplication_pipeline(...)` | Run identify, rationalize, process_duplicate_groups | 5 |
| `_generate_reports(...)` | Generate markdown + HTML, handle browser opening | 8 |
| `main` (refactored) | Orchestrate: resolve, connect, pipeline, reports, error handling | 10 |

**Risk:** Medium — wide blast radius but mostly delegation
**Effort:** 60-90 min

#### 3.1D: `compare_quality` — `api/quality_compare.py:852` (CC: 64 → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_create_proposed_as_existing(proposed)` | Convert ProposedQuality for comparison | 8 |
| `_apply_bluray_native_exception(proposed, best_existing, recommendation)` | BluRay 1080p vs AI upscaled 4K | 8 |
| `compare_quality` (refactored) | Orchestrate: convert, sort, override (uses shared `apply_smart_language_override`), exception, score | 10 |

**Risk:** High — shares logic with `determine_items_to_delete` (already extracted as shared function in Phase 1.20)
**Effort:** 30-45 min

### Step 3.2: Deduplication.py Chain (MUST Be Sequential)

**Critical dependency chain — do in this exact order:**

#### 3.2A: `build_disjoint_set` — `api/deduplication.py:56` (CC: ~130 after debug removal → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_initialize_disjoint_set(media_items_by_provider)` | Create DS, calculate total, initial provider union | 8 |
| `_classify_items_as_tv_or_movie(items)` | Separate into TV episode groups and movie items | 7 |
| `_union_episode_groups(ds, tv_episode_groups)` | Union items within same episode group | 4 |
| `_union_movie_groups(ds, movie_items)` | Group movies by provider_id and union | 6 |
| `build_disjoint_set` (refactored) | Orchestrate: init, classify, union, log stats | 10 |

**Note:** Remove redundant second pass (lines 148-166 do same union as lines 96-112)
**Risk:** Medium — core grouping algorithm
**Effort:** 90-120 min

#### 3.2B: `rationalize_duplicates` — `api/deduplication.py:254` (CC: ~135 after debug removal → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_collect_items_metadata(media_items_by_provider)` | Build all_items_dict from provider tables | 5 |
| `_group_by_disjoint_root(ds)` | Create groups dict from DS parent map | 4 |
| `_verify_movie_group(items, all_items_dict)` | Check if group is movie-only | 5 |
| `_verify_tv_series_group(items, all_items_dict)` | Group by series/season/episode with path verification (uses shared `_extract_episode_key_from_path`) | 12 |
| `rationalize_duplicates` (refactored) | Orchestrate: collect, build DS, group, verify, assemble | 10 |

**Risk:** Medium — shares state with `build_disjoint_set`
**Effort:** 90-120 min

#### 3.2C: `determine_items_to_delete` — `api/deduplication.py:460` (CC: ~100 after debug/DRY → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_group_items_by_episode_path(items_details)` | Group items by series/season/episode from path (uses shared `_extract_episode_key_from_path`) | 10 |
| `_deduplicate_by_path(items_details, is_movie_group)` | Apply movie vs TV path dedup strategy | 8 |
| `_calculate_language_scores(rated_items, lang_priorities)` | Score items with language priority (uses shared `LANGUAGE_NORMALIZATION_MAP`) | 8 |
| `_record_language_decision(top_item, default_top_item, lang_priorities)` | Add language priority metadata | 5 |
| `determine_items_to_delete` (refactored) | Orchestrate: filter, rate, language, override (uses shared `apply_smart_language_override`), decide | 10 |

**Risk:** HIGH — subtle quality ratio thresholds (1.5x, 3x) critical to behavior
**Effort:** 90-120 min

#### 3.2D: `process_duplicate_groups` — `api/deduplication.py:754` (CC: ~110 after debug removal → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_build_exclusion_map(excluded_ids)` | Create provider-type-keyed exclusion map | 3 |
| `_check_group_exclusion(items_details, exclusion_map)` | Check if any item should be excluded | 8 |
| `_extract_excluded_item_info(item, base_url, api_key)` | Extract full metadata for excluded item | 12 |
| `_enrich_keep_item(keep_item, items_details, base_url, api_key)` | Add image URL, group name, episode flags | 8 |
| `_enrich_delete_item(delete_item, items_details, base_url, keep_serverid, api_key)` | Add image URL, provider IDs, episode flags | 10 |
| `process_duplicate_groups` (refactored) | Iterate groups, check exclusion, determine_items_to_delete, enrich | 12 |

**Risk:** Medium — coupling to `determine_items_to_delete` and enrichment logic
**Effort:** 60-90 min

#### 3.2E: `process_deletion_and_generate_report` — `api/deduplication.py:1041` (CC: 44 → target < 15)

| Extract | Purpose | Target CC |
|---------|---------|-----------|
| `_get_fallback_image_url(item, decisions, base_url)` | Find image from kept item or TMDB | 8 |
| `_perform_deletion(client, base_url, item, doit, credentials)` | Delete item and restore original data | 6 |
| `process_deletion_and_generate_report` (refactored) | Iterate decisions, handle dry-run, generate report | 10 |

**Risk:** Medium — actual deletion logic
**Effort:** 30-45 min

### Step 3.3: Remaining Medium-Complexity Functions

| Function | File:Line | CC | Fix Strategy | Effort |
|----------|-----------|-----|-------------|--------|
| `format_missing_episodes_report` | cli/missing_episodes.py:176 | 38 | Extract `_format_statistics_section()`, `_format_series_episodes()` | 25 min |
| `run_missing_episodes_command` | cli/missing_episodes.py:274 | 26 | Reuse config resolution helpers from refactored `main()` | 20 min |
| `search_media` | api/search.py:387 | 25 | Extract `_search_by_provider_ids()` | 20 min |
| `fetch_and_process_media_items` | api/client.py:274 | 29 | Extract pagination loop into helper | 20 min |
| `build_provider_id_tables` | api/client.py:465 | 21 | Simplify provider table building | 15 min |
| `EmbyChecker.check` | api/checker.py:365 | 22 | Extract `_lookup_existing_items()` | 15 min |
| `enrich_episodes_with_series_metadata` | api/missing_episodes.py:17 | 19 | Simplify enrichment loop | 15 min |
| `get_missing_episodes` | api/missing_episodes.py:100 | 29 | Extract auth/request building | 20 min |
| `calculate_report_statistics` | reports/common.py:8 | 21 | Extract sub-calculations | 15 min |
| `get_quality_description` (#2) | api/metadata.py:257 | 25 | Extract sub-logic into helper | 20 min |
| `validate_and_resolve_config` | utils/config.py:199 | 17 | Flatten config validation | 10 min |
| `format_markdown_table` | reports/markdown.py:124 | 24 | Extract `_format_table_row()` | 15 min |
| `format_deleted_items_table` | reports/markdown.py:55 | 19 | Extract formatting sub-logic | 15 min |
| `compare_video_streams` | quality_compare.py:322 | 32 | Extract scoring sub-logic | 20 min |
| `compare_media_streams` | quality_compare.py:591 | 33 | Extract comparison sub-logic | 20 min |
| `apply_language_priority` | quality_compare.py:374 | 19 | Flatten nested conditionals | 15 min |
| `ExistingQuality.from_emby_item` | quality_compare.py:464 | 16 | Simplify branching | 10 min |

**Phase 3 Verification:** After each function, `make test` → all pass. After completing all:
- `make sonar` → 0 SonarQube issues, quality gate PASSES
- All functions < CC 15
- Test count: ~420+

---

## Phase 4: Polish & Hardening

**Goal:** Exception handling, type hints, Docker, cleanup.
**Effort:** ~2-3 hours | **Risk:** Low

### 4A: Exception Handling Refinement (5 of 19 need changes)

| # | File:Line | Current | Recommended Fix |
|---|-----------|---------|----------------|
| 4.1 | `api/metadata.py:128` | `except Exception` (filesystem date) | `except OSError` |
| 4.2 | `api/metadata.py:140` | `except Exception` (generic date scan) | `except (ValueError, TypeError, OSError)` |
| 4.3 | `reports/html.py:61` | `except Exception` (unreachable) | **Remove entirely** — string comparison never raises |
| 4.4 | `reports/html.py:41` | `except Exception` (in dead `compare_dates`) | Already removed in Phase 1.13 |
| 4.5 | `api/client.py:266` | `(EmbyServerConnectionError, Exception)` | Already fixed in Phase 1.2 |

**The following 14 broad handlers are INTENTIONAL and should NOT be changed:**
- `api/metadata.py` lines 73, 90, 117, 153 — date parsing cascades (Emby API returns unpredictable formats)
- `cli/main.py` lines 263, 268, 287 — CLI boundary catch-all (correct pattern)
- `api/client.py` lines 103, 399 — logout/delete catch-all (acceptable)
- `api/deduplication.py` lines 1022, 1145 — processing loop skip-on-error (acceptable)
- `utils/file_ops.py` line 99 — file read fallback (acceptable)
- `reports/html.py` lines 188, 299 — report generation boundary (acceptable)

### 4B: Docker Improvements

| # | What | Effort |
|---|------|--------|
| 4.6 | Add `.dockerignore` (exclude tests/, docs/, .git/, debug scripts, *.md, htmlcov/) | 5 min |
| 4.7 | Add `HEALTHCHECK` instruction to Dockerfile | 10 min |

### 4C: CI/CD Enhancement

| # | What | Effort |
|---|------|--------|
| 4.8 | Add `pip-audit` to `security-scan.yaml` for dependency CVE scanning | 20 min |

### 4D: Cleanup

| # | What | Effort |
|---|------|--------|
| 4.9 | Clean up untracked debug/test files from repo root (38 files) | 15 min |
| 4.10 | Fix debug `print("Download it!")` in `checker.py:21` → `logger.info()` | 1 min |

**Phase 4 Verification:** `make sonar` → all clear. `make lint` → clean. Docker build succeeds.

---

## File Ownership Map (For Execution Team)

To avoid merge conflicts, each agent owns specific files:

### Agent A: "Deduplication Specialist" (Opus — highest complexity)
**Owns:**
- `emby_dedupe/api/deduplication.py` (8 SonarQube issues, 4 Grade-F functions)
- `emby_dedupe/models/disjoint_set.py` (3 issues)
- `tests/unit/api/test_deduplication.py` (safety net additions)
- `tests/unit/api/test_deduplication_advanced.py`
- New: `emby_dedupe/utils/constants.py` additions (shared language map, episode regex)

### Agent B: "API & Metadata Specialist" (Sonnet)
**Owns:**
- `emby_dedupe/api/metadata.py` (2 issues)
- `emby_dedupe/api/client.py` (4 issues)
- `emby_dedupe/api/search.py` (2 issues)
- `emby_dedupe/api/quality_compare.py` (5 issues)
- `tests/unit/api/test_metadata.py`
- `tests/unit/api/test_client.py`
- `tests/unit/api/test_quality_compare.py`

### Agent C: "Test & Coverage Specialist" (Sonnet)
**Owns:**
- `emby_dedupe/api/checker.py` (4 issues + 0% coverage)
- `emby_dedupe/api/missing_episodes.py` (6 issues + 0% coverage)
- New: `tests/unit/api/test_checker.py` (28-32 tests)
- New: `tests/unit/api/test_missing_episodes.py` (25-28 tests)

### Agent D: "CLI & Reports Specialist" (Sonnet)
**Owns:**
- `emby_dedupe/cli/main.py` (3 issues)
- `emby_dedupe/cli/check.py` (0% coverage)
- `emby_dedupe/cli/missing_episodes.py` (3 issues + 0% coverage)
- `emby_dedupe/reports/html.py` (3 issues)
- `emby_dedupe/reports/markdown.py` (3 issues)
- `emby_dedupe/reports/common.py` (1 issue)
- New: `tests/unit/cli/test_check.py` (15-18 tests)
- New: `tests/unit/cli/test_missing_episodes_cli.py` (22-25 tests)
- `tests/unit/cli/test_main.py` (safety net additions)
- `tests/unit/reports/test_html.py`
- `tests/unit/reports/test_markdown.py`

### Shared Files (Coordinate Access):
- `emby_dedupe/utils/constants.py` — Agent A adds language map + episode regex; Agent B may reference
- `conftest.py` — Any agent may add shared fixtures
- `Makefile`, `pyproject.toml` — Orchestrator only

---

## Dependency Graph

```
PHASE 0: Immediate Blockers
  ├── 0.1 Fix BLOCKER S3516 (missing_episodes.py)
  ├── 0.2-0.3 Fix type mismatches (main.py, missing_episodes.py)
  └── 0.4 Fix variable shadowing (deduplication.py)
       │
PHASE 1: Quick Wins ─────────────────────────────────────────
  ├── 1A Zero-risk fixes (no dependencies)
  ├── 1B Constant extraction (no dependencies)
  ├── 1C Type annotations (no dependencies)
  ├── 1D Dead code removal (no dependencies)
  ├── 1E Debug code removal (no dependencies) ◄── HIGHEST VALUE
  ├── 1F Parameter bundling (no dependencies)
  └── 1G DRY extractions ──────────────────────────┐
       │  ├── 1.18 Episode regex → used by 3.2B, 3.2C  │
       │  ├── 1.19 Language map → used by 3.2C, 3.1C    │
       │  └── 1.20 Smart override → used by 3.2C, 3.1D  │
       │                                                  │
PHASE 2: Test Safety Net ─────────────────────────────────│──
  ├── 2A.1 test_checker.py (no blockers)                  │
  ├── 2A.2 test_missing_episodes.py (no blockers)         │
  ├── 2A.3 test_check.py (after 2A.1 for confidence)     │
  ├── 2A.4 test_missing_episodes_cli.py (after 2A.2)     │
  └── 2B Safety net tests for Grade-F functions           │
       │  ├── deduplication.py tests (MUST before 3.2)    │
       │  ├── metadata.py tests (MUST before 3.1A)        │
       │  ├── html.py tests (MUST before 3.1B)            │
       │  └── main.py tests (MUST before 3.1C)            │
       │                                                   │
PHASE 3: Complexity Reduction ─────────────────────────────
  ├── 3.1 Independent (parallel) ◄── Requires Phase 2 complete
  │   ├── 3.1A get_quality_description (metadata.py)
  │   ├── 3.1B format_html_report (html.py)
  │   ├── 3.1C main (cli/main.py)
  │   └── 3.1D compare_quality (quality_compare.py) ◄── Uses 1.20
  │
  ├── 3.2 Sequential chain ◄── Requires 3.1 complete, uses 1.18-1.20
  │   ├── 3.2A build_disjoint_set ◄── No deps on other 3.2 items
  │   ├── 3.2B rationalize_duplicates ◄── Depends on 3.2A, uses 1.18
  │   ├── 3.2C determine_items_to_delete ◄── Uses 1.18, 1.19, 1.20
  │   ├── 3.2D process_duplicate_groups ◄── Depends on 3.2C
  │   └── 3.2E process_deletion_and_report ◄── Depends on 3.2D
  │
  └── 3.3 Remaining medium-complexity (parallel, after 3.2)
       │
PHASE 4: Polish ──────────────────────────────────────────
  ├── 4A Exception handling (3-5 fixes)
  ├── 4B Docker improvements
  ├── 4C CI/CD enhancement
  └── 4D Cleanup
```

**Critical Path:** Phase 0 → Phase 1.G → Phase 2.B (dedup tests) → Phase 3.2A → 3.2B → 3.2C → 3.2D → 3.2E

---

## Risk Register

| ID | Risk | Likelihood | Impact | Mitigation | Owner |
|----|------|-----------|--------|------------|-------|
| R1 | Metric mismatch (SonarQube CC vs Radon CC) causes wrong effort estimates | HIGH | HIGH | All planning uses SonarQube cognitive complexity only | All |
| R2 | `create_http_client()` removed as "dead" — breaks missing_episodes | HIGH | BLOCKER | **Removed from dead code list.** Only 2 dead functions remain. | Phase 1 |
| R3 | Refactoring CC=169 functions changes deduplication behavior (wrong items deleted) | MEDIUM | CRITICAL | Behavioral tests (Phase 2B) before any structural changes. Golden-file tests with known inputs/outputs. | Agent A |
| R4 | Tests written for monolithic functions become invalid after refactoring | MEDIUM | MEDIUM | Write behavioral tests (input→output), not structural tests (mock internals). These survive refactoring. | Agent C, D |
| R5 | SonarQube "new code" coverage trap — refactored code needs 80% coverage | HIGH | HIGH | Add unit tests for each extracted helper immediately after extraction. Budget 2-3 tests per helper. | All |
| R6 | DRY extractions (Phase 1.G) introduce coupling between previously independent modules | MEDIUM | MEDIUM | Place shared functions in `utils/` to maintain clean dependency direction. No circular imports. | Agent A |
| R7 | Variable shadowing bug at deduplication.py:1120 causes subtle image URL issues | HIGH | LOW-MEDIUM | Fix in Phase 0 (trivial rename). | Agent A |
| R8 | 12 intentional broad exception handlers get "fixed" — introduces new bugs | MEDIUM | MEDIUM | Document which handlers are intentional (see Phase 4A). Only fix the 5 identified as problematic. | Agent D |
| R9 | Effort underestimation for CC=162/169 functions | HIGH | HIGH | Budget 90-120 min per function (not 30-60 min from health report). Include test-writing time. | All |
| R10 | Missing functions in SonarQube scan — some Tier 2 functions don't exist in current codebase | MEDIUM | LOW | Verify each function exists before attempting refactoring. Skip non-existent ones. | Agent B |
| R11 | Debug code removal (IDs "99424"/"20131603") removes useful diagnostic capability | LOW | LOW | Replace with conditional debug logging (`if logger.isEnabledFor(DEBUG)`) if future debugging needed. | Agent A |

### Go/No-Go Criteria

**Proceed when:**
- All tests pass (272/272 baseline, increasing as tests added)
- No new SonarQube BLOCKER/CRITICAL issues introduced
- Git working tree clean (can revert any phase)
- Phase dependency tasks complete

**STOP and reassess when:**
- More than 3 tests break from a single refactoring step
- CC reduction < 30% after extracting helpers (strategy isn't working)
- New SonarQube issues appear after changes
- Test coverage drops below current 56%

**Escalate to user when:**
- Refactoring requires changing public CLI interface
- Function behavior must change to reduce complexity (not just structure)
- Test coverage can't reach 80% without mocking internals
- Estimated remaining effort exceeds 2x plan

---

## Verification Checklist

### After Phase 0:
- [ ] `make test` → 272 tests pass
- [ ] S3516 BLOCKER resolved
- [ ] S5655 type mismatches resolved
- [ ] Variable shadowing fixed

### After Phase 1:
- [ ] `make test` → 272 tests pass (minus removed dead code tests)
- [ ] `make sonar` → issue count drops from 48 to ~26
- [ ] Debug code removed from deduplication.py
- [ ] Shared utilities extracted (episode regex, language map, smart override)
- [ ] All quick-win SonarQube issues resolved

### After Phase 2:
- [ ] `make test` → ~400+ tests pass
- [ ] `make coverage` → 70%+ overall
- [ ] 4 new test files created and passing
- [ ] Safety net tests for all Grade-F functions
- [ ] All behavioral tests are input→output (not structural)

### After Phase 3:
- [ ] `make test` → ~420+ tests pass
- [ ] `make sonar` → 0 issues, quality gate PASSES (exit code 0)
- [ ] All functions < SonarQube CC 15
- [ ] Unit tests for every extracted helper function
- [ ] Coverage ≥ 80% overall
- [ ] No behavioral changes (verified by Phase 2 tests)

### After Phase 4:
- [ ] `make sonar` → clean
- [ ] `make lint` → clean
- [ ] `make mypy` → clean (or improved)
- [ ] Docker build succeeds with .dockerignore and HEALTHCHECK
- [ ] No untracked debug files in repo root
- [ ] All 5 problematic exception handlers narrowed

---

## Recommended Execution Team Structure

### For Implementation (Phase 1-4):

| Role | Model | Files Owned | Primary Tasks |
|------|-------|-------------|---------------|
| **Agent A: Deduplication Specialist** | **Opus** | deduplication.py, disjoint_set.py, constants.py | Phase 0 (blocker), Phase 1E-G (debug removal, DRY), Phase 3.2 (the big chain) |
| **Agent B: API & Metadata Specialist** | Sonnet | metadata.py, client.py, search.py, quality_compare.py | Phase 1A-C (quick fixes), Phase 3.1A+D (independent refactoring), Phase 3.3 |
| **Agent C: Test Specialist** | Sonnet | checker.py, missing_episodes.py (api), new test files | Phase 2A (4 new test suites), Phase 2B (safety net tests) |
| **Agent D: CLI & Reports Specialist** | Sonnet | cli/*.py, reports/*.py | Phase 0.2-0.3 (type fixes), Phase 1D (dead code), Phase 3.1B-C, Phase 4 |

### Execution Phases by Agent:

```
Timeline:  ──Phase 0──│──Phase 1──│──Phase 2──│──Phase 3──│──Phase 4──
Agent A:   Fix blocker │ Debug+DRY │  (wait)   │ dedup.py  │  (done)
Agent B:   (wait)      │ Quick fix │  (wait)   │ meta+QC   │  (done)
Agent C:   (wait)      │  (wait)   │ ALL tests │ test help │  (done)
Agent D:   Fix types   │ Dead code │ CLI tests │ cli+rpts  │ Polish
```

### Plan Approval:
- **Agent A** should require plan approval for Phase 3.2 (the deduplication chain) — highest risk
- **Agents B, C, D** can operate autonomously with `make test` verification after each change

---

## Appendix A: Complete SonarQube Issue Inventory (48 Issues)

### By Severity:
- **BLOCKER (1):** S3516 in api/missing_episodes.py:17
- **CRITICAL (34):** 26x S3776 (cognitive complexity), 3x S1192 (duplicate strings), 2x S5655 (type mismatch), 1x S5754 (bare except)
- **MAJOR (8):** 2x S107 (too many params), 1x S3457 (empty f-string), 2x S5886 (return type), 3x S6546 (union type)
- **MINOR (5):** 1x S5713 (redundant exception), 2x S1481 (unused vars), 1x S7504 (unnecessary list)

### By File (Hotspots):
1. `api/deduplication.py` — 8 issues
2. `api/missing_episodes.py` — 6 issues
3. `api/quality_compare.py` — 5 issues
4. `api/checker.py` — 4 issues
5. `api/client.py` — 4 issues
6. All other files — 21 issues across 9 files

## Appendix B: Overlap Between SonarQube and Health Report

| SonarQube Issue | Health Report Match | Double-Count? |
|----------------|-------------------|---------------|
| 7 S3776 issues on Grade-F functions | R2-R6, R9, R3 | YES — same functions, same problem |
| 19 additional S3776 issues (CC 16-64) | Not in health report | NO — net new |
| S5754 bare except | R8 (19 broad exceptions) | Partial overlap |
| S5655 type mismatches | R10 (type hints) | Partial overlap |
| S1192 duplicate strings | Not in health report | NO — net new |
| Dead code `compare_dates` | R11 (dead functions) | YES — same finding |

**Net unique action items:** 48 SonarQube + 15 health report - 10 overlapping = **53 unique items**

---

## Appendix C: Devil's Advocate Late-Stage Refinements

These additional findings were submitted after the initial synthesis. They refine (not change) the plan.

### C1: BLOCKER S3516 — Detailed Verdict

The function `enrich_episodes_with_series_metadata()` at `api/missing_episodes.py:17` uses **in-place mutation** of list items (adds `SeriesName` and `OriginalSeriesName` to each dict). SonarQube is technically correct that every path returns the same variable. This is NOT a runtime bug — it's an intentional Python idiom for method chaining.

**Recommended fix for Phase 0:** Change return type to `None`, remove the redundant return, update callers. OR mark as accepted in SonarQube since this is a 0% coverage module where caller verification is risky.

### C2: Test Strategy Refinements

The devil's advocate challenged the test count estimate (132-155) as over-estimated. Key arguments:

1. **Over-mocking risk:** `api/checker.py` needs 9+ mock layers per test. Tests end up testing mock wiring, not logic. **Recommendation:** Use fixture factories and `respx`/`pytest-httpx` for HTTP mocking instead of patching individual functions.

2. **Revised test count:** 80-100 well-designed behavioral tests may be more valuable than 155 structural tests. Behavioral tests (input→output) survive refactoring; structural tests (mock internals) break when helpers are extracted.

3. **Missing test categories identified:**
   - **Golden-file/snapshot tests** for the deduplication pipeline (input→full output capture)
   - **Error injection tests** for 0% coverage modules (HTTP errors, timeouts, malformed responses)
   - **Regression test** for variable shadowing bug at deduplication.py:1120

4. **75% milestone:** Only 5 additional statements beyond the 4 untested modules are needed to reach 75%. Consider targeting 75% as an intermediate milestone before pushing to 80%.

### C3: Effort Estimate Revision

| Phase | Original Estimate | Devil's Advocate Estimate | Reason |
|-------|------------------|--------------------------|--------|
| Phase 0 | 30 min | 30 min | Agreed |
| Phase 1 | 2 hours | 2 hours | Agreed |
| Phase 2 | 8-10 hours | 6-8 hours (80-100 tests vs 132-155) | Fewer but better tests |
| Phase 3 | 8-12 hours | 15-20 hours | CC=169 and CC=162 each need 8-12 helper extractions, not 4-5 |
| Phase 4 | 2-3 hours | 2-3 hours | Agreed |
| **Total** | **18-25 hours** | **25-35 hours** | Phase 3 is the main underestimate |

### C4: Safe Early Refactoring (Can Proceed in Parallel with Phase 2)

These specific changes are safe WITHOUT additional tests because existing 272 tests cover the behavior:

1. Remove hardcoded debug IDs ("99424", "20131603") — pure deletion of logging
2. Extract duplicated regex patterns into utility — mechanical extraction
3. Fix variable shadowing at line 1120 — single variable rename
4. Extract language normalization constant — pure constant extraction

This means **Phase 1.E, 1.G, and Phase 0.4 can run in parallel with Phase 2** test writing, reducing the critical path timeline.

---

## Appendix D: Decomposition Order Refinement (Devil's Advocate Final Review)

### D1: Revised 11-Step Decomposition Order

Steps 7-8 of the original 10-step order have a **cross-module dependency**: `determine_items_to_delete()` and `compare_quality()` share duplicated smart-override logic. They MUST be refactored together. Revised order:

1. Remove debug code (IDs "99424", "20131603")
2. Extract `_extract_episode_key_from_path()`
3. Extract `LANGUAGE_NORMALIZATION_MAP` constant (**use 8-entry superset** — see D3)
4. Refactor `get_quality_description` (isolated)
5. Refactor `build_disjoint_set`
6. Refactor `format_html_report` (isolated)
7. Refactor `determine_items_to_delete` **AND extract shared smart-override logic**
8. **Immediately refactor `compare_quality`** using shared helper from step 7
9. Refactor `rationalize_duplicates`
10. Refactor `process_duplicate_groups`
11. Refactor `main()` (last — it's the orchestrator)

### D2: Smart Language Override — Shared Helper Design

The duplicated logic operates on **different data types** (dicts vs ExistingQuality objects). Extract as a pure function taking pre-computed values:

```python
def should_quality_override_language(
    quality_ratio: float,
    lang_item_has_priority_lang: bool,
    quality_item_has_priority_lang: bool,
    is_single_lang_scenario: bool
) -> bool:
```

This avoids coupling data types while eliminating the duplicated decision logic (~60 lines).

### D3: Language Normalization Maps Are NOT Identical

The 4 duplicate locations have **different entry counts**:
- `cli/main.py:111-117` — 6 entries
- `api/deduplication.py:611-618` — **8 entries** (includes "slovak" and "czech" full names)
- `api/quality_compare.py:818` — 7 entries
- `api/quality_compare.py:938` — 7 entries

**The shared constant MUST use the 8-entry superset** from deduplication.py, or the full-name mappings ("slovak"→"sk", "czech"→"cs") will be lost.

### D4: Redundant Union Pass in build_disjoint_set

Pass 1 (lines 96-112) and Pass 2 (lines 148-169) both union the same items. **Recommended fix:** Keep Pass 1 for **parent initialization only** (ensure all items have entries in the DisjointSet). Remove the redundant `ds.union()` calls from Pass 1. Pass 2's unions are needed for the TV episode and movie grouping logic.

### D5: Debug Code Removal Caveat

At `build_disjoint_set()` lines 153-169, debug code is **interleaved with the union logic**. The `target_in_items` variable and its detection loop can be safely removed, but the `for item in items[1:]` loop with `ds.union()` calls **MUST be preserved**. Review the diff carefully after removal.

### D6: Revised Risk Ratings

| Function | Original Rating | Revised Rating | Reason |
|----------|----------------|---------------|--------|
| `build_disjoint_set` | Medium | **Medium-High** | Redundant double-union pass — need to understand which is needed |
| `main()` | Medium | **Low-Medium** | Mechanical extraction, no tricky algorithms |
| All others | Unchanged | Unchanged | — |
