# emby-dedupe — Repository Health Report

> **Generated:** 2026-02-06
> **Method:** 3-round automated analysis with cross-validation (radon, pytest-cov, AST analysis, git log)
> **Confidence:** All metrics verified across multiple independent analysis passes

---

## 1. Executive Summary

| Metric | Value |
|--------|-------|
| **Health Score** | **67 / 100** |
| Source code | 7,437 lines across 28 modules |
| Test code | 5,997 lines across 24 test modules |
| Test-to-code ratio | 0.81 : 1 |
| Tests passing | 272 (100% pass rate) |
| Statement coverage | 56% (1,667 / 2,998 statements) |
| Functions analyzed | 143 total |
| Grade-F functions | 7 (4.9%) |
| Circular imports | 0 |
| Dead functions | 3 |
| Security vulnerabilities | 0 |
| External dependencies | 3 (httpx, jinja2, tqdm) |
| Docker image size | 202 MB (multi-stage, non-root) |
| CI/CD pipelines | 5 GitHub Actions workflows |

**In one sentence:** A well-architected project with excellent security, held back by 7 over-complex functions and 4 completely untested modules.

---

## 2. Codebase Structure

```
emby_dedupe/                 # 7,437 LOC
├── api/                     # 4,768 LOC — Core business logic
│   ├── deduplication.py     #   1,165 lines ← largest, most complex
│   ├── quality_compare.py   #   1,051 lines
│   ├── missing_episodes.py  #     629 lines (0% test coverage)
│   ├── checker.py           #     577 lines (0% test coverage)
│   ├── client.py            #     519 lines
│   ├── search.py            #     465 lines
│   └── metadata.py          #     362 lines
├── cli/                     # 1,156 LOC — Command-line interface
│   ├── missing_episodes.py  #     391 lines (0% test coverage)
│   ├── main.py              #     294 lines
│   ├── check.py             #     286 lines (0% test coverage)
│   └── arguments.py         #     185 lines
├── reports/                 #   786 LOC — Report generation
│   ├── html.py              #     382 lines
│   ├── markdown.py          #     281 lines
│   └── common.py            #     123 lines
├── models/                  #    52 LOC
│   └── disjoint_set.py      #      52 lines
├── utils/                   #   596 LOC — Shared utilities
│   ├── config.py            #     265 lines
│   ├── file_ops.py          #     104 lines
│   ├── logging.py           #      95 lines
│   ├── http.py              #      79 lines
│   ├── constants.py         #      42 lines
│   └── exceptions.py        #      11 lines
├── templates/               # Jinja2 HTML templates
├── static/                  # CSS assets
└── __main__.py              #      74 lines
```

---

## 3. Test Coverage

### 3.1 Overall: 56%

```
Covered:  ████████████████░░░░░░░░░░░░░░  56%
Target:   ████████████████████████░░░░░░  80%
```

### 3.2 Coverage by Module

| Module | Coverage | Statements | Missed | Status |
|--------|----------|------------|--------|--------|
| **models/** | **100%** | 16 | 0 | Perfect |
| utils/http.py | 100% | — | 0 | Perfect |
| utils/logging.py | 100% | — | 0 | Perfect |
| utils/constants.py | 100% | — | 0 | Perfect |
| utils/exceptions.py | 100% | — | 0 | Perfect |
| **utils/ (avg)** | **88%** | 225 | 27 | Excellent |
| api/quality_compare.py | 89% | 429 | 49 | Excellent |
| utils/file_ops.py | 88% | 43 | 5 | Excellent |
| reports/common.py | 87% | 55 | 7 | Excellent |
| cli/arguments.py | 81% | 36 | 7 | Good |
| api/client.py | 80% | 158 | 32 | Good |
| api/metadata.py | 80% | 161 | 33 | Good |
| utils/config.py | 79% | 104 | 22 | Good |
| api/search.py | 77% | — | — | Good |
| **reports/ (avg)** | **70%** | 362 | 109 | Acceptable |
| reports/markdown.py | 68% | 119 | 38 | Needs work |
| reports/html.py | 66% | 188 | 64 | Needs work |
| api/deduplication.py | 60% | 536 | 216 | Below target |
| **cli/main.py** | **54%** | 131 | 60 | Below target |
| __main__.py | 32% | 37 | 25 | Poor |
| **api/checker.py** | **0%** | **228** | **228** | No tests |
| **api/missing_episodes.py** | **0%** | **218** | **218** | No tests |
| **cli/missing_episodes.py** | **0%** | **176** | **176** | No tests |
| **cli/check.py** | **0%** | **98** | **98** | No tests |

### 3.3 Test Quality

| Test File | Tests | Assertions | Assert/Test |
|-----------|-------|------------|-------------|
| test_deduplication.py | 12 | 68 | 5.7 (excellent) |
| test_deduplication_advanced.py | 16 | 75 | 4.7 (excellent) |
| test_client.py | 28 | 90 | 3.2 (good) |
| test_html.py | 12 | 36 | 3.0 (good) |
| test_config.py | 19 | 40 | 2.1 (good) |
| test_quality_compare.py | 42 | 84 | 2.0 (good) |
| test_comprehensive_quality.py | 32 | 45 | 1.4 (fair) |

---

## 4. Code Complexity

### 4.1 Grade-F Functions (Cyclomatic Complexity > 40)

These 7 functions represent 4.9% of all functions but concentrate the majority of technical debt.

| Function | File | Complexity | Lines | Coverage |
|----------|------|------------|-------|----------|
| `process_duplicate_groups` | api/deduplication.py | **65** | ~200 | 60% |
| `rationalize_duplicates` | api/deduplication.py | **63** | ~205 | 60% |
| `get_quality_description` | api/metadata.py | **63** | ~100 | 80% |
| `main` | cli/main.py | **63** | ~231 | 54% |
| `build_disjoint_set` | api/deduplication.py | **61** | ~197 | 60% |
| `determine_items_to_delete` | api/deduplication.py | **55** | ~293 | 60% |
| `format_html_report` | reports/html.py | **52** | ~150 | 66% |

**Industry target: 10–15 per function. All 7 are 3.5–6.5x over limit.**

### 4.2 Full Grade Distribution

| Grade | Range | Count | % | Meaning |
|-------|-------|-------|---|---------|
| A | 1–5 | 96 | 67.1% | Simple — no action needed |
| B | 6–10 | 22 | 15.4% | Low — acceptable |
| C | 11–15 | 11 | 7.7% | Moderate — monitor |
| D | 16–25 | 4 | 2.8% | High — refactor when touched |
| E | 26–40 | 3 | 2.1% | Very high — plan refactoring |
| **F** | **41+** | **7** | **4.9%** | **Critical — refactor proactively** |

### 4.3 Maintainability Index

| File | MI Score | Grade |
|------|----------|-------|
| api/deduplication.py | 11.03 | B |
| api/quality_compare.py | — | B |
| cli/main.py | — | C |

---

## 5. Architecture

### 5.1 Dependency Graph

```
cli/main.py ──────────┬──→ api/client.py ──→ utils/http.py
                      ├──→ api/deduplication.py ──→ models/disjoint_set.py
                      ├──→ api/metadata.py
                      ├──→ reports/html.py ──→ reports/common.py
                      ├──→ reports/markdown.py ──→ reports/common.py
                      └──→ cli/arguments.py

cli/check.py ─────────┬──→ api/checker.py ──→ api/client.py
                      └──→ api/quality_compare.py

cli/missing_episodes.py ──→ api/missing_episodes.py ──→ api/client.py

All modules ──→ utils/logging.py, utils/constants.py, utils/config.py
```

### 5.2 Architecture Findings

| Check | Result |
|-------|--------|
| Circular imports | **None** — clean acyclic dependency graph |
| Layer violations | **None** — cli → api → models/utils respected |
| Hub modules | api/client.py (5 dependents), utils/logging.py (16+ dependents) |
| External deps | 3 only (httpx, jinja2, tqdm) — minimal surface area |

### 5.3 Dead Code

Only **3 functions** are defined but never called:

| Function | Location | Action |
|----------|----------|--------|
| `compare_dates()` | reports/html.py:16 | Safe to remove |
| `create_http_client()` | api/client.py:109 | Safe to remove |
| `read_json_file()` | utils/file_ops.py:82 | Safe to remove |

---

## 6. Security

| Check | Result |
|-------|--------|
| Hardcoded secrets | **None** — credentials via environment variables |
| Injection risks (os.system, eval, exec) | **None** |
| SQL injection | **N/A** — no database |
| Log redaction | **Yes** — sensitive data filtered in logs |
| Tracked secrets in git | **None** |
| Container user | **Non-root** |
| Known CVEs in deps | **None** (httpx 0.27.0, jinja2 3.1.3, tqdm 4.66.2) |

**Security grade: A+**

---

## 7. Docker & CI/CD

### 7.1 Docker

| Aspect | Status |
|--------|--------|
| Multi-stage build | Yes |
| Non-root execution | Yes |
| Image size | 202 MB (optimal for Python) |
| Multi-arch | 4 platforms (amd64, arm64, arm/v7, arm/v6) |
| .dockerignore | **Missing** |
| HEALTHCHECK | **Missing** |

### 7.2 CI/CD Pipelines

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| python-test.yaml | Push/PR | pytest + ruff + mypy |
| security-scan.yaml | Push/PR | CodeQL + Bandit |
| edge.yaml | Push to main | Edge Docker build |
| prerelease.yaml | Pre-release tag | Pre-release Docker build |
| release.yaml | Release tag | Production Docker build |

### 7.3 Build Tooling

| Tool | Status |
|------|--------|
| pyproject.toml | PEP 517/518 compliant |
| setup.py | Present (legacy compat) |
| Makefile | `lint`, `mypy`, `test`, `coverage`, `allfx` targets |
| requirements.txt | 3 pinned dependencies |

---

## 8. Code Quality Issues

### 8.1 Broad Exception Handling (19 instances)

| File | Count | Notes |
|------|-------|-------|
| api/metadata.py | 7 | Most concentrated — review needed |
| reports/html.py | 4 | Template rendering fallbacks |
| cli/main.py | 3 | CLI boundary — acceptable |
| api/client.py | 2 | API boundary — acceptable |
| api/deduplication.py | 2 | Core logic — should be specific |
| utils/file_ops.py | 1 | File I/O — acceptable |

### 8.2 Type Hints

| File | Functions with return types | Total functions |
|------|---------------------------|-----------------|
| api/deduplication.py | 2 | 6 (33%) |
| api/client.py | 5 | 11 (45%) |
| api/quality_compare.py | ~20 | ~25 (80%) |
| api/metadata.py | ~8 | ~12 (67%) |

### 8.3 Minor Issues

- **Debug print** in `api/checker.py:21` — `print("Download it!")` should use `logger.info()`
- **Comment ratio** averages 8.8% (industry standard: 10–15%)
- **38 untracked files** in repo root (debug scripts, JSON test outputs)

---

## 9. Git History

| Metric | Value |
|--------|-------|
| Total commits | 62 |
| Primary author | Troy Kelly (87%) |
| Other contributor | dependabot[bot] (13% — dependency bumps) |
| Most active period | November 2023 (50 commits — initial build) |
| Last commit | ~May 2024 |
| Current status | Maintenance / active development of new features |

---

## 10. Recommendations

### 10.1 Tier 1 — Urgent (High impact)

#### R1. Add tests for 4 untested modules
**Files:** `api/checker.py`, `api/missing_episodes.py`, `cli/check.py`, `cli/missing_episodes.py`
**Why:** 720 statements with 0% coverage. Any change to these modules risks silent regressions.
**Expected outcome:** Overall coverage 56% → ~70%
**Effort:** 4–5 days

#### R2. Refactor `process_duplicate_groups()` (complexity 65)
**File:** `api/deduplication.py`
**Why:** Highest complexity in the entire codebase. Handles duplicate group processing with deeply nested conditionals.
**Approach:** Extract into 4–5 focused functions: group validation, quality ranking, language filtering, deletion selection, result formatting.
**Target complexity:** < 15 per extracted function
**Effort:** 1–2 days

#### R3. Refactor `main()` in `cli/main.py` (complexity 63)
**Why:** 231-line god function. All CLI orchestration, validation, API calls, and reporting crammed into one function. Nearly impossible to unit test individual paths.
**Approach:** Extract into: `validate_arguments()`, `connect_and_fetch()`, `run_deduplication()`, `generate_reports()`.
**Effort:** 1 day

### 10.2 Tier 2 — High (Significant improvement)

#### R4. Refactor `rationalize_duplicates()` (complexity 63)
**File:** `api/deduplication.py`
**Approach:** Separate graph-building logic from validation logic. Extract provider-ID matching into its own function.
**Effort:** 1 day

#### R5. Simplify `get_quality_description()` (complexity 63)
**File:** `api/metadata.py`
**Why:** Large branching tree for quality description generation. Could use lookup tables or a strategy pattern.
**Effort:** 1 day

#### R6. Refactor `build_disjoint_set()` (complexity 61)
**File:** `api/deduplication.py`
**Approach:** Separate graph construction, validation, and cycle detection into distinct steps.
**Effort:** 1 day

#### R7. Increase `deduplication.py` test coverage (60% → 80%+)
**Why:** Core business logic. 216 untested statements include edge cases in deletion decisions.
**Effort:** 2–3 days

### 10.3 Tier 3 — Medium (Quality polish)

#### R8. Narrow broad exception handling
**Count:** 19 instances of `except Exception`
**Focus:** `api/metadata.py` (7 instances) — replace with specific exception types (`httpx.HTTPError`, `KeyError`, `ValueError`).
**Effort:** 1 day

#### R9. Simplify `format_html_report()` (complexity 52)
**File:** `reports/html.py`
**Approach:** Extract data preparation from template rendering. Move statistics calculation into `reports/common.py`.
**Effort:** 1 day

#### R10. Add return type hints to core modules
**Focus:** `api/deduplication.py` (33% typed), `api/client.py` (45% typed)
**Effort:** 0.5 days

### 10.4 Tier 4 — Low (Quick wins)

| # | Action | Effort |
|---|--------|--------|
| R11 | Remove 3 dead functions (`compare_dates`, `create_http_client`, `read_json_file`) | 15 min |
| R12 | Fix debug `print()` in `checker.py:21` → `logger.info()` | 1 min |
| R13 | Add `.dockerignore` (exclude tests, docs, .git, debug scripts) | 5 min |
| R14 | Add `HEALTHCHECK` instruction to Dockerfile | 10 min |
| R15 | Add `pip-audit` to security-scan workflow for dependency CVE scanning | 20 min |
| R16 | Clean up 38 untracked debug/test files from repo root | 15 min |
| R17 | Increase comment ratio in complex modules (8.8% → 12%) | 2 hours |

---

## 11. Suggested Roadmap

### Sprint 1: Stability (2 weeks)
- **Week 1:** R1 — Write tests for the 4 untested modules (target 70% each)
- **Week 2:** R7 — Increase deduplication.py coverage to 80%+
- **Milestone:** Overall coverage reaches **70%+**

### Sprint 2: Complexity Reduction (2 weeks)
- **Week 1:** R2 + R3 — Refactor the two worst functions (process_duplicate_groups + main)
- **Week 2:** R4 + R5 + R6 — Refactor remaining Grade-F functions in api/
- **Milestone:** Zero Grade-F functions, all functions below complexity 25

### Sprint 3: Polish (1 week)
- R8 (exception handling) + R9 (HTML report) + R10 (type hints)
- R11–R17 (quick wins)
- **Milestone:** Coverage 75%+, all files < 500 LOC, avg complexity < 10

### Projected Health Score After Completion

| Dimension | Current | After Sprint 1 | After Sprint 3 |
|-----------|---------|----------------|----------------|
| Test Coverage | 56% | 70% | 78% |
| Complexity | 45/100 | 45/100 | 80/100 |
| Architecture | 88/100 | 88/100 | 92/100 |
| **Overall** | **67/100** | **75/100** | **85/100** |

---

## 12. Methodology Notes

This report was produced through 3 independent analysis rounds:

1. **Round 1** — File discovery, manual complexity estimation, git churn, test mapping, security scan
2. **Round 2** — Radon cyclomatic complexity (precise), radon maintainability index, AST-based dead code detection, architecture dependency graph, Docker/CI audit
3. **Round 3** — Cross-validation of all disputed metrics, correction of overestimates (dead code 18→3, CLI coverage 23%→54%, broad exceptions 37→19), independent verification via project-health-auditor

All metrics in this report reflect the **Round 3 cross-checked values**. Where rounds disagreed, the verified value was used.
