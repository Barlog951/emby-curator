#!/usr/bin/env python

"""
Package entry point — delegates to the typer CLI application.
"""

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

from emby_dedupe.cli.app import app


def main() -> None:
    """Entry point wrapper — delegates to the typer app."""
    app()


if __name__ == "__main__":
    main()
