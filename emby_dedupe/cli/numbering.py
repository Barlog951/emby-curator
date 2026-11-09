"""``numbering``: episodes whose Emby season/episode numbers disagree with their file names.

Read-only. Lists, per series, the episodes Emby shows under another episode's number,
leaves unnumbered, spreads over several seasons or numbers inconsistently, so they can be
fixed before viewers run into them. Series numbered differently on purpose go into an
allowlist file. With ``--marks`` every listed episode also shows who has it marked watched:
an episode under another one's number carries that episode's watched state.
The classification rules live in :mod:`emby_dedupe.api.numbering`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx

from emby_dedupe.api.client import check_emby_connection, get_library_id, handle_host_and_port
from emby_dedupe.api.numbering import (
    ATTENTION_KINDS,
    KINDS,
    REMAPPED,
    NumberingFinding,
    audit_numbering,
    load_allowlist,
)
from emby_dedupe.api.pagination import paginate_emby_items
from emby_dedupe.utils.exceptions import EmbyServerConnectionError
from emby_dedupe.utils.http import make_http_request
from emby_dedupe.utils.logging import logger

ENV_ALLOW_FILE = "DEDUPE_NUMBERING_ALLOW_FILE"
_DEFAULT_ALLOW = Path.home() / ".emby-dedupe" / "numbering-allow.txt"
_EPISODE_FIELDS = "Path"  # numbers, names and series ids are part of the default item fields
_PAGE_SIZE = 2000
MARKS_LIMIT = 300  # episodes checked for watched marks; each one costs a request per user
_LINES_PER_SERIES = 12


def allowlist_path(explicit: str | None = None) -> Path:
    """``--allow-file``, then ``$DEDUPE_NUMBERING_ALLOW_FILE``, then ``~/.emby-dedupe/numbering-allow.txt``."""
    return Path(explicit or os.environ.get(ENV_ALLOW_FILE) or _DEFAULT_ALLOW)


def read_allowlist(path: Path) -> set[str]:
    """Series ids from the allowlist file; empty when the file does not exist."""
    try:
        return load_allowlist(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()


def fetch_episodes(client: httpx.Client, base_url: str, library_ids: list[str]) -> list[dict]:
    """Every episode of the given libraries (the whole server when none are given), once each."""
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Episode",
        "Fields": _EPISODE_FIELDS,
        "EnableImages": "false",
        "EnableUserData": "false",
        "Limit": str(_PAGE_SIZE),
    }
    episodes: dict[str, dict] = {}
    parents: list[str | None] = list(library_ids) or [None]
    for parent_id in parents:
        page_params = {**params, "ParentId": parent_id} if parent_id else params
        for page, _total in paginate_emby_items(
            client, f"{base_url}/Items", page_params, error_context="fetch episodes", raise_on_error=True
        ):
            episodes.update((item["Id"], item) for item in page)
    return list(episodes.values())


def collect_played_by(
    client: httpx.Client, base_url: str, findings: list[NumberingFinding], limit: int = MARKS_LIMIT
) -> int:
    """Fill ``played_by`` for the findings that need attention, reading each user's state per item.

    A bulk ``/Users/{id}/Items?Ids=`` query can report played items as unplayed, so every
    user and episode is read on its own. Returns the number of episodes checked.
    """
    targets = [f for f in findings if f.kind in ATTENTION_KINDS][:limit]
    if not targets:
        return 0
    users = make_http_request(client, "GET", f"{base_url}/Users").json()
    for finding in targets:
        for user in users:
            item = make_http_request(client, "GET", f"{base_url}/Users/{user['Id']}/Items/{finding.item_id}").json()
            if (item.get("UserData") or {}).get("Played"):
                finding.played_by.append(user.get("Name") or user["Id"])
    return len(targets)


def _number(number: tuple[int | None, int | None]) -> str:
    season, episode = number
    return f"S{'?' if season is None else season}E{'?' if episode is None else episode}"


def _line(finding: NumberingFinding) -> str:
    season, episode = finding.file_number
    watched = f" | watched: {', '.join(finding.played_by)}" if finding.played_by else ""
    return (f"  {finding.kind:<9} file S{season:02d}E{episode:02d} -> Emby {_number(finding.emby_number)}"
            f" {finding.name!r} [{finding.item_id}]{watched}")


def format_report(findings: list[NumberingFinding], allowlisted: int, show_remapped: bool = False) -> list[str]:
    """Console lines: a summary, then one block per series, most serious first."""
    attention = [f for f in findings if f.kind in ATTENTION_KINDS]
    remapped = [f for f in findings if f.kind == REMAPPED]
    series: dict[tuple[str, str], list[NumberingFinding]] = defaultdict(list)
    for finding in findings:
        series[(finding.series_name, finding.series_id)].append(finding)
    lines = [
        f"Numbering: {len(attention)} episode(s) in "
        f"{len({(f.series_name, f.series_id) for f in attention})} series need a look; "
        f"{len(remapped)} remapped by a constant offset; {allowlisted} allowlisted series skipped"
    ]
    order = sorted(series.items(), key=lambda kv: (min(KINDS.index(f.kind) for f in kv[1]), kv[0][0].lower()))
    for (name, series_id), items in order:
        counts = Counter(f.kind for f in items)
        lines.append(f"\n{name} ({series_id}): " + ", ".join(f"{k} {counts[k]}" for k in KINDS if counts[k]))
        shown = [f for f in items if show_remapped or f.kind != REMAPPED]
        lines.extend(_line(f) for f in shown[:_LINES_PER_SERIES])
        if len(shown) > _LINES_PER_SERIES:
            lines.append(f"  ... {len(shown) - _LINES_PER_SERIES} more (see --json-out)")
        if not shown:
            lines.append("  the provider numbers this season differently; allowlist the series id if that is expected")
    return lines


def write_json(path: Path, findings: list[NumberingFinding], episode_count: int, allowlisted: list[str]) -> None:
    """Write every finding with the run's counts."""
    data = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "episodes": episode_count,
        "allowlisted_series": sorted(allowlisted),
        "counts": dict(Counter(f.kind for f in findings)),
        "findings": [f.as_dict() for f in findings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def run_numbering(client: httpx.Client, base_url: str, library_ids: list[str], args: argparse.Namespace) -> int:
    """Audit the episodes, print the report and optionally write JSON. Returns the attention count."""
    allowed = read_allowlist(allowlist_path(getattr(args, "allow_file", None)))
    episodes = fetch_episodes(client, base_url, library_ids)
    findings = audit_numbering(episodes, allowed)
    present = sorted(allowed & {str(e.get("SeriesId") or "") for e in episodes})
    if getattr(args, "marks", False):
        checked = collect_played_by(client, base_url, findings)
        logger.info(f"Watched marks read for {checked} episode(s)")
    print(f"Checked {len(episodes)} episodes")
    print("\n".join(format_report(findings, len(present), getattr(args, "show_remapped", False))))
    if getattr(args, "json_out", None):
        write_json(Path(args.json_out), findings, len(episodes), present)
        print(f"\nJSON: {args.json_out}")
    return sum(1 for f in findings if f.kind in ATTENTION_KINDS)


def run_numbering_command(args: argparse.Namespace) -> None:
    """Connect to Emby and run the numbering audit."""
    if not args.host or not args.api_key:
        logger.error("numbering needs the Emby host and API key (DEDUPE_EMBY_HOST / DEDUPE_EMBY_API_KEY).")
        sys.exit(1)
    host, port = handle_host_and_port(args.host, int(args.port) if args.port else None)
    base_url = f"{host}:{port}"
    with httpx.Client(headers={"X-Emby-Token": args.api_key}, timeout=120) as client:
        try:
            if not check_emby_connection(client, f"{base_url}/System/Info"):
                sys.exit(1)
            library_ids = []
            for name in args.library or []:
                library_id = get_library_id(client, base_url, name)
                if not library_id:
                    sys.exit(1)
                library_ids.append(library_id)
            run_numbering(client, base_url, library_ids, args)
        except (EmbyServerConnectionError, httpx.HTTPError) as e:
            logger.error(f"numbering failed: {e}")
            sys.exit(1)
