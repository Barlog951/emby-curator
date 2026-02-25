"""
CLI check command for checking if media should be downloaded.

Usage:
    emby-dedupe check --name "Inception" --year 2010 --resolution 2160p
    emby-dedupe check --simple --name "Breaking Bad" --season 1 --episode 1
    emby-dedupe check --exit-code --imdb tt1375666 --resolution 4k
"""

import json
from typing import Any

from emby_dedupe.api.checker import EmbyChecker
from emby_dedupe.utils.config import Config
from emby_dedupe.utils.logging import logger


def run_check(args) -> int:
    """Run the check command.

    Args:
        args: Parsed argparse namespace.

    Returns:
        Exit code: 0=download, 1=skip
    """
    # Build configuration from CLI args
    config = Config.from_cli_args(args)

    # Validate configuration
    errors = config.validate()
    if errors:
        if not getattr(args, 'simple', False) and not getattr(args, 'exit_code', False):
            print(json.dumps({
                "error": f"Configuration error: {', '.join(errors)}",
                "recommendation": "error",
            }))
        else:
            logger.error(f"Configuration error: {', '.join(errors)}")
        return 2

    # Create checker
    checker = EmbyChecker(config=config)

    try:
        # Get search parameters from args
        search_params = _extract_search_params(args)
        quality_params = _extract_quality_params(args)

        # Run check
        result = checker.check(**search_params, **quality_params)

        # Output based on format
        output_format = _get_output_format(args)

        if output_format == "simple":
            print(result.recommendation)
        elif output_format == "exit_code":
            pass  # Just return exit code
        else:  # json
            print(json.dumps(result.to_dict(), indent=2))

        # Return exit code
        return 0 if result.should_download else 1

    except Exception as e:
        if not getattr(args, 'simple', False) and not getattr(args, 'exit_code', False):
            print(json.dumps({
                "error": str(e),
                "recommendation": "error",
            }))
        else:
            logger.error(f"Error: {e}")
        return 2

    finally:
        checker.close()


def _extract_search_params(args) -> dict[str, Any]:
    """Extract search parameters from args."""
    params = {}

    if getattr(args, 'name', None):
        params['name'] = args.name
    if getattr(args, 'year', None):
        params['year'] = args.year
    if getattr(args, 'imdb', None):
        params['imdb'] = args.imdb
    if getattr(args, 'tmdb', None):
        params['tmdb'] = args.tmdb
    if getattr(args, 'tvdb', None):
        params['tvdb'] = args.tvdb
    season = getattr(args, 'season', None)
    if season is not None:
        params['season'] = season
    episode = getattr(args, 'episode', None)
    if episode is not None:
        params['episode'] = episode

    return params


def _extract_quality_params(args) -> dict[str, Any]:
    """Extract quality parameters from args."""
    params = {}

    if getattr(args, 'resolution', None):
        params['resolution'] = args.resolution
    if getattr(args, 'codec', None):
        params['codec'] = args.codec
    if getattr(args, 'hdr', None):
        params['hdr'] = args.hdr
    if getattr(args, 'audio', None):
        params['audio'] = args.audio
    audio_lang = getattr(args, 'audio_lang', None)
    if audio_lang:
        params['audio_languages'] = [lang.strip() for lang in audio_lang.split(',')]
    if getattr(args, 'size_mb', None):
        params['size_mb'] = args.size_mb
    if getattr(args, 'bitrate_kbps', None):
        params['bitrate_kbps'] = args.bitrate_kbps

    return params


def _get_output_format(args) -> str:
    """Get the output format from args."""
    if getattr(args, 'simple', False):
        return "simple"
    if getattr(args, 'exit_code', False):
        return "exit_code"
    return "json"


def add_check_arguments(parser) -> None:
    """Add check-specific arguments to a parser.

    Args:
        parser: ArgumentParser or subparser to add arguments to.
    """
    # Connection arguments
    parser.add_argument(
        "--host",
        type=str,
        help="Emby server URL (e.g., https://emby.example.com)",
    )
    parser.add_argument(
        "-a", "--api-key",
        type=str,
        help="Emby API key",
    )
    parser.add_argument(
        "-l", "--library",
        type=str,
        action="append",
        help="Library to search (can be specified multiple times). Omit to search all libraries.",
    )
    parser.add_argument(
        "--all-libraries",
        action="store_true",
        help="Search all libraries (default if no --library specified)",
    )

    # Deduplication settings
    parser.add_argument(
        "--lang-prio",
        type=str,
        help="Comma-separated language priority (e.g., 'sk,cs,en')",
    )
    parser.add_argument(
        "--exclude-ids",
        type=str,
        help="Comma-separated provider IDs to exclude",
    )

    # Search criteria
    parser.add_argument(
        "--name",
        type=str,
        help="Media name to search for",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Release year (for movies)",
    )
    parser.add_argument(
        "--imdb",
        type=str,
        help="IMDB ID (e.g., tt1375666)",
    )
    parser.add_argument(
        "--tmdb",
        type=str,
        help="TMDB ID",
    )
    parser.add_argument(
        "--tvdb",
        type=str,
        help="TVDB ID",
    )
    parser.add_argument(
        "--season",
        type=int,
        help="Season number (for TV shows)",
    )
    parser.add_argument(
        "--episode",
        type=int,
        help="Episode number (for TV shows)",
    )

    # Quality information
    parser.add_argument(
        "--resolution",
        type=str,
        help="Resolution (2160p, 1080p, 720p, 480p)",
    )
    parser.add_argument(
        "--codec",
        type=str,
        help="Video codec (x265, x264, HEVC, AV1)",
    )
    parser.add_argument(
        "--hdr",
        type=str,
        help="HDR type (HDR, DV, HDR10+, SDR)",
    )
    parser.add_argument(
        "--audio",
        type=str,
        help="Audio type (Atmos, DTS-HD, TrueHD, AC3)",
    )
    parser.add_argument(
        "--audio-lang",
        type=str,
        help="Audio languages in torrent (comma-separated, e.g., 'cze,eng')",
    )
    parser.add_argument(
        "--size-mb",
        type=int,
        help="File size in MB",
    )
    parser.add_argument(
        "--bitrate-kbps",
        type=int,
        help="Video bitrate in kbps",
    )

    # Output format
    parser.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Output as JSON (default)",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Simple output: 'download' or 'skip'",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit code only: 0=download, 1=skip",
    )

    # Caching
    parser.add_argument(
        "--cache",
        action="store_true",
        default=None,
        help="Use cached library data",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching",
    )

    # Verbosity
    parser.add_argument(
        "-v", "--verbosity",
        action="count",
        default=0,
        help="Increase verbosity",
    )
