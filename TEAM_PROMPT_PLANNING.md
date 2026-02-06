# Agent Team Prompt: Emby-Dedupe Quality Remediation Planning

## How to Use This Prompt

**Launch from the project directory:**
```bash
cd /Users/dodko/DEV/emby-dedupe
claude
```

**Then paste the prompt below (everything between the `---` markers) into Claude Code:**

---

## THE PROMPT

Create an agent team to build a comprehensive remediation plan for the emby-dedupe repository. This is a PLANNING-ONLY phase — no code changes. The output is a structured execution plan that will be used as ground zero for an implementation team in the next session.

### Context

**Repository:** `/Users/dodko/DEV/emby-dedupe` (Python 3.12, ~7,400 LOC, 28 modules)
**Health Report:** Read `HEALTH_REPORT.md` in the repo root — it has the full analysis (67/100 health score)
**SonarQube:** Local instance at `http://sonarqube.sonarcube.orb.local`, project key `emby-dedupe`
**Existing Agent:** There's already a `sonarqube-issue-fixer` agent at `.claude/agents/sonarqube-issue-fixer.md` — read it for SonarQube workflow context

**Current state summary:**
- 48 SonarQube issues (1 BLOCKER, 34 CRITICAL, rest MAJOR/MINOR)
- 7 Grade-F functions (complexity 52-65, industry target is 10-15)
- 4 modules with 0% test coverage (720 statements)
- Overall test coverage: 56% (target: 80%)
- 3 dead functions to remove
- 19 broad exception handlers
- 272 tests passing (100% pass rate)

**SonarQube data access — use these Makefile commands:**
```bash
make sonar-issues      # Issue summary (severity, type, files)
make sonar-bugs        # All bugs grouped by severity
make sonar-critical    # Critical/blocker issues (top 10 files)
make sonar-all         # All issues grouped by type & severity
make sonar-new         # NEW CODE issues only
make sonar-metrics     # Project quality metrics
```

If MCP SonarQube tools are available, prefer those:
```
mcp__sonarqube__search_sonar_issues_in_projects(projects=["emby-dedupe"])
mcp__sonarqube__get_project_quality_gate_status(projectKey="emby-dedupe")
```

### Team Structure — Spawn 4 teammates with specific models:

**Model strategy:** Opus for roles requiring deep architectural reasoning, Sonnet for structured/mechanical work.

| Role | Model | Why |
|------|-------|-----|
| Lead/Orchestrator | Sonnet | Coordination only, no deep analysis |
| SonarQube Analyst | Sonnet | Structured issue categorization |
| Complexity Analyst | **Opus** | Deep reasoning for 7 Grade-F refactoring strategies |
| Test Strategist | Sonnet | Pattern-based test design |
| Devil's Advocate | **Opus** | Deep critical thinking, finding subtle risks |

**1. SonarQube Analyst** (name: `sonar-analyst`, model: **Sonnet**)
- Fetch ALL 48 SonarQube issues using `make sonar-all` and `make sonar-critical`
- Categorize every issue: file, line, severity, type, rule, estimated effort
- Group by file to identify hotspot files (files with most issues)
- Map dependencies: which issues must be fixed before others
- Identify quick wins (2-5 min fixes) vs. deep work (refactoring)
- Determine which SonarQube issues OVERLAP with health report recommendations (avoid double-counting)
- **Deliverable:** Complete issue inventory with prioritized fix order, grouped by file

**2. Complexity & Refactoring Analyst** (name: `complexity-analyst`, model: **Opus**)
- Read ALL 7 Grade-F functions identified in HEALTH_REPORT.md:
  - `process_duplicate_groups` (api/deduplication.py, complexity 65)
  - `rationalize_duplicates` (api/deduplication.py, complexity 63)
  - `get_quality_description` (api/metadata.py, complexity 63)
  - `main` (cli/main.py, complexity 63)
  - `build_disjoint_set` (api/deduplication.py, complexity 61)
  - `determine_items_to_delete` (api/deduplication.py, complexity 55)
  - `format_html_report` (reports/html.py, complexity 52)
- For EACH function: read the actual code, understand what it does, then propose a specific refactoring strategy
- Identify what helper functions to extract, with proposed names and responsibilities
- Estimate target complexity after refactoring (must be < 15 per function)
- Identify dependencies between functions (e.g., 4 functions in deduplication.py share state)
- Flag which refactors are safe (isolated) vs. risky (coupled to other functions)
- **Deliverable:** Refactoring blueprint for each Grade-F function with before/after structure

**3. Test Coverage Strategist** (name: `test-strategist`, model: **Sonnet**)
- Analyze the 4 modules with 0% test coverage:
  - `api/checker.py` (577 lines, 228 statements)
  - `api/missing_episodes.py` (629 lines, 218 statements)
  - `cli/check.py` (286 lines, 98 statements)
  - `cli/missing_episodes.py` (391 lines, 176 statements)
- Read each module, understand its functionality, identify testable units
- Design a test strategy: what to mock (HTTP calls, API responses), what fixtures to create
- Look at existing test patterns in `tests/` directory for consistency
- Estimate how many tests needed per module to reach 80% coverage
- Also analyze: which of the 7 Grade-F functions need additional tests BEFORE refactoring (safety net)
- Map out the test dependency: which tests must exist BEFORE which refactoring can safely proceed
- Consider that refactored code counts as "new code" in SonarQube (needs 80% coverage)
- **Deliverable:** Test matrix with module, test count estimate, mock strategy, and dependency on refactoring order

**4. Devil's Advocate / Risk Reviewer** (name: `devils-advocate`, model: **Opus**)
- Wait for the other 3 teammates to share initial findings (ask them to share when ready)
- Challenge EVERY major proposal:
  - Is the refactoring strategy for each Grade-F function actually safe? What could break?
  - Are there hidden dependencies between modules that weren't identified?
  - Is the proposed fix order optimal? Could we get blocked midway?
  - Are we underestimating effort? What typically goes wrong in large refactoring efforts?
  - Could any SonarQube fix introduce new issues?
  - Are there any "false economy" fixes that look quick but have hidden complexity?
- Specifically validate:
  - The 19 broad exception handlers — are some actually intentional? (CLI boundary handlers may be correct)
  - Dead code — is it truly dead or used via dynamic dispatch/reflection?
  - The test strategy — does it have gaps that would leave refactoring unsafe?
- Red-team the execution order: find the scenario where it fails
- **Deliverable:** Risk assessment with warnings, alternative approaches, and recommended safeguards

### Orchestrator Instructions (Lead, model: **Sonnet**):

You coordinate the team. Your job:

1. **Start phase:** Spawn all 4 teammates. Tell them to read `HEALTH_REPORT.md` and `.claude/agents/sonarqube-issue-fixer.md` first for context.

2. **Research phase:** Let all 4 work in parallel. The devil's advocate should wait and observe until the other 3 share initial findings.

3. **Challenge phase:** Once findings are in, have the devil's advocate review and challenge. Route their challenges to the relevant analyst for response.

4. **Synthesis phase:** Combine all findings into the final plan document.

5. **Output:** Write the final plan to `REMEDIATION_PLAN.md` in the repo root. It MUST include:

```markdown
# Emby-Dedupe Remediation Plan

## Executive Summary
- Total issues to fix (combined SonarQube + Health Report, deduplicated)
- Estimated total effort
- Risk level assessment

## Phase 1: Quick Wins & SonarQube Gate Fix (unblock commits)
- Every issue with exact file:line, fix description, effort estimate
- Ordered so commits can be unblocked ASAP
- Expected: 48 SonarQube issues, ~4-6 hours

## Phase 2: Test Safety Net (must come before refactoring)
- Test matrix for 4 untested modules
- Additional tests needed for Grade-F functions before refactoring
- Mock strategies, fixture designs
- Expected: Coverage 56% → 70%+

## Phase 3: Complexity Reduction (the big refactoring)
- For EACH Grade-F function:
  - Current state (complexity, lines, what it does)
  - Proposed refactoring (helper functions to extract, with names)
  - Target state (complexity, structure)
  - Prerequisites (which tests must exist first)
  - Risk level and mitigation
- Ordered by: dependencies first, then risk (safest first)
- Expected: 0 Grade-F functions, all < complexity 15

## Phase 4: Polish & Hardening
- Exception handling improvements (19 → specific types)
- Dead code removal (3 functions)
- Type hint additions
- Docker improvements (.dockerignore, HEALTHCHECK)
- Untracked file cleanup

## File Ownership Map (for execution team)
- Which files each execution agent should own (to avoid conflicts)
- Proposed 4-agent split for implementation

## Dependency Graph
- What blocks what — visual or textual DAG
- Critical path identification

## Risk Register
- Every risk identified by devil's advocate
- Mitigation strategy for each
- Go/no-go criteria

## Verification Checklist
- How to verify each phase is complete
- make sonar must pass after each phase
- Test count and coverage targets per phase

## Recommended Execution Team Structure
- Propose the ideal agent team for Phase 2 (implementation)
- File ownership to avoid conflicts
- Which agents need plan approval
```

### Rules for the team:

- **READ-ONLY** — no code changes, only analysis and planning
- **Use `mcp__claude-context__search_code`** for semantic code search (the index is already built)
- **Use Grep only** for exact string matching after semantic search
- **Cross-reference** SonarQube issues with HEALTH_REPORT.md — many overlap, don't double-count
- **Be realistic about effort** — AI can fix things faster than humans, but complexity reduction still requires careful reasoning
- **The plan must be self-contained** — someone who hasn't read the health report should understand it
- **Prioritize unblocking commits** — the pre-commit hook enforces SonarQube quality gate, so fixing gate failures is Priority 0

### Success Criteria:

The plan is complete when:
1. Every SonarQube issue is accounted for with a fix strategy
2. Every HEALTH_REPORT.md recommendation has an action item
3. The devil's advocate has reviewed and all critical risks are addressed
4. The execution order has no circular dependencies
5. File ownership is mapped for conflict-free parallel execution
6. `REMEDIATION_PLAN.md` is written to the repo root

Use delegate mode (Shift+Tab) to stay focused on coordination. Don't implement anything yourself.

---

## After the Team Completes

The output `REMEDIATION_PLAN.md` becomes the input for Phase 2. To launch the execution team:

```bash
cd /Users/dodko/DEV/emby-dedupe
claude
# Then reference REMEDIATION_PLAN.md and launch implementation team
```

## Tips

- Use **in-process mode** (default) — it works in any terminal
- Press **Shift+Up/Down** to select and message individual teammates
- Press **Ctrl+T** to toggle the shared task list
- If the lead starts implementing instead of delegating, tell it: "Use delegate mode, don't implement anything yourself"
- If a teammate gets stuck, message them directly with more specific instructions
- The devil's advocate should be the LAST to complete — they need input from others first
