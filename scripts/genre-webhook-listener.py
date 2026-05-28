#!/usr/bin/env python3
"""
Emby genre + description webhook listener.

Receives ItemAdded webhooks from Emby, debounces for DEBOUNCE_SECONDS,
then runs TWO pipelines for all queued items:

1. ``genres process --validate`` — normalize variant genre names + fill gaps
   from TMDB/OMDb.  For episodes, queued under the parent SeriesId because
   genres live on the Series.
2. ``descriptions fill --update-title`` — replace English Overview/Tagline
   with Slovak/Czech from TMDB, with title-language policy.  For episodes,
   queued by Episode ID because each episode has its own Overview.

The two queues share the same debounce timer so a single Emby webhook
fires both pipelines.  Both share the on-disk TMDB cache, so repeat events
on the same items cost nothing.

Configure in Emby: Dashboard > Notifications > Webhooks
  URL: http://localhost:8765/webhook
  Events: New Media Added (ItemAdded)

Logs to stdout — captured by systemd journal:
  journalctl -u emby-dedupe-genre-watcher -f
"""

import http.server
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading

LISTEN_PORT = int(os.environ.get("WEBHOOK_PORT", "8765"))
DEBOUNCE_SECONDS = int(os.environ.get("DEBOUNCE_SECONDS", "300"))  # 5 min after last item
VENV_BIN = os.environ.get("VENV_BIN", "/home/barlog/emby-dedupe/.venv/bin")
WORKDIR = os.environ.get("WORKDIR", "/home/barlog/emby-dedupe")

_log_level = logging.DEBUG if os.environ.get("DEBUG") else logging.INFO
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("genre-watcher")

_lock = threading.Lock()
_timer: threading.Timer | None = None
# Two queues — different IDs needed for each pipeline:
#   _queued_for_genres: episodes collapse to their SeriesId (genres live on Series)
#   _queued_for_descriptions: episodes are queued by their own ID (per-episode Overview)
_queued_for_genres: dict[str, str] = {}        # {series_or_item_id: display_name}
_queued_for_descriptions: dict[str, str] = {}  # {item_id: display_name}


def _run_subprocess(label: str, cmd: list[str]) -> None:
    """Run a CLI command, stream its output to the journal, log a clear divider."""
    logger.info(f"--- {label} ---")
    try:
        result = subprocess.run(
            cmd, cwd=WORKDIR, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        for line in result.stdout.splitlines():
            if line.strip():
                logger.info(line)
        if result.returncode != 0:
            logger.error(f"{label} exited with code {result.returncode}")
    except Exception as exc:
        logger.error(f"{label} failed: {exc}")


def _run_pipelines() -> None:
    """Fired by the debounce timer: snapshot both queues and run both CLIs."""
    global _queued_for_genres, _queued_for_descriptions
    with _lock:
        genre_items = dict(_queued_for_genres)
        desc_items = dict(_queued_for_descriptions)
        _queued_for_genres = {}
        _queued_for_descriptions = {}

    cli = f"{VENV_BIN}/emby-dedupe"

    # === Pipeline 1: genres ===
    if genre_items:
        genre_ids = list(genre_items.keys())
        genre_names = list(genre_items.values())
        logger.info(
            f"=== Genre pipeline: {len(genre_ids)} target(s): "
            f"{', '.join(genre_names[:5])}{'...' if len(genre_names) > 5 else ''} ==="
        )
        _run_subprocess(
            "genres process",
            [cli, "genres", "process", "--doit", "--validate", "--item-ids", ",".join(genre_ids)],
        )

    # === Pipeline 2: descriptions ===
    if desc_items:
        desc_ids = list(desc_items.keys())
        desc_names = list(desc_items.values())
        logger.info(
            f"=== Description pipeline: {len(desc_ids)} target(s): "
            f"{', '.join(desc_names[:5])}{'...' if len(desc_names) > 5 else ''} ==="
        )
        _run_subprocess(
            "descriptions fill",
            [cli, "descriptions", "fill", "--update-title", "--doit", "--item-ids", ",".join(desc_ids)],
        )

    logger.info("=== Webhook batch complete ===")


def _schedule_fix(
    desc_id: str, desc_name: str,
    genre_id: str, genre_name: str,
) -> None:
    """Queue an item for both pipelines and (re)arm the debounce timer."""
    global _timer, _queued_for_genres, _queued_for_descriptions
    with _lock:
        _queued_for_descriptions[desc_id] = desc_name
        _queued_for_genres[genre_id] = genre_name
        count_d = len(_queued_for_descriptions)
        count_g = len(_queued_for_genres)
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, _run_pipelines)
        _timer.daemon = True
        _timer.start()

    logger.info(
        f"ItemAdded: '{desc_name}' "
        f"(queue: {count_d} desc, {count_g} genre) — running in {DEBOUNCE_SECONDS}s"
    )


def _parse_event(
    body: bytes, content_type: str
) -> tuple[str, str, str, str, str]:
    """Return (event_name, desc_id, desc_name, genre_id, genre_name).

    - desc_id / desc_name: the actual item — descriptions are per-item, so for
      episodes this is the episode's own Id.
    - genre_id / genre_name: for episodes this collapses to the parent SeriesId
      (genres live on the Series); for movies/series it is the item Id itself.

    Handles both lowercase 'event' (Plex-style) and uppercase 'Event' (Emby native).
    """
    if "application/json" in content_type:
        try:
            data = json.loads(body)
            event = data.get("Event") or data.get("event", "")
            item = data.get("Item") or data.get("Metadata") or data.get("item") or {}
            item_type = item.get("Type", "")
            item_id = item.get("Id") or item.get("ratingKey", "")

            if item_type == "Episode":
                series_id = item.get("SeriesId", "")
                series_name = item.get("SeriesName") or "unknown"
                ep_name = item.get("Name", "")
                desc_display = f"{series_name} – {ep_name}" if series_name else ep_name
                # For descriptions: queue the episode itself.
                # For genres: queue the series (or fall back to episode id if SeriesId is missing).
                if series_id:
                    logger.debug(
                        f"Episode '{ep_name}' (id={item_id}) → "
                        f"desc={item_id}, genre={series_id} ('{series_name}')"
                    )
                    return event, item_id, desc_display, series_id, series_name
                logger.debug(f"Episode '{ep_name}' has no SeriesId — using episode id for both queues")
                return event, item_id, desc_display, item_id, ep_name

            title = item.get("Name") or item.get("title") or data.get("Title", "unknown")
            if item_id:
                logger.debug(f"Item id={item_id} type={item_type or 'unknown'}")
            return event, item_id, title, item_id, title
        except json.JSONDecodeError:
            pass

    # multipart/form-data or unknown — extract from raw body text
    text = body.decode("utf-8", errors="replace")
    event_match = re.search(r'"[Ee]vent"\s*:\s*"([^"]+)"', text)
    title_match = re.search(r'"(?:Name|title|Title)"\s*:\s*"([^"]+)"', text)
    id_match = re.search(r'"Id"\s*:\s*"([^"]+)"', text)
    event = event_match.group(1) if event_match else ""
    item_id = id_match.group(1) if id_match else ""
    title = title_match.group(1) if title_match else "unknown"
    return event, item_id, title, item_id, title


# Events Emby sends for new media added (varies by plugin version)
_ITEM_ADDED_EVENTS = {
    "item.add",          # Emby Webhooks plugin v1.x
    "item.added",
    "library.new",       # Emby native (confirmed from real payload)
    "itemadded",
    "media.added",
    "system.itemadded",
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/webhook":
            self._respond(404, b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        raw_text = body.decode("utf-8", errors="replace")
        logger.debug(f"RAW PAYLOAD [{content_type}]: {raw_text[:2000]}")

        event, desc_id, desc_name, genre_id, genre_name = _parse_event(body, content_type)

        if event.lower() in _ITEM_ADDED_EVENTS:
            if not desc_id or not genre_id:
                logger.warning(f"ItemAdded event but no item ID in payload — skipping: '{desc_name}'")
            else:
                _schedule_fix(desc_id, desc_name, genre_id, genre_name)
        else:
            logger.debug(f"Ignored event: '{event}'")

        self._respond(200, b"ok")

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_) -> None:
        pass  # suppress default access log — we log ourselves


def main() -> None:
    logger.info(f"Listening on :{LISTEN_PORT}  debounce={DEBOUNCE_SECONDS}s  workdir={WORKDIR}")

    server = http.server.ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), _Handler)

    def _stop(sig, _frame):
        logger.info("Shutting down")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    server.serve_forever()
    sys.exit(0)


if __name__ == "__main__":
    main()
