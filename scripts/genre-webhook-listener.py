#!/usr/bin/env python3
"""
Emby genre webhook listener.

Receives ItemAdded webhooks from Emby, debounces for DEBOUNCE_SECONDS,
then runs genre normalize + fix --validate for all queued items.

Targeted mode: collects item IDs during the debounce window and passes
them to the CLI via --item-ids. Only the new items are processed —
no full library scan needed.

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
# Keyed by item ID — deduplicates if same item fires multiple events
_queued_items: dict[str, str] = {}  # {item_id: item_name}


def _run_genre_fix() -> None:
    global _queued_items
    with _lock:
        items = dict(_queued_items)
        _queued_items = {}

    item_ids = list(items.keys())
    names = list(items.values())
    logger.info(
        f"=== Genre fix triggered for {len(item_ids)} new item(s): "
        f"{', '.join(names[:5])}{'...' if len(names) > 5 else ''} ==="
    )

    cli = f"{VENV_BIN}/emby-dedupe"
    ids_arg = ",".join(item_ids)

    for label, cmd in [
        ("normalize", [cli, "genres", "normalize", "--doit", "--item-ids", ids_arg]),
        ("fix+validate", [cli, "genres", "fix", "--doit", "--validate", "--item-ids", ids_arg]),
    ]:
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

    logger.info("=== Genre fix complete ===")


def _schedule_fix(item_id: str, item_name: str) -> None:
    global _timer, _queued_items
    with _lock:
        _queued_items[item_id] = item_name
        count = len(_queued_items)
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, _run_genre_fix)
        _timer.daemon = True
        _timer.start()

    logger.info(
        f"ItemAdded: '{item_name}' (id={item_id}) "
        f"({count} queued — running in {DEBOUNCE_SECONDS}s if no more arrive)"
    )


def _parse_event(body: bytes, content_type: str) -> tuple[str, str, str]:
    """Return (event_name, queue_id, display_name) from webhook payload.

    For Episode items, queue_id is the SeriesId (not the episode ID) so that
    50 episodes of the same series collapse to a single queue entry.
    For Movies/Series, queue_id is the item Id.

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
                # Genres live on the Series — queue the series, not the episode
                series_id = item.get("SeriesId", "")
                series_name = item.get("SeriesName") or item.get("Name", "unknown")
                ep_name = item.get("Name", "")
                if series_id:
                    logger.debug(
                        f"Episode '{ep_name}' (id={item_id}) → queuing series '{series_name}' (id={series_id})"
                    )
                    return event, series_id, series_name
                # No SeriesId in payload — fall back to episode ID
                logger.debug(f"Episode '{ep_name}' has no SeriesId, using episode id={item_id}")
                return event, item_id, ep_name

            title = item.get("Name") or item.get("title") or data.get("Title", "unknown")
            if item_id:
                logger.debug(f"Item id={item_id} type={item_type or 'unknown'}")
            return event, item_id, title
        except json.JSONDecodeError:
            pass

    # multipart/form-data or unknown — extract from raw body text
    text = body.decode("utf-8", errors="replace")
    event_match = re.search(r'"[Ee]vent"\s*:\s*"([^"]+)"', text)
    title_match = re.search(r'"(?:Name|title|Title)"\s*:\s*"([^"]+)"', text)
    id_match = re.search(r'"Id"\s*:\s*"([^"]+)"', text)
    return (
        event_match.group(1) if event_match else "",
        id_match.group(1) if id_match else "",
        title_match.group(1) if title_match else "unknown",
    )


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

        event, item_id, title = _parse_event(body, content_type)

        if event.lower() in _ITEM_ADDED_EVENTS:
            if not item_id:
                logger.warning(f"ItemAdded event but no item ID in payload — skipping: '{title}'")
            else:
                _schedule_fix(item_id, title)
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
