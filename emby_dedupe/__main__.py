#!/usr/bin/env python

"""
Enhanced main entry point for the Emby Dedupe tool.
Supports both deduplication and missing episodes functionality.
"""

import sys
from pathlib import Path

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # python-dotenv not available, environment variables must be set manually
    pass

from emby_dedupe.cli.main import main as dedupe_main


def main():
    """
    Main entry point that routes to appropriate functionality based on arguments.
    """
    try:
        # Check if we have the --missing-episodes flag
        if "--missing-episodes" in sys.argv:
            # Import missing episodes functionality only when needed
            import argparse

            from emby_dedupe.cli.missing_episodes import run_missing_episodes_command

            # Create a parser for all arguments including missing episodes
            parser = argparse.ArgumentParser(description="Emby Media Deduplication and Missing Episodes Tool.")

            # Add all existing arguments
            parser.add_argument("-v", "--verbosity", action="count", default=0, help="Increase verbosity of logging for each occurrence.")
            parser.add_argument("--host", type=str, help="The hostname of the Emby server.")
            parser.add_argument("-p", "--port", type=int, help="The port number to use for the Emby server.")
            parser.add_argument("-a", "--api-key", type=str, help="The Emby server API key.")
            parser.add_argument("-l", "--library", type=str, action="append", help="The Emby library to scan. Can be specified multiple times.")
            parser.add_argument("--doit", action="store_true", help="Must be provided for the script to remove media.")
            parser.add_argument("--username", type=str, help="The Emby username to use for authentication.")
            parser.add_argument("--password", type=str, help="The Emby password to use for authentication.")
            parser.add_argument("--html-report", action="store_true", help="Generate an HTML report and open it in the browser.")
            parser.add_argument("--no-open", action="store_true", help="Generate an HTML report but don't open it in the browser.")
            parser.add_argument("--html-only", action="store_true", help="Generate only HTML report without terminal output.")
            parser.add_argument("--lang-prio", type=str, help="Comma-separated list of language codes in priority order.")
            parser.add_argument("--exclude-ids", type=str, help="Comma-separated list of provider IDs to exclude from deduplication.")

            # Add missing episodes specific arguments
            parser.add_argument("--missing-episodes", action="store_true", help="Search for missing episodes instead of duplicates")
            parser.add_argument("--format", choices=["console", "html", "json", "structured_json"], default="console", help="Output format for missing episodes report")
            parser.add_argument("--output", type=str, help="Output file path for JSON formats (default: missing_episodes-YYYYMMDD_HHMMSS.json)")

            # Parse all arguments
            args = parser.parse_args()

            # Run missing episodes command
            run_missing_episodes_command(args)
        else:
            # Run existing deduplication functionality unchanged
            dedupe_main()

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)


if __name__ == "__main__":
    main()
