"""Shared helpers for the Emby marimo dashboards.

Provides:
- Environment/credential loading (os.environ first, ``dashboards/.env`` fallback)
- File-based JSON cache helpers with configurable TTL
- An httpx client factory for the Emby API

This is a plain Python module (not a marimo notebook). Dashboards import it
after inserting ``mo.notebook_dir()`` into ``sys.path``.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import httpx

_DASHBOARDS_DIR = Path(__file__).resolve().parent
_ENV_KEYS = ("EMBY_HOST", "EMBY_API_KEY", "TMDB_TOKEN", "EMBY_SERVER_ID")

CACHE_DIR = Path.home() / ".cache" / "emby-dashboards"


def load_dashboard_env():
    """Load dashboard config: os.environ wins, ``dashboards/.env`` is the fallback.

    The .env parser is intentionally simple (KEY=VALUE lines, ``#`` comments,
    optional surrounding quotes) to avoid a python-dotenv dependency.

    Returns:
        dict: Mapping of known config keys to their resolved values.
    """
    env = {}
    env_file = _DASHBOARDS_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")
    for key in _ENV_KEYS:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def get_emby_config():
    """Return ``(host, api_key)`` for the Emby server, empty strings if unset."""
    env = load_dashboard_env()
    return env.get("EMBY_HOST", ""), env.get("EMBY_API_KEY", "")


def get_tmdb_token():
    """Return the TMDB v4 read access token, empty string if unset."""
    return load_dashboard_env().get("TMDB_TOKEN", "")


def cache_load(name, max_age_hours=2.0):
    """Load a cached JSON payload if it is fresh enough.

    Args:
        name: Cache entry name (filename without extension).
        max_age_hours: Maximum age in hours before the entry is considered stale.

    Returns:
        tuple: ``(data, status)`` where status is e.g. ``"cached (12m ago)"``,
        or ``(None, None)`` on miss/stale/corrupt entry.
    """
    path = CACHE_DIR / f"{name}.json"
    try:
        age = datetime.now().timestamp() - path.stat().st_mtime
        if age < max_age_hours * 3600:
            return json.loads(path.read_text()), f"cached ({int(age / 60)}m ago)"
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return None, None


def cache_save(name, data):
    """Save data as JSON to the shared cache directory."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{name}.json").write_text(json.dumps(data, default=str))


def cache_clear(pattern="*.json"):
    """Delete cache files matching the glob pattern (all JSON files by default)."""
    for f in CACHE_DIR.glob(pattern):
        f.unlink()


def make_emby_client(host, api_key, timeout=120):
    """Create an httpx client and a JSON GET helper for the Emby API.

    Args:
        host: Emby base URL, e.g. ``https://emby.example.com``.
        api_key: Emby API key (appended to every request).
        timeout: Request timeout in seconds.

    Returns:
        tuple: ``(client, emby_get)`` — the raw ``httpx.Client`` and a helper
        ``emby_get(path, **params)`` that raises on HTTP errors and returns JSON.
    """
    client = httpx.Client(timeout=timeout, verify=False)

    def emby_get(path, **params):
        params["api_key"] = api_key
        resp = client.get(f"{host}/emby/{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    return client, emby_get
