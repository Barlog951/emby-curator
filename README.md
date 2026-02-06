# Emby Deduplication Tool

The Emby Deduplication Tool assists in managing media libraries on Emby servers by identifying potential duplicate items. It compares media items within your Emby library and generates a report detailing duplicates that may warrant removal.

## Latest Versions

![GitHub release (latest by date)](https://img.shields.io/github/v/release/troykelly/emby-dedupe)
![GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/troykelly/emby-dedupe?include_prereleases&label=pre-release)

## Build Status

![Release Build Status](https://github.com/troykelly/emby-dedupe/actions/workflows/release.yaml/badge.svg)
![Python Tests](https://github.com/troykelly/emby-dedupe/actions/workflows/python-test.yaml/badge.svg)
![Security Scan](https://github.com/troykelly/emby-dedupe/actions/workflows/security-scan.yaml/badge.svg)

The following architectures are supported in the latest Docker version:

| Architecture    | Supported          |
|-----------------|--------------------|
| `amd64`         | :white_check_mark: |
| `arm64`         | :white_check_mark: |
| `arm/v7`        | :white_check_mark: |
| `arm/v6`        | :white_check_mark: |
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
docker pull ghcr.io/troykelly/emby-dedupe:latest
```

Replace `latest` with the appropriate tag to use a specific version of the container.

### Using Python

You can also install the tool directly using pip:

```shell
pip install git+https://github.com/troykelly/emby-dedupe.git
```

Or clone the repository and install locally:

```shell
git clone https://github.com/troykelly/emby-dedupe.git
cd emby-dedupe
pip install -e .
```

## Usage

The `emby-dedupe` tool can be run either through the Docker container or directly as a Python command.

### Basic Usage

```shell
emby-dedupe --host http://your-emby-server --library "Your Library Name" --api-key your_api_key
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

Here are the available command-line arguments:

```
usage: emby-dedupe [-h] [-v] [--host HOST] [-p PORT] [-a API_KEY] [-l LIBRARY] [--doit] [--username USERNAME] 
                   [--password PASSWORD] [--html-report] [--no-open] [--html-only] [--lang-prio LANG_PRIO]
                   [--exclude-ids EXCLUDE_IDS] [--exclude EXCLUDE]

options:
  -h, --help            show this help message and exit
  -v, --verbosity       increase verbosity of logging for each occurrence
  --host HOST           the hostname of the Emby server
  -p PORT, --port PORT  the port number to use for the Emby server
  -a API_KEY, --api-key API_KEY
                        the Emby server API key
  -l LIBRARY, --library LIBRARY
                        the Emby library to scan for duplicates. Can be specified multiple times.
  --doit                must be provided for the script to remove media
  --username USERNAME   the Emby username to use for authentication
  --password PASSWORD   the Emby password to use for authentication
  --html-report         generate an HTML report and open it in the browser
  --no-open             generate an HTML report but don't open it in the browser
  --html-only           generate only HTML report without terminal output
  --lang-prio LANG_PRIO comma-separated list of language codes in priority order (e.g., 'slo,cze,eng').
                        Items with higher priority languages will be kept over others.
  --exclude-ids EXCLUDE_IDS
                        comma-separated list of provider IDs to exclude from deduplication (e.g., 'tt1234567,123456').
                        Works with IMDB (tt prefix), TMDB, and TVDB IDs.
  --exclude EXCLUDE     comma-separated list of terms to exclude from deduplication.
                        If a movie title contains any of these terms, it will be skipped.
```

## Examples

### Generating a List of Duplicates (Dry Run)

The following command simulates the deduplication process to provide a list of proposed changes without applying any:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Your Library Name" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  ghcr.io/troykelly/emby-dedupe

# For multiple libraries
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Movies,TV Shows" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  ghcr.io/troykelly/emby-dedupe
```

Using Python:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key"
```

To scan multiple libraries:

```shell
emby-dedupe --host "http://your-emby-server" --library "Movies" --library "TV Shows" --api-key "your_api_key"
```

### Generating an HTML Report

To generate an HTML report with images and detailed metadata:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" --html-report
```

Or to generate only the HTML report without terminal output:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" --html-only
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
  ghcr.io/troykelly/emby-dedupe
```

Using Python:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" \
            --username "your_emby_username" --password "your_emby_password" --doit
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
  ghcr.io/troykelly/emby-dedupe
```

Using Python:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" --lang-prio "slo,cze,eng"
```

This example will prioritize keeping files with Slovak audio tracks first, then Czech, then English. When multiple files have the same highest-priority language, the one with better quality will be kept.

### Excluding Media by Provider IDs

To exclude specific media items from deduplication using their provider IDs:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Movies" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  -e DEDUPE_EXCLUDE_IDS="tt0468569,tt0080684,550" \
  ghcr.io/troykelly/emby-dedupe
```

Using Python:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" --exclude-ids "tt0468569,tt0080684,550"
```

This example will exclude The Dark Knight (tt0468569), The Empire Strikes Back (tt0080684), and Fight Club (TMDB ID 550) from the deduplication process. This is useful for preserving multiple versions of the same movie or TV show.

The excluded IDs can be:
- IMDB IDs (start with "tt" followed by 7-8 digits)
- TMDB IDs (numeric values)
- TVDB IDs (numeric values)

### Using Exclusion Terms

To exclude certain movies from deduplication based on their titles:

Using Docker:

```shell
docker run \
  -e DEDUPE_EMBY_HOST="http://your-emby-server" \
  -e DEDUPE_EMBY_LIBRARY="Movies" \
  -e DEDUPE_EMBY_API_KEY="your_api_key" \
  -e DEDUPE_EXCLUDE="extended,unrated,director" \
  ghcr.io/troykelly/emby-dedupe
```

Using Python:

```shell
emby-dedupe --host "http://your-emby-server" --library "Your Library Name" --api-key "your_api_key" --exclude "extended,unrated,director"
```

This example will exclude movies with "extended", "unrated", or "director" in their titles from the deduplication process. This is useful for keeping special editions alongside regular versions.

The exclusion feature uses smart title matching:

- **Case-insensitive**: "Lord of the Rings" will match "LORD OF THE RINGS"
- **Partial matching**: A term like "lord of the rings" will match any title containing this phrase, such as "The Lord of the Rings: The Fellowship of the Ring", "Lord of the Rings 2", etc.
- **Multiple fields**: Matching is performed on the main title, original title, and series name (for TV shows)
- **Special character handling**: Punctuation and special characters are ignored during matching

When items are excluded from deduplication, they'll appear in a separate section in the generated reports.

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
git clone https://github.com/troykelly/emby-dedupe.git
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

We welcome your contributions. If you encounter bugs or have suggestions for improvement, please feel free to open an issue on the [GitHub repository](https://github.com/troykelly/emby-dedupe). Pull requests are also greatly appreciated.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.