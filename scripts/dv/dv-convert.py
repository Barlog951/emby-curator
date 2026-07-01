#!/usr/bin/env python3
"""
dv-convert.py — convert a Dolby Vision Profile-5 (no-fallback) file to clean HDR10.

Fixes the green/pink problem permanently. Proven pipeline:
  NVDEC decode -> libplacebo apply_dolbyvision (Vulkan) -> HDR10 PQ/BT.2020
  -> NVENC HEVC 10-bit, encoded straight to a RAW HEVC stream (no DV container
  record ever written) -> mkvmerge muxes the ORIGINAL audio + subtitles back.
Memory-safe (~1.8GB RSS via shared CUDA+Vulkan device), GPU-accelerated.

Safe by default: writes a NEW "<name> [HDR10].mkv" and does NOT delete the source.
Placement is chosen so the dedupe can later remove the P5 original via the Emby API
WITHOUT fold-deleting the keeper (see dv_common.safe_output_path): episodes go loose
in the season folder, movies go in a sibling "<name> [HDR10]/" folder, loose files
stay beside. --replace swaps it in only after the output verifies.

Usage:
  dv-convert.py INPUT [--outdir DIR] [--replace] [--mark] [--clip SECONDS] [-n|--dry-run]

  --replace   after verifying output, atomically replace the original
  --mark      mark the source 'converted' in dv-cache.db (via dv-scan.py)
  --clip N    encode only first N seconds, video-only (fast pipeline self-test)
  -n          print the commands, do nothing
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

import dv_common as dvc

# children to tear down if we're killed (e.g. the worker's per-file `timeout`): the ffmpeg
# encode + its GPU-priority supervisor. Without this a timeout would orphan the ffmpeg.
_PROCS = []


def _cleanup(signum, _frame):
    for p in _PROCS:
        try:
            p.kill()
        except Exception:
            pass
    sys.exit(143)


_HERE = os.path.dirname(os.path.abspath(__file__))
DV_SCAN = os.path.join(_HERE, "dv-scan.py")
DV_LOAD = os.path.join(_HERE, "dv-load.py")
BACKUP_DIR = os.environ.get("DV_BACKUP", "/Movies/.dv-originals")
HISTORY = os.environ.get("DV_HISTORY", os.path.expanduser("~/dv-convert-history.jsonl"))
# Encode quality (tunable). cq 18 + p7 = near-transparent (~source bitrate). Lower cq = higher
# quality/bigger; p7 = slowest/best NVENC preset.
CQ = os.environ.get("DV_CQ", "18")
PRESET = os.environ.get("DV_PRESET", "p7")


def hist(rec):
    """Append one JSON record per conversion outcome — durable 'look later' ledger.
    Never lets a logging error break a conversion."""
    try:
        rec = {"ts": dvc.utc_now(), **rec}
        with open(HISTORY, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def dv_of(path):
    return dvc.dv_profile_of(dvc.probe(path, dvc.DV_ENTRIES))


def run(cmd, dry):
    dvc.print_cmd(cmd)
    if dry:
        return 0
    return subprocess.run(cmd).returncode


def main():
    p = argparse.ArgumentParser(
        description="Convert a Dolby Vision Profile-5 file to clean HDR10 (keeps the original).")
    p.add_argument("input", help="the DV Profile-5 source file")
    p.add_argument("--outdir", help="force output directory (default: safe library "
                                     "placement via dv_common.safe_output_path)")
    p.add_argument("--replace", action="store_true",
                   help="after verifying, atomically replace the original (backs it up)")
    p.add_argument("--mark", action="store_true",
                   help="mark the source 'converted' in dv-cache.db")
    p.add_argument("--supervise", action="store_true",
                   help="run a GPU-priority supervisor that yields to Emby/Ollama")
    p.add_argument("--clip", help="encode only the first N seconds (pipeline self-test)")
    p.add_argument("-n", "--dry-run", dest="dry", action="store_true",
                   help="print the commands, do nothing")
    args = p.parse_args()
    inp, outdir, clip = args.input, args.outdir, args.clip
    replace, mark, supervise, dry = args.replace, args.mark, args.supervise, args.dry

    if not os.path.exists(inp):
        print(f"ERROR: no such file: {inp}"); sys.exit(2)

    # 1) SAFETY: only convert genuine Profile-5 files (the only green/pink case).
    #    P7/P8/P4 carry a real HDR10/SDR base and play fine — never touch them.
    dvp, comp = dv_of(inp)
    if not dvc.is_problematic(dvp):
        print(f"SKIP (not Profile 5 — has a usable base, plays fine): "
              f"dv_profile={dvp} compat={comp}  {inp}")
        sys.exit(1)

    # 2) frame rate (for the raw-stream mux) + CFR sanity
    s2 = (dvc.probe(inp, "stream=r_frame_rate,avg_frame_rate").get("streams") or [{}])[0]
    rfr = s2.get("r_frame_rate") or "24000/1001"
    afr = s2.get("avg_frame_rate")
    if afr and rfr != afr:
        print(f"WARN: r_frame_rate {rfr} != avg {afr} (possible VFR — verify A/V sync)")

    base = os.path.splitext(os.path.basename(inp))[0]
    # SAFE placement: keep the converted HDR10 OUT of any folder Emby would
    # fold-delete when the dedupe later removes the P5 original via the API
    # (see dv_common.safe_output_path / the emby-delete-folder-rule). --outdir
    # still forces a location for pipeline self-tests.
    if outdir:
        out = os.path.join(outdir, base + " [HDR10].mkv")
    elif replace:
        out = os.path.join(os.path.dirname(inp), base + " [HDR10].mkv")  # swapped in place anyway
    else:
        out = dvc.safe_output_path(inp)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp_hevc = f"/tmp/dvconv-{os.getpid()}.hevc"

    print(f"[convert] {inp}\n          P{dvp} compat{comp}  fps={rfr}  -> {out}", file=sys.stderr)

    # 3) STAGE 1 — DV->HDR10, raw HEVC (no DV container record), memory-safe
    vf = ("libplacebo=apply_dolbyvision=true:colorspace=bt2020nc:color_primaries=bt2020:"
          "color_trc=smpte2084:range=tv:format=p010le,hwupload_cuda")
    cmd1 = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
            "-init_hw_device", "cuda=cu:0", "-init_hw_device", "vulkan=vk@cu"]
    if clip:
        cmd1 += ["-t", str(clip)]
    cmd1 += ["-i", inp, "-map", "0:v:0", "-vf", vf,
             "-c:v", "hevc_nvenc", "-preset", PRESET, "-cq", CQ, "-profile:v", "main10",
             "-colorspace", "bt2020nc", "-color_primaries", "bt2020",
             "-color_trc", "smpte2084", "-color_range", "tv",
             "-an", "-sn", "-f", "hevc", tmp_hevc]
    t0 = time.time()
    dvc.print_cmd(cmd1)
    if not dry:
        signal.signal(signal.SIGTERM, _cleanup)    # clean teardown if worker's timeout kills us
        proc = subprocess.Popen(cmd1)              # proc.pid == the ffmpeg pid
        _PROCS.append(proc)
        sup = None
        if supervise:
            sup = subprocess.Popen(["python3", DV_LOAD, "--supervise", str(proc.pid)])
            _PROCS.append(sup)
        rc = proc.wait()
        if sup:
            sup.terminate()
            try: sup.wait(timeout=5)
            except Exception: sup.kill()
        if rc != 0:
            if not clip: hist({"file": inp, "status": "fail", "stage": "encode"})
            print("ERROR: stage-1 transcode failed"); sys.exit(3)

    # 4) STAGE 2 — mux raw video + original audio/subs (clip mode: video only)
    cmd2 = ["mkvmerge", "-q", "-o", out, "--default-duration", f"0:{rfr}fps", tmp_hevc]
    if not clip:
        cmd2 += ["--no-video", inp]
    if run(cmd2, dry) != 0:
        if not clip: hist({"file": inp, "status": "fail", "stage": "mux"})
        print("ERROR: stage-2 mux failed"); sys.exit(3)
    if not dry:
        try: os.remove(tmp_hevc)
        except OSError: pass

    if dry:
        print("[dry-run] done"); return

    # 5) VERIFY output: DV gone, HDR10, duration sane
    odvp, _ = dv_of(out)
    vinfo = (dvc.probe(out, "stream=color_transfer").get("streams") or [{}])[0]
    dur = float((dvc.probe(out, "format=duration", streams="v:0").get("format") or {}).get("duration") or 0)
    src_dur = float((dvc.probe(inp, "format=duration", streams="v:0").get("format") or {}).get("duration") or 0)
    ok = (odvp is None and vinfo.get("color_transfer") == "smpte2084"
          and (clip or abs(dur - src_dur) < 2.0))
    elapsed = time.time() - t0
    print(f"[verify] dv_stripped={odvp is None} transfer={vinfo.get('color_transfer')} "
          f"dur={dur:.0f}s (src {src_dur:.0f}s) {'OK' if ok else 'FAIL'}  ({elapsed:.0f}s)",
          file=sys.stderr)
    if not ok:
        if not clip:
            hist({"file": inp, "status": "verify_fail", "dur": round(dur),
                  "src_dur": round(src_dur), "transfer": vinfo.get("color_transfer")})
        print(f"ERROR: output verification failed, leaving both files for inspection: {out}")
        sys.exit(4)

    # 6) optional replace + DB mark
    if replace and not clip:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ig = os.path.join(BACKUP_DIR, ".ignore")
        if not os.path.exists(ig):
            open(ig, "a").close()        # Emby skips a folder containing .ignore
        bak = os.path.join(BACKUP_DIR, os.path.basename(inp))
        if os.path.exists(bak):
            bak += f".{int(t0)}"
        os.replace(inp, bak)             # move broken P5 original to backup (undoable)
        os.replace(out, inp)             # converted HDR10 takes the original's path/name
        print(f"[replace] {inp}\n          original backed up -> {bak}")
        target = inp
    else:
        target = out
    if mark:
        subprocess.run(["python3", DV_SCAN, "--mark", "converted", inp])
        print(f"[db] marked converted: {inp}")
    try:
        out_bytes = os.path.getsize(target)
    except OSError:
        out_bytes = None
    if not clip:
        hist({"file": inp, "status": "ok", "out": target, "src_sec": round(src_dur),
              "out_bytes": out_bytes, "encode_sec": round(elapsed), "dv_profile": dvp})
    print(f"DONE -> {target}")


if __name__ == "__main__":
    main()
