-include .sonar-token
export SONAR_TOKEN

.PHONY: help clean test test-fast test-all test-parallel test-stats coverage lint lint-all lint-fix mypy deadcode quality install dev allfx all sonar sonar-quick sonar-reports sonar-issues sonar-bugs sonar-critical sonar-new sonar-all sonar-metrics sonar-gate-check sonar-gate-wait install-hooks

help:
	@echo "================================================================"
	@echo "  Emby Dedupe - Development Commands"
	@echo "================================================================"
	@echo ""
	@echo "  Test Commands:"
	@echo "  make test             Fast tests (recommended)"
	@echo "  make test-fast        Same as test (skip slow/integration)"
	@echo "  make test-parallel    All tests in parallel"
	@echo "  make test-all         All tests sequential verbose"
	@echo "  make test-stats       Show test statistics"
	@echo "  make coverage         Tests with coverage report"
	@echo ""
	@echo "  Code Quality Commands:"
	@echo "  make lint             Run linter (ruff)"
	@echo "  make lint-all         Lint source + tests"
	@echo "  make lint-fix         Auto-fix lint issues"
	@echo "  make mypy             Type checking (mypy)"
	@echo "  make deadcode         Dead code detection (vulture)"
	@echo "  make allfx            Auto-fix all lint issues (unsafe)"
	@echo "  make quality          Run all quality checks"
	@echo ""
	@echo "  SonarQube Analysis:"
	@echo "  make sonar            Full analysis (FAILS if quality gate fails)"
	@echo "  make sonar-quick      Quick scan (verbose)"
	@echo "  make sonar-reports    Generate coverage/test reports only"
	@echo "  make sonar-gate-check Check quality gate only"
	@echo "  make sonar-gate-wait  Check quality gate (waits for processing)"
	@echo "  make sonar-issues     Issue summary (severity, type, files)"
	@echo "  make sonar-bugs       All bugs (grouped by severity)"
	@echo "  make sonar-critical   Critical/blocker issues (top 10 files)"
	@echo "  make sonar-new        NEW CODE issues only (since last version)"
	@echo "  make sonar-all        All issues (grouped by type & severity)"
	@echo "  make sonar-metrics    Project quality metrics"
	@echo ""
	@echo "  Other Commands:"
	@echo "  make install          Install package (editable)"
	@echo "  make dev              Install with dev dependencies"
	@echo "  make install-hooks    Install git pre-commit hooks"
	@echo "  make clean            Remove build artifacts"
	@echo "  make all              Clean + dev + lint + mypy + test"
	@echo ""
	@echo "================================================================"

# ============ Test Commands ============

# Default: fast tests (skip slow/integration markers)
test: test-fast

# Fast tests only (skip integration, slow, api_dependent)
test-fast:
	@echo "Running FAST tests..."
	python -m pytest tests/ -m "not integration and not slow and not api_dependent" -q --no-cov --tb=line -p no:warnings

# All tests with parallel execution (requires pytest-xdist)
test-parallel:
	@echo "Running ALL tests in PARALLEL..."
	python -m pytest tests/ -n auto -q --no-cov --tb=line -p no:warnings

# All tests sequentially with verbose output
test-all:
	@echo "Running ALL tests SEQUENTIALLY..."
	python -m pytest tests/ -v

# Show test statistics
test-stats:
	@echo "Test Suite Statistics:"
	@echo ""
	@python -m pytest tests/ --collect-only -q 2>/dev/null | tail -1
	@echo ""
	@echo "Integration tests:"
	@python -m pytest tests/ -m "integration" --collect-only -q 2>/dev/null | tail -1
	@echo ""
	@echo "Slow tests:"
	@python -m pytest tests/ -m "slow" --collect-only -q 2>/dev/null | tail -1

# Coverage report
coverage:
	@echo "Generating coverage report..."
	python -m pytest tests/ --cov=emby_dedupe --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

# ============ Code Quality Commands ============

lint:
	ruff check emby_dedupe/ tests/ dashboards/ scripts/

lint-all:
	ruff check emby_dedupe/ tests/ dashboards/ scripts/

lint-fix:
	ruff check emby_dedupe/ tests/ dashboards/ scripts/ --fix

mypy:
	mypy emby_dedupe/

# Dead code detection
deadcode:
	@echo "Checking for dead code..."
	vulture emby_dedupe/ --min-confidence 80

allfx:
	ruff check emby_dedupe/ --fix --unsafe-fixes

# Run all quality checks
quality:
	@echo "Running all quality checks..."
	@$(MAKE) lint
	@$(MAKE) mypy
	@$(MAKE) test
	@echo "All quality checks passed"

# ============ SonarQube Analysis ============

# Generate coverage reports for SonarQube
sonar-reports:
	@echo "Generating coverage and test reports for SonarQube..."
	python -m pytest tests/ --cov=emby_dedupe --cov-report=xml --junitxml=test-results.xml -q
	@echo "Reports generated: coverage.xml, test-results.xml"

# Full SonarQube analysis - FAILS with exit code 1 if quality gate fails
sonar: sonar-reports
	@echo "Running SonarQube analysis..."
	@sonar-scanner
	@echo ""
	@echo "Waiting for SonarQube processing and checking quality gate..."
	@python -m tools.sonar.quality_gate --wait

# Quick SonarQube scan with verbose output
sonar-quick: sonar-reports
	sonar-scanner -X
	@echo "View results: http://sonarqube.sonarcube.orb.local/dashboard?id=emby-dedupe"

# Fetch and display SonarQube issues
sonar-issues:
	@python -m tools.sonar.issues summary

sonar-bugs:
	@python -m tools.sonar.issues bugs

sonar-critical:
	@python -m tools.sonar.issues critical

sonar-all:
	@python -m tools.sonar.issues all

sonar-new:
	@python -m tools.sonar.issues new

sonar-metrics:
	@python -m tools.sonar.issues metrics

# Check quality gate (fails with exit code 1 if gate fails)
sonar-gate-check:
	@python -m tools.sonar.quality_gate

# Check quality gate with wait for processing to complete
sonar-gate-wait:
	@python -m tools.sonar.quality_gate --wait

# ============ Other Commands ============

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

# Install the version-controlled git hooks (.githooks/)
install-hooks:
	@chmod +x .githooks/pre-commit
	@git config core.hooksPath .githooks
	@echo "✅ Git hooks active (core.hooksPath=.githooks)"
	@echo "   pre-commit: gitleaks secret scan + SonarQube quality gate"
	@command -v gitleaks >/dev/null 2>&1 || echo "⚠️  gitleaks not installed — run: brew install gitleaks"

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf test-results.xml
	rm -rf htmlcov/
	rm -rf .mypy_cache
	rm -rf .sonar/
	rm -rf .scannerwork/
	find . -type d -name __pycache__ -exec rm -rf {} +

all: clean dev lint mypy test
