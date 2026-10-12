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
"""

from __future__ import annotations

import math
import re
import shlex
import shutil
import statistics
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
NARROW_WINDOW_SECONDS = 20.0  # once an offset is known: search this far either side of it first
TWIN_CORRELATION = 0.6  # a keyframe has a twin at or above this correlation
OFFSET_TOLERANCE_SECONDS = 2.0  # twins this close agree on one offset (keyframes ~3 s apart)
MATCH_SCORE = 0.5  # a point matches when half of its keyframes agree on one offset
MATCHES_FOR_SAME = 3  # of the 5 points
MISS_SCORE = 0.3  # a point clearly misses below this
MISSES_FOR_DIFFERENT = 4  # of the 5 points
EARLY_MISS_SCORE = 0.15  # two points this low settle "different" without the rest
MAX_SPEEDUP_DRIFT_SECONDS = 15.0  # offset change allowed across matched points of a speed-up
MIN_KEYFRAMES = 2
WINDOW_FPS = 4  # the other copy is sampled this densely, so every keyframe has a near twin
FFMPEG_TIMEOUT_SECONDS = 120
PARALLEL_PAIRS = 4  # streaming from Emby over the network
PARALLEL_PAIRS_ON_HOST = 8  # ffmpeg on the media host, next to the files
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
        return text + (f", offset drift {drift:.0f}s" if drift is not None else "")


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


class FrameChecker:
    """Compares two Emby items frame by frame (results cached per pair).

    With ``ssh_host`` (``user@host`` of the machine that serves the media) ffmpeg runs
    there and reads each item's own file path; only the 32x18 frames (a few KB) come
    back. Without it, the copies are streamed from Emby, about 0.5 GB per 1080p pair.
    """

    def __init__(self, base_url: str, api_key: str, ffmpeg_path: str | None = None,
                 ssh_host: str | None = None) -> None:
        self._base_url = stream_base_url(base_url)
        self._api_key = api_key
        self._ssh_host = ssh_host or None
        self._parallel = PARALLEL_PAIRS_ON_HOST if self._ssh_host else PARALLEL_PAIRS
        self._ffmpeg: str | None
        if self._ssh_host:
            self._ffmpeg = ffmpeg_path or "ffmpeg"  # resolved on the media host
        else:
            self._ffmpeg = ffmpeg_path if ffmpeg_path is not None else shutil.which("ffmpeg")
        self._cache: dict[tuple[str, str], FrameVerdict] = {}
        self._sources: dict[str, str | None] = {}
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

    def compare(self, keeper: dict, other: dict) -> FrameVerdict:
        """Same / different / unsure for the pair, or unavailable when it cannot be checked."""
        key = (str(keeper.get("id")), str(other.get("id")))
        if key not in self._cache:
            self._cache[key] = self._compare(keeper, other)
        return self._cache[key]

    def prefetch(self, pairs: list[tuple[dict, dict]]) -> None:
        """Check many pairs up front, a few at a time (ffmpeg runs as separate processes)."""
        todo = [(k, o) for k, o in pairs if (str(k.get("id")), str(o.get("id"))) not in self._cache]
        if not (todo and self.available):
            return
        where = f"on {self._ssh_host}" if self._ssh_host else "streamed from Emby"
        logger.info(f"Frame check: comparing {len(todo)} pair(s), {self._parallel} at a time, {where}...")
        counts: Counter[str] = Counter()
        with ThreadPoolExecutor(max_workers=self._parallel) as pool:
            futures = {pool.submit(self._compare, keeper, other): (keeper, other) for keeper, other in todo}
            with tqdm(as_completed(futures), total=len(futures), desc="Frame check", unit="pair") as progress:
                for future in progress:
                    keeper, other = futures[future]
                    verdict = future.result()
                    self._cache[(str(keeper.get("id")), str(other.get("id")))] = verdict
                    counts[verdict.verdict] += 1
                    progress.set_postfix(dict(counts))
        summary = ", ".join(f"{n} {verdict}" for verdict, n in counts.most_common())
        (logger.warning if counts["unavailable"] else logger.info)(f"Frame check: {summary}.")

    def _score_point(self, keeper: dict, other: dict, at: float, speed: float,
                     shift: float | None) -> tuple[float, float | None] | None:
        """Score and offset of the point ``at`` (None when either copy cannot be read).

        The other copy is searched around ``at * speed + shift``. ``shift`` is the offset
        the previous matched point found (None before the first match): a cut or an extra
        scene then does not push the rest of the episode out of the window, and only
        +/-20 s around it are decoded first; the full window is the fallback on a miss.
        """
        keyframes = self._keyframes(keeper, at, KEEPER_SPAN_SECONDS)
        if len(keyframes) < MIN_KEYFRAMES:
            return None
        windows = [(NARROW_WINDOW_SECONDS, NARROW_WINDOW_SECONDS)] if shift is not None else []
        windows.append((WINDOW_BEFORE_SECONDS, WINDOW_AFTER_SECONDS))
        result: tuple[float, float | None] = (0.0, None)
        for before, after in windows:
            window = self._keyframes(other, at * speed + (shift or 0.0) - before,
                                     (before + KEEPER_SPAN_SECONDS + after) * speed, dense=True)
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
