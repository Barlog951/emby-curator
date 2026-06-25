# dv — Dolby Vision Profile-5 → HDR10 library converter

Detects and fixes the "green sky / pink faces" problem: Dolby Vision **Profile 5** files
(single-layer ICtCp, no HDR10 fallback) render with a green+magenta tint on any non-DV
playback path. Profiles 7/8 carry a real HDR10 base layer and play fine — they are **not**
touched. The fix re-encodes P5 → clean **HDR10** (libplacebo applies the DV RPU, NVENC
encodes 10-bit), keeping both files so the dedupe pass can remove the P5 original.

## Components
| File | Role |
|---|---|
| `dv_common.py` | Shared helpers — single source of truth for ffprobe access and the `dv_profile == 5` criterion (imported by `dv-scan`/`dv-convert` so they can't drift apart). |
| `dv-scan.py` | SQLite cache + queue. Fingerprints every video by size+mtime → re-runs only probe new/changed files. Flags `dv_profile == 5`. CLI: `--pile --pile-paths --good --new --removed --runs --stats --mark <status> <path> --fail <path>`. DB: `~/dv-cache.db`. |
| `dv-convert.py` | One P5 → HDR10. `--mark` (keep both files + mark original `converted`), `--supervise` (GPU-priority), `--replace` (swap + back up original). Quality via `DV_CQ` (18) / `DV_PRESET` (p7) — VMAF-verified transparent. Appends `~/dv-convert-history.jsonl`. |
| `dv-load.py` | GPU-priority gate. `nvidia-smi pmon` per-process pressure; `--supervise <pid>` SIGSTOP/SIGCONT-pauses conversions whenever Emby/Ollama actually need the GPU. |
| `dv-worker.sh` | Continuous drainer: scan → ask `dv-load` for slots → convert up to 2 in parallel → repeat until pile empty. Per-file timeout + skip-after-3-fails. |
| `systemd/dv-worker.{service,timer}` | Hourly timer → worker. Edit `User=` and the `/home/YOUR_USER` paths in the unit for your install (`DV_DB`/`DV_ROOT` env). |

## Pipeline
NVDEC decode → libplacebo `apply_dolbyvision` → HDR10 → NVENC HEVC 10-bit, on a **shared
CUDA+Vulkan device** (avoids a RAM blow-up), encoded to raw HEVC then muxed with the
original audio/subs via `mkvmerge` (no orphaned DV metadata). 2-wide = ~full NVENC (the
card has two NVENC engines).

## Deploy
Edit `User=`/`Group=` and the `/home/YOUR_USER` paths in `systemd/dv-worker.service`
first, then:
```
cp dv*.py dv-worker.sh /home/YOUR_USER/emby-dedupe/scripts/dv/   # dv*.py also ships dv_common.py
sudo cp systemd/dv-worker.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dv-worker.timer
```

## Operate
```
dv-scan.py /Movies        # incremental scan + report
dv-scan.py --pile         # the rebuild queue
dv-scan.py --runs         # scan history
journalctl -u dv-worker.service -f
tail -f ~/dv-convert-history.jsonl
```
