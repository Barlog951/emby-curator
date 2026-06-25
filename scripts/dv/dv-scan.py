#!/usr/bin/env python3
"""
dv-scan.py — stateful Dolby Vision "green/pink" (Profile 5 / no-fallback) library scanner.

Keeps a SQLite cache of every video file's DV verdict, fingerprinted by (size, mtime)
so re-runs only ffprobe NEW or CHANGED files (torrent fast-resume style). Tracks which
files are good, which need rebuilding, and surfaces new arrivals automatically.

THE PROBLEM it detects: Dolby Vision **Profile 5** only. P5's base layer is ICtCp with NO
HDR10 fallback (color_transfer=unknown) -> green sky + pink faces on any non-DV player.
Profiles 7/8/4 carry a real HDR10/SDR base layer (color_transfer=smpte2084) and play fine
on non-DV players regardless of compatibility_id -> NOT flagged. (compat-id alone is the
wrong signal: P8 with compat-id 0 still has a usable HDR10 base.)

USAGE
  dv-scan.py [root ...]        incremental scan (default root /Movies) + report
  dv-scan.py --full            ignore cache, re-probe everything
  dv-scan.py --pile            list the rebuild pile (problematic, not yet handled)
  dv-scan.py --good            list good files (ok/converted)
  dv-scan.py --stats           summary counts only (no scan)
  dv-scan.py --mark STATUS PATH   set a file's status: ok|needs_rebuild|converted|ignored
  dv-scan.py --new             show files first seen in the most recent scan

DB: ~/dv-cache.db    (override with $DV_DB)
"""
import argparse
import concurrent.futures
import os
import sqlite3
import subprocess
import sys
import time

import dv_common as dvc

DB_PATH = os.environ.get("DV_DB", os.path.expanduser("~/dv-cache.db"))
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".ts", ".m2ts", ".mov", ".avi", ".wmv"}
WORKERS = int(os.environ.get("DV_WORKERS", "48"))
ENTRIES = ("stream=codec_name,width,height,pix_fmt,color_transfer:"
           "stream_side_data=dv_profile,dv_bl_signal_compatibility_id")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
  codec TEXT, width INTEGER, height INTEGER, pix_fmt TEXT,
  is_dv INTEGER, dv_profile INTEGER, dv_compat INTEGER, problematic INTEGER,
  status TEXT, first_seen TEXT, last_checked TEXT, last_probed TEXT, probe_error TEXT
);
CREATE INDEX IF NOT EXISTS i_status ON files(status);
CREATE INDEX IF NOT EXISTS i_prob   ON files(problematic);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, roots TEXT,
  total INTEGER, probed INTEGER, new INTEGER, changed INTEGER, deleted INTEGER,
  problematic INTEGER, seconds REAL
);
CREATE TABLE IF NOT EXISTS removed(
  path TEXT, size INTEGER, is_dv INTEGER, problematic INTEGER,
  last_status TEXT, first_seen TEXT, removed_at TEXT
);
"""
NOW = dvc.utc_now()


def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")   # survive a concurrent scan / --mark
    c.executescript(SCHEMA)
    try:                                       # migration: add fail_count if missing
        c.execute("ALTER TABLE files ADD COLUMN fail_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    return c


def probe(path):
    """ffprobe one file -> verdict dict. Pure: no DB."""
    try:
        s = (dvc.probe(path, ENTRIES).get("streams") or [{}])[0]
        dvp, comp = dvc.dv_profile_of(s)
        return {"codec": s.get("codec_name"), "width": s.get("width"),
                "height": s.get("height"), "pix_fmt": s.get("pix_fmt"),
                "is_dv": 1 if dvp is not None else 0, "dv_profile": dvp,
                "dv_compat": comp, "problematic": 1 if dvc.is_problematic(dvp) else 0,
                "probe_error": None}
    except subprocess.TimeoutExpired:
        return {"probe_error": "timeout", "is_dv": 0, "problematic": 0}
    except Exception as e:
        return {"probe_error": str(e)[:120], "is_dv": 0, "problematic": 0}


def derive_status(v, prev_status):
    # 'ignored'/'converted'/'failed' are sticky pipeline decisions. 'converted' must stick even
    # though the P5 original is STILL on disk (we keep both files so the emby-curator dedupe
    # can flag+remove the P5); 'failed' keeps a repeatedly-broken source out of the pile.
    if prev_status in ("ignored", "converted", "failed"):
        return prev_status
    return "needs_rebuild" if v.get("problematic") else "ok"


def scan(roots, full=False):
    c = db()
    cache = {r["path"]: r for r in c.execute(
        "SELECT path,size,mtime,status FROM files")}
    t0 = time.time()

    on_disk = {}
    for root in roots:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in VIDEO_EXT:
                    p = os.path.join(dp, fn)
                    try:
                        st = os.stat(p)
                        on_disk[p] = (st.st_size, st.st_mtime)
                    except OSError:
                        pass

    to_probe, unchanged = [], 0
    for p, (size, mtime) in on_disk.items():
        row = cache.get(p)
        if (not full and row and row["size"] == size
                and abs((row["mtime"] or 0) - mtime) < 1e-6):
            unchanged += 1
        else:
            to_probe.append((p, size, mtime, "new" if not row else "changed"))

    print(f"[scan] {len(on_disk)} on disk | {unchanged} cached | "
          f"probing {len(to_probe)} ...", file=sys.stderr)

    new_cnt = sum(1 for _, _, _, k in to_probe if k == "new")
    chg_cnt = len(to_probe) - new_cnt
    results = {}
    if to_probe:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(probe, p): (p, size, mtime, kind)
                    for p, size, mtime, kind in to_probe}
            done = 0
            for f in concurrent.futures.as_completed(futs):
                p, size, mtime, kind = futs[f]
                results[p] = (f.result(), size, mtime, kind)
                done += 1
                if done % 2000 == 0:
                    print(f"[scan] probed {done}/{len(to_probe)}", file=sys.stderr)

    new_prob = []
    for p, (v, size, mtime, kind) in results.items():
        prev = cache.get(p)
        prev_status = prev["status"] if prev else None
        status = derive_status(v, prev_status)
        fs = (c.execute("SELECT first_seen FROM files WHERE path=?", (p,)).fetchone()
              if prev else None)
        first_seen = fs["first_seen"] if fs and fs["first_seen"] else NOW
        c.execute("""INSERT INTO files(path,size,mtime,codec,width,height,pix_fmt,
                     is_dv,dv_profile,dv_compat,problematic,status,first_seen,
                     last_checked,last_probed,probe_error)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,
                     codec=excluded.codec,width=excluded.width,height=excluded.height,
                     pix_fmt=excluded.pix_fmt,is_dv=excluded.is_dv,dv_profile=excluded.dv_profile,
                     dv_compat=excluded.dv_compat,problematic=excluded.problematic,
                     status=excluded.status,last_checked=excluded.last_checked,
                     last_probed=excluded.last_probed,probe_error=excluded.probe_error""",
                  (p, size, mtime, v.get("codec"), v.get("width"), v.get("height"),
                   v.get("pix_fmt"), v.get("is_dv"), v.get("dv_profile"), v.get("dv_compat"),
                   v.get("problematic"), status, first_seen, NOW, NOW, v.get("probe_error")))
        if kind == "new" and v.get("problematic"):
            new_prob.append((p, v))

    # touch last_checked for unchanged
    c.executemany("UPDATE files SET last_checked=? WHERE path=?",
                  [(NOW, p) for p in on_disk if p not in results])

    # deletions -> archive to removed[] (snapshot history) then drop from live table
    deleted = [p for p in cache if p not in on_disk]
    for p in deleted:
        r = c.execute("SELECT size,is_dv,problematic,status,first_seen FROM files "
                      "WHERE path=?", (p,)).fetchone()
        if r:
            c.execute("INSERT INTO removed(path,size,is_dv,problematic,last_status,"
                      "first_seen,removed_at) VALUES(?,?,?,?,?,?,?)",
                      (p, r["size"], r["is_dv"], r["problematic"], r["status"],
                       r["first_seen"], NOW))
    c.executemany("DELETE FROM files WHERE path=?", [(p,) for p in deleted])
    del_prob = sum(1 for p in deleted
                   if (cache.get(p) and cache[p]["status"] == "needs_rebuild"))

    pile = c.execute("SELECT COUNT(*) FROM files WHERE problematic=1 AND "
                     "status='needs_rebuild'").fetchone()[0]
    c.execute("""INSERT INTO runs(ts,roots,total,probed,new,changed,deleted,problematic,seconds)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
              (NOW, ",".join(roots), len(on_disk), len(to_probe), new_cnt, chg_cnt,
               len(deleted), pile, round(time.time() - t0, 1)))
    c.commit()

    # report
    print(f"\n=== DV scan {NOW} ===")
    print(f"files: {len(on_disk)} | probed: {len(to_probe)} (new={new_cnt} "
          f"changed={chg_cnt}) | deleted={len(deleted)} | {time.time()-t0:.1f}s")
    if new_prob:
        print(f"\n!! NEW problematic files (decide: convert or re-download) [{len(new_prob)}]:")
        for p, v in sorted(new_prob):
            print(f"   P{v['dv_profile']} {v['width']}x{v['height']}  ::  {p}")
    fixed = [p for p, (v, *_x) in results.items()
             if (cache.get(p) and cache[p]["status"] == "needs_rebuild"
                 and not v.get("problematic"))]
    if fixed:
        print(f"\n** resolved (was problematic, now clean) [{len(fixed)}]:")
        for p in sorted(fixed):
            print(f"   {p}")
    if deleted:
        tag = f", {del_prob} were in the rebuild pile" if del_prob else ""
        print(f"\n-- removed since last scan [{len(deleted)}{tag}] (archived in removed[]):")
        for p in sorted(deleted):
            print(f"   {p}")
    stats(c)


def stats(c=None):
    c = c or db()
    rows = dict(c.execute("SELECT status,COUNT(*) FROM files GROUP BY status").fetchall())
    dv = c.execute("SELECT COUNT(*) FROM files WHERE is_dv=1").fetchone()[0]
    tot = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    print(f"\nlibrary: {tot} files | DV: {dv} | "
          + " ".join(f"{k}={v}" for k, v in sorted(rows.items())))
    print(f"REBUILD PILE: {rows.get('needs_rebuild',0)}  (dv-scan.py --pile to list)")


def _pile_rows(c):
    return c.execute("SELECT path,dv_profile,width,height FROM files WHERE problematic=1 "
                     "AND status='needs_rebuild' ORDER BY path").fetchall()


def list_pile(c=None):
    c = c or db()
    rows = _pile_rows(c)
    print(f"=== REBUILD PILE ({len(rows)}) ===")
    for r in rows:
        print(f"  P{r['dv_profile']} {r['width']}x{r['height']}  ::  {r['path']}")


def list_pile_paths(c=None):
    """Bare rebuild-pile paths, one per line — machine-readable for the worker."""
    c = c or db()
    for r in _pile_rows(c):
        print(r["path"])


def list_good(c=None):
    c = c or db()
    n = c.execute("SELECT COUNT(*) FROM files WHERE status IN('ok','converted')").fetchone()[0]
    print(f"good files (ok/converted): {n}")


def list_new(c=None):
    c = c or db()
    last = c.execute("SELECT ts FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not last:
        print("no runs yet"); return
    rows = c.execute("SELECT path,problematic,dv_profile FROM files WHERE first_seen=? "
                     "ORDER BY path", (last["ts"],)).fetchall()
    print(f"=== first seen in last run ({last['ts']}): {len(rows)} ===")
    for r in rows:
        flag = "PROBLEMATIC" if r["problematic"] else ("DV-ok" if r["dv_profile"] is not None else "")
        print(f"  {flag:11} {r['path']}")


def list_removed(c=None):
    c = c or db()
    rows = c.execute("SELECT path,problematic,last_status,removed_at FROM removed "
                     "ORDER BY removed_at DESC, path").fetchall()
    print(f"=== removed history ({len(rows)}) ===")
    for r in rows:
        flag = "WAS-PROBLEMATIC" if r["problematic"] else ""
        print(f"  {r['removed_at']}  {flag:15} {r['path']}")


def list_runs(c=None):
    c = c or db()
    print("=== scan runs (most recent first) ===")
    for r in c.execute("SELECT ts,total,probed,new,deleted,problematic,seconds "
                        "FROM runs ORDER BY id DESC LIMIT 25"):
        print(f"  {r['ts']}  files={r['total']} probed={r['probed']} new={r['new']} "
              f"removed={r['deleted']} pile={r['problematic']} {r['seconds']}s")


def fail(path, threshold=3):
    """Record a conversion failure; after `threshold` fails the file is marked 'failed'
    so the pile skips it (one corrupt/hanging source can't block the queue forever)."""
    c = db()
    c.execute("UPDATE files SET fail_count = COALESCE(fail_count,0)+1 WHERE path=?", (path,))
    row = c.execute("SELECT fail_count FROM files WHERE path=?", (path,)).fetchone()
    fc = row[0] if row else 0
    if fc >= threshold:
        c.execute("UPDATE files SET status='failed' WHERE path=?", (path,))
        print(f"failed {fc}x -> marked 'failed' (skipped): {path}")
    else:
        print(f"recorded failure {fc}/{threshold}: {path}")
    c.commit()


def mark(status, path):
    if status not in ("ok", "needs_rebuild", "converted", "ignored"):
        print("status must be: ok|needs_rebuild|converted|ignored"); sys.exit(2)
    c = db()
    n = c.execute("UPDATE files SET status=? WHERE path=?", (status, path)).rowcount
    c.commit()
    print(f"marked {n} row(s) -> {status}")


def main():
    p = argparse.ArgumentParser(
        description="Stateful Dolby Vision Profile-5 (green/pink) library scanner.")
    p.add_argument("roots", nargs="*", help="roots to scan (default: /Movies)")
    p.add_argument("--full", action="store_true", help="ignore cache, re-probe everything")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--pile", action="store_true", help="list the rebuild pile")
    g.add_argument("--pile-paths", action="store_true",
                   help="rebuild-pile paths only, one per line (for scripts)")
    g.add_argument("--good", action="store_true", help="count good files (ok/converted)")
    g.add_argument("--stats", action="store_true", help="summary counts only (no scan)")
    g.add_argument("--new", action="store_true", help="files first seen in the most recent scan")
    g.add_argument("--removed", action="store_true", help="removed-file history")
    g.add_argument("--runs", action="store_true", help="scan-run history")
    g.add_argument("--mark", nargs="+", metavar="ARG",
                   help="set a file's status: --mark STATUS PATH")
    g.add_argument("--fail", nargs="+", metavar="PATH",
                   help="record a conversion failure for PATH")
    args = p.parse_args()

    if args.pile: list_pile()
    elif args.pile_paths: list_pile_paths()
    elif args.good: list_good()
    elif args.stats: stats()
    elif args.new: list_new()
    elif args.removed: list_removed()
    elif args.runs: list_runs()
    elif args.fail: fail(" ".join(args.fail))
    elif args.mark:
        if len(args.mark) < 2: print("usage: --mark STATUS PATH"); sys.exit(2)
        mark(args.mark[0], " ".join(args.mark[1:]))
    else:
        scan(args.roots or ["/Movies"], full=args.full)


if __name__ == "__main__":
    main()
