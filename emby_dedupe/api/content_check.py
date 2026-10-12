"""Frame check: do two Emby items show the same picture?

Dedupe pairs copies by provider id, season/episode and running time, which cannot tell
two different episodes of similar length apart: on 2026-10-12 a Slovak NCIS S19E08 that
was really E07 (swapped at the source) paired with the Czech E08, the lengths agreed
within 2 %, and the Czech E08 was deleted. It also cannot prove that a PAL speed-up
(25 fps, ~4 % shorter) is the same episode, so 42 such pairs were blocked every run.

The check takes the keyframes of the kept copy at five points of its running time and
looks for each one's twin in the other copy around the matching position. Frames are read
through Emby's ``/Videos/{id}/stream?static=true`` with the token in a request header
(never in the URL), so it works on any machine that reaches the server without mounting
the media share. Only keyframes are decoded (encoders often place them every few seconds,
not on scene cuts), scaled to 32x18 grey and compared by normalised correlation.

A look-alike frame is easy to find in any episode of the same show, so a point counts by
how many keyframes have a twin at ONE common time offset: the same episode lines up
(0.7-1.0 of its keyframes), another episode scatters (0-0.2). The offset found at one
point is carried to the next, so a copy with a cut or an extra scene stays in the search
window. The offsets also tell a real speed-up (offset stays flat) from a copy that is
simply longer (offset keeps growing), which dedupe uses to allow only the former.

When the two copies differ in length, ``covers`` asks the opposite question: is every
part of the copy that would be deleted also in the kept copy? It samples the copy to
delete from its first to its last minute, needs every point found in the kept copy on one
straight line, at the speed the two frame rates predict, with almost nothing left over at
the edges. Only then is nothing lost by the delete (Fuki, 2026-10-12).
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from tqdm import tqdm

from emby_dedupe.utils.logging import logger

FRAME_WIDTH, FRAME_HEIGHT = 32, 18
SAMPLE_POINTS = (0.15, 0.35, 0.55, 0.75, 0.9)
KEEPER_SPAN_SECONDS = 30.0  # keyframes of the kept copy taken from this span at each point
WINDOW_BEFORE_SECONDS = 45.0  # where their twins are searched in the other copy
WINDOW_AFTER_SECONDS = 90.0  # (an extra intro or recap shifts them later)
NARROW_WINDOW_SECONDS = 20.0  # search this far either side of the expected spot first
TWIN_CORRELATION = 0.6  # a keyframe has a twin at or above this correlation
OFFSET_TOLERANCE_SECONDS = 2.0  # twins this close agree on one offset (keyframes ~3 s apart)
MATCH_SCORE = 0.5  # a point matches when half of its keyframes agree on one offset
MATCHES_FOR_SAME = 3  # of the 5 points
MISS_SCORE = 0.3  # a point clearly misses below this
MISSES_FOR_DIFFERENT = 4  # of the 5 points
EARLY_MISS_SCORE = 0.15  # two points this low settle "different" without the rest
MAX_SPEEDUP_DRIFT_SECONDS = 15.0  # offset change allowed across matched points of a speed-up
COVER_HEAD_SECONDS = 45.0  # "covers" samples the copy to delete from this far into it...
COVER_TAIL_SECONDS = 75.0  # ...to this far before its end, plus every SAMPLE_POINTS point
COVER_LINE_TOLERANCE_SECONDS = 3.0  # a point this far off the straight line = a cut or extra scene
COVER_EDGE_EXTRA_SECONDS = 10.0  # unaccounted seconds allowed at the edges (studio logos, black)
MIN_KEYFRAMES = 2
WINDOW_FPS = 4  # the other copy is sampled this densely, so every keyframe has a near twin
FFMPEG_TIMEOUT_SECONDS = 120
PARALLEL_PAIRS = 4  # streaming from Emby over the network
PARALLEL_PAIRS_ON_HOST = 12  # ffmpeg on the media host, next to the files (24 cores on emby-gpu)
# Saved verdicts are keyed by this: bump it whenever the method or a threshold changes,
# so every pair is checked again under the new rules.
CHECK_VERSION = 5
ENV_CACHE_FILE = "DEDUPE_FRAME_CHECK_CACHE"
CACHE_KEEP_DAYS = 180  # a saved verdict not used for this long is dropped
_DEFAULT_CACHE = Path.home() / ".emby-dedupe" / "frame-check-cache.json"
_SAVED_VERDICTS = frozenset({"same", "different", "unsure"})
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/emby-curator-%r@%h:%p",
               "-o", "ControlPersist=120")

# PAL speed-up: film shot at 23.976 or 24 fps played at 25 fps runs 4.2 % shorter.
SPEEDUP_RATIOS = (25 / (24000 / 1001), 25 / 24)
SPEEDUP_TOLERANCE = 0.006
_PAL_FPS = (24.9, 25.1)
_FILM_FPS = (23.9, 24.1)
_TICKS_PER_SECOND = 10_000_000
_PTS_TIME_RE = re.compile(r"Parsed_showinfo\S* @ \S+\] n:\s*\d+ .*?pts_time:\s*(-?[\d.]+)")

Verdict = Literal["same", "different", "unsure", "unavailable"]
Keyframe = tuple[float, list[float]]  # (seconds into the item, unit-length frame vector)


@dataclass(frozen=True)
class FrameVerdict:
    """Outcome of a frame comparison: the score of each sample point and the time
    offsets (other copy minus kept copy, at the assumed speed) of the points that matched."""

    verdict: Verdict
    scores: tuple[float, ...] = field(default_factory=tuple)
    offsets: tuple[float, ...] = field(default_factory=tuple)
    note: str = ""  # why "covers" said what it said

    @property
    def drift(self) -> float | None:
        """How far the matched points' offsets spread, in seconds (None below 2 points)."""
        return max(self.offsets) - min(self.offsets) if len(self.offsets) >= 2 else None

    @property
    def steady(self) -> bool:
        """True when most points matched at nearly one offset, as a pure speed-up does.

        A copy that is longer for another reason (an extra scene, a different cut) shows
        its offset growing from point to point even when every picture matches.
        """
        drift = self.drift
        return len(self.offsets) >= MATCHES_FOR_SAME and drift is not None and drift <= MAX_SPEEDUP_DRIFT_SECONDS

    def describe(self) -> str:
        """Short reason text for logs and the report."""
        scores = ", ".join(f"{s:.2f}" for s in self.scores)
        text = f"frame-check {self.verdict}" + (f" ({scores})" if scores else "")
        drift = self.drift
        text += f", offset drift {drift:.0f}s" if drift is not None else ""
        return text + (f", {self.note}" if self.note else "")


def _in(value: float | None, bounds: tuple[float, float]) -> bool:
    return value is not None and bounds[0] <= value <= bounds[1]


def is_speedup_pair(keeper: dict, other: dict) -> bool:
    """True when the running-time gap is exactly a PAL speed-up.

    The shorter copy must run at 25 fps and the longer one at ~24 fps, and the running
    times must differ by 25/23.976 or 25/24 (within 0.6 %). Anything else, e.g. a copy
    with an extra recap, is not explained by speed and stays a mismatch.
    """
    a, b = keeper.get("runtime_ticks"), other.get("runtime_ticks")
    if not a or not b:
        return False
    shorter, longer = (keeper, other) if a < b else (other, keeper)
    if not (_in(shorter.get("frame_rate"), _PAL_FPS) and _in(longer.get("frame_rate"), _FILM_FPS)):
        return False
    ratio = max(a, b) / min(a, b)
    return any(abs(ratio - r) <= SPEEDUP_TOLERANCE for r in SPEEDUP_RATIOS)


STANDARD_FRAME_RATES = (24000 / 1001, 24.0, 25.0, 30000 / 1001, 30.0)
_SNAP_TOLERANCE = 0.01


def standard_frame_rate(fps: float) -> float:
    """The picture rate behind what Emby reports.

    Emby reports interlaced video at its field rate (a 1080i TV recording of The Killing:
    50 for 25 pictures a second) and some x265 encodes at 23.809525 for 23.976, so a rate
    of 47 or more is halved and a rate within 1 % of a standard one is snapped to it.
    """
    rate = fps / 2 if fps >= 47 else fps
    nearest = min(STANDARD_FRAME_RATES, key=lambda r: abs(rate / r - 1))
    return nearest if abs(rate / nearest - 1) <= _SNAP_TOLERANCE else rate


def expected_speed(keeper: dict, other: dict) -> float | None:
    """Kept-copy seconds per second of ``other`` if both show the same frames (None when a frame rate is unknown).

    25 fps from a 23.976 fps master runs 23.976/25 = 0.959 as long; equal rates give 1.0.
    """
    keeper_fps, other_fps = keeper.get("frame_rate"), other.get("frame_rate")
    if not keeper_fps or not other_fps:
        return None
    return standard_frame_rate(float(other_fps)) / standard_frame_rate(float(keeper_fps))


def coverage_verdict(points: list[tuple[float, float]], other_seconds: float, keeper_seconds: float,
                     speed: float, expected: float | None) -> tuple[Verdict, str]:
    """Does the kept copy hold everything of the other copy? ("same" = yes) and why.

    ``points`` are (time in the other copy, offset) of every sampled point, all of them
    found in the kept copy; offset = kept-copy time minus time * ``speed``. They must lie on
    one straight line (a cut or extra scene shows as a step), the line's speed must be what
    the frame rates predict (NCIS S05E15: an avi ~110 s longer at the SAME speed had its
    extra minutes spread so evenly that the line looked like a PAL speed-up, but both frame
    rates said otherwise), and the line may leave at most ``COVER_EDGE_EXTRA_SECONDS`` of
    the other copy outside the kept one (a TV lead-in or a cold open is content).
    """
    if expected is None:
        return "unsure", "frame rate unknown"
    if len(points) < 3:
        return "unsure", "too few points"
    times, offsets = [t for t, _ in points], [o for _, o in points]
    slope, intercept = statistics.linear_regression(times, offsets)
    off_line = max(abs(o - (slope * t + intercept)) for t, o in points)
    if off_line > COVER_LINE_TOLERANCE_SECONDS:
        return "unsure", f"a part is missing or extra ({off_line:.0f}s off the line)"
    fitted = speed + slope
    if abs(fitted / expected - 1) > SPEEDUP_TOLERANCE:
        return "unsure", f"speed {fitted:.4f} is not the {expected:.4f} the frame rates give"
    head = max(0.0, -intercept / fitted)  # other-copy seconds before the kept copy starts
    tail = max(0.0, (fitted * other_seconds + intercept - keeper_seconds) / fitted)
    if head + tail > COVER_EDGE_EXTRA_SECONDS:
        return "unsure", f"{head:.0f}s at the start and {tail:.0f}s at the end are not in the kept copy"
    return "same", f"covered (speed {fitted:.4f}, {head + tail:.0f}s at the edges)"


def classify(scores: list[float]) -> Verdict:
    """Majority vote over the sample points.

    One point may miss where a version is cut (a TV edit, an ad break), so "same" needs
    most points to match and "different" needs most points to miss; anything between is
    "unsure" and is treated like "different" by dedupe (the pair is kept).
    """
    if sum(s >= MATCH_SCORE for s in scores) >= MATCHES_FOR_SAME:
        return "same"
    if sum(s < MISS_SCORE for s in scores) >= MISSES_FOR_DIFFERENT:
        return "different"
    return "unsure"


def _vector(chunk: bytes) -> list[float] | None:
    """Mean-centred, unit-length frame; None for a flat (single-colour) frame."""
    mean = sum(chunk) / len(chunk)
    centred = [px - mean for px in chunk]
    norm = math.sqrt(sum(v * v for v in centred))
    if norm < 1.0:
        return None
    return [v / norm for v in centred]


def frames_from_raw(raw: bytes, times: list[float], start: float = 0.0) -> list[Keyframe]:
    """Pair ffmpeg's raw grey frames with their showinfo times (flat frames dropped).

    The two streams must line up one to one; when they do not (a truncated read), nothing
    is returned rather than frames with the wrong times, which would corrupt the offsets.
    """
    size = FRAME_WIDTH * FRAME_HEIGHT
    if len(raw) % size or len(raw) // size != len(times):
        return []
    keyframes = []
    for n, at in enumerate(times):
        vector = _vector(raw[n * size:(n + 1) * size])
        if vector is not None:
            keyframes.append((start + at, vector))
    return keyframes


def point_match(keyframes: list[Keyframe], window: list[Keyframe], speed: float) -> tuple[float, float | None]:
    """Share of the kept copy's keyframes whose twins in ``window`` agree on one offset.

    Each keyframe's best correlating window frame is its twin when the correlation reaches
    ``TWIN_CORRELATION``; the twin's offset is its time minus the keyframe's time scaled by
    ``speed``. Returns that share and the common offset (None when no twin was found).
    """
    offsets = []
    for at, frame in keyframes:
        correlation, twin_at = max((math.sumprod(frame, other), other_at) for other_at, other in window)
        if correlation >= TWIN_CORRELATION:
            offsets.append(twin_at - at * speed)
    if not offsets:
        return 0.0, None
    centre = max(offsets, key=lambda o: sum(abs(o - x) <= OFFSET_TOLERANCE_SECONDS for x in offsets))
    agreeing = [x for x in offsets if abs(x - centre) <= OFFSET_TOLERANCE_SECONDS]
    return len(agreeing) / len(keyframes), statistics.median(agreeing)


def stream_base_url(base_url: str) -> str:
    """``base_url`` without a default port (``:443`` on https, ``:80`` on http).

    dedupe builds ``https://host:443``; httpx leaves the default port out of the Host
    header, but ffmpeg sends ``Host: host:443`` and the reverse proxy in front of Emby
    answers 5xx, so on 2026-10-12 every frame check of a real run came back "unavailable".
    """
    base_url = base_url.rstrip("/")
    parts = urlsplit(base_url)
    if (parts.scheme, parts.port) in (("https", 443), ("http", 80)):
        return parts._replace(netloc=parts.netloc.rsplit(":", 1)[0]).geturl()
    return base_url


def cache_path() -> Path:
    """Where saved verdicts live: ``$DEDUPE_FRAME_CHECK_CACHE`` or ``~/.emby-dedupe/frame-check-cache.json``."""
    return Path(os.environ.get(ENV_CACHE_FILE) or _DEFAULT_CACHE)


def cache_key(keeper: dict, other: dict, kind: str = "compare") -> str:
    """The saved-verdict key: method version, check kind, then id, path and running time of both copies.

    A replaced or renamed file, or a re-probed running time, makes a new key, so a saved
    verdict is only ever reused for exactly the files it was measured on.
    """
    def part(item: dict) -> str:
        return f"{item.get('id')}|{item.get('path') or ''}|{item.get('runtime_ticks') or ''}"
    kind_part = "" if kind == "compare" else f"{kind}|"
    return f"v{CHECK_VERSION}|{kind_part}{part(keeper)}|{part(other)}"


def load_cache(path: Path) -> dict[str, dict]:
    """Saved verdicts of this method version; empty when there are none or the file is unreadable."""
    try:
        with path.open(encoding="utf-8") as fh:
            pairs = (json.load(fh) or {}).get("pairs") or {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning(f"Frame check cache {path} unreadable, starting fresh: {exc}")
        return {}
    prefix = f"v{CHECK_VERSION}|"
    return {key: entry for key, entry in pairs.items()
            if key.startswith(prefix) and isinstance(entry, dict) and entry.get("verdict") in _SAVED_VERDICTS}


def save_cache(path: Path, pairs: dict[str, dict]) -> None:
    """Write the verdicts atomically, dropping those unused for ``CACHE_KEEP_DAYS``."""
    cutoff = (datetime.now(UTC) - timedelta(days=CACHE_KEEP_DAYS)).isoformat()
    kept = {key: entry for key, entry in pairs.items() if str(entry.get("used", "")) >= cutoff}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".frame-check-cache-", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": CHECK_VERSION, "pairs": kept}, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning(f"Frame check cache {path} not written: {exc}")


class FrameChecker:
    """Compares two Emby items frame by frame (results cached per pair).

    With ``ssh_host`` (``user@host`` of the machine that serves the media) ffmpeg runs
    there and reads each item's own file path; only the 32x18 frames (a few KB) come
    back. Without it, the copies are streamed from Emby, about 0.5 GB per 1080p pair.

    With ``cache_file`` the same/different/unsure verdicts are saved between runs, so a pair
    that stays (kept by the guard, or not deleted yet) is checked once, not every run.
    """

    def __init__(self, base_url: str, api_key: str, ffmpeg_path: str | None = None,
                 ssh_host: str | None = None, cache_file: Path | None = None) -> None:
        self._base_url = stream_base_url(base_url)
        self._api_key = api_key
        self._ssh_host = ssh_host or None
        self._parallel = PARALLEL_PAIRS_ON_HOST if self._ssh_host else PARALLEL_PAIRS
        self._ffmpeg: str | None
        if self._ssh_host:
            self._ffmpeg = ffmpeg_path or "ffmpeg"  # resolved on the media host
        else:
            self._ffmpeg = ffmpeg_path if ffmpeg_path is not None else shutil.which("ffmpeg")
        self._cache: dict[tuple[str, str, str], FrameVerdict] = {}
        self._sources: dict[str, str | None] = {}
        self._cache_file = cache_file
        self._saved: dict[str, dict] = load_cache(cache_file) if cache_file else {}
        if not self._ffmpeg:
            logger.warning("ffmpeg not found: the dedupe frame check is off "
                           "(deletes rely on the running-time and folder checks only).")

    @property
    def available(self) -> bool:
        return bool(self._ffmpeg)

    def _media_source_id(self, item_id: str) -> str | None:
        """The media source of the item's OWN file (None when it cannot be found).

        Emby groups copies in one folder as versions of each other, and
        ``/Videos/{id}/stream`` without ``MediaSourceId`` serves the group's default copy
        for every item id: on 2026-10-12 a 720p BluRay and an NCIS SD avi both streamed
        their 1080p sibling, so the check compared a file with itself and said "same".
        """
        if item_id not in self._sources:
            try:
                response = httpx.get(f"{self._base_url}/Items", params={"Ids": item_id, "Fields": "MediaSources"},
                                     headers={"X-Emby-Token": self._api_key}, timeout=30)
                response.raise_for_status()
                items = response.json().get("Items") or []
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(f"Frame check could not look up the media source of item {item_id}: {exc}")
                return None
            sources = items[0].get("MediaSources") or [] if items else []
            own = [s for s in sources if str(s.get("ItemId")) == item_id] or (sources if len(sources) == 1 else [])
            self._sources[item_id] = str(own[0]["Id"]) if own and own[0].get("Id") else None
        return self._sources[item_id]

    def _input_args(self, item: dict) -> list[str] | None:
        """ffmpeg input arguments for the item's own file, or None when it cannot be named.

        On the media host that is the file path Emby reports. Over the network it is the
        Emby stream of the item's own media source (never the version group's default).
        """
        item_id = str(item.get("id"))
        if self._ssh_host:
            path = item.get("path")
            return ["-i", str(path)] if path else None
        source = self._media_source_id(item_id)
        if source is None:
            return None
        return ["-headers", f"X-Emby-Token: {self._api_key}\r\n",
                "-i", f"{self._base_url}/Videos/{item_id}/stream?static=true&MediaSourceId={quote(source)}"]

    def _keyframes(self, item: dict, start: float, seconds: float, dense: bool = False) -> list[Keyframe]:
        """Grey 32x18 frames of ``seconds`` from ``start`` with their times.

        By default only keyframes are decoded (``-skip_frame nokey``), ~25x cheaper than
        every frame. ``dense`` decodes everything and keeps ``WINDOW_FPS`` frames a
        second: encoders that place a keyframe every few seconds regardless of the
        picture (TVD S07/S08 WEB-DL, every 4 s) leave a scene-cut keyframe of the other
        copy with no twin nearby, which made the same episode look "unsure".
        ``showinfo`` logs each frame's time (relative to ``start``) on stderr.
        """
        item_id = item.get("id")
        source = self._input_args(item)
        if source is None:
            logger.warning(f"Frame check: no file of its own for item {item_id}; not compared.")
            return []
        start = max(0.0, start)
        sampling = [] if dense else ["-skip_frame", "nokey"]
        rate = f"fps={WINDOW_FPS}," if dense else ""
        cmd = [
            str(self._ffmpeg), "-hide_banner", "-nostats", "-loglevel", "info", *sampling,
            "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", *source,
            "-an", "-sn", "-fps_mode", "passthrough",
            "-vf", f"{rate}scale={FRAME_WIDTH}:{FRAME_HEIGHT},format=gray,showinfo", "-f", "rawvideo", "-",
        ]
        if self._ssh_host:
            cmd = ["ssh", *SSH_OPTIONS, self._ssh_host, shlex.join(cmd)]
        try:
            run = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"Frame check could not read item {item_id}: {exc}")
            return []
        if run.returncode != 0 and not run.stdout:
            error = run.stderr.decode("utf-8", "replace").strip().splitlines()
            logger.warning(f"Frame check could not read item {item_id}: {error[-1] if error else run.returncode}")
            return []
        times = [float(t) for t in _PTS_TIME_RE.findall(run.stderr.decode("utf-8", "replace"))]
        keyframes = frames_from_raw(run.stdout, times, start)
        if run.stdout and not keyframes and len(times) * FRAME_WIDTH * FRAME_HEIGHT != len(run.stdout):
            logger.warning(f"Frame check: item {item_id} gave {len(times)} frame times for "
                           f"{len(run.stdout) // (FRAME_WIDTH * FRAME_HEIGHT)} frames; treated as unreadable.")
        return keyframes

    def _known(self, keeper: dict, other: dict, kind: str = "compare") -> FrameVerdict | None:
        """The verdict already measured for this pair, this run or a saved earlier one."""
        verdict = self._cache.get((kind, str(keeper.get("id")), str(other.get("id"))))
        if verdict is None and self._cache_file:
            entry = self._saved.get(cache_key(keeper, other, kind))
            if entry:
                entry["used"] = datetime.now(UTC).replace(microsecond=0).isoformat()
                verdict = FrameVerdict(entry["verdict"], tuple(entry.get("scores") or ()),
                                       tuple(entry.get("offsets") or ()), str(entry.get("note") or ""))
                self._cache[(kind, str(keeper.get("id")), str(other.get("id")))] = verdict
        return verdict

    def _remember(self, keeper: dict, other: dict, verdict: FrameVerdict, kind: str = "compare") -> None:
        self._cache[(kind, str(keeper.get("id")), str(other.get("id")))] = verdict
        if self._cache_file and verdict.verdict in _SAVED_VERDICTS:  # never "unavailable"
            now = datetime.now(UTC).replace(microsecond=0).isoformat()
            self._saved[cache_key(keeper, other, kind)] = {
                "verdict": verdict.verdict, "scores": list(verdict.scores), "offsets": list(verdict.offsets),
                "note": verdict.note, "checked": now, "used": now}

    def save(self) -> None:
        """Write the saved verdicts (no-op without a cache file)."""
        if self._cache_file:
            save_cache(self._cache_file, self._saved)

    def compare(self, keeper: dict, other: dict) -> FrameVerdict:
        """Same / different / unsure for the pair, or unavailable when it cannot be checked."""
        return self._checked(keeper, other, "compare")

    def covers(self, keeper: dict, other: dict) -> FrameVerdict:
        """"same" only when the kept copy holds every part of ``other`` (see ``coverage_verdict``)."""
        return self._checked(keeper, other, "cover")

    def _checked(self, keeper: dict, other: dict, kind: str) -> FrameVerdict:
        verdict = self._known(keeper, other, kind)
        if verdict is None:
            verdict = self._run(kind, keeper, other)
            self._remember(keeper, other, verdict, kind)
            self.save()
        return verdict

    def _run(self, kind: str, keeper: dict, other: dict) -> FrameVerdict:
        return self._covers(keeper, other) if kind == "cover" else self._compare(keeper, other)

    def prefetch(self, pairs: list[tuple[dict, dict]], covering: list[tuple[dict, dict]] | None = None) -> None:
        """Check many pairs up front, a few at a time (ffmpeg runs as separate processes).

        ``pairs`` get ``compare``, ``covering`` (pairs whose lengths differ) get ``covers``.
        """
        tasks = [("compare", k, o) for k, o in pairs] + [("cover", k, o) for k, o in covering or []]
        todo = [(kind, k, o) for kind, k, o in tasks if self._known(k, o, kind) is None]
        reused = len(tasks) - len(todo)
        if reused:
            logger.info(f"Frame check: {reused} pair(s) already checked in an earlier run.")
        if not (todo and self.available):
            self.save()
            return
        where = f"on {self._ssh_host}" if self._ssh_host else "streamed from Emby"
        logger.info(f"Frame check: comparing {len(todo)} pair(s), {self._parallel} at a time, {where}...")
        counts: Counter[str] = Counter()
        with ThreadPoolExecutor(max_workers=self._parallel) as pool:
            futures = {pool.submit(self._run, kind, keeper, other): (kind, keeper, other)
                       for kind, keeper, other in todo}
            with tqdm(as_completed(futures), total=len(futures), desc="Frame check", unit="pair") as progress:
                for future in progress:
                    kind, keeper, other = futures[future]
                    verdict = future.result()
                    self._remember(keeper, other, verdict, kind)
                    counts[verdict.verdict] += 1
                    progress.set_postfix(dict(counts))
        self.save()
        summary = ", ".join(f"{n} {verdict}" for verdict, n in counts.most_common())
        (logger.warning if counts["unavailable"] else logger.info)(f"Frame check: {summary}.")

    def _score_point(self, keeper: dict, other: dict, at: float, speed: float,
                     shift: float | None) -> tuple[float, float | None] | None:
        """Score and offset of the point ``at`` (None when either copy cannot be read).

        The other copy is searched around ``at * speed + shift``. ``shift`` is the offset
        the previous matched point found (None before the first match, then 0): a cut or an
        extra scene then does not push the rest of the episode out of the window. Only
        +/-20 s around the expected spot are decoded first, where real copies line up
        (offsets of a few seconds); the full window is the fallback on a miss.
        """
        keyframes = self._keyframes(keeper, at, KEEPER_SPAN_SECONDS)
        if len(keyframes) < MIN_KEYFRAMES:
            return None
        windows = [(NARROW_WINDOW_SECONDS, NARROW_WINDOW_SECONDS), (WINDOW_BEFORE_SECONDS, WINDOW_AFTER_SECONDS)]
        result: tuple[float, float | None] = (0.0, None)
        for before, after in windows:
            start = at * speed + (shift or 0.0) - before
            seconds = (before + KEEPER_SPAN_SECONDS + after) * speed
            if start < 0:  # ffmpeg reads from 0 then, so the frame times must start there too
                start, seconds = 0.0, seconds + start
            if seconds <= 0:
                return result
            window = self._keyframes(other, start, seconds, dense=True)
            if not window:
                return None
            result = point_match(keyframes, window, speed)
            if result[0] >= MATCH_SCORE:
                break
        return result

    def _compare(self, keeper: dict, other: dict) -> FrameVerdict:
        keeper_ticks, other_ticks = keeper.get("runtime_ticks"), other.get("runtime_ticks")
        if not (self.available and keeper.get("id") and other.get("id") and keeper_ticks and other_ticks):
            return FrameVerdict("unavailable")
        speed = other_ticks / keeper_ticks
        keeper_seconds = keeper_ticks / _TICKS_PER_SECOND
        scores: list[float] = []
        offsets: list[float] = []
        shift: float | None = None
        for point in SAMPLE_POINTS:
            result = self._score_point(keeper, other, keeper_seconds * point, speed, shift)
            if result is None:
                return FrameVerdict("unavailable", tuple(scores), tuple(offsets))
            score, offset = result
            scores.append(round(score, 2))
            if score >= MATCH_SCORE and offset is not None:  # only a matched point moves the window
                offsets.append(round(offset, 1))
                shift = offset
            if len(scores) == 2 and max(scores) < EARLY_MISS_SCORE:  # two clearly foreign points settle it
                return FrameVerdict("different", tuple(scores), tuple(offsets))
            if len(offsets) == MATCHES_FOR_SAME:  # the vote is won; the rest cannot change it
                return FrameVerdict("same", tuple(scores), tuple(offsets))
        return FrameVerdict(classify(scores), tuple(scores), tuple(offsets))

    def _covers(self, keeper: dict, other: dict) -> FrameVerdict:
        """Sample ``other`` (the copy to delete) and find every point in the kept copy."""
        keeper_ticks, other_ticks = keeper.get("runtime_ticks"), other.get("runtime_ticks")
        if not (self.available and keeper.get("id") and other.get("id") and keeper_ticks and other_ticks):
            return FrameVerdict("unavailable")
        speed = keeper_ticks / other_ticks
        other_seconds = other_ticks / _TICKS_PER_SECOND
        times = [COVER_HEAD_SECONDS, *(other_seconds * p for p in SAMPLE_POINTS), other_seconds - COVER_TAIL_SECONDS]
        times = sorted(t for t in times if 0 <= t <= other_seconds - KEEPER_SPAN_SECONDS)
        scores: list[float] = []
        points: list[tuple[float, float]] = []
        shift: float | None = None
        for at in times:
            result = self._score_point(other, keeper, at, speed, shift)
            if result is None:
                return FrameVerdict("unavailable", tuple(scores), tuple(o for _, o in points))
            score, offset = result
            scores.append(round(score, 2))
            if score < MATCH_SCORE or offset is None:  # a part of the copy to delete is not in the kept one
                verdict: Verdict = "different" if score < MISS_SCORE else "unsure"
                return FrameVerdict(verdict, tuple(scores), tuple(o for _, o in points),
                                    f"{at:.0f}s of the copy to delete not found in the kept copy")
            points.append((at, round(offset, 1)))
            shift = offset
        verdict, note = coverage_verdict(points, other_seconds, keeper_ticks / _TICKS_PER_SECOND,
                                         speed, expected_speed(keeper, other))
        return FrameVerdict(verdict, tuple(scores), tuple(o for _, o in points), note)
