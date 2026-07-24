"""Provider-ID (IMDB/TMDB/TVDB) helpers shared across search, dedupe, and the checker.

Centralises two patterns that were copy-pasted across the codebase: the
``IMDB > TMDB > TVDB`` lookup waterfall and the case-insensitive normalisation of
Emby ``ProviderIds`` (Emby returns inconsistent key casing, e.g. "Imdb" vs "IMDB").
Lives in ``utils`` so the API layer can import it without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

# Lookup order, most authoritative first: IMDB is a globally unique title ID, TMDB and
# TVDB are next. Keys are lowercase — the canonical internal form; Emby *API* params
# want the capitalised form ("Imdb"/"Tmdb"/"Tvdb"), so callers ``.capitalize()`` there.
PROVIDER_PRIORITY = ("imdb", "tmdb", "tvdb")


def normalize_provider_ids(provider_ids: dict[str, Any] | None) -> dict[str, Any]:
    """Return an Emby ``ProviderIds`` dict with lowercased keys.

    Emby returns inconsistent casing ("Imdb"/"IMDB"/"imdb"); lowercasing gives every
    caller a stable ``{"imdb": …, "tmdb": …, "tvdb": …}`` lookup. None-safe.
    """
    return {k.lower(): v for k, v in (provider_ids or {}).items()}


def iter_provider_ids(
    imdb: str | None, tmdb: str | None, tvdb: str | None
) -> Iterator[tuple[str, str]]:
    """Yield ``(provider, id_value)`` in :data:`PROVIDER_PRIORITY` order.

    Providers whose ID is missing/empty are skipped, so callers can implement the
    "try each provider ID until one resolves" waterfall as a simple loop.
    """
    ids = {"imdb": imdb, "tmdb": tmdb, "tvdb": tvdb}
    for provider in PROVIDER_PRIORITY:
        value = ids.get(provider)
        if value:
            yield provider, value
