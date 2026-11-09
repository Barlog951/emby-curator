"""Frame posters for Emby movies: pick the best of several frames, smart-crop to 2:3, upload.

Usage:  frame_poster.py IDS_FILE [--apply] [--sheet DIR] [--clip] [--backdrop]
  --backdrop: upload the chosen frame uncropped (16:9, 1920 wide) as the item's Backdrop instead of a poster
  --clip: also score relevance of each frame to title+overview with multilingual CLIP (sentence-transformers)
  default: score only, print the pick;  --sheet DIR writes a contact sheet per movie;
  --apply uploads the chosen poster as the item's Primary image.
Scoring per candidate (12 timestamps between 8 % and 88 % of the runtime):
  mean luma outside 45..205 -> rejected (fade/black/blown-out); contrast (luma std) < 18 -> rejected;
  score = detail (edge energy, weight 1.0) + contrast (0.6) - distance from mid brightness (0.4).
Crop: the 2:3 window is placed where a smoothed column edge-energy profile peaks, not blindly centred.
"""
import base64
import io
import json
import os
import subprocess
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageStat

KEY = os.environ["DEDUPE_EMBY_API_KEY"]; BASE = "http://localhost:8096/emby"
FFMPEG, FFPROBE = "/usr/bin/ffmpeg", "/usr/bin/ffprobe"
FRACS = [0.08 + i * 0.8 / 11 for i in range(12)]
PROBE_TIMEOUT, GRAB_TIMEOUT = 60, 120  # seconds; one unreadable file must not stall the sweep
LUMA_MIN, LUMA_MAX, CONTRAST_MIN = 45, 205, 18


def api(path, data=None, headers=None):
    h = {"X-Emby-Token": KEY}; h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h), timeout=120)


def duration(path):
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=PROBE_TIMEOUT).stdout.strip()
    except subprocess.TimeoutExpired:
        return 0.0
    try:
        return float(out or 0)
    except ValueError:
        return 0.0


def grab(path, ts, height):
    try:
        r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{ts:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", f"scale=-2:{height}", "-q:v", "2", "-f", "image2", "pipe:1"],
                           capture_output=True, timeout=GRAB_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    return Image.open(io.BytesIO(r.stdout)).convert("RGB") if r.returncode == 0 and r.stdout else None


_CASCADES = None


def faces(img):
    """Largest face as a fraction of frame height; 0 when none.

    A face counts only when two different Haar cascades (default + alt2) both find it
    at roughly the same place — a single cascade fires on rooftops, foliage and rock.
    """
    global _CASCADES
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0.0, None
    if _CASCADES is None:
        _CASCADES = [cv2.CascadeClassifier(cv2.data.haarcascades + n)
                     for n in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml")]
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY); min_side = int(g.shape[0] * 0.08)
    a, b = (c.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side)) for c in _CASCADES)
    best, centre = 0.0, None
    for (x, y, w, h) in a:
        for (x2, y2, w2, h2) in b:
            if abs(x - x2) < w / 2 and abs(y - y2) < h / 2 and 0.6 < w2 / w < 1.6 and h / g.shape[0] > best:
                best, centre = h / g.shape[0], (x + w / 2) / g.shape[1]
    return best, centre


def score(img):
    """Higher is better. None = rejected (fade, black, blown-out or flat frame).

    detail: edge energy, best around 25-40 — foliage/clutter (>45) is penalised, not rewarded
    faces:  a frontal face >= 8 % of the height is the strongest poster cue (capped bonus)
    colour: saturation rewards vivid frames over grey/desaturated ones
    """
    g = img.convert("L").resize((320, int(320 * img.height / img.width)))
    st = ImageStat.Stat(g); mean, std = st.mean[0], st.stddev[0]
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    sat = ImageStat.Stat(img.convert("HSV").getchannel("S")).mean[0]
    face, _ = faces(img)
    stats = (mean, std, edges, sat, face)
    if not (LUMA_MIN <= mean <= LUMA_MAX) or std < CONTRAST_MIN:
        return None, stats
    detail = 1.0 - abs(edges - 32) / 32            # peaks at 32, negative when very cluttered
    s = (1.2 * max(detail, -1.0) + 0.6 * std / 50 + 0.5 * sat / 128
         - 0.4 * abs(mean - 115) / 115 + min(face, 0.35) * 8)
    return s, stats


def smart_crop(img, face_cx=None):
    """Crop to 2:3 centred on the detected face; otherwise where edge energy peaks."""
    w, h = img.size; tw = int(h * 2 / 3)
    if tw >= w:
        th = int(w * 3 / 2); return img.crop((0, (h - th) // 2, w, (h - th) // 2 + th))
    if face_cx is not None:
        left = max(0, min(int(face_cx * w - tw / 2), w - tw))
        return img.crop((left, 0, left + tw, h))
    g = img.convert("L").resize((320, int(320 * h / w))).filter(ImageFilter.FIND_EDGES)
    px = g.load(); cols = [sum(px[x, y] for y in range(g.height)) for x in range(g.width)]
    win = max(1, int(g.width * tw / w)); best_x, best_e = 0, -1
    for x in range(0, g.width - win + 1):
        e = sum(cols[x:x + win])
        if e > best_e: best_x, best_e = x, e
    left = int(best_x * w / g.width); left = max(0, min(left, w - tw))
    return img.crop((left, 0, left + tw, h))


_CLIP = None


def clip_scores(imgs, text):
    """Cosine similarity of each frame to the (Slovak/Czech/English) description, via multilingual CLIP."""
    global _CLIP
    if _CLIP is None:
        from sentence_transformers import SentenceTransformer
        _CLIP = (SentenceTransformer("clip-ViT-B-32"), SentenceTransformer("clip-ViT-B-32-multilingual-v1"))
    img_model, txt_model = _CLIP
    import numpy as np
    ie = img_model.encode(imgs, convert_to_numpy=True, normalize_embeddings=True)
    te = txt_model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
    return [float(np.dot(e, te)) for e in ie]


def choose(path, text=None):
    """Return (pick, candidates). With `text`, blend the heuristic with CLIP relevance to the description."""
    dur = duration(path)
    cands = []
    for frac in FRACS:
        img = grab(path, dur * frac, 360)
        if img is None: continue
        s, stats = score(img); cands.append([frac, img, s, stats, None])
    if text and cands:
        sims = clip_scores([c[1] for c in cands], text)
        lo, hi = min(sims), max(sims)
        for c, sim in zip(cands, sims):
            c[4] = sim
            if c[2] is not None and hi > lo:
                c[2] = c[2] + 3.0 * (sim - lo) / (hi - lo)   # relevance worth up to +3, same order as a face
    ok = [c for c in cands if c[2] is not None]
    pick = max(ok, key=lambda c: c[2]) if ok else (max(cands, key=lambda c: c[3][2]) if cands else None)
    return pick, cands


def sheet(name, cands, pick, out):
    tw, th = 240, 135; cols = 6; rows = (len(cands) + cols - 1) // cols
    im = Image.new("RGB", (cols * tw, rows * (th + 18) + 22), "black"); d = ImageDraw.Draw(im)
    d.text((4, 4), name[:80], fill="white")
    for i, (frac, img, s, (m, sd, e, sat, face), sim) in enumerate(cands):
        x, y = (i % cols) * tw, 22 + (i // cols) * (th + 18)
        im.paste(img.resize((tw, th)), (x, y))
        label = f"{int(frac*100)}% F{face:.2f} " + (f"clip{sim:.2f} " if sim is not None else "") + (f"S{s:.2f}" if s is not None else "REJ")
        d.text((x + 3, y + th + 2), label, fill="lime" if pick and frac == pick[0] else "white")
        if pick and frac == pick[0]: d.rectangle((x, y, x + tw - 1, y + th - 1), outline="lime", width=3)
    im.save(out)


def main():
    """Process every id; content misses (no frame, no episode) are reported, errors fail the run."""
    ids = [line.strip() for line in open(sys.argv[1]) if line.strip()]
    ok = fail = errors = 0
    for item_id in ids:
        try:
            result = process(item_id)
        except Exception as exc:  # one broken item must not end the sweep
            print("  ERROR", item_id, exc)
            result = "error"
        ok += result == "ok"
        fail += result == "miss"
        errors += result == "error"
    print(f"done: ok {ok} | failed {fail} | errors {errors} | mode {'APPLY' if '--apply' in sys.argv else 'score-only'}")
    sys.exit(1 if errors else 0)


def process(item_id):
    """Pick and (with --apply) upload one item's frame. Returns "ok", "miss" or "error"."""
    apply = "--apply" in sys.argv
    sheet_dir = sys.argv[sys.argv.index("--sheet") + 1] if "--sheet" in sys.argv else None
    item = json.load(api(f"/Items?Ids={item_id}&Fields=Path,Overview,OriginalTitle,Genres"))["Items"][0]
    if item.get("Type") == "Series":   # no file of its own: use the first episode's video
        eps = json.load(api(f"/Items?ParentId={item_id}&Recursive=true&IncludeItemTypes=Episode"
                            f"&SortBy=ParentIndexNumber,IndexNumber&Fields=Path&Limit=1"))["Items"]
        if not eps or not eps[0].get("Path"):
            print("NO EPISODE", item_id, item["Name"])
            return "miss"
        item["Path"] = eps[0]["Path"]
    text = None
    if "--clip" in sys.argv:
        text = ". ".join(t for t in (item.get("Name"), item.get("OriginalTitle"), ", ".join(item.get("Genres") or []),
                                     (item.get("Overview") or "")[:400]) if t)
    pick, cands = choose(item["Path"], text)
    if pick is None:
        print("NO FRAME", item_id, item["Name"])
        return "miss"
    frac, _, s, (m, sd, e, sat, face), _sim = pick
    print(f"{item_id} {item['Name'][:45]:45s} pick {int(frac*100)}% score={s if s is not None else 'fallback'} L{m:.0f} C{sd:.0f} E{e:.0f} S{sat:.0f} face={face:.2f}")
    if sheet_dir:
        os.makedirs(sheet_dir, exist_ok=True)
        sheet(item["Name"], cands, pick, os.path.join(sheet_dir, f"{item_id}.jpg"))
    if not apply:
        return "ok"
    full = grab(item["Path"], duration(item["Path"]) * frac, 1080)
    if full is None:
        print("  full-res grab failed")
        return "miss"
    backdrop = "--backdrop" in sys.argv
    if backdrop:
        out = full.resize((1920, int(1920 * full.height / full.width))) if full.width != 1920 else full
    else:
        _, face_cx = faces(full.resize((640, int(640 * full.height / full.width))))
        out = smart_crop(full, face_cx)
    kind = "Backdrop" if backdrop else "Primary"
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    try:
        api(f"/Items/{item_id}/Images/{kind}", data=base64.b64encode(buf.getvalue()), headers={"Content-Type": "image/jpeg"})
        if api(f"/Items/{item_id}/Images/{kind}").status != 200:
            raise RuntimeError("uploaded image is not served")
    except Exception as exc:
        print("  UPLOAD/SERVE FAIL", exc)
        return "error"
    return "ok"


if __name__ == "__main__":
    main()
