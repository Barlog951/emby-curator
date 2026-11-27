"""
Library cleanup command — identify and optionally remove unwatched stale media.

Handles both movies and TV series. Libraries are auto-probed for content type
and the appropriate pipeline is run for each.

Movies pass through a 7-layer filter pipeline (age, exclusion, play/interest,
actors, path, rating decay, whole collections).

Series pass through a 5-layer filter pipeline (staleness, exclusion,
play/favorites, path, rating decay). Staleness is measured as years since
the last episode was added to Emby.

Both use a dynamic rating decay model: older items must have a higher
community rating to be protected.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from tqdm import tqdm

import emby_dedupe.api.client as _client_mod
from emby_dedupe.api.cleanup_pipeline import (
    _probe_library_content,
    _resolve_primary_user_id,
    _run_cleanup_pipeline,
    _run_series_cleanup_pipeline,
)
from emby_dedupe.api.client import (
    check_emby_connection,
    delete_item,
    fetch_library_owners,
    fetch_library_paths,
    handle_host_and_port,
    logout,
)
from emby_dedupe.api.deletion_guard import cleanup_delete_reaches, is_cleanup_delete_safe
from emby_dedupe.api.search import get_all_library_ids, get_library_ids_by_name
from emby_dedupe.models.cleanup import (
    _DEFAULT_PROTECT_PATH,
    CleanupCandidate,
    CleanupConfig,
    SeriesCleanupCandidate,
)
from emby_dedupe.reports.cleanup import (
    _format_cleanup_report_console,
    _format_cleanup_report_json,
    _generate_cleanup_html_report,
    _save_cleanup_html_report,
    prefetch_cleanup_posters,
)
from emby_dedupe.utils.exceptions import EmbyReadError
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_cleanup_args(
    host: str | None,
    api_key: str | None,
    libraries: list[str],
    all_libraries: bool,
    doit: bool,
    username: str | None,
    password: str | None,
    protect_user: str | None = None,
) -> None:
    """Validate cleanup command arguments.

    Custom validation (not validate_required_arguments) to support --all-libraries
    and the nuanced username requirement (DA fix #4, #11).

    Args:
        host: Emby server URL.
        api_key: Emby API key.
        libraries: List of library names.
        all_libraries: If True, libraries list may be empty.
        doit: If True, deletions will be performed.
        username: Emby username for DELETE auth (required for --doit).
        password: Emby password (required for --doit).
        protect_user: Emby user whose favourite actors are protected; None falls back to ``username``.

    Raises:
        SystemExit: If required arguments are missing.
    """
    import sys

    errors = []
    if not host:
        errors.append("--host / DEDUPE_EMBY_HOST is required.")
    if not api_key:
        errors.append("--api-key / DEDUPE_EMBY_API_KEY is required.")
    if not libraries and not all_libraries:
        errors.append("Specify at least one --library / -l or use --all-libraries.")
    if doit and not username:
        errors.append("--username is required when --doit is set (needed for DELETE auth).")
    if doit and not password:
        errors.append("--password is required when --doit is set (needed for DELETE auth).")

    if errors:
        for err in errors:
            logger.error(err)
        sys.exit(1)

    if not (protect_user or username):
        logger.warning(
            "No --protect-user or --username provided; favorite-actor protection will use the first Emby user. "
            "Pass --protect-user <name> for accurate results."
        )
    elif not protect_user:
        logger.warning(
            f"No --protect-user provided; favorite-actor protection follows the login user '{username}'. "
            "Pass --protect-user <name> when deletions run under a dedicated account."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_excluded_ids(raw: str) -> set[str]:
    """Parse comma-separated provider IDs into a set.

    Args:
        raw: Comma-separated string of provider IDs.

    Returns:
        Set of stripped, non-empty provider ID strings.
    """
    return {s.strip() for s in raw.split(",") if s.strip()} if raw else set()


def _normalize_protect_paths(raw: str | list | tuple) -> list[str]:
    """Normalize protect_paths input to a list of non-empty path strings.

    Args:
        raw: Protect paths as a comma-string, list, or tuple.

    Returns:
        List of non-empty path strings, defaulting to [_DEFAULT_PROTECT_PATH].
    """
    if isinstance(raw, str):
        paths = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        paths = list(raw)
    else:
        paths = []
    return paths or [_DEFAULT_PROTECT_PATH]


def _probe_and_split_libraries(
    client: httpx.Client,
    base_url: str,
    library_ids: list[str],
    lib_id_to_name: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Probe libraries to split into movie-containing and series-containing lists.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        library_ids: Library IDs to probe.
        lib_id_to_name: Mapping of library ID to display name.

    Returns:
        Tuple of (movie_lib_ids, series_lib_ids).
    """
    movie_lib_ids: list[str] = []
    series_lib_ids: list[str] = []

    for lib_id in library_ids:
        movie_count, series_count = _probe_library_content(client, base_url, lib_id)
        lib_name = lib_id_to_name.get(lib_id, lib_id)
        if movie_count > 0:
            movie_lib_ids.append(lib_id)
        if series_count > 0:
            series_lib_ids.append(lib_id)
        logger.info(f"Library '{lib_name}': {movie_count} movies, {series_count} series")

    return movie_lib_ids, series_lib_ids


def _perform_deletions(
    client: httpx.Client,
    base_url: str,
    candidates: list,
    username: str | None,
    password: str | None,
    api_key: str | None,
    label: str = "movies",
) -> None:
    """Delete cleanup candidates from Emby and record results.

    Every verdict is first planned for a run in which every delete succeeds
    (:func:`_guard_verdicts`). Deletes then run in an order where a file whose Emby delete
    would reach another candidate's files goes only after that candidate was deleted
    (:func:`_issue_order`), and each delete is judged again just before it runs, against
    the library as it stands (:func:`_recheck`): a candidate whose delete failed, or that
    was held or refused, is still on disk and counts as a survivor.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        candidates: List of CleanupCandidate or SeriesCleanupCandidate objects.
        username: Emby username for DELETE auth.
        password: Emby password for DELETE auth.
        api_key: Emby API key.
        label: Label for progress bar (e.g. "movies" or "series").

    Raises:
        ValueError: If username, password, or api_key is missing (deletions
            require full auth; enforced earlier by _validate_cleanup_args).
    """
    if username is None or password is None or api_key is None:
        raise ValueError("username, password and api_key are required for deletions")

    # A series candidate's path is a FOLDER Emby owns as one item, not a media file, so it
    # needs the container rule rather than the file one (which refused 100% of series).
    is_series = label == "series"
    known_paths, paths_complete, owners = _library_listing(client, base_url, is_series, label)
    verdicts, held = _guard_verdicts(
        candidates, known_paths, delete_is_container=is_series, paths_complete=paths_complete,
        owners=owners,
    )
    order, waits, _ = _issue_order(candidates, verdicts, delete_is_container=is_series, held=held)
    issued = {c.item_id for c in order}
    run = _DeleteRun(known_paths, owners, is_series, paths_complete, waits)

    logger.info(f"Deleting {len(candidates)} {label} candidates...")
    queue = [*order, *(c for c in candidates if c.item_id not in issued)]
    with tqdm(queue, desc=f"Deleting {label}", unit=label.rstrip("s")) as progress:
        for candidate in progress:
            progress.set_postfix_str(candidate.name[:40])
            safe, reason = verdicts[candidate.item_id]
            collection = getattr(candidate, "collection", None)
            if safe and collection in held:
                safe, reason = False, held[collection]
            if safe:
                safe, reason = _issue_check(candidate, run, order, held)
            if not safe:
                run.status[candidate.item_id] = "skipped"
                candidate.deletion_result = {"status": "skipped_unsafe", "error": reason}
                logger.error(
                    "SAFETY GUARD blocked cleanup deletion of %r — %s (path=%r). "
                    "Item kept; resolve the layout manually.",
                    candidate.name, reason, getattr(candidate, "path", None),
                )
                continue
            result = delete_item(
                client, base_url, candidate.item_id,
                username=username, password=password, api_key=api_key,
            )
            candidate.deletion_result = result
            run.record(candidate, result.get("status") == "success")
            logger.info(f"Deleted {candidate.name}: {result.get('status', 'unknown')}")
            if collection and result.get("status") != "success":
                held[collection] = f"collection kept: deleting {candidate.name!r} failed, the other parts stay"


def _library_listing(client: httpx.Client, base_url: str, is_series: bool, label: str):
    """Fetch the library once for the guard: every media path, whether the listing is
    complete, and for a series run which series each item belongs to.

    Emby fold-deletes an item's owning directory, so a candidate sharing its folder with
    media the run is KEEPING can take that media with it; every delete is gated on the same
    guard the dedup path uses.

    Returns:
        ``(known_paths, complete, owners)``; ``owners`` is None for a movie run.
    """
    owners = None
    if is_series:
        owners, complete = fetch_library_owners(client, base_url)
        known_paths = list(owners)
    else:
        known_paths, complete = fetch_library_paths(client, base_url)
    if not complete:
        logger.warning(
            "The library listing is incomplete (%d items), so every %s delete is refused "
            "this run.", len(known_paths), label,
        )
    return known_paths, complete, owners


def _issue_check(candidate, run: _DeleteRun, order: list, held: dict) -> tuple[bool, str]:
    """Re-check a cleared candidate at its turn; a refused collection part holds its collection."""
    pending = [c.path for c in order
               if c.item_id not in run.status and getattr(c, "collection", None) not in held]
    safe, reason = _recheck(candidate, run, pending)
    collection = getattr(candidate, "collection", None)
    if not safe and collection:
        held[collection] = f"collection kept: the re-check refused {candidate.name!r}, the other parts stay"
    return safe, reason


class _DeleteRun:
    """What one cleanup delete loop knows about the library as its deletes proceed."""

    def __init__(self, known_paths, owners, is_series: bool, paths_complete: bool, waits: dict) -> None:
        self.known_paths = list(known_paths)
        self.owners = dict(owners) if owners is not None else None
        self.is_series = is_series
        self.paths_complete = paths_complete
        self.waits = waits
        self.status: dict[str, str] = {}

    def record(self, candidate, succeeded: bool) -> None:
        """Note a delete's outcome. A successful one removes the candidate's path, and for a
        folder everything under it, from the library as later re-checks see it."""
        self.status[candidate.item_id] = "success" if succeeded else "failed"
        path = getattr(candidate, "path", None)
        if not (succeeded and path):
            return
        inside = path.rstrip("/") + "/"
        self.known_paths = [p for p in self.known_paths if p != path and not p.startswith(inside)]
        if self.owners is not None:
            self.owners = {p: s for p, s in self.owners.items() if p != path and not p.startswith(inside)}


def _recheck(candidate, run: _DeleteRun, pending_paths: list) -> tuple[bool, str]:
    """Judge a cleared candidate again just before its delete, against what is still on disk.

    Every candidate this delete would reach must already have been deleted: one that
    failed, was held or was refused is still on disk, and this delete would take its
    files. The guard then runs on the library as it stands: deleted files are gone, and
    only candidates still waiting their turn count as going.

    Returns:
        ``(safe, reason)``; a refusal's reason starts with ``re-check:``.
    """
    for other, why in run.waits.get(candidate.item_id, ()):
        if run.status.get(other.item_id) != "success":
            return False, f"re-check: {other.name!r} was not deleted, and this delete would reach it ({why})"
    extra: dict[str, Any] = {"owners": run.owners, "series_id": str(candidate.item_id)} if run.is_series else {}
    safe, reason = is_cleanup_delete_safe(
        getattr(candidate, "path", None), run.known_paths, pending_paths,
        delete_is_container=run.is_series, paths_complete=run.paths_complete, **extra,
    )
    return (True, reason) if safe else (False, f"re-check: {reason}")


def _issue_order(
    candidates: list, verdicts: dict, delete_is_container: bool, held=(),
) -> tuple[list, dict, dict[str, tuple[bool, str]]]:
    """Order the cleared candidates so each delete runs after every candidate it would reach.

    Emby's delete of one file can take part of another candidate (name-prefix sidecars,
    subtitles listed under both, or a folder named for the deleted title). That is
    harmless only once the other candidate is gone, so it is deleted first. Two candidates
    that would each reach the other can never go in a safe order and are both refused.
    Series folders hold no other candidate's files and are not ordered.

    Returns:
        ``(order, waits, blocked)``: the cleared candidates in issue order (input order
        among equals); for each, the candidates it must wait for with the linking rule;
        and a refusal for every cleared candidate that can never go.
    """
    cleared = [c for c in candidates if verdicts[c.item_id][0] and getattr(c, "collection", None) not in held]
    waits: dict[str, list] = {}
    if not delete_is_container:
        for c in cleared:
            reach = ((o, cleanup_delete_reaches(c.path, o.path)) for o in cleared if o is not c)
            waits[c.item_id] = [(o, why) for o, why in reach if why]
    order: list = []
    done: set[str] = set()
    pending = list(cleared)
    while pending:
        ready = next((c for c in pending if all(o.item_id in done for o, _ in waits.get(c.item_id, ()))), None)
        if ready is None:
            break
        order.append(ready)
        done.add(ready.item_id)
        pending.remove(ready)
    blocked = {}
    for c in pending:
        other, why = next((o, w) for o, w in waits[c.item_id] if o.item_id not in done)
        blocked[c.item_id] = (
            False,
            f"delete-order: this delete would reach {other.name!r} ({why}), and that candidate "
            "cannot be deleted first, so this one stays",
        )
    return order, waits, blocked


def _journal_cleanup(candidates: list, series_candidates: list, doit: bool) -> None:
    """Append every title a real cleanup run deleted to the deletion journal. Never raises."""
    if not doit:
        return
    from emby_dedupe.reports.journal import append_rows, cleanup_rows, run_timestamp

    try:
        run_at = run_timestamp()
        append_rows(cleanup_rows(candidates, run_at, "Movie") + cleanup_rows(series_candidates, run_at, "Series"))
    except Exception as e:  # noqa: BLE001 — the deletions already happened; the run must finish
        logger.error("Deletion journal: not written (%s).", e)

def _guard_verdicts(
    candidates: list, known_paths, delete_is_container: bool, paths_complete: bool = True,
    owners: dict | None = None,
) -> tuple[dict[str, tuple[bool, str]], dict[str, str]]:
    """Run the fold-delete guard over every candidate, keeping collections whole.

    The plan assumes every delete that is cleared succeeds. A collection goes whole or not
    at all, so one part the guard refuses holds every part. A refused candidate and every
    held part then SURVIVE the run, which can make a neighbour's delete unsafe (it would
    fold their shared folder or take their sidecars), so the guard is re-run without them
    until nothing new is refused. A candidate that can never be deleted in a safe order
    (see :func:`_issue_order`) is refused the same way. ``paths_complete`` False makes the
    guard refuse every delete (see :func:`is_cleanup_delete_safe`).

    Returns:
        ``({item_id: (safe, reason)}, {collection name: reason it is held})``.
    """
    held: dict[str, str] = {}
    refused: dict[str, tuple[bool, str]] = {}
    while True:
        going = [c for c in candidates
                 if c.item_id not in refused and getattr(c, "collection", None) not in held]
        delete_paths = [c.path for c in going if getattr(c, "path", None)]
        verdicts = {
            c.item_id: refused.get(c.item_id) or _guard_one(
                c, known_paths, delete_paths, delete_is_container, paths_complete, owners,
            )
            for c in candidates
        }
        verdicts.update(_issue_order(going, verdicts, delete_is_container)[2])
        stopped = {c.item_id: verdicts[c.item_id] for c in going if not verdicts[c.item_id][0]}
        if not stopped:
            return verdicts, held
        refused |= stopped
        held |= {
            c.collection: f"collection kept whole: the safety guard holds {c.name!r}"
            for c in going
            if c.item_id in stopped and getattr(c, "collection", None) and c.collection not in held
        }


def _guard_one(
    candidate, known_paths, delete_paths: list, delete_is_container: bool, paths_complete: bool,
    owners: dict | None,
) -> tuple[bool, str]:
    """The guard's verdict for one candidate, with the series ownership data for a series."""
    extra: dict[str, Any] = (
        {"owners": owners, "series_id": str(candidate.item_id)} if delete_is_container else {}
    )
    return is_cleanup_delete_safe(
        getattr(candidate, "path", None), known_paths, delete_paths,
        delete_is_container=delete_is_container, paths_complete=paths_complete, **extra,
    )


def _resolve_library_ids(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    libraries: list[str],
    all_libraries: bool,
) -> tuple[list[str], dict[str, str]]:
    """Resolve library IDs and build a name mapping.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        api_key: Emby API key.
        libraries: List of library names (empty if all_libraries is True).
        all_libraries: If True, use all libraries.

    Returns:
        Tuple of (library_ids, lib_id_to_name).
    """
    all_lib_infos = get_all_library_ids(client, base_url, api_key)
    lib_id_to_name: dict[str, str] = {
        lib["id"]: lib["name"] for lib in all_lib_infos if lib.get("id") and lib.get("name")
    }

    if all_libraries:
        library_ids = list(lib_id_to_name.keys())
    else:
        library_ids = get_library_ids_by_name(client, base_url, api_key, libraries)

    return library_ids, lib_id_to_name


_EMPTY_MOVIE_STATS: dict = {
    "total_analyzed": 0, "age_filtered": 0, "excluded_filtered": 0,
    "play_protected": 0, "interest_protected": 0, "actor_protected": 0,
    "path_protected": 0, "rating_protected": 0,
    "collection_protected": 0, "collections_removed": 0, "final_candidates": 0,
}


def _output_report(
    output_format: str,
    candidates: list[CleanupCandidate],
    protection_stats: dict,
    config: CleanupConfig,
    series_candidates: list[SeriesCleanupCandidate] | None,
    series_stats: dict | None,
    movie_near_miss: list[CleanupCandidate] | None = None,
    series_near_miss: list[SeriesCleanupCandidate] | None = None,
) -> None:
    """Output cleanup report in the requested format (console or JSON).

    Args:
        output_format: "json" or "console".
        candidates: Movie candidates.
        protection_stats: Movie filter stage counts.
        config: Cleanup configuration.
        series_candidates: Series candidates (None if no series scanned).
        series_stats: Series filter stage counts.
        movie_near_miss: Movies protected only by rating.
        series_near_miss: Series protected only by rating.
    """
    series_for_report = series_candidates if series_stats else None
    if output_format == "json":
        report_data = _format_cleanup_report_json(
            candidates, protection_stats, config,
            series_candidates=series_for_report, series_stats=series_stats,
        )
        print(json.dumps(report_data, indent=2, default=str))
    else:
        _format_cleanup_report_console(
            candidates, protection_stats, config,
            series_candidates=series_for_report, series_stats=series_stats,
            movie_near_miss=movie_near_miss, series_near_miss=series_near_miss,
        )


def _prefetch_doomed_posters(
    base_url: str, api_key: str, will_report: bool,
    doomed: list[CleanupCandidate | SeriesCleanupCandidate],
) -> dict[str, str]:
    """Fetch the posters of what is about to be deleted, for a --doit run with an HTML report.

    After the delete Emby no longer has them, and the report is rendered last.
    """
    if not (will_report and doomed):
        return {}
    return prefetch_cleanup_posters(base_url, doomed, api_key)


def _execute_cleanup(
    client: httpx.Client,
    base_url: str,
    config: CleanupConfig,
    api_key: str,
    libraries: list[str],
    all_libraries: bool,
    username: str | None,
    password: str | None,
    output_format: str,
    html_report: bool,
    html_only: bool,
    no_open: bool,
    doit: bool,
) -> None:
    """Execute the full cleanup workflow after connection is established.

    Args:
        client: Configured httpx client with auth headers.
        base_url: Emby server base URL.
        config: Cleanup configuration.
        api_key: Emby API key.
        libraries: Library names to scan.
        all_libraries: If True, scan all libraries.
        username: Emby username for DELETE auth.
        password: Emby password.
        output_format: "json" or "console".
        html_report: Whether to generate HTML report.
        html_only: HTML-only mode (no console, no browser).
        no_open: Whether to suppress browser auto-open.
        doit: Whether to perform actual deletions.
    """
    server_id = make_http_request(client, "GET", f"{base_url}/System/Info").json().get("Id", "")

    primary_user_id = _resolve_primary_user_id(client, base_url, config.protect_user or username)
    if not primary_user_id:
        logger.error("Cannot resolve a valid Emby user ID; aborting cleanup.")
        return

    library_ids, lib_id_to_name = _resolve_library_ids(
        client, base_url, api_key, libraries, all_libraries
    )
    if not library_ids:
        logger.error("No library IDs resolved. Check library names and permissions.")
        return

    movie_lib_ids, series_lib_ids = _probe_and_split_libraries(
        client, base_url, library_ids, lib_id_to_name
    )

    # Run pipelines
    candidates, protection_stats, movie_near_miss = _run_cleanup_pipeline(
        client, base_url, config, movie_lib_ids, primary_user_id,
        lib_id_to_name=lib_id_to_name,
    ) if movie_lib_ids else ([], dict(_EMPTY_MOVIE_STATS), [])

    series_candidates, series_stats, series_near_miss = _run_series_cleanup_pipeline(
        client, base_url, config, series_lib_ids, primary_user_id,
        lib_id_to_name=lib_id_to_name,
    ) if series_lib_ids else ([], None, [])

    _output_report(
        output_format, candidates, protection_stats, config,
        series_candidates, series_stats,
        movie_near_miss=movie_near_miss, series_near_miss=series_near_miss,
    )

    prefetched = _prefetch_doomed_posters(
        base_url, api_key, doit and (html_report or html_only), [*candidates, *series_candidates],
    )

    if doit and candidates:
        _perform_deletions(client, base_url, candidates, username, password, api_key, "movies")
    if doit and series_candidates:
        _perform_deletions(client, base_url, series_candidates, username, password, api_key, "series")
    _journal_cleanup(candidates, series_candidates, doit)

    if html_report or html_only:
        series_for_report = series_candidates if series_stats else None
        html_content = _generate_cleanup_html_report(
            base_url, candidates, protection_stats, config, doit,
            server_id=server_id, api_key=api_key,
            series_candidates=series_for_report, series_stats=series_stats,
            movie_near_miss=movie_near_miss, series_near_miss=series_near_miss,
            prefetched_posters=prefetched,
        )
        report_path = _save_cleanup_html_report(html_content, no_open=no_open)
        print(f"\nHTML report: {report_path}")


def run_cleanup_command(args) -> None:
    """Entry point for the cleanup subcommand.

    Orchestrates: validation -> connection -> pipeline -> report -> optional
    deletion -> HTML report -> logout.

    Args:
        args: Namespace with cleanup arguments (from argparse.Namespace or typer ctx).
    """
    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    api_key = getattr(args, "api_key", None)
    libraries = getattr(args, "library", []) or []
    all_libraries = getattr(args, "all_libraries", False)
    doit = getattr(args, "doit", False)
    username = getattr(args, "username", None)
    password = getattr(args, "password", None)
    explicit_protect_user = getattr(args, "protect_user", None)
    protect_user = explicit_protect_user or username

    excluded_provider_ids = _parse_excluded_ids(getattr(args, "exclude_ids", "") or "")
    protect_paths = _normalize_protect_paths(
        getattr(args, "protect_paths", [_DEFAULT_PROTECT_PATH])
    )

    _validate_cleanup_args(host, api_key, libraries, all_libraries, doit, username, password, explicit_protect_user)
    # _validate_cleanup_args exits on missing host/api_key; narrow for type checking
    assert host is not None and api_key is not None

    base_url, resolved_port = handle_host_and_port(host, port)
    if resolved_port not in (80, 443):
        base_url = f"{base_url}:{resolved_port}"

    config = CleanupConfig(
        min_age_years=getattr(args, "min_age_years", 3),
        protect_paths=protect_paths,
        base_rating=getattr(args, "base_rating", 6.0),
        decay_step=getattr(args, "decay_step", 0.5),
        max_rating=getattr(args, "max_rating", 8.0),
        excluded_provider_ids=excluded_provider_ids,
        near_miss_count=getattr(args, "near_miss_count", 5),
        protect_user=protect_user,
    )

    client = httpx.Client(headers={"X-Emby-Token": api_key}, timeout=120)

    try:
        check_emby_connection(client, f"{base_url}/System/Info")
        _execute_cleanup(
            client, base_url, config, api_key, libraries, all_libraries,
            username, password,
            output_format=getattr(args, "format", "console"),
            html_report=getattr(args, "html_report", False),
            html_only=getattr(args, "html_only", False),
            no_open=getattr(args, "no_open", False),
            doit=doit,
        )
    except EmbyReadError as e:
        # The pipelines finish before any delete is sent, so nothing was deleted.
        logger.error(f"Cleanup stopped before deleting anything: {e}")
        raise SystemExit(1) from e
    except Exception as e:
        logger.error(f"Cleanup command failed: {e}")
        raise
    finally:
        if _client_mod.auth_state.token_for_delete and doit:
            logout(client, base_url, _client_mod.auth_state.token_for_delete)
        client.close()
