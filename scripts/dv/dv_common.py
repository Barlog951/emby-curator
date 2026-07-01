"""Shared helpers for the dv/ Dolby-Vision P5->HDR10 tools (dv-scan, dv-convert).

Single source of truth for ffprobe access and the Profile-5 ("green/pink")
criterion, so the scanner and the converter can never drift apart.

THE criterion: Dolby Vision **Profile 5** only (single-layer ICtCp, no HDR10
fallback -> green sky + pink faces on any non-DV player). Profiles 4/7/8 carry a
real HDR10/SDR base and play fine -> never flagged. (compatibility_id alone is the
wrong signal: P8 with compat-id 0 still has a usable HDR10 base.)
"""
import json
import os
import re
import subprocess
import sys
import time

# ffprobe show_entries string for the DV side-data we key on.
DV_ENTRIES = "stream_side_data=dv_profile,dv_bl_signal_compatibility_id"

# The only problematic profile (see module docstring).
PROBLEM_PROFILE = 5

# --- Safe HDR10 output placement -------------------------------------------------
# The converted "<name> [HDR10].mkv" must NEVER end up inside a folder that Emby
# would fold-delete when the dedupe later removes the P5 original via the API.
# Emby's DELETE removes the file's *owning* folder (a movie folder or a per-episode
# subfolder) but only the file when it is loose in a shared folder. So we place the
# keeper one level OUT of any dedicated folder. (Proven live 2026-06-25 — see the
# emby-delete-folder-rule reference / the dv-p5 fix.)
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm"}
_EP_RE = re.compile(r"s\d{1,2}\s*e\d{1,3}", re.I)                       # SxxEyy in a filename
_SEASON_RE = re.compile(r"^(s\d{1,3}|season\s*\d{1,3}|series\s*\d{1,3}|specials)$", re.I)
_EXTRA_RE = re.compile(                                                  # trailer/sample/extras
    r"(?:^|[-_. ])(?:trailer|sample|featurette|behindthescenes|deleted|interview|extra)s?(?:[-_. ]|$)",
    re.I)


def utc_now():
    """ISO-8601 UTC timestamp, e.g. '2026-06-24T19:13:04Z'."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def probe(path, entries, streams="v:0", timeout=120):
    """Run ffprobe for `entries` on `path`; return parsed JSON dict (or {})."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", streams,
         "-show_entries", entries, "-of", "json", path],
        capture_output=True, text=True, timeout=timeout)
    return json.loads(r.stdout or "{}")


def dv_profile_of(probe_json):
    """(dv_profile, compatibility_id) from an ffprobe result, or (None, None).

    Accepts either the full ffprobe dict (with a "streams" key) or a single
    stream dict.
    """
    s = probe_json
    if isinstance(s, dict) and "streams" in s:
        s = (s.get("streams") or [{}])[0]
    for sd in (s or {}).get("side_data_list", []):
        if "dv_profile" in sd:
            return sd.get("dv_profile"), sd.get("dv_bl_signal_compatibility_id")
    return None, None


def is_problematic(dv_profile):
    """True iff this DV profile is the green/pink case (Profile 5)."""
    return dv_profile == PROBLEM_PROFILE


def print_cmd(cmd, file=sys.stderr):
    """Echo a subprocess command, quoting args that contain spaces."""
    print("  $ " + " ".join(x if " " not in x else repr(x) for x in cmd), file=file)


def _is_video(name):
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def _season_folder(path):
    """Nearest ancestor directory that names a season (`S03`, `Season 3`,
    `Specials`), or None. Walks up from the file toward the filesystem root."""
    p = os.path.dirname(path)
    while p and p != os.path.dirname(p):
        if _SEASON_RE.match(os.path.basename(p)):
            return p
        p = os.path.dirname(p)
    return None


def _owns_folder(inp):
    """True if deleting `inp` through Emby would fold-delete its whole folder.

    A folder is "owned" (a fold-delete trap) in either of two ways:

    1. **Per-title folder** — the folder is named after this movie/episode (the file
       stem starts with the folder name). Emby removes the ENTIRE directory when any
       item in it is deleted, *no matter how many other versions/files sit there*.
       (Proven live 2026-07-01: `/Movies/4K/Marty Supreme (2025)/` held a REMUX keeper
       + a P5 + the HDR10; deleting the P5 fold-deleted the directory and destroyed the
       73.6 GB REMUX. The old rule wrongly saw the co-located versions as "shared" and
       dropped the HDR10 loose here, inflating the folder so the dedupe guard also
       mis-cleared the delete.) So a per-title folder is a trap regardless of contents.

    2. **Sole primary file** — `inp` is effectively the only primary-media file in the
       folder (its own `<name> [HDR10].mkv` sibling and obvious extras don't count).

    If listing the folder fails, assume NOT owned (place beside — only ever safe for a
    genuinely shared/loose location).
    """
    d = os.path.dirname(inp)
    folder = os.path.basename(d)
    inp_name = os.path.basename(inp)
    stem = os.path.splitext(inp_name)[0]

    # (1) per-title folder → always a fold-delete trap, even with co-located versions.
    if folder and stem.startswith(folder):
        return True

    # (2) sole-primary-file test — for a movie loose in a shared container.
    hdr10_sib = stem + " [HDR10].mkv"
    try:
        names = os.listdir(d)
    except OSError:
        return False
    for name in names:
        if name == inp_name or name == hdr10_sib:
            continue
        if not _is_video(name):
            continue
        if _EXTRA_RE.search(os.path.splitext(name)[0]):
            continue
        return False  # a DIFFERENT title lives here → genuinely shared → file-only delete
    return True


def safe_output_path(inp):
    """Where the converted HDR10 file must be written so deleting the P5 original
    through the Emby API can never fold-delete the keeper.

    See the emby-delete-folder-rule reference:
      * loose in a shared folder        -> beside the original (already safe)
      * episode (SxxEyy) owning a folder -> loose in the season folder (one level up)
      * movie owning its own folder      -> sibling `<name> [HDR10]/` under the category
    """
    d = os.path.dirname(inp)
    stem = os.path.splitext(os.path.basename(inp))[0]
    fname = stem + " [HDR10].mkv"

    if not _owns_folder(inp):
        return os.path.join(d, fname)                        # shared folder → safe beside

    if _EP_RE.search(stem):                                  # episode in a dedicated subfolder
        season = _season_folder(inp)
        if season is not None:
            return os.path.join(season, fname)               # loose in the season folder
        return os.path.join(os.path.dirname(d), fname)       # fallback: escape the trap, loose

    # movie in its own folder → sibling `[HDR10]` folder under the same category
    return os.path.join(os.path.dirname(d), stem + " [HDR10]", fname)
