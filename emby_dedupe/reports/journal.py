"""Append-only record of every copy a real run deleted, one JSON line per copy.

Watch-history tools use it to follow a play on a deleted copy to the copy that was kept:
Emby forgets a deleted item's provider ids, so the record keeps both copies' ids.

- Written only after a ``--doit`` run, for files that are really gone.
- Local file: ``$DEDUPE_JOURNAL_FILE`` or ``~/.emby-dedupe/deletions.jsonl``.
- With ``$DEDUPE_JOURNAL_HOST`` (``user@host``) and ``$DEDUPE_JOURNAL_REMOTE_FILE`` (an absolute
  path on that host) the same lines are also appended there over SSH, world-readable.
- A journal failure is logged and never aborts the run: the deletions already happened.
"""
from __future__ import annotations

import datetime
import json
import os
import shlex
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from emby_dedupe.reports.common import DELETED_STATUSES
from emby_dedupe.utils.logging import logger

SCHEMA = 1
ENV_FILE = "DEDUPE_JOURNAL_FILE"
ENV_HOST = "DEDUPE_JOURNAL_HOST"
ENV_REMOTE_FILE = "DEDUPE_JOURNAL_REMOTE_FILE"
_DEFAULT = Path.home() / ".emby-dedupe" / "deletions.jsonl"

Runner = Callable[[str, str, str], tuple[int, str]]


def journal_path() -> Path:
    """Where the local record lives: ``$DEDUPE_JOURNAL_FILE`` or ``~/.emby-dedupe/deletions.jsonl``."""
    return Path(os.environ.get(ENV_FILE) or _DEFAULT)


def run_timestamp() -> str:
    """The run's local time with its UTC offset, e.g. ``2026-11-08T20:39:34+01:00``."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _ids(item: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (item.get("provider_ids") or {}).items() if v}


def _copy(item: dict[str, Any]) -> dict[str, Any]:
    quality = item.get("quality_description") or {}
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "path": item.get("path") or quality.get("path"),
        "size": quality.get("size"),
        "provider_ids": _ids(item),
    }


def _item_type(keep: dict[str, Any], item: dict[str, Any]) -> str | None:
    flag = item.get("is_episode", keep.get("is_episode"))
    if flag is None:
        return None
    return "Episode" if flag else "Movie"


def dedupe_rows(decisions: Iterable[dict[str, Any]] | None, run_at: str) -> list[dict[str, Any]]:
    """One row per delete item that is really gone (an Emby delete or a file-only removal)."""
    rows = []
    for decision in decisions or []:
        keep = decision.get("keep") or {}
        for item in decision.get("delete") or []:
            status = (item.get("deletion_result") or {}).get("status")
            if status not in DELETED_STATUSES:
                continue
            kept_ids, deleted_ids = _ids(keep), _ids(item)
            row: dict[str, Any] = {
                "schema": SCHEMA,
                "kind": "dedupe_delete",
                "deleted_at": run_at,
                "type": _item_type(keep, item),
                "title": keep.get("name") or item.get("name"),
                "file_only": status == "fold_safe_removed",
                "shared_provider_ids": {k: v for k, v in kept_ids.items() if deleted_ids.get(k) == v},
                "deleted": _copy(item),
                "kept": _copy(keep),
            }
            if row["type"] == "Episode":
                row["series"] = keep.get("series_name") or item.get("series_name")
                row["season"] = keep.get("season_number", item.get("season_number"))
                row["episode_number"] = keep.get("episode_number", item.get("episode_number"))
            rows.append(row)
    return rows


def cleanup_rows(candidates: Iterable[Any] | None, run_at: str, item_type: str) -> list[dict[str, Any]]:
    """One row per cleanup candidate Emby deleted. Cleanup keeps no copy, so ``kept`` is None."""
    rows = []
    for c in candidates or []:
        if (getattr(c, "deletion_result", None) or {}).get("status") != "success":
            continue
        rows.append({
            "schema": SCHEMA,
            "kind": "cleanup_delete",
            "deleted_at": run_at,
            "type": item_type,
            "title": c.name,
            "deleted": {
                "id": c.item_id,
                "name": c.name,
                "year": c.year,
                "path": c.path,
                "size": c.size_bytes,
                "library": c.library,
                "collection": getattr(c, "collection", None),
            },
            "kept": None,
        })
    return rows


def _ssh_append(host: str, remote_file: str, data: str) -> tuple[int, str]:
    """Append ``data`` to ``remote_file`` on ``host``; the file stays world-readable."""
    directory = os.path.dirname(remote_file) or "."
    script = f"umask 022; mkdir -p -- {shlex.quote(directory)} && cat >> {shlex.quote(remote_file)}"
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, script],
        input=data, capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def _append_local(path: Path, data: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except OSError as e:
        logger.error("Deletion journal: could not append to %s (%s).", path, e)
        return False


def _append_remote(data: str, count: int, runner: Runner) -> None:
    host = os.environ.get(ENV_HOST)
    if not host:
        return
    remote_file = os.environ.get(ENV_REMOTE_FILE)
    if not remote_file or not remote_file.startswith("/"):
        logger.error("Deletion journal: %s is set but %s is not an absolute path; lines kept locally only.",
                     ENV_HOST, ENV_REMOTE_FILE)
        return
    try:
        rc, err = runner(host, remote_file, data)
    except (OSError, subprocess.SubprocessError) as e:
        rc, err = -1, str(e)
    if rc:
        logger.error("Deletion journal: append to %s:%s failed (rc=%s, %s); lines kept locally only.",
                     host, remote_file, rc, err[:200])
    else:
        logger.info("Deletion journal: %d line(s) appended to %s:%s.", count, host, remote_file)


def append_rows(rows: list[dict[str, Any]], path: Path | None = None, runner: Runner = _ssh_append) -> int:
    """Append ``rows`` to the local journal (and the remote one when configured).

    Returns:
        The number of lines written locally (0 when there was nothing to write or it failed).
    """
    if not rows:
        return 0
    data = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
    path = path or journal_path()
    written = len(rows) if _append_local(path, data) else 0
    if written:
        logger.info("Deletion journal: %d line(s) appended to %s.", written, path)
    _append_remote(data, len(rows), runner)
    return written
