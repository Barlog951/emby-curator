# Agent Team Prompt: Emby-Dedupe Execution — Full Remediation

## How to Use This Prompt

**Launch from the project directory:**
```bash
cd /Users/dodko/DEV/emby-dedupe
claude
```

**Then paste the prompt below (everything between the `---` markers) into Claude Code:**

---

## THE PROMPT

Create an agent team to execute the full remediation of the emby-dedupe repository. The detailed plan is in `REMEDIATION_PLAN.md` — read it first. This team IMPLEMENTS the plan. Every agent must read `REMEDIATION_PLAN.md` before starting work.

### Goal

Fix all 48 SonarQube issues + all health report recommendations. End state:
- 0 SonarQube issues, quality gate PASSES
- 0 Grade-F functions, all functions < cognitive complexity 15
- Test coverage 80%+
- ~420+ tests passing
- Clean `make sonar`, `make lint`, `make mypy`

### Team Structure — Spawn 4 teammates:

**Model assignments:** Opus for the hardest refactoring, Sonnet for everything else.

| Role | Model | Files Owned | Phases |
|------|-------|-------------|--------|
| Agent A: `dedup-specialist` | **Opus** | deduplication.py, disjoint_set.py, utils/constants.py | P0 blocker, P1 debug+DRY, P3 dedup chain |
| Agent B: `api-specialist` | Sonnet | metadata.py, client.py, search.py, quality_compare.py | P1 quick fixes, P3 independent refactors |
| Agent C: `test-specialist` | Sonnet | checker.py, missing_episodes.py (api), new test files | P2 all new tests + safety net |
| Agent D: `cli-reports-specialist` | Sonnet | cli/*.py, reports/*.py | P0 type fixes, P1 dead code, P2 CLI tests, P3 cli+reports, P4 polish |

### ABSOLUTE RULES — ZERO EXCEPTIONS (ALL AGENTS MUST FOLLOW):

**QUALITY GATE ENFORCEMENT — ALL FOUR MUST PASS:**
1. **`make test`** — ALL tests must pass at ALL times. Run after every single change. If tests break, fix IMMEDIATELY. A broken test suite blocks ALL agents.
2. **`make lint`** — ruff must be clean. No new lint issues permitted. Run after every batch of changes.
3. **`make mypy`** — type checking must be clean (or improving). No new type errors permitted.
4. **`make sonar`** — SonarQube quality gate must pass (exit code 0). ZERO new SonarQube issues (security hotspots exempt).

**The full quality gate command sequence at every phase gate:**
```bash
make test && make lint && make mypy && make sonar
```
**ALL FOUR must pass. If ANY fails, the phase is NOT complete — go back and fix.**

**SECURITY HOTSPOT EXCEPTION:**
5. **Security hotspots are EXCLUDED from the zero-new-issues rule** — the user will review these manually in a separate session. Do NOT spend time fixing security hotspots. Do NOT mark them as resolved. Leave them for the user.

**NO COMMITS — ABSOLUTELY NONE:**
6. **NEVER run `git commit`, `git add`, or any git write operation** — no commits, no staging, no branches. ALL changes stay as uncommitted working tree modifications. The user will make ONE big commit after all remediation is reviewed and approved. This is NON-NEGOTIABLE.
7. **NEVER use `--no-verify`** — this rule exists even though you won't commit, to prevent accidents.

**CODE RULES:**
8. **Read `REMEDIATION_PLAN.md` FIRST** — it has exact file:line locations, fix descriptions, and risk warnings
9. **NEVER edit files you don't own** — check the ownership map above. If you need a change in another agent's file, message the lead.
10. **Write behavioral tests (input→output), NOT structural tests** — they must survive refactoring (Risk R4)
11. **Every extracted helper needs 2-3 unit tests immediately** — refactored code = "new code" in SonarQube, needs 80% coverage (Risk R5)
12. **NEVER remove `create_http_client()` from api/client.py** — it's used by api/missing_episodes.py:135, NOT dead code (Risk R2)
13. **Use `mcp__claude-context__search_code`** for understanding code before changing it

**VERIFICATION CADENCE:**
```
After each individual fix:     make test                                    (must pass)
After batch of fixes:          make test && make lint                       (must pass)
After completing a file:       make test && make lint && make mypy          (must pass)
After completing a phase:      make test && make lint && make mypy && make sonar   (ALL must pass)
Before reporting to lead:      make test && make lint                       (must pass)
```

### Shared Files Protocol:
- `emby_dedupe/utils/constants.py` — Agent A writes shared constants (language map, episode regex). Other agents READ ONLY after Agent A signals completion.
- `conftest.py` — Any agent may add fixtures, but message lead first to avoid conflicts.
- No other file should be edited by more than one agent.

---

### Phase 0: Immediate Blockers (Agent A + Agent D in parallel)

**Must complete before any other work. ~30 minutes.**

**Agent A — Fix blocker + shadowing bug:**
- Fix 0.1: `api/missing_episodes.py:17` — S3516 BLOCKER. `enrich_episodes_with_series_metadata()` always returns same value. The function uses in-place mutation (intentional). Fix: change return type to `None`, remove redundant return, update callers. (15 min)
  - WARNING: This is a 0% coverage module. Be extra careful verifying callers. Search for all calls to this function before changing.
- Fix 0.4: `api/deduplication.py:1120` — Variable shadowing bug. Inner `for decision in decisions` shadows outer loop variable. Rename inner to `candidate`. (2 min)
- Run `make test` → verify 272 tests pass

**Agent D — Fix type mismatches:**
- Fix 0.2: `cli/main.py:94` — S5655. `override_warning()` receives wrong type from `logger.level`. Cast to `str()` or update signature. (5 min)
- Fix 0.3: `cli/missing_episodes.py:294` — S5655. Same pattern as 0.2. Fix consistently. (5 min)
- Run `make test` → verify 272 tests pass

**Lead:** Wait for both to report success. Run full gate check:
```bash
make test && make lint && make mypy && make sonar
```
All four must pass. Issue count must be ≤ 48 (security hotspots exempt). Then proceed to Phase 1.

---

### Phase 1: Quick Wins (Agents A, B, D in parallel | Agent C waits)

**Goal: Resolve 17+ SonarQube issues. ~2 hours.**

**Agent A — Debug removal + DRY extractions (HIGHEST VALUE):**
- Fix 1.5: `api/deduplication.py:220` — S7504. Remove unnecessary `list()` wrapper. Verify no dict mutation during iteration first. (3 min)
- Fix 1.15: Remove ALL hardcoded debug references to item IDs `"99424"` and `"20131603"` from `api/deduplication.py`. Lines ~153-169, ~191-212, ~305-320, etc. This removes ~100 lines and reduces CC by 30-40 points.
  - **CRITICAL WARNING (from plan D5):** At `build_disjoint_set()` lines 153-169, debug code is INTERLEAVED with union logic. The `target_in_items` variable and detection loop CAN be removed, but the `for item in items[1:]` loop with `ds.union()` calls MUST be preserved. Review every deletion carefully.
- Fix 1.18: Extract `_extract_episode_key_from_path()` — shared regex used by `rationalize_duplicates` and `determine_items_to_delete`. Place in `emby_dedupe/utils/constants.py` or as module-level function in deduplication.py. (10 min)
- Fix 1.19: Extract `LANGUAGE_NORMALIZATION_MAP` to `emby_dedupe/utils/constants.py`. **MUST use the 8-entry superset** from `api/deduplication.py:611-618` which includes `"slovak"→"sk"` and `"czech"→"cs"` full-name entries. The other 3 locations have fewer entries (6, 7, 7). Replace all 4 usages. (10 min)
  - Locations: `cli/main.py:111-117`, `api/deduplication.py:611-618`, `api/quality_compare.py:818`, `api/quality_compare.py:938`
  - For files you don't own (cli/main.py, quality_compare.py): message Agent D and Agent B to update their imports after you publish the constant.
- Fix 1.20: Extract shared `should_quality_override_language()` function. Use the pure-function signature from plan D2:
  ```python
  def should_quality_override_language(
      quality_ratio: float,
      lang_item_has_priority_lang: bool,
      quality_item_has_priority_lang: bool,
      is_single_lang_scenario: bool
  ) -> bool:
  ```
  Place in `emby_dedupe/utils/constants.py`. (10 min)
- Fix 1.1: `api/deduplication.py:671` — S3457. Remove `f` prefix from string with no interpolation. (1 min)
- Fix 1.10: `api/deduplication.py:1038` — S5886. Fix return type annotation. (5 min)
- Run `make test` after each batch of changes. Message lead + Agent B + Agent D when shared constants are ready.

**Agent B — Quick fixes in owned files:**
- Fix 1.2: `api/client.py:266` — S5713. Remove redundant `Exception` from `except (EmbyServerConnectionError, Exception)`. (2 min)
- Fix 1.3: `api/client.py:372` — S1481. Replace unused `user_id` with `_`. (2 min)
- Fix 1.11: `api/client.py:462` — S5886. Fix return type annotation. (5 min)
- Fix 1.8: `api/search.py:93` — S1192. Extract long Fields string to `SEARCH_FIELDS` constant. (5 min)
- Wait for Agent A to signal shared constants are ready, then:
  - Update `api/quality_compare.py` imports to use `LANGUAGE_NORMALIZATION_MAP` from utils/constants.py (both line 818 and 938)
  - Update `api/quality_compare.py` to use shared `should_quality_override_language()`
- Run `make test` after each batch.

**Agent D — Dead code + misc fixes:**
- Fix 1.13: `reports/html.py:16` — Remove dead `compare_dates()` function AND its tests. (5 min)
- Fix 1.14: `utils/file_ops.py:82` — Remove dead `read_json_file()` function AND its tests. (5 min)
  - NOTE: Agent D does not own utils/file_ops.py. Message lead for permission or ask Agent A/B.
- Fix 1.4: `api/missing_episodes.py:136` — S1481. Replace unused `auth_token` with `_`. (2 min)
  - NOTE: This file is owned by Agent C. Coordinate via lead.
- Fix 1.6: `models/disjoint_set.py:18,36` — S6546 (x3). Change `Union[str, dict]` to `str | dict`. (3 min)
  - NOTE: This file is owned by Agent A. Coordinate via lead.
- Fix 1.9: `reports/markdown.py:199` — S1192. Extract `"Items to Delete"` to constant. (5 min)
- Fix 1.12: `api/missing_episodes.py:46` — S5754. Change bare `except:` to specific exceptions. (3 min)
  - NOTE: This file is owned by Agent C. Coordinate via lead.
- Wait for Agent A to signal shared constants are ready, then:
  - Update `cli/main.py` imports to use `LANGUAGE_NORMALIZATION_MAP` from utils/constants.py
- Run `make test` after each batch.

**Lead — Coordinate cross-ownership fixes:**
- Some Phase 1 fixes touch files owned by other agents. Route these:
  - 1.4 + 1.12 (missing_episodes.py) → Agent C owns this file. Either Agent C does these, or grant Agent D one-time access.
  - 1.6 (disjoint_set.py) → Agent A owns this. Either Agent A does it, or grant Agent D one-time access.
  - 1.14 (file_ops.py) → Not clearly owned. Assign to whichever agent is free.
- **PHASE 1 GATE:** After all agents report Phase 1 complete:
  ```bash
  make test && make lint && make mypy && make sonar
  ```
  - All four must pass. SonarQube issue count must drop (expected: 48 → ~26).
  - If ANY new issues (lint, mypy, or sonar) → identify which agent introduced them, send back to fix.
  - Only proceed to Phase 2 after all four checks pass (security hotspots exempt from sonar count).

---

### Phase 2: Test Safety Net (Agent C primary + Agent D for CLI tests | Agents A, B can do safe early refactoring)

**Goal: Write tests for 4 untested modules + safety net for Grade-F functions. ~6-8 hours.**
**Target: Coverage 56% → 70%+, test count 272 → ~370+**

**IMPORTANT: Phase 1.E, 1.G, and Phase 0.4 safe changes can run IN PARALLEL with Phase 2** (per plan Appendix C4). If Agent A hasn't finished all Phase 1 work, they can continue during Phase 2.

**Agent C — New test suites for 0% coverage modules:**

Write ALL tests as behavioral (input→output), not structural. Use `respx` or `pytest-httpx` for HTTP mocking instead of patching individual functions (plan recommendation C2).

1. `tests/unit/api/test_checker.py` (28-32 tests, target 80% of 228 statements)
   - Read `api/checker.py` first using `mcp__claude-context__search_code`
   - Look at existing test patterns in `tests/` for consistency (fixtures, conftest, assertions style)
   - Mock: httpx.Client, fetch_and_process_media_items, search_media, compare_quality, file I/O via tmp_path, time.time for TTL
   - Categories: init (3), cache ops (6), provider table (5), provider lookup (3), check flow (5), should_download + batch + context manager (4), error handling (3)
   - Fix 1.16 + 1.17 while writing tests: Create `CheckConfig` dataclass to bundle the 16 and 14 params

2. `tests/unit/api/test_missing_episodes.py` (25-28 tests, target 80% of 218 statements)
   - Mock: make_http_request, tqdm suppressed, sample episode/series dicts as fixtures
   - Categories: analyze_missing_episodes (5), get_missing_episodes (5), get_missing_episodes_alternative (3), per-series/season/episode (5), enrich_episodes (2), process_for_libraries (3), error paths (3)

3. Safety net tests for Grade-F functions in deduplication.py (add to existing test files):
   - `process_duplicate_groups`: 6-8 behavioral tests (empty groups, single-item, equal quality, language priority, provider exclusion)
   - `rationalize_duplicates`: 5-6 tests (empty providers, single type, cross-provider, no provider IDs)
   - `build_disjoint_set`: 4-5 tests (direct unit test, chain merges, self-refs, empty input)
   - `determine_items_to_delete`: 5-7 tests (language priority, exclude IDs, equal quality tie-breaking, single item)
   - Add regression test for variable shadowing bug at deduplication.py:1120

Run `make test` after each test file is complete. Message lead with progress.

**Agent D — CLI test suites:**

4. `tests/unit/cli/test_check.py` (15-18 tests, target 80% of 98 statements)
   - Mock: EmbyChecker class, Config.from_cli_args, capsys for output, argparse.Namespace
   - Categories: param extraction (6), output formatting json/simple/exit_code (3), run_check flow (5), resource cleanup (2)

5. `tests/unit/cli/test_missing_episodes_cli.py` (22-25 tests, target 80% of 176 statements)
   - Mock: check_emby_connection, get_library_id, process_missing_episodes_for_libraries, tmp_path, datetime.now, capsys
   - Categories: pure functions generate_default_filename/format_structured_json/format_report (10), file I/O (3), CLI entry (5), arg parsing (2)

6. Safety net tests for Grade-F functions in owned files:
   - `format_html_report`: 4-5 behavioral tests (missing quality_description, callable languages, empty decisions, unicode)
   - `main()` in cli/main.py: 8-10 tests (full flow mocked, multiple libraries, HTML-only, error paths)

Run `make test` after each test file. Message lead with progress.

**Agent A + Agent B — Safe early work (parallel with Phase 2):**

Per plan Appendix C4, these changes are safe without additional tests:
- Agent A: Continue any unfinished Phase 1 work (debug removal, DRY extractions)
- Agent B: Can start reading and planning Phase 3 refactoring for their owned functions

**Lead — PHASE 2 GATE:**
After Agents C and D report test suites complete:
```bash
make test && make lint && make mypy && make sonar
```
- All four must pass. Run `make coverage` → verify 70%+ overall.
- Verify ~370+ tests all passing
- If coverage < 70%, ask Agent C to add more edge case tests
- If lint/mypy/sonar show new issues from test files → fix (e.g., unused imports, missing type hints in tests)
- Only signal Phase 3 start after all four checks pass clean.

---

### Phase 3: Complexity Reduction (All agents active)

**Goal: All functions < SonarQube cognitive complexity 15. ~15-20 hours.**
**Prerequisites: Phase 2 MUST be complete. All behavioral tests passing.**

**EXECUTION RULES FOR ALL AGENTS:**
1. One function at a time — refactor, `make test`, verify, proceed
2. Behavioral equivalence — input/output must NOT change
3. Add 2-3 unit tests for each extracted helper immediately
4. Run `make sonar` after completing each file (catch new issues early)
5. If > 3 tests break from one change → STOP, message lead, reassess

**MASTER DECOMPOSITION ORDER (from plan Appendix D1 — this overrides the Phase 3 tables in the main plan body):**

```
Step  1: [Agent A] Remove remaining debug code if not done in Phase 1
Step  2: [Agent A] Extract _extract_episode_key_from_path() if not done in Phase 1
Step  3: [Agent A] Extract LANGUAGE_NORMALIZATION_MAP (8-entry superset) if not done in Phase 1
Step  4: [Agent B] Refactor get_quality_description (metadata.py) — isolated, safe
Step  5: [Agent A] Refactor build_disjoint_set (deduplication.py) — PLAN APPROVAL REQUIRED
Step  6: [Agent D] Refactor format_html_report (reports/html.py) — isolated, safe
Step  7: [Agent A] Refactor determine_items_to_delete AND extract shared smart-override logic — PLAN APPROVAL REQUIRED
Step  8: [Agent B] IMMEDIATELY refactor compare_quality using shared helper from step 7
Step  9: [Agent A] Refactor rationalize_duplicates — PLAN APPROVAL REQUIRED
Step 10: [Agent A] Refactor process_duplicate_groups — PLAN APPROVAL REQUIRED
Step 11: [Agent D] Refactor main() (cli/main.py) — last, it's the orchestrator
```

**Steps 4, 6 can run in parallel** (independent functions in different files).
**Steps 7 + 8 are coupled** — Agent A must finish step 7 before Agent B starts step 8.
**Steps 5, 7, 9, 10 require plan approval** — Agent A presents plan to lead before implementing.

**Agent A — Deduplication chain (Steps 1-3, 5, 7, 9, 10):**

**Require plan approval before implementing steps 5, 7, 9, 10.**

For each function, follow the extraction tables in `REMEDIATION_PLAN.md` Phase 3:

- Step 5: `build_disjoint_set` (CC ~130 after debug removal → target < 15)
  - Extract: `_initialize_disjoint_set`, `_classify_items_as_tv_or_movie`, `_union_episode_groups`, `_union_movie_groups`
  - WARNING (D4): Pass 1 (lines 96-112) and Pass 2 (lines 148-169) both union same items. Keep Pass 1 for parent initialization only. Remove redundant `ds.union()` from Pass 1. Pass 2 unions are needed.
  - Risk: Medium-High (revised per D6)

- Step 7: `determine_items_to_delete` (CC ~100 → target < 15) + extract shared `should_quality_override_language()`
  - Extract: `_group_items_by_episode_path`, `_deduplicate_by_path`, `_calculate_language_scores`, `_record_language_decision`
  - CRITICAL: Quality ratio thresholds (1.5x, 3x) are critical to behavior — preserve exactly
  - After extraction, message Agent B that shared smart-override helper is ready for step 8

- Step 9: `rationalize_duplicates` (CC ~135 → target < 15)
  - Extract: `_collect_items_metadata`, `_group_by_disjoint_root`, `_verify_movie_group`, `_verify_tv_series_group`

- Step 10: `process_duplicate_groups` (CC ~110 → target < 15)
  - Extract: `_build_exclusion_map`, `_check_group_exclusion`, `_extract_excluded_item_info`, `_enrich_keep_item`, `_enrich_delete_item`

After each function: `make test` → all pass. Add 2-3 tests per extracted helper. Message lead so they can run `make sonar` to verify no new issues.

Also handle step 3.2E from the plan: `process_deletion_and_generate_report` (CC 44 → < 15)

**Agent B — Independent refactors (Steps 4, 8) + remaining medium-complexity:**

- Step 4: `get_quality_description` in `api/metadata.py` (CC 100 → target < 15)
  - Extract: `_format_file_size`, `_parse_iso_date`, `_resolve_date_added`, `_extract_premiere_date`, `_build_tv_metadata`
  - Safe — pure data extraction, no side effects. 45-60 min.

- Step 8: `compare_quality` in `api/quality_compare.py` (CC 64 → target < 15)
  - Wait for Agent A to complete step 7 and signal shared helper is ready
  - Extract: `_create_proposed_as_existing`, `_apply_bluray_native_exception`
  - Use shared `should_quality_override_language()` from utils/constants.py

- After steps 4 + 8, work on remaining medium-complexity in owned files (Phase 3.3):
  - `search_media` (CC 25), `fetch_and_process_media_items` (CC 29), `build_provider_id_tables` (CC 21)
  - `compare_video_streams` (CC 32), `compare_media_streams` (CC 33), `apply_language_priority` (CC 19), `ExistingQuality.from_emby_item` (CC 16)
  - Also: `get_quality_description` #2 at metadata.py:257 (CC 25)

**Agent C — Test support during Phase 3:**

- As agents extract helpers, write unit tests for each new helper function (2-3 tests each)
- Monitor coverage: run `make coverage` periodically, identify gaps
- Focus on deduplication.py helpers (Agent A's extractions) since that's the highest-risk code
- Add golden-file/snapshot tests for the deduplication pipeline if time permits (plan C2 recommendation)

**Agent D — CLI + Reports refactors (Steps 6, 11) + remaining:**

- Step 6: `format_html_report` in `reports/html.py` (CC 93 → target < 15)
  - Extract: `_validate_decisions`, `_detect_language_priority_usage`, `_ensure_quality_fields`, `_process_delete_item`, `_create_language_priority_message`, `_process_decision_group`
  - Safe — pure data transformation. 45-60 min.

- Step 11: `main()` in `cli/main.py` (CC 71 → target < 15)
  - Extract: `_resolve_configuration`, `_parse_language_priorities` (uses shared LANGUAGE_NORMALIZATION_MAP), `_connect_and_fetch_libraries`, `_run_deduplication_pipeline`, `_generate_reports`
  - Risk: Low-Medium (revised per D6). Mechanical extraction.

- Remaining medium-complexity in owned files (Phase 3.3):
  - `format_missing_episodes_report` (CC 38), `run_missing_episodes_command` (CC 26)
  - `calculate_report_statistics` (CC 21), `format_markdown_table` (CC 24), `format_deleted_items_table` (CC 19)

**Lead — Phase 3 coordination + SONAR GATE ENFORCEMENT:**

- Review and approve Agent A's plans for steps 5, 7, 9, 10 before they implement
- Approval criteria: extracted functions have clear single responsibility, target CC < 15, no behavioral changes, test plan included
- After Agent A completes step 7: signal Agent B to start step 8
- **MANDATORY: Run full gate check after EACH completed file** (not just at the end):
  ```bash
  make test && make lint && make mypy && make sonar
  ```
  - All four must pass. Issue counts must be DECREASING or stable — never increasing.
  - If new issues appear (lint, mypy, or sonar) → send agent back to fix before they move to next function
  - Security hotspots are exempt from sonar count — ignore them, user will review separately
- If any agent reports > 3 broken tests: STOP all work, assess, potentially revert
- **No agent proceeds to next function until all four checks pass for their current file**

---

### Phase 4: Polish & Hardening (Agent D primary, others assist)

**Goal: Clean up remaining issues. ~2-3 hours.**

**Agent D — Primary:**
- 4A: Narrow 5 problematic exception handlers:
  - `api/metadata.py:128` → `except OSError`
  - `api/metadata.py:140` → `except (ValueError, TypeError, OSError)`
  - `reports/html.py:61` → Remove entirely (unreachable)
  - (4.4 and 4.5 already handled in earlier phases)
  - **DO NOT change the 14 intentional broad handlers** — they are documented in REMEDIATION_PLAN.md Phase 4A
- 4B: Add `.dockerignore` (exclude tests/, docs/, .git/, debug scripts, *.md, htmlcov/)
- 4B: Add `HEALTHCHECK` to Dockerfile
- 4C: Add `pip-audit` to `.github/workflows/security-scan.yaml`
- 4D: Fix `print("Download it!")` in `checker.py:21` → `logger.info()`
  - NOTE: checker.py is owned by Agent C. Coordinate.
- 4D: Clean up 38 untracked debug/test files from repo root

**Agent C — Support:**
- Fix debug print in checker.py:21 if Agent D can't access it
- Final coverage push: add any remaining tests needed to hit 80%

**Agent B — Support:**
- Address any remaining SonarQube issues in owned files
- Final type annotation cleanup

**Agent A — Done** unless remaining issues in deduplication.py

---

### Final Verification (Lead performs this)

After all agents report Phase 4 complete:

```bash
# THE FINAL CHECK — everything must pass
make sonar
```

This single command:
1. Runs ALL tests with coverage
2. Generates coverage.xml and test-results.xml
3. Runs SonarQube analysis
4. Waits for processing
5. Checks quality gate
6. **Exit 0 = SUCCESS | Exit 1 = FAILED**

**Full final verification (ALL must pass):**
```bash
make test && make lint && make mypy && make sonar
make sonar-all      # Should show 0 issues
make sonar-metrics  # Record final metrics
```

**Success criteria:**
- [ ] `make test` passes (~420+ tests)
- [ ] `make lint` clean (0 ruff issues)
- [ ] `make mypy` clean (0 type errors, or strictly fewer than baseline)
- [ ] `make sonar` exits 0 (quality gate PASSES)
- [ ] 0 SonarQube issues (security hotspots exempt — user reviews those separately)
- [ ] All functions < cognitive complexity 15
- [ ] Test coverage ≥ 80%
- [ ] No behavioral changes (all Phase 2 behavioral tests still pass)
- [ ] Docker build succeeds
- [ ] Git status shows ONLY uncommitted changes — no commits, no staged files, no branches created

**Report results to user. NEVER commit. NEVER stage. NEVER create branches. All work stays as working tree changes for one future big commit after user review.**

---

### Orchestrator (Lead, model: Sonnet) Instructions:

You coordinate the team. Use **delegate mode** (Shift+Tab) to stay focused on coordination.

**Your workflow:**

1. **Spawn all 4 agents** with their specific prompts. Tell each to read `REMEDIATION_PLAN.md` first.
   - `dedup-specialist` (Opus) — require plan approval mode
   - `api-specialist` (Sonnet)
   - `test-specialist` (Sonnet)
   - `cli-reports-specialist` (Sonnet)

2. **Phase 0:** Assign Agent A (blocker + shadowing) and Agent D (type fixes). Wait for both. Run `make test`.

3. **Phase 1:** Assign Agent A (debug+DRY), Agent B (quick fixes), Agent D (dead code+misc). Agent C waits.
   - Route cross-ownership fixes (see coordination notes in Phase 1 section)
   - When Agent A signals shared constants ready → notify Agent B and Agent D to update imports

4. **Phase 2:** Assign Agent C (api test suites + safety net) and Agent D (CLI test suites + safety net). Agent A can continue safe Phase 1 work. Agent B can start planning Phase 3 reads.
   - Checkpoint: `make coverage` → 70%+, `make test` → ~370+ passing

5. **Phase 3:** Follow the master decomposition order (Steps 1-11).
   - Review and approve Agent A's plans for steps 5, 7, 9, 10
   - Approval criteria: single responsibility per helper, CC < 15, behavioral equivalence, tests included
   - Signal Agent B when step 7 completes (for step 8)
   - Run `make sonar` after each completed file

6. **Phase 4:** Assign Agent D as primary. Others assist as needed.

7. **Final verification:** Run `make sonar` + full check suite. Report to user.

**Escalation: SonarQube Issue Fixer Agent (Opus)**
There is a pre-built agent at `.claude/agents/sonarqube-issue-fixer.md` — it's Opus-powered and knows the full SonarQube workflow (MCP tools, quality gate checks, fix patterns). If a Sonnet agent (B, C, or D) struggles with a SonarQube issue — can't reduce complexity, introduces new issues, or gets stuck on a rule they don't understand — **spawn the sonarqube-issue-fixer agent** to handle that specific fix. Use it as a targeted specialist, not a replacement. Give it the specific file:line and rule, let it fix and verify with `make test`, then hand the file back to the owning agent.

**Go/No-Go rules (ENFORCED BY `make sonar` AT EVERY GATE):**
- PROCEED when: all four checks pass (`make test && make lint && make mypy && make sonar`), issue counts decreasing, no new issues (security hotspots exempt)
- STOP when: > 3 tests break from one change, any of the four checks show new issues, CC reduction < 30% after extraction, coverage drops below 56%
- ESCALATE OPTIONS when an agent is stuck:
  1. **First:** Spawn `sonarqube-issue-fixer` agent for the specific issue (it's Opus, it can handle complex fixes)
  2. **Second:** Reassign the file to Agent A (Opus) if it's an architectural problem
  3. **Third:** Escalate to user if it requires a design decision
- ESCALATE to user when: public CLI interface must change, behavior must change, effort exceeds 2x estimate, security hotspot needs decision
- **NEVER proceed to next phase if `make sonar` fails** — fix first, then gate check again

**Git rules:**
- NEVER commit, stage, or create branches. All changes = uncommitted working tree modifications.
- The user will make one big commit after full review. This is final.

**Communication protocol:**
- Message individual agents, don't broadcast (saves tokens)
- When an agent is idle between phases, tell them to wait (don't let them start unauthorized work)
- If Agent A gets stuck on a CC=169 function, let them spend time on it — don't rush

---

## After Execution Completes

The team should produce:
1. All code changes (uncommitted)
2. A summary of what was fixed and what metrics changed
3. Final `make sonar` output showing quality gate status

The user will review and commit manually.

## Tips

- Use **in-process mode** (default) — works in any terminal
- Press **Shift+Up/Down** to select and message individual teammates
- Press **Ctrl+T** to toggle the shared task list
- If the lead starts coding instead of coordinating: "Use delegate mode, coordinate only"
- Agent A's deduplication work is the critical path — keep them unblocked
- If SonarQube processing is slow, agents can continue working while waiting
- The plan is comprehensive but not perfect — if an agent finds the plan's extraction doesn't work for a specific function, they should adapt and message the lead with the revised approach
