"""Remember which pairs the safety guard refused, run after run.

A pair the guard refuses three runs in a row (Imma Tataranni, 2026-10) is a pair the
owner has already looked at: the report says "blocked in 3 runs" next to it so the
``--exclude-ids`` decision is obvious. The record is a small JSON file keyed by
``keep_path|delete_path``; nothing in it is needed for correctness, so every failure
to read or write it is logged and ignored.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from emby_dedupe.utils.logging import logger

ENV_HISTORY_FILE = "DEDUPE_HISTORY_FILE"
_DEFAULT = Path.home() / ".emby-dedupe" / "dedupe-history.json"
_BLOCKED = frozenset({"skipped_unsafe", "would_block"})


def history_path() -> Path:
    """Where the record lives: ``$DEDUPE_HISTORY_FILE`` or ``~/.emby-dedupe/dedupe-history.json``."""
    return Path(os.environ.get(ENV_HISTORY_FILE) or _DEFAULT)


def pair_key(keep: dict[str, Any], item: dict[str, Any]) -> str:
    """The key reports/clusters uses: both paths, keeper first."""
    def path(d: dict[str, Any]) -> str:
        return str(d.get("path") or (d.get("quality_description") or {}).get("path") or "")
    return f"{path(keep)}|{path(item)}"


def load_history(path: Path | None = None) -> dict[str, int]:
    """``{pair_key: runs_blocked}`` from the record; empty when there is none or it is unreadable."""
    path = path or history_path()
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        pairs = data.get("pairs") or {}
        return {key: int(entry.get("runs", 0)) for key, entry in pairs.items() if isinstance(entry, dict)}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("Dedupe history %s unreadable, starting fresh: %s", path, exc)
        return {}


def record_run(decisions: list[dict[str, Any]], path: Path | None = None) -> dict[str, int]:
    """Count this run's guard refusals into the record and return the updated counts.

    Pairs the guard did not refuse this time are dropped: a pair that stopped appearing
    (fixed, excluded, or deleted) must not come back as "blocked in 4 runs" months later.
    """
    path = path or history_path()
    previous = load_history(path)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    pairs: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        keep = decision.get("keep") or {}
        for item in decision.get("delete") or []:
            if (item.get("deletion_result") or {}).get("status") not in _BLOCKED:
                continue
            key = pair_key(keep, item)
            pairs[key] = {"runs": previous.get(key, 0) + 1, "last": now}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".dedupe-history-", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "updated": now, "pairs": pairs}, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Dedupe history %s not written: %s", path, exc)
    return {key: entry["runs"] for key, entry in pairs.items()}
