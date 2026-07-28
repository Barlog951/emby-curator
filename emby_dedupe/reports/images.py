"""Poster inlining for HTML reports — keeps the Emby API key out of report files.

Emby's image endpoints require authentication. The obvious way to satisfy an ``<img
src=...>`` is to append ``?api_key=<key>``, but that writes a **live credential** into
every generated report — and reports are opened in browsers, kept in temp dirs, and
shared. A single forwarded report would hand over full API access, deletes included.

So the poster is fetched here with the key in an ``X-Emby-Token`` **header** and embedded
as a ``data:`` URI. The resulting report is self-contained (posters render with no server
round-trip, even offline) and contains no credential.

If a fetch fails the URL is still emitted **without** the key: a broken image is an
acceptable outcome, leaking the key is not.
"""
from __future__ import annotations

import base64
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from emby_dedupe.utils.logging import logger

# Query parameters that carry a credential and must never survive into a report.
_CREDENTIAL_PARAMS = frozenset({"api_key", "apikey", "x-emby-token"})

# Posters are requested at maxWidth/maxHeight a few hundred px, so anything much larger
# than this is not a poster; skip it rather than bloat the report.
MAX_INLINE_BYTES = 2_000_000

# Reports routinely carry a few hundred posters; fetch them concurrently but politely.
_MAX_WORKERS = 8

_DEFAULT_TIMEOUT = 10.0


def strip_credentials(url: str) -> str:
    """Return ``url`` with any API-key query parameter removed.

    Used both before fetching (the key moves to a header) and as the fallback value
    written into the report when inlining fails.
    """
    if not url or "?" not in url:
        return url
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _CREDENTIAL_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def extract_api_key(url: str) -> str | None:
    """Pull the API key out of a URL's query string, if present.

    Lets the report layer reuse a key that an earlier stage already embedded, without
    having to thread the credential through extra function signatures.
    """
    if not url or "?" not in url:
        return None
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key.lower() in _CREDENTIAL_PARAMS and value:
            return value
    return None


def _fetch_data_uri(client: httpx.Client, url: str, api_key: str | None) -> str:
    """Fetch one poster and return it as a ``data:`` URI (or the keyless URL on failure)."""
    clean = strip_credentials(url)
    try:
        headers = {"X-Emby-Token": api_key} if api_key else {}
        response = client.get(clean, headers=headers)
        response.raise_for_status()
        payload = response.content
        if not payload or len(payload) > MAX_INLINE_BYTES:
            return clean
        media_type = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not media_type.startswith("image/"):
            return clean
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{media_type};base64,{encoded}"
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("Poster inline failed for %s: %s", clean, exc)
        return clean


def inline_poster_urls(
    urls: Iterable[str],
    api_key: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, str]:
    """Map each Emby poster URL to a ``data:`` URI, fetched with header auth.

    Args:
        urls: The image URLs as built by the scan (they may still carry ``api_key``).
        api_key: Token to authenticate with. When omitted, it is recovered from the
            first URL that carries one.
        timeout: Per-request timeout in seconds.

    Returns:
        ``{original_url: replacement}``. The replacement is a ``data:`` URI on success
        and the credential-stripped URL on failure — never a URL containing the key.
        Non-Emby/static URLs (no credential, e.g. the placeholder asset) map to
        themselves.
    """
    targets = [u for u in dict.fromkeys(urls) if u]
    if not targets:
        return {}

    if api_key is None:
        api_key = next((k for k in (extract_api_key(u) for u in targets) if k), None)

    # Anything without a credential is a static asset — leave it alone.
    to_fetch = [u for u in targets if extract_api_key(u) or api_key]
    replacements: dict[str, str] = {u: u for u in targets if u not in to_fetch}
    if not to_fetch:
        return replacements

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            results = pool.map(lambda u: _fetch_data_uri(client, u, api_key), to_fetch)
            replacements.update(zip(to_fetch, results, strict=True))

    inlined = sum(1 for v in replacements.values() if v.startswith("data:"))
    logger.info("Report posters: %d/%d inlined (no API key written to the report)",
                inlined, len(to_fetch))
    return replacements
