"""Name the torrents a dedupe run takes data from.

Emby deletes the file on disk (and, for a movie in its own folder, the whole folder).
When a torrent client seeds that data in place, the torrent loses it. This check reads
every torrent's file list from Transmission once per run and records, on each copy to
delete, which torrents still seed the data it removes and at what upload ratio. It never
holds a delete: the record is there so the torrents can be cleaned up afterwards.

Paths are compared as Emby reports them, so Transmission must see the media under the
same paths as Emby. A seed that was moved elsewhere (for example a hardlink in a
separate seeding folder) is not affected by the library delete and is not reported.
"""

from __future__ import annotations

import os
import posixpath

import httpx

from emby_dedupe.utils.logging import logger

SESSION_HEADER = "X-Transmission-Session-Id"
_TIMEOUT = 60.0


def _norm(path: str | None) -> str:
    return posixpath.normpath(path) if path else ""


def fetch_torrent_files(url: str, http: httpx.Client | None = None) -> dict[str, dict]:
    """Map every file of every torrent in Transmission to ``{"torrent", "ratio"}``.

    Basic-auth credentials come from ``DEDUPE_TRANSMISSION_USER`` and
    ``DEDUPE_TRANSMISSION_PASSWORD`` when set.

    Raises:
        httpx.HTTPError, ValueError, KeyError: the RPC call failed or returned
            something other than a torrent list.
    """
    user = os.environ.get("DEDUPE_TRANSMISSION_USER")
    auth = (user, os.environ.get("DEDUPE_TRANSMISSION_PASSWORD", "")) if user else None
    client = http or httpx.Client(timeout=_TIMEOUT, auth=auth)
    body = {"method": "torrent-get", "arguments": {"fields": ["name", "downloadDir", "files", "uploadRatio"]}}
    try:
        response = client.post(url, json=body)
        if response.status_code == 409:
            # Transmission hands out its CSRF session id on the first request.
            response = client.post(url, json=body, headers={SESSION_HEADER: response.headers[SESSION_HEADER]})
        response.raise_for_status()
        torrents = response.json()["arguments"]["torrents"]
    finally:
        if http is None:
            client.close()
    owned: dict[str, dict] = {}
    for torrent in torrents:
        info = {"torrent": torrent.get("name", "?"), "ratio": torrent.get("uploadRatio")}
        for entry in torrent.get("files") or []:
            owned[_norm(posixpath.join(torrent["downloadDir"], entry["name"]))] = info
    return owned


def removal_scope(delete_path: str, known_paths, delete_paths=None) -> tuple[str, bool]:
    """What an Emby delete of ``delete_path`` removes from disk.

    Mirrors :func:`emby_dedupe.api.deletion_guard.is_delete_safe`: a folder named for the
    title, or one with no other media that survives the run, is deleted whole; otherwise
    only the file goes.

    Returns:
        ``(folder, True)`` when the whole folder goes, else ``(file path, False)``.
    """
    dp = _norm(delete_path)
    folder = posixpath.dirname(dp)
    stem = posixpath.splitext(posixpath.basename(dp))[0]
    name = posixpath.basename(folder)
    if name and stem.startswith(name):
        return folder, True
    going = {_norm(p) for p in (delete_paths or [])} | {dp}
    survivors = any(
        posixpath.dirname(_norm(p)) == folder and _norm(p) not in going for p in known_paths or []
    )
    return (dp, False) if survivors else (folder, True)


def seeding_hits(delete_path: str, owned: dict[str, dict], known_paths, delete_paths=None) -> list[str]:
    """Torrent-owned paths the delete removes: the file, its sidecars (same stem), and
    everything under the folder when Emby deletes the folder whole."""
    target, whole_folder = removal_scope(delete_path, known_paths, delete_paths)
    if whole_folder:
        prefix = target.rstrip("/") + "/"
        return sorted(p for p in owned if p.startswith(prefix))
    stem = posixpath.splitext(target)[0]
    return sorted(p for p in owned if p == target or p.startswith(stem + "."))


def _describe(seeding: list[dict]) -> str:
    """``"Name (ratio 0.04)"`` once per torrent."""
    parts = {}
    for entry in seeding:
        ratio = entry.get("ratio")
        parts[entry["torrent"]] = f"{entry['torrent']} (ratio {ratio:.2f})" if isinstance(ratio, (int, float)) \
            else entry["torrent"]
    return ", ".join(parts[name] for name in sorted(parts))


def annotate_seeding(url: str | None, decisions: list, known_paths, delete_paths=None,
                     http: httpx.Client | None = None) -> int:
    """Record on every copy to delete which torrents still seed the data it removes.

    Sets ``item["seeding"]`` to a list of ``{"torrent", "ratio"}`` and logs one warning
    per such copy. Nothing is held. No URL, or a Transmission that cannot be read,
    records nothing.

    Returns:
        The number of copies whose delete takes data from a torrent.
    """
    targets = [item for decision in decisions for item in decision.get("delete", []) if item.get("path")]
    if not url or not targets:
        return 0
    try:
        owned = fetch_torrent_files(url, http)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"Could not read the torrent list from Transmission ({e}); seeding torrents are not reported")
        return 0
    count = 0
    for item in targets:
        hits = seeding_hits(item["path"], owned, known_paths, delete_paths)
        if not hits:
            continue
        item["seeding"] = [owned[p] for p in hits]
        count += 1
        logger.warning(f"Deleting id={item.get('id')} removes data a torrent still seeds: "
                       f"{_describe(item['seeding'])} ({item['path']})")
    if count:
        logger.warning(f"{count} delete(s) take data from seeding torrents; clean those torrents up afterwards.")
    return count
