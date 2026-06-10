#!/usr/bin/env bash
# CocoIndex Search Validation Script
# Run this after switching models to verify semantic search still works correctly.
#
# Reference baseline: 2026-03-14 (Opus 4.6)
# Expected: All 5 queries return PASS (correct file in top results)
#
# Usage: bash scripts/validate-cocoindex.sh

set -euo pipefail

PASS=0
FAIL=0
TOTAL=5

echo "=== CocoIndex Search Validation ==="
echo "Checking semantic search returns expected files..."
echo ""

# Helper: run claude with a cocoindex query and check if expected file appears
check_query() {
    local query="$1"
    local expected_file="$2"
    local label="$3"

    # Use the MCP tool via claude CLI
    result=$(claude -p "Use mcp__cocoindex-code__search with query='$query', refresh_index=false, limit=5. Return ONLY the file_path values from results, one per line. Nothing else." 2>/dev/null)

    if echo "$result" | grep -q "$expected_file"; then
        echo "PASS: $label"
        echo "  Query: '$query'"
        echo "  Expected: $expected_file -> FOUND"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $label"
        echo "  Query: '$query'"
        echo "  Expected: $expected_file -> NOT FOUND"
        echo "  Got: $result"
        FAIL=$((FAIL + 1))
    fi
    echo ""
}

# Test 1: Core deduplication logic
check_query \
    "identify duplicates provider ID grouping" \
    "emby_dedupe/api/deduplication.py" \
    "Deduplication logic"

# Test 2: Cleanup protection / rating decay
check_query \
    "cleanup movie protection rating decay threshold" \
    "emby_dedupe/cli/cleanup.py" \
    "Cleanup rating decay"

# Test 3: HTML report generation with Jinja
check_query \
    "HTML report template generation jinja render" \
    "emby_dedupe/reports/html.py" \
    "HTML report generation"

# Test 4: Genre normalization and fixing
check_query \
    "genre normalize fix TMDB OMDb provider" \
    "emby_dedupe/cli/app.py" \
    "Genre management CLI"

# Test 5: Emby API client authentication
check_query \
    "emby API client authenticate delete item" \
    "emby_dedupe/api/client.py" \
    "API client authentication"

echo "=================================="
echo "Results: $PASS/$TOTAL passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    echo "WARNING: CocoIndex search may not be working correctly!"
    exit 1
else
    echo "All searches returning expected results."
    exit 0
fi
