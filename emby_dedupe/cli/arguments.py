"""
Command-line argument parsing for the Emby Dedupe tool.
"""

import argparse
import os
import sys
from typing import Optional

from emby_dedupe.utils.logging import logger


def add_dedupe_arguments(parser: argparse.ArgumentParser) -> None:
    """Add deduplication-specific arguments to a parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "-v",
        "--verbosity",
        action="count",
        default=0,
        help="Increase verbosity of logging for each occurrence.",
    )
    parser.add_argument("--host", type=str, help="The hostname of the Emby server.")
    parser.add_argument(
        "-p", "--port", type=int, help="The port number to use for the Emby server."
    )
    parser.add_argument("-a", "--api-key", type=str, help="The Emby server API key.")
    parser.add_argument(
        "-l", "--library", type=str, action="append", help="The Emby library to scan for duplicates. Can be specified multiple times."
    )
    parser.add_argument(
        "--doit",
        action="store_true",
        help="Must be provided for the script to remove media.",
    )
    parser.add_argument(
        "--username",
        type=str,
        help="The Emby username to use for authentication.",
    )
    parser.add_argument(
        "--password",
        type=str,
        help="The Emby password to use for authentication.",
    )
    parser.add_argument(
        "--html-report",
        action="store_true",
        help="Generate an HTML report and open it in the browser. In Docker, use DEDUPE_HTML_REPORT=true.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Generate an HTML report but don't open it in the browser (only with --html-report).",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Generate only HTML report without terminal output. Implies --html-report.",
    )
    parser.add_argument(
        "--lang-prio",
        type=str,
        help="Comma-separated list of language codes in priority order (e.g., 'slo,cze,eng'). Items with higher priority languages will be kept over others.",
    )
    parser.add_argument(
        "--exclude-ids",
        type=str,
        help="Comma-separated list of provider IDs to exclude from deduplication (e.g., 'tt1234567,123456'). Works with IMDB (tt prefix), TMDB, and TVDB IDs.",
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments and return the parsed arguments.

    Supports subcommands:
    - (no subcommand): Run deduplication (default, backward compatible)
    - check: Check if media should be downloaded
    - genres: Genre audit and normalization

    Returns:
        argparse.Namespace: An object holding attributes based on command-line arguments.
    """
    # Check if 'genres' is the first argument to route to genres subcommand
    if len(sys.argv) > 1 and sys.argv[1] == 'genres':
        return parse_genres_args()

    # Check if 'check' is the first argument to route to check subcommand
    if len(sys.argv) > 1 and sys.argv[1] == 'check':
        return parse_check_args()

    # Default: parse dedupe arguments (backward compatible)
    parser = argparse.ArgumentParser(description="Emby Media Deduplication Script.")
    add_dedupe_arguments(parser)
    return parser.parse_args()


def parse_check_args() -> argparse.Namespace:
    """Parse arguments for the check subcommand.

    Returns:
        argparse.Namespace: Parsed arguments for check command.
    """
    from emby_dedupe.cli.check import add_check_arguments

    parser = argparse.ArgumentParser(
        description="Check if media should be downloaded based on existing Emby library.",
        prog="emby-dedupe check",
    )
    add_check_arguments(parser)

    # Parse arguments (skip 'check' command)
    args = parser.parse_args(sys.argv[2:])
    args.command = 'check'
    return args


def parse_genres_args() -> argparse.Namespace:
    """Parse arguments for the genres subcommand.

    Returns:
        argparse.Namespace: Parsed arguments for genres command.
    """
    from emby_dedupe.cli.genres import add_genres_arguments

    parser = argparse.ArgumentParser(
        description="Genre audit and normalization for Emby libraries.",
        prog="emby-dedupe genres",
    )
    add_genres_arguments(parser)

    # Parse arguments (skip 'genres' command word)
    args = parser.parse_args(sys.argv[2:])
    args.command = 'genres'
    return args


def get_env_variable(name: str) -> Optional[str]:
    """Get the value of an environment variable.

    Args:
        name (str): The name of the environment variable to retrieve.

    Returns:
        Optional[str]: The value of the environment variable, if it exists.
    """
    return os.environ.get(name)


def override_warning(arg_name: str, cmd_val: Optional[str], env_val: Optional[str]) -> None:
    """Print a warning if a command-line argument overrides an environment variable.

    Args:
        arg_name (str): The name of the argument being overridden.
        cmd_val (str): The value from the command line.
        env_val (str): The value from the environment variable.
    """
    if cmd_val and env_val:
        logger.warning(
            f"Warning: The command-line argument {arg_name} ('{cmd_val}') "
            f"overrides the environment variable ('{env_val}')."
        )


def validate_required_arguments(
    host: Optional[str],
    api_key: Optional[str],
    libraries: list,
    doit: bool,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """Validate that required arguments are provided.

    Args:
        host (Optional[str]): The host of the Emby server.
        api_key (Optional[str]): The API key for the Emby server.
        libraries (list): A list of libraries to scan for duplicates.
        doit (bool): True if the script will perform deletions.
        username (Optional[str], optional): The username to use for authentication. Defaults to None.
        password (Optional[str], optional): The password to use for authentication. Defaults to None.
    """
    missing_args = []

    for arg, value in {
        "host": host,
        "api-key": api_key,
    }.items():
        if not value:
            missing_args.append(arg)

    if not libraries:
        missing_args.append("library")

    # Check for username and password if deletions will be performed
    if doit:
        if not username:
            missing_args.append("username")
        if not password:
            missing_args.append("password")

    if missing_args:
        missing_args_str = ", ".join(missing_args)
        logger.error(f"Error: Missing required arguments: {missing_args_str}")
        logger.error("Use -h for help.")
        import sys
        sys.exit(1)
