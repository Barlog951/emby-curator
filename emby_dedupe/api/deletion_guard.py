"""Safety guard for Emby item deletion.

Emby's ``DELETE /Items/{id}`` removes the item's media file and, when that file owns a
dedicated folder (a movie folder or a per-episode subfolder), the whole folder with it.
A keeper co-located in that folder is destroyed together with the duplicate.

This module decides, from paths alone, whether deleting an item could destroy its
keeper. It never deletes anything and never touches the filesystem; it only lets the
deletion code decline an unsafe Emby call. It is the backstop for co-located pairs
that the converter did not separate (old files, manual operations).
"""
import logging
import posixpath
import re

logger = logging.getLogger(__name__)


def _norm(path: str | None) -> str:
    """Normalise a path for comparison (forward slashes, no trailing slash)."""
    if not path:
        return ""
    return path.strip().replace("\\", "/").rstrip("/")


def _is_usable_path(path: str) -> bool:
    """True only if ``path`` is something the guard can actually reason about.

    A normalised path must contain a separator to have a parent directory — and the
    parent directory is the entire basis of every fold-delete rule here. Anything else
    (empty, whitespace, or the literal ``"unknown"`` that
    :mod:`emby_dedupe.api.metadata` substitutes when Emby returns no ``Path``) yields
    ``dirname() == ""``, which silently matches nothing and lets a delete through
    unchecked.
    """
    return "/" in path


def _under(path: str | None, folder: str | None) -> bool:
    """True if the file at ``path`` lives directly in or under ``folder``."""
    path, folder = _norm(path), _norm(folder)
    if not path or not folder:
        return False
    return path == folder or path.startswith(folder + "/")


# ---------------------------------------------------------------------------
# Episode identity: season folders
# ---------------------------------------------------------------------------
# The season folder is evidence of identity alongside the SxxEyy file name. Some packs
# keep one folder per season but name every season's files S01Exx; two such files are
# different episodes even though Emby and the names agree.

# "S05", "Season 5", "Staffel 5", "Série 7", "7. Série", "5. séria", "Sezona 2" …
_SEASON_DIR_RE = re.compile(
    r"^(?:s|season|staffel|s[eé]rie|s[eé]ria|sezona|sez[oó]na)\s*[._ -]?\s*(\d{1,3})$"
    r"|^(\d{1,3})\s*\.?\s*(?:season|staffel|s[eé]rie|s[eé]ria|sezona|sez[oó]na)$",
    re.IGNORECASE,
)
_EPISODE_RE = re.compile(r"[Ss](\d{1,3})[ ._-]?[Ee](\d{1,4})")
# A release/season-pack folder: "Wednesday.S02.2160p", "Show.S02E05-E08.1080p".
_PACK_SEASON_RE = re.compile(r"(?:^|[._ \-\[])[Ss](\d{1,2})(?=$|[Ee]\d|[._ \-\]])")


def _dir_season(name: str) -> int | None:
    """Season a single folder name stands for, or None."""
    match = _SEASON_DIR_RE.match(name.strip())
    if match:
        return int(match.group(1) or match.group(2))
    seasons = {int(m) for m in _PACK_SEASON_RE.findall(name)}
    return seasons.pop() if len(seasons) == 1 else None  # "S01-S09" says nothing


def folder_season(path: str | None) -> int | None:
    """Season number of the nearest season-like folder above the file, or None (flat layout).

    Looks at the file's own folder, then one level up, so ``S01/7. Série/x.mkv`` is
    season 7 (a season-7 set nested in S01) and ``S05/extras/x.mkv`` is season 5. A
    season-pack folder counts too (``Wednesday.S02.2160p`` is season 2).
    """
    parts = _norm(path).split("/")[:-1]
    for name in reversed(parts[-2:]):
        season = _dir_season(name)
        if season is not None:
            return season
    return None


def filename_season(path: str | None) -> int | None:
    """Season from the file name's SxxEyy, or None when the name has no SxxEyy."""
    match = _EPISODE_RE.search(posixpath.basename(_norm(path)))
    return int(match.group(1)) if match else None


def _season_label(season: int | None) -> str:
    return "flat folder" if season is None else f"S{season:02d}"


def episode_identity_conflict(keeper_path: str | None, delete_path: str | None) -> str | None:
    """Why deleting ``delete_path`` as a copy of ``keeper_path`` could delete a DIFFERENT
    episode, or None when the folders agree.

    Only episode files (an SxxEyy name) are judged. Refuses when the two files sit in
    different season folders (or one flat, one in a season folder), or when the
    duplicate's own season folder contradicts its file name. A missed duplicate only
    costs disk space; a wrong one costs an episode.
    """
    file_season = filename_season(delete_path)
    if file_season is None:
        return None
    keeper_folder, delete_folder = folder_season(keeper_path), folder_season(delete_path)
    if keeper_folder != delete_folder:
        return (f"different season folders (keeper {_season_label(keeper_folder)}, duplicate "
                f"{_season_label(delete_folder)}): may be a different episode")
    if delete_folder is not None and delete_folder != file_season:
        return (f"season folder {_season_label(delete_folder)} contradicts the file name's "
                f"S{file_season:02d}: episode identity is ambiguous")
    return None


def is_delete_safe(
    keeper_path: str | None, delete_path: str | None, known_paths, delete_paths=None
) -> tuple[bool, str]:
    """Decide whether deleting the item at ``delete_path`` via Emby can destroy the
    keeper at ``keeper_path``.

    Args:
        keeper_path: filesystem path of the item being KEPT for this duplicate group.
        delete_path: filesystem path of the item about to be deleted.
        known_paths: every other media path the run can see — ideally *every* media path
            in the library (see ``fetch_all_media_paths``), not just the duplicated items,
            so a folder's non-duplicate neighbours are visible. That is what tells a
            *dedicated* folder (Emby fold-deletes it) from a *shared* one (Emby deletes
            only the file). Decision paths alone make a single-duplicate folder look
            dedicated and over-refuse (safe, but blocks valid deletes).
        delete_paths: every path being deleted in this run. A same-folder sibling that is
            itself a delete target does not prove the folder is shared, because it is gone
            after the run too. Without it, a movie folder holding a keeper and two
            co-located deletes looks shared and Emby fold-deletes the folder with the
            keeper. None treats all ``known_paths`` as survivors (legacy).

    Returns:
        (safe: bool, reason: str). ``safe`` False means: refuse the Emby delete,
        because it would fold-delete a folder containing the keeper.
    """
    dp, kp = _norm(delete_path), _norm(keeper_path)
    if not _is_usable_path(dp):
        # Refuse rather than allow: without the delete path none of the rules below can be
        # evaluated, so allowing would switch the guard off. A missing Path is transient
        # (an item caught mid-scan); the delete happens on the next run.
        return False, "delete path unknown — cannot verify fold safety, refusing"
    if not kp:
        return True, "no keeper path to protect"

    ddir = posixpath.dirname(dp)
    if not _under(kp, ddir):
        return True, f"keeper is in a different folder than {ddir!r}"

    # Per-title folder: the folder is named for the item being deleted (the file stem
    # starts with the folder name). Emby fold-deletes the entire directory on any item
    # delete here, whatever else survives in it, so with the keeper inside there is no
    # safe delete; refuse without consulting siblings. Same rule as the converter's
    # dv_common._owns_folder; keep the two in sync.
    folder = posixpath.basename(ddir)
    delete_stem = posixpath.splitext(posixpath.basename(dp))[0]
    if folder and delete_stem.startswith(folder):
        return (
            False,
            f"keeper co-located in a dedicated folder named for this title ({ddir!r}); "
            "Emby fold-deletes the whole directory on any delete and destroys the keeper",
        )

    # Not a per-title folder (a season/collection/category container). Deleting here is
    # safe only if Emby removes just the file, i.e. the folder is shared with other
    # media that survives the run. Two refinements, both only ever over-refuse:
    #   * direct children only: an item nested in a subfolder does not stop Emby
    #     fold-deleting ddir;
    #   * surviving children only: a sibling that is itself deleted this run cannot keep
    #     the folder alive.
    delete_set = {_norm(p) for p in (delete_paths or [])}
    others = [
        p for p in known_paths
        if _norm(p) not in (dp, kp)
        and _norm(p) not in delete_set
        and posixpath.dirname(_norm(p)) == ddir
    ]
    if others:
        return True, f"shared folder ({len(others)} surviving item(s)) → Emby deletes file only"

    return (
        False,
        f"keeper co-located in a dedicated folder ({ddir!r}); Emby would fold-delete it "
        "and destroy the keeper",
    )


def collect_known_paths(decisions) -> list:
    """Gather every keep + delete media path across all decision groups.

    This is the *minimal* path set (duplicated items only). For complete folder
    visibility the caller unions in every library path (``fetch_all_media_paths``);
    decision paths alone can leave a single-duplicate folder looking "dedicated" and
    make the guard over-refuse a safe file-only delete.
    """
    paths = []
    for decision in decisions or []:
        keep = decision.get("keep") or {}
        if keep.get("path"):
            paths.append(keep["path"])
        for item in decision.get("delete", []) or []:
            if item.get("path"):
                paths.append(item["path"])
    return paths


def collect_delete_paths(decisions) -> list:
    """Gather every path being DELETED across all decision groups.

    Passed to :func:`is_delete_safe` so a same-folder sibling that is itself a delete
    target does not falsely vouch that the folder is "shared" (it won't survive the run).
    """
    paths = []
    for decision in decisions or []:
        for item in decision.get("delete", []) or []:
            if item.get("path"):
                paths.append(item["path"])
    return paths


# A series folder must sit at least this deep to be deletable, so a mis-constructed
# candidate handing over a library root (e.g. "/Movies/Serials") is rejected. Series
# folders live at depth 5 in the expected layout (library/section/show).
_MIN_CONTAINER_DEPTH = 4


def _is_container_delete_safe(dp: str) -> tuple[bool, str]:
    """Decide whether deleting a FOLDER-backed item (a Series) is safe.

    Deleting a Series item removes exactly that series' own folder; sibling series in the
    same container and the container itself are untouched. The only media at risk is
    what lives inside the series folder, which is what the run intends to delete.

    The file-based reasoning in :func:`is_cleanup_delete_safe` does not apply: for a
    file, ``dirname(path)`` is the folder Emby may fold-delete; for a series folder it
    is the library container, whose direct children are folders, never media files, so
    the survivor test would refuse every series.
    """
    depth = len([part for part in dp.split("/") if part])
    if depth < _MIN_CONTAINER_DEPTH:
        return (
            False,
            f"refusing to delete {dp!r}: too shallow to be a series folder "
            f"({depth} path components, need >= {_MIN_CONTAINER_DEPTH}) — this looks like "
            "a library root, and deleting it would remove the whole library",
        )
    return (
        True,
        f"series folder ({dp!r}) — Emby removes only this folder; siblings in the same "
        "container are unaffected",
    )


def is_cleanup_delete_safe(
    delete_path: str | None, known_paths, delete_paths=None,
    delete_is_container: bool = False,
) -> tuple[bool, str]:
    """Decide whether deleting ``delete_path`` can destroy media the run is KEEPING.

    The cleanup path has no per-group keeper the way dedup does; the thing to protect is
    anything that survives the run in the same folder. Emby fold-deletes an item's
    owning directory, so removing one movie can take unrelated titles with it when they
    share that directory.

    Every survivor under the delete's directory is checked with the same rules dedup uses
    (:func:`is_delete_safe`): per-title-folder trap, direct children only, surviving
    children only.

    Args:
        delete_path: filesystem path of the item about to be deleted.
        known_paths: every media path in the library (``fetch_all_media_paths``). Without
            full visibility a shared folder can look dedicated and the guard over-refuses.
        delete_paths: every path being deleted in THIS run, so a same-folder sibling that
            is itself a target does not count as a survivor.
        delete_is_container: True when ``delete_path`` is a FOLDER Emby owns as one item
            (a Series) rather than a media file; see :func:`_is_container_delete_safe`.

    Returns:
        (safe, reason). ``safe`` False means refuse the Emby delete — it would fold-delete
        a directory containing media that is meant to survive.
    """
    dp = _norm(delete_path)
    if not _is_usable_path(dp):
        # As in is_delete_safe: no usable path means no rule can be evaluated, so allowing
        # would switch the guard off.
        return False, "delete path unknown — cannot verify fold safety, refusing"

    if delete_is_container:
        return _is_container_delete_safe(dp)

    ddir = posixpath.dirname(dp)
    delete_set = {_norm(p) for p in (delete_paths or [])}
    survivors = [
        p for p in known_paths
        if _norm(p) != dp and _norm(p) not in delete_set and _under(_norm(p), ddir)
    ]
    if not survivors:
        return True, "nothing in this folder survives the run — fold-delete harms nothing"

    # Delegate to the dedup guard once per survivor; any single unsafe verdict refuses.
    for survivor in survivors:
        safe, reason = is_delete_safe(survivor, delete_path, known_paths, delete_paths)
        if not safe:
            return False, reason
    return True, f"shared folder ({len(survivors)} surviving item(s)) → Emby deletes file only"
