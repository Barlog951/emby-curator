"""Append-only record of every copy a real run deleted, one JSON line per copy.

Watch-history tools use it to follow a play on a deleted copy to the copy that was kept:
Emby forgets a deleted item's provider ids, so the record keeps both copies' ids.

- Written only after a ``--doit`` run, for files that are really gone.
- Local file: ``$DEDUPE_JOURNAL_FILE`` or ``~/.emby-dedupe/deletions.jsonl``.
- With ``$DEDUPE_JOURNAL_HOST`` (``user@host``) and ``$DEDUPE_JOURNAL_REMOTE_COMMAND`` (a shell
  command on that host that appends its stdin, e.g. ``cat >> /srv/journal/deletions.jsonl``) the
  lines are also delivered there over SSH. ``<journal>.sent`` holds the byte offset delivered so
  far, so lines a failed delivery missed are sent with the next run.
- A journal failure is logged and never aborts the run: the deletions already happened.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from emby_dedupe.reports.common import DELETED_STATUSES
from emby_dedupe.utils.logging import logger

SCHEMA = 1
ENV_FILE = "DEDUPE_JOURNAL_FILE"
ENV_HOST = "DEDUPE_JOURNAL_HOST"
ENV_REMOTE_COMMAND = "DEDUPE_JOURNAL_REMOTE_COMMAND"
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


def _ssh_append(host: str, command: str, data: str) -> tuple[int, str]:
    """Run ``command`` on ``host`` with ``data`` on its stdin."""
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
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


def _sent_path(path: Path) -> Path:
    return path.with_name(path.name + ".sent")


def _read_offset(path: Path) -> int:
    try:
        return int(_sent_path(path).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_offset(path: Path, offset: int) -> None:
    sent = _sent_path(path)
    tmp = sent.with_name(sent.name + ".tmp")
    tmp.write_text(f"{offset}\n", encoding="utf-8")
    os.replace(tmp, sent)


def _pending(path: Path) -> tuple[int, bytes]:
    """The delivered offset and the complete lines after it (a torn last line waits)."""
    try:
        content = path.read_bytes()
    except OSError:
        return 0, b""
    offset = _read_offset(path)
    if offset > len(content):  # the journal was replaced: deliver it again from the start
        offset = 0
    chunk = content[offset:]
    return offset, chunk[: chunk.rfind(b"\n") + 1]


def _deliver(path: Path, runner: Runner) -> None:
    """Send every local line not yet delivered to the remote command, when one is configured."""
    host, command = os.environ.get(ENV_HOST), os.environ.get(ENV_REMOTE_COMMAND)
    if not (host or command):
        return
    if not (host and command):
        logger.error("Deletion journal: set both %s and %s to deliver lines; kept locally only.",
                     ENV_HOST, ENV_REMOTE_COMMAND)
        return
    offset, chunk = _pending(path)
    if not chunk:
        return
    lines = chunk.count(b"\n")
    try:
        rc, err = runner(host, command, chunk.decode("utf-8"))
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as e:
        rc, err = -1, str(e)
    if rc:
        logger.error("Deletion journal: delivery to %s failed (rc=%s, %s); %d line(s) wait for the next run.",
                     host, rc, err[:200], lines)
        return
    try:
        _write_offset(path, offset + len(chunk))
    except OSError as e:
        logger.warning("Deletion journal: delivered, but %s not updated (%s); lines may be sent twice.",
                       _sent_path(path), e)
    logger.info("Deletion journal: %d line(s) delivered to %s.", lines, host)


def append_rows(rows: list[dict[str, Any]], path: Path | None = None, runner: Runner = _ssh_append) -> int:
    """Append ``rows`` to the local journal, then deliver any undelivered lines when configured.

    Returns:
        The number of lines written locally (0 when there was nothing to write or it failed).
    """
    path = path or journal_path()
    written = 0
    if rows:
        data = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
        if _append_local(path, data):
            written = len(rows)
            logger.info("Deletion journal: %d line(s) appended to %s.", written, path)
    _deliver(path, runner)
    return written
