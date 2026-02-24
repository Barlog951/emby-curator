#!/usr/bin/env python3
"""
Emby genre webhook listener.

Receives ItemAdded webhooks from Emby, debounces for DEBOUNCE_SECONDS,
then runs genre normalize + fix --gaps-only once for all queued items.

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("genre-watcher")

_lock = threading.Lock()
_timer: threading.Timer | None = None
_queued: int = 0


def _run_genre_fix() -> None:
    global _queued
    with _lock:
        count = _queued
        _queued = 0

    logger.info(f"=== Genre fix triggered after {count} new item(s) ===")
    cli = f"{VENV_BIN}/emby-dedupe"

    for label, cmd in [
        ("normalize", [cli, "genres", "normalize", "--doit", "--all-libraries"]),
        ("fix gaps",  [cli, "genres", "fix",       "--doit", "--all-libraries"]),
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


def _schedule_fix(item_name: str) -> None:
    global _timer, _queued
    with _lock:
        _queued += 1
        count = _queued
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, _run_genre_fix)
        _timer.daemon = True
        _timer.start()

    logger.info(
        f"ItemAdded: '{item_name}' "
        f"({count} queued — running in {DEBOUNCE_SECONDS}s if no more arrive)"
    )


def _parse_event(body: bytes, content_type: str) -> tuple[str, str]:
    """Return (event_name, item_title) from webhook payload."""
    if "application/json" in content_type:
        try:
            data = json.loads(body)
            event = data.get("event", "")
            title = data.get("Metadata", {}).get("title", "") or data.get("item", {}).get("Name", "")
            return event, title
        except json.JSONDecodeError:
            pass

    # multipart/form-data or unknown — extract from raw body text
    text = body.decode("utf-8", errors="replace")
    event_match = re.search(r'"event"\s*:\s*"([^"]+)"', text)
    title_match = re.search(r'"(?:title|Name)"\s*:\s*"([^"]+)"', text)
    return (
        event_match.group(1) if event_match else "",
        title_match.group(1) if title_match else "unknown",
    )


# Events Emby sends for new media added (varies by version)
_ITEM_ADDED_EVENTS = {"library.new", "item.added", "itemadded", "media.added"}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/webhook":
            self._respond(404, b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        event, title = _parse_event(body, content_type)

        if event.lower() in _ITEM_ADDED_EVENTS:
            _schedule_fix(title)
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
