"""Shared helpers for the dv/ Dolby-Vision P5->HDR10 tools (dv-scan, dv-convert).

Single source of truth for ffprobe access and the Profile-5 ("green/pink")
criterion, so the scanner and the converter can never drift apart.

THE criterion: Dolby Vision **Profile 5** only (single-layer ICtCp, no HDR10
fallback -> green sky + pink faces on any non-DV player). Profiles 4/7/8 carry a
real HDR10/SDR base and play fine -> never flagged. (compatibility_id alone is the
wrong signal: P8 with compat-id 0 still has a usable HDR10 base.)
"""
import json
import subprocess
import sys
import time

# ffprobe show_entries string for the DV side-data we key on.
DV_ENTRIES = "stream_side_data=dv_profile,dv_bl_signal_compatibility_id"

# The only problematic profile (see module docstring).
PROBLEM_PROFILE = 5


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
