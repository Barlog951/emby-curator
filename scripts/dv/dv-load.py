#!/usr/bin/env python3
"""
dv-load.py — GPU-priority primitive for DV conversions (behaves like Linux `nice`).

Conversions are LOW priority: they run on spare GPU, and get SIGSTOP-paused the moment
Emby playback or Ollama inference actually demands the GPU — not merely when those
processes exist. "Pressure" is measured PER PROCESS via `nvidia-smi pmon`, so 3 idle
Emby ffmpegs count as 0; one real transcode at enc=50% counts as 50.

Modes:
  dv-load.py                 print current others-pressure + recommended start slots
  dv-load.py --supervise PIDS   "GPU nice" loop: SIGSTOP/SIGCONT the given convert PIDs
                                (comma-separated) to yield whenever others need the GPU

Tunables (env): DV_MAXSLOTS=2  DV_HIGH=20  DV_LOW=8  DV_SAMPLE=1  DV_HYST=3
  HIGH = others-pressure% at/above which we yield (pause / start 0)
  LOW  = others-pressure% below which we may use full headroom / resume
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

MAXSLOTS = int(os.environ.get("DV_MAXSLOTS", "2"))
HIGH = int(os.environ.get("DV_HIGH", "20"))
LOW = int(os.environ.get("DV_LOW", "8"))
SAMPLE = int(os.environ.get("DV_SAMPLE", "1"))
HYST = int(os.environ.get("DV_HYST", "3"))


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def is_ours(pid):
    c = cmdline(pid)
    return ("dvconv" in c or "vk@cu" in c or "libplacebo" in c)


def pmon_procs():
    """[(pid, sm, enc, dec, name)] from one pmon sample. '-' -> 0."""
    try:
        out = subprocess.run(["nvidia-smi", "pmon", "-c", str(SAMPLE)],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    procs = {}
    for ln in out.splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.split()
        if len(f) < 7:
            continue
        try:
            pid = int(f[1])
        except ValueError:
            continue
        def num(x): return 0 if x in ("-", "") else int(x)
        sm, enc, dec = num(f[3]), num(f[5]), num(f[6])
        name = f[-1]
        # keep the peak sample per pid across the -c window
        prev = procs.get(pid)
        val = (pid, sm, enc, dec, name)
        if not prev or max(sm, enc, dec) > max(prev[1], prev[2], prev[3]):
            procs[pid] = val
    return list(procs.values())


def others_pressure(our_pids=frozenset()):
    """Peak GPU unit util (sm/enc/dec) demanded by anything that ISN'T our convert."""
    detail = []
    pressure = 0
    for pid, sm, enc, dec, name in pmon_procs():
        if pid in our_pids or is_ours(pid):
            continue
        peak = max(sm, enc, dec)
        if peak > 0:
            detail.append({"pid": pid, "name": name, "sm": sm, "enc": enc, "dec": dec})
        pressure = max(pressure, peak)
    return pressure, detail


def recommend_slots(running=0, our_pids=frozenset()):
    p, detail = others_pressure(our_pids)
    if p >= HIGH:
        allow = 0
    elif p < LOW:
        allow = MAXSLOTS
    else:
        allow = 1
    return max(0, allow - running), p, detail


def supervise(pids):
    """Linux-nice for the GPU: pause convert pids when others demand GPU, resume when free."""
    pidset = frozenset(pids)
    paused = False
    above = below = 0
    print(f"[supervise] managing {sorted(pids)}  HIGH={HIGH} LOW={LOW}", file=sys.stderr)
    while True:
        alive = [p for p in pids if os.path.exists(f"/proc/{p}")]
        if not alive:
            print("[supervise] all convert pids exited", file=sys.stderr)
            return
        p, detail = others_pressure(pidset)
        if p >= HIGH:
            above += 1; below = 0
            if not paused and above >= 1:
                for pid in alive:
                    try: os.kill(pid, signal.SIGSTOP)
                    except ProcessLookupError: pass
                paused = True
                print(f"[supervise] PAUSE (others {p}% :: {detail})", file=sys.stderr)
        elif p < LOW:
            below += 1; above = 0
            if paused and below >= HYST:
                for pid in alive:
                    try: os.kill(pid, signal.SIGCONT)
                    except ProcessLookupError: pass
                paused = False
                print(f"[supervise] RESUME (others {p}%)", file=sys.stderr)
        else:
            above = below = 0
        time.sleep(2)


def main():
    p = argparse.ArgumentParser(description="GPU-priority gate for DV conversions.")
    p.add_argument("--supervise", metavar="PIDS",
                   help="SIGSTOP/SIGCONT-pause these comma-separated convert PIDs to yield GPU")
    p.add_argument("--running", type=int, default=0,
                   help="conversions already running (affects recommended start slots)")
    args = p.parse_args()
    if args.supervise:
        supervise([int(x) for x in args.supervise.split(",") if x.strip()])
    else:
        slots, pr, detail = recommend_slots(args.running)
        print(f"[dv-load] others_pressure={pr}%  start_now={slots} (max {MAXSLOTS}, "
              f"running {args.running})  active_others={json.dumps(detail)}", file=sys.stderr)
        print(slots)


if __name__ == "__main__":
    main()
