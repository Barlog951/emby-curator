# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🔍 MANDATORY: USE CLAUDE CONTEXT MCP FOR CODE SEARCH (CRITICAL PRIORITY)

**Claude Context MCP (`mcp__claude-context__*`) is the PRIMARY tool for codebase exploration. USE IT FIRST, ALWAYS.**

### **RULE 1: ALWAYS USE `mcp__claude-context__search_code` FOR:**

**Deduplication & Media Processing:**
- Finding functionality: "where is duplicate detection logic", "find quality rating algorithm", "show deduplication workflow"
- Understanding architecture: "how does language prioritization work", "what handles provider ID exclusions", "duplicate grouping strategy"
- API integration: "Emby API client implementation", "metadata fetching logic", "image URL handling"
- Report generation: "HTML report creation", "Markdown formatting", "template rendering"

**Media Quality & Analysis:**
- Quality comparison: "video codec rating", "audio quality scoring", "resolution detection"
- Provider ID handling: "IMDB ID matching", "TMDB provider integration", "TVDB exclusions"
- Language handling: "audio language detection", "language normalization", "priority matching"

**Data Structures & Algorithms:**
- Disjoint sets: "union-find implementation", "duplicate grouping", "rationalize duplicates"
- Rating systems: "media item scoring", "quality factors", "weighted ratings"

**ANY conceptual/semantic query about deduplication, media analysis, or API integration**

### **RULE 2: Use Grep/Glob ONLY FOR:**
- **Grep**: Exact string matching (`def determine_items_to_delete`, specific function names, literal imports)
- **Glob**: File patterns only (`**/*.py`, `tests/unit/api/*.py`, `emby_dedupe/reports/*.py`)

### **WHY THIS MATTERS:**
- **Semantic search understands context and meaning** (not just text matching)
- **Index persists in Zilliz Cloud between sessions** (instant results, no re-indexing)
- **Reduces token usage dramatically** - no grep flooding with thousands of code lines
- **Finds related code** that grep would miss (e.g., "quality rating" finds codec scoring, resolution detection, bitrate analysis)
- **Cross-module relationships** - understands how API client, deduplication, and reports interact

### **ENFORCEMENT:**
❌ **NEVER** start with grep for conceptual queries
✅ **ALWAYS** use `mcp__claude-context__search_code` first, then grep for exact refinement if needed

### **EXAMPLES:**

**❌ WRONG APPROACH:**
```
User: "How does duplicate detection work?"
Claude: *uses Grep to search for "duplicate"*
```

**✅ CORRECT APPROACH:**
```
User: "How does duplicate detection work?"
Claude: *uses mcp__claude-context__search_code with query: "duplicate detection algorithm provider ID grouping disjoint set"*
Result: Finds identify_duplicates(), rationalize_duplicates(), DisjointSet class, and all related logic
```

**Additional Examples:**
- "Where is quality rating implemented?" → `mcp__claude-context__search_code` with query: "quality rating media items codec resolution audio bitrate scoring"
- "How do we handle language priorities?" → `mcp__claude-context__search_code` with query: "language prioritization audio tracks normalization preference"
- "Find HTML report generation" → `mcp__claude-context__search_code` with query: "HTML report template rendering generation statistics"
- "Show Emby API integration" → `mcp__claude-context__search_code` with query: "Emby API client authentication fetch items metadata"

## **PLANNING MODE WORKFLOW:**
- **BEFORE entering plan mode**, use `mcp__claude-context__search_code` to gather relevant code context
- Search for: related functionality, similar patterns, integration points, existing implementations
- **THEN** enter plan mode with context already gathered
- This reduces token usage and provides better results than Explore agents using grep
- **NEVER skip semantic search** - it's mandatory before any significant code changes

## **INDEXING STATUS:**
- **Repository**: `/Users/dodko/DEV/emby-dedupe`
- **Status**: ✅ Fully indexed (57 files, 605 chunks)
- **Coverage**: All Python modules, tests, configuration, documentation
- **Performance**: Instant semantic search results, <100ms query time

## **VERIFICATION COMMANDS:**
```python
# Check index status
mcp__claude-context__get_indexing_status(path="/Users/dodko/DEV/emby-dedupe")

# Search codebase semantically
mcp__claude-context__search_code(
    path="/Users/dodko/DEV/emby-dedupe",
    query="your semantic query here",
    limit=15
)

# Filter by file type (optional)
mcp__claude-context__search_code(
    path="/Users/dodko/DEV/emby-dedupe",
    query="deduplication logic",
    extensionFilter=[".py"],
    limit=10
)
```

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
# ALWAYS start with Claude Context MCP for understanding relationships and usage
mcp__claude-context__search_code(
    path="/Users/dodko/DEV/emby-dedupe",
    query="method_name function usage purpose relationships dependencies"
)

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
mcp__claude-context__search_code(
    path="/Users/dodko/DEV/emby-dedupe",
    query="method_name calls usage imports integration points"
)
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
# Must show: "588 passed, 1 skipped" or similar success status
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
  - `api/` - Emby API client and media operations
  - `cli/` - Command-line interface
  - `models/` - Data models
  - `reports/` - Report generation (HTML and Markdown)
  - `templates/` - HTML templates for report generation
  - `static/` - Static assets (CSS, images, etc.)
  - `utils/` - Utility functions

## Build/Run/Test Commands
- Install locally: `pip install -e .`
- Run tool: `emby-dedupe` or `python -m emby_dedupe`
- Build container: `docker build -t emby-dedupe .`
- Run container: `docker run -e DEDUPE_EMBY_HOST="http://your-emby-server" -e DEDUPE_EMBY_LIBRARY="Your Library" -e DEDUPE_EMBY_API_KEY="your_api_key" emby-dedupe`
- Lint: `ruff check emby_dedupe/`
- Type check: `mypy emby_dedupe/`
- Run tests: `pytest`
- Check test coverage: `pytest --cov-report term-missing --cov=emby_dedupe`

## Code Style Guidelines
- Use Python 3.12 compatible code (Docker container uses Python 3.12-slim)
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

## Testing
- Comprehensive test suite with 129 tests covering all key functionality
- Current test coverage: 70% (over 1400 statements covered)
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