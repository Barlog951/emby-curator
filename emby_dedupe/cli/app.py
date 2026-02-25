"""
Typer CLI application for emby-dedupe.

This module is the central typer-based CLI entry point.  Shared options (host,
port, api-key, library, verbosity) are declared once in the @app.callback() and
passed to all subcommands via ctx.obj (AppConfig dataclass).

The business logic remains in the original cli/*.py modules; this file is a thin
routing layer so that existing internal functions and their tests are undisturbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import typer

# CRITICAL: invoke_without_command=True ensures @app.callback() fires BEFORE
# subcommands — without it ctx.obj is never set when a subcommand runs.
app = typer.Typer(
    name="emby-dedupe",
    no_args_is_help=True,
    invoke_without_command=True,
    help="Emby duplicate media manager and genre tool.",
)

genres_app = typer.Typer(name="genres", help="Genre audit and management.")
app.add_typer(genres_app, name="genres")

# Shared help strings used across multiple genre subcommands
_LOCK_OPT = "--lock/--no-lock"
_ALL_LIBS_HELP = "Scan all Emby libraries."
_ITEM_IDS_HELP = "Comma-separated Emby item IDs to process (skips full library scan)."


@dataclass
class AppConfig:
    host: Optional[str] = None
    port: Optional[int] = None
    api_key: Optional[str] = None
    libraries: list[str] = field(default_factory=list)
    verbosity: int = 0
    lock: bool = True
    doit: bool = False


@app.callback()
def common(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(
        None, "--host", "-H", envvar="DEDUPE_EMBY_HOST", help="Emby server URL."
    ),
    port: Optional[int] = typer.Option(
        None, "--port", "-p", envvar="DEDUPE_EMBY_PORT", help="Emby server port."
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-a", envvar="DEDUPE_EMBY_API_KEY", help="Emby API key."
    ),
    library: Optional[list[str]] = typer.Option(
        None,
        "--library",
        "-l",
        envvar="DEDUPE_EMBY_LIBRARY",
        help="Library name (repeatable).",
    ),
    verbose: int = typer.Option(
        0, "--verbose", "-v", count=True, help="Verbosity (-v, -vv, -vvv)."
    ),
    lock: bool = typer.Option(
        True, _LOCK_OPT, envvar="DEDUPE_LOCK", help="Lock genres after normalization."
    ),
    doit: bool = typer.Option(
        False, "--doit/--no-doit", envvar="DEDUPE_DOIT", help="Execute changes (dry-run by default)."
    ),
) -> None:
    """Emby duplicate media manager and genre tool."""
    ctx.ensure_object(dict)
    ctx.obj = AppConfig(
        host=host,
        port=port,
        api_key=api_key,
        libraries=library or [],
        verbosity=verbose,
        lock=lock,
        doit=doit,
    )


# ---------------------------------------------------------------------------
# dedupe subcommand
# ---------------------------------------------------------------------------

@app.command("dedupe")
def dedupe_cmd(
    ctx: typer.Context,
    username: Optional[str] = typer.Option(
        None, "--username", envvar="DEDUPE_EMBY_USERNAME", help="Emby username for auth."
    ),
    password: Optional[str] = typer.Option(
        None, "--password", envvar="DEDUPE_EMBY_PASSWORD", help="Emby password for auth."
    ),
    lang_prio: Optional[str] = typer.Option(
        None,
        "--lang-prio",
        envvar="DEDUPE_LANG_PRIO",
        help="Comma-separated language priority (e.g. 'sk,cs,en').",
    ),
    exclude_ids: Optional[str] = typer.Option(
        None,
        "--exclude-ids",
        envvar="DEDUPE_EXCLUDE_IDS",
        help="Comma-separated provider IDs to exclude.",
    ),
    html_report: bool = typer.Option(
        False, "--html-report", envvar="DEDUPE_HTML_REPORT", help="Generate HTML report."
    ),
    html_only: bool = typer.Option(
        False, "--html-only", envvar="DEDUPE_HTML_ONLY", help="HTML report only, no terminal output."
    ),
    no_open: bool = typer.Option(
        False, "--no-open", help="Don't open HTML report in browser."
    ),
) -> None:
    """Find and optionally remove duplicate media items."""
    import json
    from argparse import Namespace

    import httpx

    import emby_dedupe.api.client as _client_mod
    from emby_dedupe.api.client import handle_host_and_port, logout
    from emby_dedupe.cli.arguments import validate_required_arguments
    from emby_dedupe.cli.main import (
        _connect_and_fetch_libraries,
        _generate_reports,
        _resolve_configuration,
        _run_deduplication_pipeline,
    )
    from emby_dedupe.utils.exceptions import EmbyServerConnectionError

    config: AppConfig = ctx.obj

    # Build a Namespace that _resolve_configuration understands
    args = Namespace(
        verbosity=config.verbosity,
        host=config.host,
        port=config.port,
        api_key=config.api_key,
        library=config.libraries or None,
        doit=config.doit,
        lang_prio=lang_prio,
        exclude_ids=exclude_ids,
        username=username,
        password=password,
        html_report=html_report,
        html_only=html_only,
        no_open=no_open,
    )

    from emby_dedupe.utils.logging import logger
    (resolved_host, resolved_port, resolved_api_key, library, doit,
     lang_priorities, excluded_ids, resolved_username, resolved_password,
     resolved_html_report, resolved_html_only, resolved_no_open) = _resolve_configuration(args)

    validate_required_arguments(
        resolved_host, resolved_api_key, library, doit, resolved_username, resolved_password
    )

    validated_host, validated_port = handle_host_and_port(resolved_host, resolved_port)

    try:
        base_url = f"{validated_host}:{validated_port}"
        client = httpx.Client(headers={"X-Emby-Token": resolved_api_key})

        all_provider_tables = _connect_and_fetch_libraries(client, base_url, library)

        decisions, exclusion_metadata, markdown_report = _run_deduplication_pipeline(
            client, base_url, all_provider_tables, excluded_ids, lang_priorities,
            resolved_api_key, doit, resolved_username, resolved_password,
        )

        _generate_reports(
            base_url, decisions, exclusion_metadata, excluded_ids,
            lang_priorities, markdown_report,
            resolved_html_report, resolved_html_only, resolved_no_open,
        )

    except EmbyServerConnectionError as e:
        logger.error(str(e))
        raise typer.Exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON: {str(e)}")
        raise typer.Exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {str(e)}")
        raise typer.Exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")
        raise typer.Exit(1)
    finally:
        if _client_mod.auth_state.token_for_delete and doit:
            logout(client, base_url, _client_mod.auth_state.token_for_delete)


# ---------------------------------------------------------------------------
# check subcommand
# ---------------------------------------------------------------------------

@app.command("check")
def check_cmd(
    ctx: typer.Context,  # NOSONAR — typer CLI requires one param per CLI option; cannot reduce
    name: Optional[str] = typer.Option(None, "--name", help="Media name to search for."),
    year: Optional[int] = typer.Option(None, "--year", help="Release year (movies)."),
    imdb: Optional[str] = typer.Option(None, "--imdb", help="IMDB ID (e.g. tt1375666)."),
    tmdb: Optional[str] = typer.Option(None, "--tmdb", help="TMDB ID."),
    tvdb: Optional[str] = typer.Option(None, "--tvdb", help="TVDB ID."),
    season: Optional[int] = typer.Option(None, "--season", help="Season number."),
    episode: Optional[int] = typer.Option(None, "--episode", help="Episode number."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Resolution (2160p, 1080p …)."),
    codec: Optional[str] = typer.Option(None, "--codec", help="Video codec (x265, x264 …)."),
    hdr: Optional[str] = typer.Option(None, "--hdr", help="HDR type (HDR, DV, SDR …)."),
    audio: Optional[str] = typer.Option(None, "--audio", help="Audio type (Atmos, DTS-HD …)."),
    audio_lang: Optional[str] = typer.Option(None, "--audio-lang", help="Comma-separated audio languages."),
    size_mb: Optional[int] = typer.Option(None, "--size-mb", help="File size in MB."),
    bitrate_kbps: Optional[int] = typer.Option(None, "--bitrate-kbps", help="Video bitrate in kbps."),
    simple: bool = typer.Option(False, "--simple", help="Simple output: 'download' or 'skip'."),
    exit_code: bool = typer.Option(False, "--exit-code", help="Exit code only: 0=download, 1=skip."),
    all_libraries: bool = typer.Option(False, "--all-libraries", help="Search all libraries."),
    lang_prio: Optional[str] = typer.Option(None, "--lang-prio", envvar="DEDUPE_LANG_PRIO"),
    exclude_ids: Optional[str] = typer.Option(None, "--exclude-ids", envvar="DEDUPE_EXCLUDE_IDS"),
    cache: bool = typer.Option(True, "--cache/--no-cache", help="Use cached library data."),
) -> None:
    """Check whether media should be downloaded based on existing library."""
    from argparse import Namespace

    from emby_dedupe.cli.check import run_check

    config: AppConfig = ctx.obj

    args = Namespace(
        host=config.host,
        api_key=config.api_key,
        library=config.libraries or None,
        verbosity=config.verbosity,
        name=name,
        year=year,
        imdb=imdb,
        tmdb=tmdb,
        tvdb=tvdb,
        season=season,
        episode=episode,
        resolution=resolution,
        codec=codec,
        hdr=hdr,
        audio=audio,
        audio_lang=audio_lang,
        size_mb=size_mb,
        bitrate_kbps=bitrate_kbps,
        simple=simple,
        exit_code=exit_code,
        all_libraries=all_libraries,
        lang_prio=lang_prio,
        exclude_ids=exclude_ids,
        cache=cache,
    )

    result = run_check(args)
    raise typer.Exit(result)


# ---------------------------------------------------------------------------
# missing-episodes subcommand
# ---------------------------------------------------------------------------

@app.command("missing-episodes")
def missing_episodes_cmd(
    ctx: typer.Context,
    format: str = typer.Option(
        "console",
        "--format",
        help="Output format: console, html, json, structured_json.",
    ),
    output: Optional[str] = typer.Option(
        None, "--output", help="Output file path for JSON formats."
    ),
    username: Optional[str] = typer.Option(
        None, "--username", envvar="DEDUPE_EMBY_USERNAME", help="Emby username."
    ),
    password: Optional[str] = typer.Option(
        None, "--password", envvar="DEDUPE_EMBY_PASSWORD", help="Emby password."
    ),
    html_report: bool = typer.Option(False, "--html-report", envvar="DEDUPE_HTML_REPORT"),
    html_only: bool = typer.Option(False, "--html-only", envvar="DEDUPE_HTML_ONLY"),
) -> None:
    """Search for missing episodes in TV series libraries."""
    from argparse import Namespace

    from emby_dedupe.cli.missing_episodes import run_missing_episodes_command

    config: AppConfig = ctx.obj

    args = Namespace(
        host=config.host,
        port=config.port,
        api_key=config.api_key,
        library=config.libraries or None,
        verbosity=config.verbosity,
        format=format,
        output=output,
        username=username,
        password=password,
        html_report=html_report,
        html_only=html_only,
    )

    run_missing_episodes_command(args)


# ---------------------------------------------------------------------------
# genres subcommands
# ---------------------------------------------------------------------------

@genres_app.callback(invoke_without_command=True)
def genres_callback(ctx: typer.Context) -> None:
    """Genre audit and management."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@genres_app.command("audit")
def genres_audit(
    ctx: typer.Context,
    suggest: bool = typer.Option(
        False, "--suggest", help="Flag non-canonical genres and suggest mappings."
    ),
    output_json: Optional[str] = typer.Option(
        None, "--output-json", help="Save audit results as JSON to this path."
    ),
    all_libraries: bool = typer.Option(False, "--all-libraries", help=_ALL_LIBS_HELP),
    item_ids: Optional[str] = typer.Option(None, "--item-ids", help=_ITEM_IDS_HELP),
) -> None:
    """Audit genre health across libraries (read-only)."""
    _run_genres_subcommand(
        ctx,
        action="audit",
        all_libraries=all_libraries,
        item_ids=item_ids,
        suggest=suggest,
        output_json=output_json,
    )


@genres_app.command("normalize")
def genres_normalize(
    ctx: typer.Context,
    doit: bool = typer.Option(False, "--doit", help="Apply normalization (dry-run by default)."),
    lock: bool = typer.Option(True, _LOCK_OPT, help="Lock genres after update."),
    repair_dupes: bool = typer.Option(
        False, "--repair-dupes", help="Also fix duplicate genres caused by normalization collisions."
    ),
    all_libraries: bool = typer.Option(False, "--all-libraries", help=_ALL_LIBS_HELP),
    item_ids: Optional[str] = typer.Option(None, "--item-ids", help=_ITEM_IDS_HELP),
) -> None:
    """Fix variant genre names (Sci-Fi→Science Fiction, dada→Comedy …)."""
    _run_genres_subcommand(
        ctx,
        action="normalize",
        doit=doit,
        lock=lock,
        repair_dupes=repair_dupes,
        all_libraries=all_libraries,
        item_ids=item_ids,
    )


@genres_app.command("fix")
def genres_fix(
    ctx: typer.Context,
    doit: bool = typer.Option(False, "--doit", help="Apply changes (dry-run by default)."),
    lock: bool = typer.Option(True, _LOCK_OPT, help="Lock genres after update."),
    gaps_only: bool = typer.Option(False, "--gaps-only", help="Only process items with no genres."),
    validate: bool = typer.Option(
        False, "--validate", help="Compare existing genres against TMDB/OMDb and add missing ones."
    ),
    tmdb_api_key: Optional[str] = typer.Option(
        None, "--tmdb-api-key", envvar="DEDUPE_TMDB_API_KEY", help="TMDB API key."
    ),
    all_libraries: bool = typer.Option(False, "--all-libraries", help=_ALL_LIBS_HELP),
    item_ids: Optional[str] = typer.Option(None, "--item-ids", help=_ITEM_IDS_HELP),
) -> None:
    """Fetch genres from TMDB/OMDb and fill gaps or validate existing genres."""
    _run_genres_subcommand(
        ctx,
        action="fix",
        doit=doit,
        lock=lock,
        gaps_only=gaps_only,
        validate=validate,
        tmdb_api_key=tmdb_api_key,
        all_libraries=all_libraries,
        item_ids=item_ids,
    )


def _run_genres_subcommand(ctx: typer.Context, **kwargs) -> None:
    """Shared dispatcher: build an argparse-like Namespace and call run_genres_command."""
    from argparse import Namespace

    from emby_dedupe.cli.genres import run_genres_command

    config: AppConfig = ctx.obj if ctx.obj else AppConfig()

    # Merge AppConfig fields with subcommand-specific kwargs.
    # Subcommand kwargs take precedence over AppConfig when both are present.
    args = Namespace(
        host=config.host,
        port=config.port,
        api_key=config.api_key,
        library=config.libraries or [],
        verbosity=config.verbosity,
        # subcommand-specific fields (with sensible defaults)
        action=kwargs.get("action", "audit"),
        doit=kwargs.get("doit", config.doit),
        lock=kwargs.get("lock", config.lock),
        repair_dupes=kwargs.get("repair_dupes", False),
        suggest=kwargs.get("suggest", False),
        output_json=kwargs.get("output_json", None),
        all_libraries=kwargs.get("all_libraries", False),
        item_ids=kwargs.get("item_ids", None),
        gaps_only=kwargs.get("gaps_only", False),
        validate=kwargs.get("validate", False),
        tmdb_api_key=kwargs.get("tmdb_api_key", None),
    )

    run_genres_command(args)


def main() -> None:
    """Entry point for the emby-dedupe CLI."""
    app()
