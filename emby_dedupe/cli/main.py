"""
Main command-line interface for the Emby Dedupe tool.
"""

import json
import logging
import sys
from pathlib import Path

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # python-dotenv not available, environment variables must be set manually
    pass

import httpx

from emby_dedupe.api.client import (
    authenticated_token_for_delete,
    check_emby_connection,
    fetch_and_process_media_items,
    get_library_id,
    handle_host_and_port,
    logout,
)
from emby_dedupe.api.deduplication import (
    identify_duplicates,
    process_deletion_and_generate_report,
    process_duplicate_groups,
    rationalize_duplicates,
)
from emby_dedupe.cli.arguments import (
    get_env_variable,
    override_warning,
    parse_args,
    validate_required_arguments,
)
from emby_dedupe.reports.html import generate_html_report
from emby_dedupe.reports.markdown import output_report_to_stdout
from emby_dedupe.utils.constants import (
    ENV_DEDUPE_DOIT,
    ENV_DEDUPE_EMBY_API_KEY,
    ENV_DEDUPE_EMBY_HOST,
    ENV_DEDUPE_EMBY_LIBRARY,
    ENV_DEDUPE_EMBY_PASSWORD,
    ENV_DEDUPE_EMBY_PORT,
    ENV_DEDUPE_EMBY_USERNAME,
    ENV_DEDUPE_EXCLUDE_IDS,
    ENV_DEDUPE_HTML_ONLY,
    ENV_DEDUPE_HTML_REPORT,
    ENV_DEDUPE_LANG_PRIO,
    ENV_DEDUPE_LOGGING,
    LANGUAGE_NORMALIZATION_MAP,
)
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.file_ops import dump_object_to_file
from emby_dedupe.utils.logging import logger, set_logging_level


def _parse_language_priorities(lang_prio_str: str) -> list:
    """
    Parse and normalize language priority string.

    Args:
        lang_prio_str: Comma-separated language priority string.

    Returns:
        List of normalized language codes.
    """
    lang_priorities = []
    if lang_prio_str:
        # Create normalized language priority list treating Slovak/Czech variants as equivalent
        lang_mapping = LANGUAGE_NORMALIZATION_MAP

        raw_langs = [lang.strip().lower() for lang in lang_prio_str.split(',') if lang.strip()]
        seen_langs = set()

        for lang in raw_langs:
            # Normalize Slovak/Czech variants, keep others as-is
            normalized_lang = lang_mapping.get(lang, lang)

            # Only add if we haven't seen this normalized language before
            if normalized_lang not in seen_langs:
                lang_priorities.append(normalized_lang)
                seen_langs.add(normalized_lang)

        logger.info(f"Language priorities set: {', '.join(lang_priorities)} (Slovak/Czech variants normalized)")
        if raw_langs != [lang_mapping.get(lang, lang) for lang in raw_langs]:
            logger.debug(f"Original input: {', '.join(raw_langs)}")
    else:
        logger.debug("No language priorities specified, using default quality-based evaluation")

    return lang_priorities


def _parse_excluded_ids(exclude_ids_str: str) -> list:
    """
    Parse excluded IDs from comma-separated string.

    Args:
        exclude_ids_str: Comma-separated ID string.

    Returns:
        List of excluded ID strings.
    """
    excluded_ids = []
    if exclude_ids_str:
        excluded_ids = [id.strip() for id in exclude_ids_str.split(',') if id.strip()]
        logger.info(f"Excluding provider IDs from deduplication: {', '.join(excluded_ids)}")
    else:
        logger.debug("No provider IDs excluded from deduplication")
    return excluded_ids


def _load_env_variables():
    """
    Load all environment variables.

    Returns:
        Dictionary of environment variable values.
    """
    return {
        'verbosity': get_env_variable(ENV_DEDUPE_LOGGING),
        'host': get_env_variable(ENV_DEDUPE_EMBY_HOST),
        'port': get_env_variable(ENV_DEDUPE_EMBY_PORT),
        'api_key': get_env_variable(ENV_DEDUPE_EMBY_API_KEY),
        'library_str': get_env_variable(ENV_DEDUPE_EMBY_LIBRARY),
        'doit': get_env_variable(ENV_DEDUPE_DOIT) in ("true", "True", "TRUE", "1"),
        'html_report': get_env_variable(ENV_DEDUPE_HTML_REPORT) in ("true", "True", "TRUE", "1"),
        'html_only': get_env_variable(ENV_DEDUPE_HTML_ONLY) in ("true", "True", "TRUE", "1"),
        'lang_prio': get_env_variable(ENV_DEDUPE_LANG_PRIO),
        'exclude_ids': get_env_variable(ENV_DEDUPE_EXCLUDE_IDS),
    }


def _apply_override_warnings(args, env_vars):
    """
    Apply override warnings for command-line arguments vs environment variables.

    Args:
        args: Parsed argument namespace.
        env_vars: Dictionary of environment variable values.
    """
    set_logging_level(args.verbosity, env_vars['verbosity'])
    override_warning("--verbosity", args.verbosity and str(logger.level), env_vars['verbosity'])
    override_warning("--host", args.host, env_vars['host'])
    override_warning("--port", args.port and str(args.port), env_vars['port'])
    override_warning("--api-key", args.api_key, env_vars['api_key'])

    env_library = [lib.strip() for lib in env_vars['library_str'].split(',')] if env_vars['library_str'] else None
    override_warning(
        "--library",
        args.library and ','.join(args.library) if args.library else None,
        env_library and ','.join(env_library) if env_library else None
    )
    override_warning("--lang-prio", args.lang_prio, env_vars['lang_prio'])
    override_warning("--exclude-ids", args.exclude_ids, env_vars['exclude_ids'])


def _resolve_auth_credentials(args, doit):
    """
    Resolve authentication credentials if doit is enabled.

    Args:
        args: Parsed argument namespace.
        doit: Whether to actually delete items.

    Returns:
        Tuple of (username, password).
    """
    if doit:
        username = args.username or get_env_variable(ENV_DEDUPE_EMBY_USERNAME)
        password = args.password or get_env_variable(ENV_DEDUPE_EMBY_PASSWORD)
        return username, password
    return None, None


def _resolve_configuration(args):
    """
    Resolve configuration from command-line arguments and environment variables.

    Args:
        args: Parsed argument namespace.

    Returns:
        Tuple of (host, port, api_key, library, doit, lang_priorities, excluded_ids, username, password,
                  html_report, html_only, no_open).
    """
    env_vars = _load_env_variables()
    _apply_override_warnings(args, env_vars)

    logger.debug("Collecting final values for required settings")

    # Parse library list from environment
    env_library = [lib.strip() for lib in env_vars['library_str'].split(',')] if env_vars['library_str'] else None

    # Resolve core configuration
    host = args.host or env_vars['host']
    port = args.port or env_vars['port'] or None
    api_key = args.api_key or env_vars['api_key']
    library = args.library or env_library or []
    doit = args.doit or env_vars['doit']

    # Handle language priorities
    lang_prio_str = args.lang_prio or env_vars['lang_prio']
    lang_priorities = _parse_language_priorities(lang_prio_str)

    # Handle excluded IDs
    exclude_ids_str = args.exclude_ids or env_vars['exclude_ids']
    excluded_ids = _parse_excluded_ids(exclude_ids_str)

    # Resolve authentication credentials
    username, password = _resolve_auth_credentials(args, doit)

    # Resolve HTML report settings
    html_report = args.html_report or env_vars['html_report'] or args.html_only or env_vars['html_only']
    html_only = args.html_only or env_vars['html_only']
    no_open = getattr(args, 'no_open', False)

    return (host, port, api_key, library, doit, lang_priorities, excluded_ids,
            username, password, html_report, html_only, no_open)


def _connect_and_fetch_libraries(client, base_url, library):
    """
    Connect to Emby and fetch provider tables from all libraries.

    Args:
        client: HTTP client.
        base_url: Emby server base URL.
        library: List of library names.

    Returns:
        Combined provider tables dict.
    """
    connection_url = f"{base_url}/System/Info"
    if not check_emby_connection(client, connection_url):
        logger.error(f"Unable to connect to the Emby server at {base_url}.")
        sys.exit(1)

    all_provider_tables = {"imdb": {}, "tvdb": {}, "tmdb": {}}

    # Process each library
    for library_name in library:
        logger.debug(f"Processing library: {library_name}")

        library_id = get_library_id(client, base_url, library_name)
        if library_id is None:
            logger.error(f"Unable to find library '{library_name}'. Skipping.")
            continue

        provider_tables = fetch_and_process_media_items(client, base_url, library_id, library_name)

        for provider in ["imdb", "tvdb", "tmdb"]:
            for provider_id, items in provider_tables[provider].items():
                if provider_id not in all_provider_tables[provider]:
                    all_provider_tables[provider][provider_id] = []
                all_provider_tables[provider][provider_id].extend(items)

    if all(not table for table in all_provider_tables.values()):
        logger.error("No media items found in any of the specified libraries.")
        sys.exit(1)

    dump_object_to_file(
        all_provider_tables, "testing/provider_tables"
    ) if logger.isEnabledFor(logging.DEBUG) else None

    return all_provider_tables


def _run_deduplication_pipeline(client, base_url, all_provider_tables, excluded_ids,
                                lang_priorities, api_key, doit, username, password):
    """
    Run the deduplication pipeline: identify, rationalize, process.

    Args:
        client: HTTP client.
        base_url: Emby server base URL.
        all_provider_tables: Provider tables dictionary.
        excluded_ids: IDs to exclude.
        lang_priorities: Language priorities list.
        api_key: API key.
        doit: Whether to actually delete.
        username: Username for auth.
        password: Password for auth.

    Returns:
        Tuple of (decisions, exclusion_metadata, markdown_report).
    """
    duplicates = identify_duplicates(all_provider_tables, excluded_ids)

    dump_object_to_file(duplicates, "testing/duplicates") if logger.isEnabledFor(
        logging.DEBUG
    ) else None

    duplicates = rationalize_duplicates(duplicates)

    dump_object_to_file(duplicates, "testing/aggregate") if logger.isEnabledFor(
        logging.DEBUG
    ) else None

    decisions, exclusion_metadata = process_duplicate_groups(
        client, base_url, duplicates, api_key, lang_priorities, excluded_ids
    )

    dump_object_to_file(decisions, "testing/decisions") if logger.isEnabledFor(
        logging.DEBUG
    ) else None

    logger.debug(f"Processing {len(decisions)} decisions for markdown report generation")

    # Create metadata dictionary for report generation
    report_metadata = {
        "excluded_ids": excluded_ids if excluded_ids else [],
        "language_priorities": lang_priorities if lang_priorities else [],
        "excluded_groups_count": exclusion_metadata.get("excluded_groups_count", 0),
        "excluded_titles": exclusion_metadata.get("excluded_titles", {})
    }

    markdown_report = process_deletion_and_generate_report(
        client, base_url, decisions, doit, username, password, api_key, report_metadata
    )

    dump_object_to_file(decisions, "testing/deletions") if logger.isEnabledFor(
        logging.DEBUG
    ) else None
    dump_object_to_file(markdown_report, "testing/report") if logger.isEnabledFor(
        logging.DEBUG
    ) else None

    return decisions, exclusion_metadata, markdown_report


def _generate_reports(base_url, decisions, exclusion_metadata, excluded_ids,
                     lang_priorities, markdown_report, html_report, html_only, no_open):
    """
    Generate and output reports (markdown and/or HTML).

    Args:
        base_url: Emby server base URL.
        decisions: Deduplication decisions.
        exclusion_metadata: Metadata about exclusions.
        excluded_ids: IDs that were excluded.
        lang_priorities: Language priorities list.
        markdown_report: Generated markdown report.
        html_report: Whether to generate HTML report.
        html_only: Whether to only output HTML (skip console).
        no_open: Whether to skip opening browser.
    """
    # Create metadata dictionary for report generation
    report_metadata = {
        "excluded_ids": excluded_ids if excluded_ids else [],
        "language_priorities": lang_priorities if lang_priorities else [],
        "excluded_groups_count": exclusion_metadata.get("excluded_groups_count", 0),
        "excluded_titles": exclusion_metadata.get("excluded_titles", {})
    }

    if html_report:
        try:
            logger.debug(f"Generating HTML report with {len(decisions)} decisions total")

            html_report_path = generate_html_report(base_url, decisions, report_metadata)

            if not no_open:
                try:
                    import webbrowser
                    print(f"Opening HTML report in browser: {html_report_path}")
                    webbrowser.open(f"file://{html_report_path}")
                except Exception as e:
                    logger.warning(f"Could not open browser: {e}")
                    print(f"HTML report generated at: {html_report_path}")
            else:
                print(f"HTML report generated at: {html_report_path}")
        except Exception as e:
            logger.error(f"Error generating HTML report: {e}")
            if html_only:
                logger.error("HTML-only mode requested but HTML report generation failed")
                sys.exit(1)
            logger.info("Continuing with console report output")

    if not html_only:
        output_report_to_stdout(markdown_report)


def main() -> None:
    """
    Main entry point for the Emby Dedupe tool.
    """
    args = parse_args()

    # Route to check command if specified
    if hasattr(args, 'command') and args.command == 'check':
        from emby_dedupe.cli.check import run_check
        exit_code = run_check(args)
        sys.exit(exit_code)

    # Route to genres command if specified
    if hasattr(args, 'command') and args.command == 'genres':
        from emby_dedupe.cli.genres import run_genres_command
        run_genres_command(args)
        sys.exit(0)

    # Resolve configuration from args and environment
    (host, port, api_key, library, doit, lang_priorities, excluded_ids,
     username, password, html_report, html_only, no_open) = _resolve_configuration(args)

    # Validate required arguments
    validate_required_arguments(host, api_key, library, doit, username, password)

    # Validate and handle host and port information
    validated_host, validated_port = handle_host_and_port(host, port)

    logger.debug(
        f"Using the following configurations: "
        f"Host: {validated_host}, Port: {validated_port}, API Key: {api_key}, "
        f"Libraries: {', '.join(library)}, DoIt: {doit}"
    )

    try:
        base_url = f"{validated_host}:{validated_port}"
        client = httpx.Client(headers={"X-Emby-Token": api_key})

        # Connect and fetch provider tables from all libraries
        all_provider_tables = _connect_and_fetch_libraries(client, base_url, library)

        # Run deduplication pipeline
        decisions, exclusion_metadata, markdown_report = _run_deduplication_pipeline(
            client, base_url, all_provider_tables, excluded_ids, lang_priorities,
            api_key, doit, username, password
        )

        # Generate reports
        _generate_reports(base_url, decisions, exclusion_metadata, excluded_ids,
                         lang_priorities, markdown_report, html_report, html_only, no_open)

    except EmbyServerConnectionError as e:
        logger.error(str(e))
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON: {str(e)}")
        sys.exit(1)
    except httpx.TimeoutException as e:
        logger.error(f"HTTP request timed out: {str(e)}")
        sys.exit(1)
    except Exception as e:
        # Catch-all for any other unexpected exceptions
        logger.error(f"An unexpected error occurred: {str(e)}")
        logger.error(e)
        sys.exit(1)
    finally:
        if authenticated_token_for_delete and doit:
            logout(client, base_url, authenticated_token_for_delete)
