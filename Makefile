.PHONY: clean test lint mypy coverage install dev

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

test:
	python -m pytest

coverage:
	python -m pytest --cov=emby_dedupe --cov-report=term-missing

lint:
	ruff check emby_dedupe/

# Original lint target that included tests directory
lint-all:
	ruff check emby_dedupe/ tests/

mypy:
	mypy emby_dedupe/

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

# Run all auto-fixes for linting issues
allfx:
	ruff check emby_dedupe/ --fix --unsafe-fixes

all: clean dev lint mypy test