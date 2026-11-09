"""Episode numbering audit: where Emby's season/episode numbers disagree with the file names.

Emby numbers an episode from its metadata provider, not from the file name. When the
provider orders a show differently, or a new season is matched onto an older one, an
episode can end up under another episode's number (and hide behind it), stay unmatched,
or show up in the wrong season. This module classifies those cases from a plain episode
list and never writes anything.

Finding kinds, most serious first:

- ``shadowed``: another episode of the same series carries the same Emby number, so one
  of the two is hard to reach and inherits the other's watched state.
- ``unmatched``: Emby has no episode number for it (an unnumbered special or a failed match).
- ``mixed``: one season folder is spread over several Emby seasons.
- ``mismatch``: the numbers differ without a single consistent pattern.
- ``remapped``: every episode of the season folder moved by the same offset (the provider
  numbers the show differently); usually harmless.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field

SHADOWED = "shadowed"
UNMATCHED = "unmatched"
MIXED = "mixed"
MISMATCH = "mismatch"
REMAPPED = "remapped"
KINDS = (SHADOWED, UNMATCHED, MIXED, MISMATCH, REMAPPED)
ATTENTION_KINDS = frozenset({SHADOWED, UNMATCHED, MIXED, MISMATCH})

_SXE_RE = re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?!\d)")
_ALLOW_LINE_RE = re.compile(r"^\s*(\d+)")


@dataclass
class NumberingFinding:
    """One episode whose Emby number or match looks wrong."""

    kind: str
    series_id: str
    series_name: str
    item_id: str
    path: str
    name: str
    file_number: tuple[int, int]
    emby_number: tuple[int | None, int | None]
    played_by: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """JSON-ready form."""
        return {
            "kind": self.kind,
            "series_id": self.series_id,
            "series_name": self.series_name,
            "item_id": self.item_id,
            "path": self.path,
            "name": self.name,
            "file_number": list(self.file_number),
            "emby_number": list(self.emby_number),
            "played_by": self.played_by,
        }


def file_episode_number(path: str | None) -> tuple[int, int] | None:
    """Season and episode from the file name's ``SxxEyy``, or None when the name has none."""
    match = _SXE_RE.search(posixpath.basename((path or "").replace("\\", "/")))
    return (int(match.group(1)), int(match.group(2))) if match else None


def load_allowlist(text: str) -> set[str]:
    """Series ids from an allowlist file: one id per line, ``#`` starts a comment."""
    ids = set()
    for line in text.splitlines():
        match = _ALLOW_LINE_RE.match(line.split("#", 1)[0])
        if match:
            ids.add(match.group(1))
    return ids


def _emby_number(item: dict) -> tuple[int | None, int | None]:
    return item.get("ParentIndexNumber"), item.get("IndexNumber")


def _is_unmatched(item: dict) -> bool:
    """Emby could not give the episode a number, so it is listed apart from its season."""
    return item.get("IndexNumber") is None


def _finding(kind: str, item: dict, file_number: tuple[int, int]) -> NumberingFinding:
    return NumberingFinding(
        kind=kind,
        series_id=str(item.get("SeriesId") or ""),
        series_name=item.get("SeriesName") or "",
        item_id=str(item.get("Id") or ""),
        path=item.get("Path") or "",
        name=item.get("Name") or "",
        file_number=file_number,
        emby_number=_emby_number(item),
    )


def _shadowed_ids(numbered: list[tuple[dict, tuple[int, int]]]) -> set[str]:
    """Ids of mis-numbered episodes that share their Emby number with a different episode."""
    by_slot: dict[tuple, list[tuple[dict, tuple[int, int]]]] = defaultdict(list)
    for item, file_number in numbered:
        season, episode = _emby_number(item)
        if season is not None and episode is not None:
            by_slot[(item.get("SeriesId"), season, episode)].append((item, file_number))
    shadowed: set[str] = set()
    for (_series, season, episode), members in by_slot.items():
        if len({file_number for _item, file_number in members}) < 2:
            continue  # copies of one episode are a dedupe matter, not a numbering one
        shadowed.update(str(item.get("Id")) for item, file_number in members if file_number != (season, episode))
    return shadowed


def _classify_folder(group: list[tuple[dict, tuple[int, int]]]) -> str:
    """Kind for the mis-numbered episodes of one series' file season."""
    emby_seasons = {_emby_number(item)[0] for item, _number in group}
    if len(emby_seasons) > 1:
        return MIXED
    offsets = set()
    for item, (file_season, file_episode) in group:
        season, episode = _emby_number(item)
        if season is None or episode is None or (season, episode) == (file_season, file_episode):
            return MISMATCH  # part of the folder agrees, or Emby has no season for it
        offsets.add((season - file_season, episode - file_episode))
    return REMAPPED if len(offsets) == 1 else MISMATCH


def audit_numbering(episodes: list[dict], allowed_series: set[str] | None = None) -> list[NumberingFinding]:
    """Classify every episode whose Emby number or match disagrees with its file name.

    Args:
        episodes: Emby Episode items with ``Path``, ``Name``, ``SeriesId``, ``SeriesName``,
            ``ParentIndexNumber`` and ``IndexNumber``.
        allowed_series: Series ids whose numbering differs on purpose; they are skipped.

    Returns:
        Findings sorted by kind (most serious first), series name and file number.
    """
    allowed = allowed_series or set()
    numbered = [
        (item, number)
        for item in episodes
        if str(item.get("SeriesId") or "") not in allowed and (number := file_episode_number(item.get("Path")))
    ]
    shadowed = _shadowed_ids(numbered)
    findings: list[NumberingFinding] = []
    folders: dict[tuple, list[tuple[dict, tuple[int, int]]]] = defaultdict(list)
    for item, number in numbered:
        if str(item.get("Id")) in shadowed:
            findings.append(_finding(SHADOWED, item, number))
        elif _is_unmatched(item):
            findings.append(_finding(UNMATCHED, item, number))
        else:
            folders[(item.get("SeriesId"), number[0])].append((item, number))
    for group in folders.values():
        wrong = [(item, number) for item, number in group if _emby_number(item) != number]
        if wrong:
            kind = _classify_folder(group)
            findings.extend(_finding(kind, item, number) for item, number in wrong)
    findings.sort(key=lambda f: (KINDS.index(f.kind), f.series_name.lower(), f.file_number))
    return findings
