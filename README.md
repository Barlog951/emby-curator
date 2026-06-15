# Emby Curator

**Emby Curator** keeps your Emby media libraries in order. It deduplicates media, localizes genres and descriptions (SK/CZ from TMDB/OMDb), cleans up stale unwatched content with rating-decay protection, and analyzes missing episodes and franchise gaps — with terminal and HTML reports throughout.

> **Maintained fork.** Emby Curator is a fork of [emby-dedupe](https://github.com/troykelly/emby-dedupe) by Troy Kelly, which has been inactive since May 2024. It has been substantially rewritten and extended (genre management, description/title localization, library cleanup, missing-episode analysis, analytics dashboards, a 1000+ test suite, and CI/CD). Distributed under the Apache License 2.0 — see [NOTICE](NOTICE) and [CHANGELOG.md](CHANGELOG.md).

## Build Status

![Python Tests](https://github.com/Barlog951/emby-dedupe/actions/workflows/python-test.yaml/badge.svg)
![Security Scan](https://github.com/Barlog951/emby-dedupe/actions/workflows/security-scan.yaml/badge.svg)
![Edge Container Build](https://github.com/Barlog951/emby-dedupe/actions/workflows/edge.yaml/badge.svg)

The following architectures are supported in the latest Docker version:

| Architecture    | Supported          |
|-----------------|--------------------|
| `amd64`         | :white_check_mark: |
| `arm64`         | :white_check_mark: |
| `arm/v7`        | :x:                |
| `arm/v6`        | :x:                |
| `i386`          | :x:                |

## Table of Contents

- [Features](#features)
- [Installation](#installation)
  - [Using Docker](#using-docker)
  - [Using Python](#using-python)
- [Usage](#usage)
- [Environment Variables](#environment-variables)
- [Command-line Arguments](#command-line-arguments)
- [Examples](#examples)
- [API Key Requirement](#api-key-requirement)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## Features

- Identifies duplicates in Emby media libraries (supports scanning multiple libraries at once)
- Generates detailed Markdown reports
- Optional HTML report with movie/episode images and detailed metadata
- Smart handling of both movies and TV shows with proper season/episode identification
- Support for multiple provider IDs (IMDB, TVDB, TMDB)
- Intelligent quality ranking to keep the best version
- Language prioritization to keep items with preferred audio tracks
- Multi-platform support (Docker and Python)

## Installation

### Using Docker

To install the Docker container, pull the image from the GitHub Container Registry:

```shell
docker pull ghcr.io/barlog951/emby-dedupe:edge
```

The `edge` tag tracks the latest build from `main`.

### Using Python

You can also install the tool directly using pip:

```shell
pip install emby-curator
```

Or clone the repository and install locally:

```shell
git clone https://github.com/Barlog951/emby-dedupe.git
cd emby-dedupe
pip install -e .
```

## Usage

The `emby-dedupe` tool uses a subcommand structure. Shared connection options go before the subcommand; subcommand-specific options go after.

```
emby-curator [shared options] SUBCOMMAND [subcommand options]
```

### Subcommands

| Subcommand | Description |
|---|---|
| `dedupe` | Find and remove duplicate media |
| `genres audit` | Read-only report of genre health |
| `genres normalize` | Fix variant genre names |
| `genres fix` | Fill missing genres from TMDB/OMDb |
| `check` | Check if media should be downloaded |
| `missing-episodes` | Find missing TV episodes |

### Shared Options (before subcommand)

| Option | Short | Env Var | Description |
|---|---|---|---|
| `--host` | `-H` | `DEDUPE_EMBY_HOST` | Emby server URL |
| `--port` | `-p` | `DEDUPE_EMBY_PORT` | Emby server port |
| `--api-key` | `-a` | `DEDUPE_EMBY_API_KEY` | Emby API key |
| `--library` | `-l` | `DEDUPE_EMBY_LIBRARY` | Library to scan (repeatable) |
| `--doit` | | `DEDUPE_DOIT` | Actually perform write actions |
| `--lock/--no-lock` | | `DEDUPE_LOCK` | File lock to prevent concurrent runs |
| `-v` | | | Verbosity (repeat for more: `-vv`, `-vvv`) |

### Basic Usage

```shell
# Dry-run duplicate scan
emby-curator --host http://your-emby-server --api-key your_api_key --library "Your Library" dedupe

# With multiple libraries
emby-curator --host http://your-emby-server --api-key your_api_key \
  --library "Movies" --library "TV Shows" \
  dedupe
```

## Environment Variables

The following environment variables can be used to configure the tool:

- `DEDUPE_EMBY_HOST`: The hostname or IP of the Emby server.
- `DEDUPE_EMBY_PORT`: The port for the Emby server (defaults to 8096 if not specified).
- `DEDUPE_EMBY_LIBRARY`: The name(s) of the libraries on the Emby server you want to deduplicate. For multiple libraries, separate with commas.
- `DEDUPE_EMBY_API_KEY`: The API key for the Emby server with appropriate permissions.
- `DEDUPE_DOIT`: Set to 'true' to perform deduplication deletion actions (defaults to 'false').
- `DEDUPE_LOGGING`: The logging level (e.g., ERROR, WARNING, INFO, DEBUG).
- `DEDUPE_EMBY_USERNAME`: Emby username for server access, required if `DEDUPE_DOIT` is 'true'.
- `DEDUPE_EMBY_PASSWORD`: Emby password for server access, required if `DEDUPE_DOIT` is 'true'.
- `DEDUPE_HTML_REPORT`: Set to 'true' to generate an HTML report.
- `DEDUPE_HTML_ONLY`: Set to 'true' to generate only the HTML report without terminal output.
- `DEDUPE_LANG_PRIO`: Comma-separated list of language codes in priority order (e.g., 'slo,cze,eng'). Media items with higher priority languages will be kept.
- `DEDUPE_EXCLUDE_IDS`: Comma-separated list of provider IDs to exclude from deduplication (e.g., 'tt1234567,123456'). Works with IMDB (tt prefix), TMDB, and TVDB IDs.
- `DEDUPE_EXCLUDE`: Comma-separated list of terms to exclude from deduplication. If a movie title contains any of these terms, it will be skipped.

## Command-line Arguments

The CLI uses a two-level structure: shared options first, then a subcommand with its own options.

### Shared options (before subcommand)

```
emby-curator [OPTIONS] SUBCOMMAND [SUBCOMMAND-OPTIONS]

  -H, --host TEXT        Emby server URL          [env: DEDUPE_EMBY_HOST]
  -p, --port INTEGER     Emby server port          [env: DEDUPE_EMBY_PORT]
  -a, --api-key TEXT     Emby API key              [env: DEDUPE_EMBY_API_KEY]
  -l, --library TEXT     Library to scan (repeatable) [env: DEDUPE_EMBY_LIBRARY]
  --doit                 Perform write/delete actions [env: DEDUPE_DOIT]
  --lock/--no-lock       Enable file lock           [env: DEDUPE_LOCK]
  -v                     Verbosity (-v, -vv, -vvv)
```

### dedupe subcommand options

```
emby-curator ... dedupe [OPTIONS]

  --username TEXT        Emby username             [env: DEDUPE_EMBY_USERNAME]
  --password TEXT        Emby password             [env: DEDUPE_EMBY_PASSWORD]
  --lang-prio TEXT       Language priority order, comma-separated (e.g. slo,cze,eng) [env: DEDUPE_LANG_PRIO]
  --exclude-ids TEXT     Provider IDs to exclude (IMDB/TMDB/TVDB, comma-separated) [env: DEDUPE_EXCLUDE_IDS]
  --html-report          Generate HTML report and open in browser
  --no-open              Generate HTML report without opening browser
  --html-only            HTML report only, no terminal output
```

### genres subcommand options

```
emby-curator ... genres audit [--suggest]
emby-curator ... genres normalize [--doit] [--repair-dupes] [--item-ids IDS]
emby-curator ... genres fix [--doit] [--gaps-only | --validate] [--item-ids IDS]
```

## Examples

### Generating a List of Duplicates (Dry Run)

The following command simulates the deduplication process without making any changes:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Your Library Name" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  ghcr.io/barlog951/emby-dedupe:edge

# For multiple libraries
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Movies,TV Shows" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  ghcr.io/barlog951/emby-dedupe:edge
```

Using Python:

```shell
emby-curator --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" dedupe
```

To scan multiple libraries:

```shell
emby-curator --host "http://your-emby-server" --library "Movies" --library "TV Shows" --api-key "your_api_key" dedupe
```

### Generating an HTML Report

To generate an HTML report with images and detailed metadata:

```shell
emby-curator --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" \
  dedupe --html-report
```

Or to generate only the HTML report without terminal output:

```shell
emby-curator --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" \
  dedupe --html-only
```

### Performing Deduplication Actions

To perform the deletion of duplicates:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Your Library Name" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  -e DEDUPE_EMBY_USERNAME="your_emby_username" \
  -e DEDUPE_EMBY_PASSWORD="your_emby_password" \
  -e DEDUPE_DOIT="true" \
  ghcr.io/barlog951/emby-dedupe:edge
```

Using Python:

```shell
emby-curator --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" \
  --doit dedupe --username "your_emby_username" --password "your_emby_password"
```

### Full Deduplication with All Options

```shell
python -m emby_dedupe \
  --host "http://your-emby-server" \
  --api-key "your_api_key" \
  --library "Movies" \
  --library "TV Shows" \
  --doit \
  dedupe \
  --username "your_username" \
  --password "your_password" \
  --lang-prio "slo,cze,eng" \
  --exclude-ids "tt0468569,tt0080684,550" \
  --html-report \
  --html-only
```

### Using Language Prioritization

To prioritize keeping media items with specific languages over others:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Movies" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  -e DEDUPE_LANG_PRIO="slo,cze,eng" \
  ghcr.io/barlog951/emby-dedupe:edge
```

Using Python:

```shell
emby-curator --host "http://your-emby-server" --library "Movies" --api-key "your_api_key" \
  dedupe --lang-prio "slo,cze,eng"
```

This will prioritize keeping files with Slovak audio tracks first, then Czech, then English. When multiple files have the same highest-priority language, the one with better quality will be kept.

### Excluding Media by Provider IDs

To exclude specific media items from deduplication using their provider IDs:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Movies" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  -e DEDUPE_EXCLUDE_IDS="tt0468569,tt0080684,550" \
  ghcr.io/barlog951/emby-dedupe:edge
```

Using Python:

```shell
emby-curator --host "http://your-emby-server" --library "Movies" --api-key "your_api_key" \
  dedupe --exclude-ids "tt0468569,tt0080684,550"
```

This will exclude The Dark Knight (tt0468569), The Empire Strikes Back (tt0080684), and Fight Club (TMDB ID 550) from deduplication. Useful for preserving intentional multiple versions.

The excluded IDs can be:
- IMDB IDs (start with "tt" followed by 7-8 digits)
- TMDB IDs (numeric values)
- TVDB IDs (numeric values)

### Genre Management

```shell
# Audit genre health (read-only)
emby-curator --host "http://your-emby-server" --api-key "your_api_key" --library "Movies" genres audit

# Preview variant name normalization
emby-curator --host "http://your-emby-server" --api-key "your_api_key" --library "Movies" genres normalize

# Apply normalization
emby-curator --host "http://your-emby-server" --api-key "your_api_key" --library "Movies" --doit genres normalize

# Fill missing genres from TMDB/OMDb (validate mode — only items with no genres)
emby-curator --host "http://your-emby-server" --api-key "your_api_key" --library "Movies" --doit genres fix --validate

# Target specific items (used by webhook listener)
emby-curator --host "http://your-emby-server" --api-key "your_api_key" --doit genres normalize --item-ids 123,456
```

## API Key Requirement

A valid API key with enough permissions to access the necessary operations on the Emby server must be provided. This API key is used to authenticate the script with the Emby server for read and list actions. Deletion operations require username and password credentials for additional authentication.

## Project Structure

The codebase is organized into modules according to their functionality:

```
emby_dedupe/
├── api/              # Emby API client and deduplication logic
├── cli/              # Command-line interface
├── models/           # Data models
├── reports/          # Report generation (Markdown, HTML)
└── utils/            # Utility functions and helpers
```

## Development and Testing

For development, install the package with development dependencies:

```shell
git clone https://github.com/Barlog951/emby-dedupe.git
cd emby-dedupe
pip install -e ".[dev]"
```

### Development Containers

#### VS Code Dev Container

This project includes a development container configuration for Visual Studio Code, which provides a consistent development environment with all necessary tools pre-installed:

1. Install [Visual Studio Code](https://code.visualstudio.com/) and the [Remote - Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension
2. Clone the repository and open it in VS Code
3. When prompted to "Reopen in Container", click "Reopen in Container"
   - Alternatively, use the Command Palette (F1) and select "Remote-Containers: Reopen in Container"
4. The container will build and start with Python 3.12, required dependencies, and useful extensions installed

The development container includes:
- Python 3.12 environment
- All project dependencies
- Development tools (ruff, mypy, pytest)
- Docker-in-Docker support for testing container builds
- GitHub CLI for managing issues and PRs
- VSCode extensions for Python development and GitHub integration

#### PyCharm Docker Integration

You can also use PyCharm with the included development Dockerfile:

1. Install [PyCharm Professional](https://www.jetbrains.com/pycharm/) (required for Docker integration)
2. Install Docker on your system
3. In PyCharm, go to File → Settings → Build, Execution, Deployment → Docker and set up Docker integration
4. Open the project in PyCharm
5. Set up a Docker-based Python interpreter:
   - Go to File → Settings → Project → Python Interpreter
   - Click the gear icon → Add
   - Select "Docker" and use the included `Dockerfile.dev`
   - Set the Python interpreter path to `/usr/local/bin/python`

For services and run configurations:
1. Go to Run → Edit Configurations
2. Add a new Python configuration
3. Set "Run with Docker" in the "Python interpreter" dropdown
4. Set the script path to your entry point (e.g., `/app/emby_dedupe/cli/main.py`)

The `Dockerfile.dev` includes:
- Python 3.12 environment
- Development dependencies 
- Common utilities needed for development
- The package installed in development mode

This provides a consistent environment for development and testing, regardless of your local setup.

### Running Tests

The project has a comprehensive test suite with 129 tests covering 70% of the codebase. The tests are organized in the same structure as the main package:

```
tests/
├── unit/
    ├── api/              # Tests for API client and deduplication
    ├── cli/              # Tests for CLI functionality
    ├── models/           # Tests for data models
    ├── reports/          # Tests for report generation
    └── utils/            # Tests for utility functions
```

To run the tests:

```shell
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=emby_dedupe

# Run tests with detailed coverage report
pytest --cov-report term-missing --cov=emby_dedupe

# Run a specific test file
pytest tests/unit/models/test_disjoint_set.py
```

You can use the provided Makefile for common development tasks:

```shell
# Run all tests
make test

# Run tests with coverage report
make coverage

# Run linting checks
make lint

# Run type checking
make mypy

# Auto-fix linting issues
make allfx

# Clean build artifacts
make clean

# Run all checks (clean, install dev dependencies, lint, type check, test)
make all
```

### Continuous Integration

The project uses GitHub Actions for continuous integration with these workflows:

1. **Python Tests**: Runs linting, type checking, and tests on every push and PR
2. **Security Scan**: Performs security analysis with Bandit and CodeQL
3. **Docker Builds**: Creates multi-architecture containers for edge, pre-release, and release tags

#### Test Coverage

Current test coverage by module:
- API Client: 80%
- Deduplication Logic: 59%
- Metadata Handling: 75%
- HTML Reports: 72%
- Markdown Reports: 68%
- Common Report Functions: 87%
- File Operations: 88%
- HTTP Utilities: 100%
- Logging: 100%
- CLI: 54%

The tests cover all key functionality including:
- Duplicate identification and rationalization
- Media quality rating
- HTML and Markdown report generation
- Language prioritization features
- Exclusion term processing
- Error handling and edge cases

## Contributing

We welcome your contributions. If you encounter bugs or have suggestions for improvement, please feel free to open an issue on the [GitHub repository](https://github.com/Barlog951/emby-dedupe). Pull requests are also greatly appreciated.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.