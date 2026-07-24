#!/usr/bin/env python

"""
Package entry point — delegates to the typer CLI application.
"""

# .env loading happens at emby_dedupe.cli.app import time (it must precede typer's
# envvar resolution), so this entry point only needs to import the app.
from emby_dedupe.cli.app import app


def main() -> None:
    """Entry point wrapper — delegates to the typer app."""
    app()


if __name__ == "__main__":
    main()
