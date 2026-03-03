# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🔍 MANDATORY: USE COCOINDEX FOR CODE SEARCH (CRITICAL PRIORITY)

**CocoIndex (`mcp__cocoindex-code__search`) is the PRIMARY tool for codebase exploration. USE IT FIRST, ALWAYS.**

### **RULE 1: ALWAYS USE `mcp__cocoindex-code__search` FOR:**
- Any conceptual/semantic query about the codebase
- Finding functionality, understanding architecture, API integration, report generation
- Quality comparison, provider ID handling, language handling
- Data structures, algorithms, rating systems
- **ANY conceptual/semantic query about deduplication, media analysis, or API integration**

### **RULE 2: Use Grep/Glob ONLY FOR:**
- **Grep**: Exact string matching (`def determine_items_to_delete`, specific function names, literal imports)
- **Glob**: File patterns only (`**/*.py`, `tests/unit/api/*.py`, `emby_dedupe/reports/*.py`)

### **ENFORCEMENT:**
❌ **NEVER** start with grep for conceptual queries
✅ **ALWAYS** use `mcp__cocoindex-code__search` first, then grep for exact refinement if needed

### **REINDEXING:**
- **Start of each session**: Run first query with `refresh_index=true` to pick up any file changes
- **After code changes**: Use `refresh_index=true` on next query to update the index
- **Subsequent queries**: Use `refresh_index=false` for faster results

### **USAGE:**
```python
# First query per session — reindex to pick up changes
mcp__cocoindex-code__search(query="your semantic query here", refresh_index=true, limit=10)

# Subsequent queries — skip reindex for speed
mcp__cocoindex-code__search(query="deduplication logic", refresh_index=false, limit=10)
```

## **PLANNING MODE WORKFLOW:**
- **BEFORE entering plan mode**, use `mcp__cocoindex-code__search` to gather relevant code context
- Search for: related functionality, similar patterns, integration points, existing implementations
- **THEN** enter plan mode with context already gathered
- **NEVER skip semantic search** - it's mandatory before any significant code changes

---

## 🎯 CRITICAL DEVELOPMENT RULES (HIGH PRIORITY) - MUST FOLLOW IN ALL SESSIONS:
- **ALWAYS run `pytest tests/` after EVERY code change** - no exceptions, never assume changes work
- **ADD test cases for ALL new functionality** - if there's no test coverage for a change, create tests immediately  
- **CONVERT ALL user feedback to test cases** - every bug report, issue, or request becomes a permanent regression test
- **These rules are MANDATORY** - follow religiously in ALL sessions to prevent regressions and ensure quality

## 🚨 MANDATORY CODE ANALYSIS PROTOCOL (CRITICAL) - PREVENT ANALYTICAL ERRORS:
Following analytical errors that nearly caused production code damage, these verification steps are now MANDATORY before ANY code removal or modification recommendations:

**STEP 0: SEMANTIC SEARCH FIRST (REQUIRED)**
```python
# ALWAYS start with CocoIndex for understanding relationships and usage
mcp__cocoindex-code__search(query="method_name function usage purpose relationships dependencies")

# Examples:
# - "determine_items_to_delete function usage caller dependencies"
# - "rate_media_items quality scoring usage integration"
# - "identify_duplicates provider ID grouping relationships"
# - "get_quality_description media streams parsing usage"
```

**WHY:** Semantic search reveals context, dependencies, and relationships that grep cannot find. It understands how methods interact across modules, finds all usage patterns, and identifies integration points.

**STEP 1: SYSTEMATIC METHOD VERIFICATION (REQUIRED)**
```bash
# Use grep ONLY for exact string confirmation after semantic search
# MANDATORY: Extract ALL method definitions first
grep -n "def " emby_dedupe/api/filename.py

# MANDATORY: Search for EACH method individually across ALL files
grep -r "method_name" emby_dedupe/ --include="*.py"
grep -r "method_name" tests/ --include="*.py"
grep -r "method_name" . --include="*.py"

# MANDATORY: Verify with multiple search patterns
grep -r -E "(self\.)?method_name\s*\(" . --include="*.py"
```

**STEP 2: PRODUCTION USAGE VERIFICATION (REQUIRED)**
**Start with semantic search to understand full usage context:**
```python
mcp__cocoindex-code__search(query="method_name calls usage imports integration points")
```
Then verify specific patterns:
- Internal calls: Search for method calls within same file
- External calls: Check if other files import and use the method
- String-based calls: Check for getattr, eval, dynamic calls
- Inheritance usage: Verify parent/child class method calls
- Test dependencies: Identify if tests rely on method functionality

**STEP 3: IMPACT ASSESSMENT (REQUIRED)**
- Production impact: What breaks if method is removed?
- Test impact: Which tests fail if method is removed?
- Integration impact: What systems depend on this method?
- User feature impact: Does removal break user-facing functionality?

**STEP 4: MANDATORY TEST VERIFICATION**
```bash
# REQUIRED: Always run full test suite before recommending changes
python -m pytest tests/ -x --tb=short
# Must show: "669 passed" or similar success status
```

**STEP 5: NEVER ASSUME - ALWAYS VERIFY**
- **START with semantic search** - understand full context before making claims
- NO assumptions about method usage - verify every claim with evidence from semantic search + grep
- NO batch removals - analyze each method individually with semantic search
- NO confident recommendations without comprehensive verification (semantic search + grep + test run)
- ASK USER TO VERIFY findings before making any changes

## 🛡️ CRITICAL ERROR PREVENTION:
- **Triple-check all analysis** before presenting findings (semantic search + grep + tests)
- **Search systematically** using semantic search FIRST, then grep for confirmation
- **Be conservative** - when in doubt, DON'T recommend removal
- **Verify incrementally** - test one change at a time
- **Maintain test coverage** at all times during modifications
- **Always use semantic search** to understand relationships before modifications

## 🏗️ MANDATORY CODE CREATION STANDARDS (PROFESSIONAL GRADE) - NEW CODE RULES:

**RULE 1: MINIMIZE NEW CODE CREATION**
- ADD code ONLY when absolutely necessary - prefer modifying existing code over creating new code
- FIX errors by CHANGING current code when possible, not by adding layers
- REFACTOR existing methods rather than creating duplicate functionality
- EXTEND existing functions rather than writing new ones with similar purposes

**RULE 2: SIMPLICITY OVER COMPLEXITY**
- PREFER simpler methods that are easy to understand and maintain
- AVOID spaghetti code - no complex interdependencies or convoluted logic flows
- ONE responsibility per method - functions should do one thing well
- CLEAR naming conventions - method and variable names should be self-documenting
- MINIMAL cognitive load - code should be readable by any developer

**RULE 3: PROFESSIONAL-GRADE CODE STANDARDS**
- HIGHEST code quality standards - you are a professional developer, act like one
- COMPREHENSIVE error handling - anticipate and handle all failure modes gracefully
- PROPER documentation - add docstrings and comments for complex logic
- CONSISTENT code style - follow existing patterns and conventions in the codebase
- PERFORMANCE awareness - write efficient code that doesn't introduce bottlenecks

**REMEMBER:** Code is read 10x more than it's written. Optimize for readability and maintainability.

## Project Structure
The project has been refactored into a proper Python package with the following structure:
- `emby_dedupe/` - Main package
  - `api/` - Emby API client, media operations, genre CRUD, external genre providers (TMDB/OMDb)
  - `cli/` - Command-line interface (dedupe, genres subcommands)
  - `models/` - Data models
  - `reports/` - Report generation (HTML and Markdown)
  - `templates/` - HTML templates for report generation
  - `static/` - Static assets (CSS, images, etc.)
  - `utils/` - Utility functions and constants
- `scripts/` - Standalone scripts (genre webhook listener, smoke tests)

## Build/Run/Test Commands
- Install locally: `pip install -e .` (on server use `.venv/bin/pip install -e .` — system pip is externally managed)
- **Server host**: use `http://localhost:8096` when running ON the Emby server — external hostname `emby.in.fukiyato.com` does not resolve from inside the server
- Run tool: `emby-dedupe` or `python -m emby_dedupe`
- Build container: `docker build -t emby-dedupe .`
- Run container: `docker run -e DEDUPE_EMBY_HOST="http://your-emby-server" -e DEDUPE_EMBY_LIBRARY="Your Library" -e DEDUPE_EMBY_API_KEY="your_api_key" emby-dedupe`
- Lint: `ruff check emby_dedupe/`
- Type check: `mypy emby_dedupe/`
- Run tests: `pytest`
- Check test coverage: `pytest --cov-report term-missing --cov=emby_dedupe`

## CLI Structure (Typer subcommands)

The CLI uses shared options before the subcommand and subcommand-specific options after:

```
emby-dedupe [shared options] SUBCOMMAND [subcommand options]
```

Shared options (env vars): `--host/-H` (`DEDUPE_EMBY_HOST`), `--port/-p` (`DEDUPE_EMBY_PORT`), `--api-key/-a` (`DEDUPE_EMBY_API_KEY`), `--library/-l` (`DEDUPE_EMBY_LIBRARY`, repeatable), `--doit` (`DEDUPE_DOIT`), `--lock/--no-lock` (`DEDUPE_LOCK`), `-v` (verbosity)

Subcommands: `dedupe`, `genres audit`, `genres normalize`, `genres fix`, `genres process`, `check`, `missing-episodes`

`dedupe` options (after `dedupe`): `--username` (`DEDUPE_EMBY_USERNAME`), `--password` (`DEDUPE_EMBY_PASSWORD`), `--lang-prio` (`DEDUPE_LANG_PRIO`), `--exclude-ids` (`DEDUPE_EXCLUDE_IDS`), `--html-report`, `--html-only`, `--no-open`

Example dry-run scan:
```bash
emby-dedupe --host "https://emby.example.com" --api-key "KEY" --library "Movies" dedupe
```

Example full run with deletion:
```bash
python -m emby_dedupe \
  --host "https://emby.example.com" --api-key "KEY" \
  --library "Movies" --library "TV Shows" --doit \
  dedupe \
  --username "admin" --password "pass" \
  --lang-prio "slo,cze,eng" --html-report --html-only
```

Genre commands:
```bash
emby-dedupe --host "..." --api-key "..." -l Movies genres audit
emby-dedupe --host "..." --api-key "..." -l Movies --doit genres normalize
emby-dedupe --host "..." --api-key "..." -l Movies --doit genres fix --validate
emby-dedupe --host "..." --api-key "..." --doit genres normalize --item-ids 123,456
emby-dedupe --host "..." --api-key "..." genres process --doit --validate --item-ids 123,456
```

## Code Style Guidelines
- Use Python 3.14 compatible code (local dev uses Python 3.14)
- Use type hints for all function parameters and return values
- Include comprehensive docstrings for all functions (Google style)
- Follow PEP 8 naming conventions (snake_case for functions/variables)
- Implement robust error handling with specific exception types
- Use consistent logging with appropriate levels
- Avoid hard-coded values; use constants or environment variables
- Maintain security best practices for API keys and credentials
- Prefer httpx over requests for HTTP operations
- Use tqdm for progress reporting in long-running operations

## Features
- Identifies duplicate media in Emby libraries (supports scanning multiple libraries at once)
- Rates duplicates by quality factors
- Supports language prioritization to keep items with preferred audio languages
- Provider ID exclusion to prevent specific media from being deduplicated (IMDB, TMDB, TVDB)
- Generates both terminal and HTML reports with comprehensive statistics
- Uses template-based HTML report generation with external CSS for better maintainability
- HTML reports can be generated without opening a browser using `--html-only --no-open`
- Maintains image links and metadata for deleted content in HTML reports
- Shows external links (IMDB/TMDB) for deleted items in reports
- Supports both interactive and non-interactive modes
- Can be run locally or via Docker
- **Genre management**: audit, normalize, and fix genres across libraries (`emby-dedupe genres`)
  - Audit: read-only report of all genres with counts and unknown/variant detection
  - Normalize: fix variant names (Sci-Fi→Science Fiction, dada→Comedy, etc.) with genre locking
  - Fix: fill missing genres from TMDB/OMDb with rate-limited API clients and local cache
  - `--item-ids` flag for targeted processing of specific items (used by webhook listener)
- **Webhook listener**: `scripts/genre-webhook-listener.py` — real-time genre processing for new media
  - Receives Emby ItemAdded webhooks, debounces, runs `genres process` (normalize+fix single pass)
  - Batch fetch via `Ids=` query param — 591 items in ~6 requests instead of 591 individual GETs
  - Episode→SeriesId deduplication (genres live on Series, not Episodes)
  - Deployed as systemd service on Emby VM with monthly full-scan safety net

## Analytics Dashboard (marimo)
- **Location**: `dashboards/emby_unplayed.py` — interactive marimo notebook
- **Venv**: `.dashboard-venv/` (separate from main project venv)
- **Install**: `uv venv .dashboard-venv --python 3.14 && source .dashboard-venv/bin/activate && uv pip install marimo plotly pandas httpx`
- **Run as app**: `.dashboard-venv/bin/marimo run dashboards/emby_unplayed.py --port 2718`
- **Edit interactively**: `.dashboard-venv/bin/marimo edit dashboards/emby_unplayed.py`
- **6 tabs**: Overview, By Library, Cleanup by Size, By Language, Added but Forgotten, Abandoned Series
- **Live data**: Connects to Emby API on load (~60s to scan all users)
- **Marimo gotcha**: All cell variables must be unique — use `_` prefix for cell-local vars

## Testing
- Comprehensive test suite with 669 tests covering all key functionality
- Current test coverage: 70%+
- Run via Makefile: `make lint`, `make mypy`, `make test`, `make coverage`, `make allfx`
- CI/CD with GitHub Actions workflows for testing, security scanning, and Docker builds
- Tests are organized in the same structure as the main package
- Uses pytest fixtures for common test data
- Mock objects used for external dependencies like HTTP requests
- Tests cover API client, deduplication logic, reports, CLI, and utilities
- Tests include error handling and edge cases
- Good coverage of HTML and Markdown report generation
- Coverage of language prioritization and provider ID exclusion features
- Tests for image preservation and external links for deleted items