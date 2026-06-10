"""
Description (Overview) management API.

Fills Emby item Overview fields from TMDB using a configurable language
fallback chain (default: sk-SK -> cs-CZ).  Mirrors the genre-fix design:
fetch-detect-update with an atomic full-object POST.
"""

from __future__ import annotations

import copy
import re
from typing import Optional

import httpx

from emby_dedupe.api.description_cache import (
    DEFAULT_TTL_SECONDS,
    build_collection_key,
    build_episode_key,
    build_movie_key,
    build_tv_key,
    is_fresh,
    make_entry,
    read_entry,
)
from emby_dedupe.api.genre_providers import TMDB_BASE, RateLimiter
from emby_dedupe.utils.logging import logger

# Default preference: Slovak first, Czech fallback.  English is the implicit
# final fallback — when neither is available the item keeps its existing text.
LANG_CHAIN_DEFAULT: tuple[str, ...] = ("sk-SK", "cs-CZ")

# Slovak/Czech-specific diacritics — a strong English-vs-Slavic signal.
_DIACRITICS = re.compile(
    r"[áčďéěíĺľňóôŕšťúýžÁČĎÉĚÍĹĽŇÓÔŔŠŤÚÝŽůŮ]"
)
# Common Slavic function words rare in English.
_SK_CS_WORDS = re.compile(
    r"\b(je|sa|na|do|po|pri|ako|že|sú|ich|jeho|jej|svoj|tak|alebo|však)\b",
    re.IGNORECASE,
)
# Common English function words rare in Slovak/Czech.
_EN_WORDS = re.compile(
    r"\b(the|and|of|with|when|after|before|while|their|there|this|that)\b",
    re.IGNORECASE,
)


def is_english_overview(text: str) -> bool:
    """Return True when the text looks English (no Slavic diacritics + EN words).

    Tuned for paragraph-length Overview text. For short text (titles, taglines)
    use ``_looks_slavic`` / language detection instead.
    """
    if not text or not text.strip():
        return False
    if _DIACRITICS.search(text):
        return False
    return len(_EN_WORDS.findall(text)) >= 2 and len(_SK_CS_WORDS.findall(text)) == 0


def _looks_slavic(text: str) -> bool:
    """Return True iff text is detected as Czech or Slovak by lingua.

    Earlier versions used a diacritic-based heuristic, but it false-positived
    on English texts containing single foreign accents (e.g. "Bogotá" in an
    English Overview was incorrectly flagged as Slavic).  Using the lingua
    detector — already loaded for the title policy — is both more accurate
    and consistent across Overview / Tagline / Title language decisions.
    """
    if not text or not text.strip():
        return False
    return detect_title_language(text) in ("cs", "sk")


# Title-policy languages: title is kept-as-is if its detected language is
# Czech, Slovak, or English; otherwise replaced with the en-US TMDB title.
_TITLE_KEEP_ISO_CODES: frozenset[str] = frozenset({"cs", "sk", "en"})
_TITLE_REPLACEMENT_LANG = "en-US"

# Lazy-initialized lingua detector restricted to languages we actually need.
# Restricting the set keeps RAM ~150 MB and improves cs/sk discrimination,
# which the full 75-language detector confuses on short input.
_TITLE_DETECTOR = None


def _get_title_detector():
    """Build (once) and return the lingua language detector for title detection."""
    global _TITLE_DETECTOR
    if _TITLE_DETECTOR is None:
        from lingua import Language, LanguageDetectorBuilder
        _TITLE_DETECTOR = LanguageDetectorBuilder.from_languages(
            Language.CZECH, Language.SLOVAK, Language.ENGLISH,
            Language.GERMAN, Language.FRENCH, Language.SPANISH, Language.ITALIAN,
            Language.POLISH, Language.SLOVENE, Language.RUSSIAN, Language.TURKISH,
            Language.JAPANESE, Language.CHINESE, Language.KOREAN,
        ).build()
    return _TITLE_DETECTOR


def detect_title_language(title: str) -> Optional[str]:
    """Return ISO 639-1 code for the title's detected language, or None.

    Uses lingua-py restricted to the languages relevant to this library.
    """
    if not title or not title.strip():
        return None
    detector = _get_title_detector()
    lang = detector.detect_language_of(title.strip())
    return lang.iso_code_639_1.name.lower() if lang else None


_DEFAULT_LOCALIZED_LANGS: tuple[str, ...] = ("en-US", "cs-CZ", "sk-SK")


def _fetch_tmdb_one_lang(
    client: httpx.Client,
    limiter: RateLimiter,
    url: str,
    lang: str,
    log_context: str,
) -> Optional[dict]:
    """Fetch a single TMDB endpoint for one language.

    Returns:
        - dict with raw TMDB JSON when the request succeeds.
        - ``{"__not_found__": True}`` sentinel on HTTP 404.
        - None on any other transport / HTTP error (already logged).
    """
    limiter.acquire()
    try:
        response = client.get(url, params={"language": lang})
        if response.status_code == 404:
            return {"__not_found__": True}
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.warning(f"TMDB request failed for {log_context} lang={lang}: {e}")
        return None


_MEDIA_TYPE_KEY_BUILDERS = {
    "movie": build_movie_key,
    "tv": build_tv_key,
    "collection": build_collection_key,
}


def _cache_lookup(
    cache: Optional[dict], cache_key: Optional[str], cache_ttl: int,
) -> tuple[bool, Optional[dict]]:
    """Return (hit, value) for a cache lookup; hit=False means caller fetches."""
    if cache is None or cache_key is None:
        return False, None
    entry = cache.get(cache_key)
    if is_fresh(entry, cache_ttl):
        return True, read_entry(entry)
    return False, None


def _parse_tmdb_payload(data: dict) -> dict[str, str]:
    """Normalize TMDB JSON to the {title, overview, tagline} shape."""
    # TMDB uses "title" for movies and "name" for TV/collection.
    title = (data.get("title") or data.get("name") or "").strip()
    overview = (data.get("overview") or "").strip()
    tagline = (data.get("tagline") or "").strip()
    return {"title": title, "overview": overview, "tagline": tagline}


def _collect_localized_results(
    client: httpx.Client,
    limiter: RateLimiter,
    url: str,
    log_ctx: str,
    langs: tuple[str, ...],
    payload_parser=_parse_tmdb_payload,
) -> tuple[dict[str, dict[str, str]], bool]:
    """Iterate ``langs``, return (collected, not_found).  ``not_found`` short-circuits on 404.

    ``payload_parser`` lets callers swap the per-language parsing — episodes
    use a slightly different shape (no tagline field on TMDB).
    """
    out: dict[str, dict[str, str]] = {}
    for lang in langs:
        data = _fetch_tmdb_one_lang(client, limiter, url, lang, log_ctx)
        if data is None:
            continue
        if data.get("__not_found__"):
            return out, True
        out[lang] = payload_parser(data)
    return out, False


def _parse_tmdb_episode_payload(data: dict) -> dict[str, str]:
    """Same as _parse_tmdb_payload but with an always-empty tagline (episodes have none)."""
    title = (data.get("name") or "").strip()
    overview = (data.get("overview") or "").strip()
    return {"title": title, "overview": overview, "tagline": ""}


def fetch_tmdb_episode_localized(
    client: httpx.Client,
    limiter: RateLimiter,
    series_tmdb_id: str,
    season: int,
    episode: int,
    langs: tuple[str, ...] = _DEFAULT_LOCALIZED_LANGS,
    cache: Optional[dict] = None,
    cache_ttl: int = DEFAULT_TTL_SECONDS,
) -> Optional[dict]:
    """Fetch title+overview for a TV episode in each language.

    Episodes don't have taglines on TMDB; the returned dict still includes a
    ``"tagline": ""`` slot so callers can use the same downstream pick logic
    as movies/series.

    When ``cache`` is provided, the result (including None for 404 / no-data)
    is stored under ``ep:{series_tmdb_id}:s{S}e{E}`` and re-used until
    ``cache_ttl`` seconds have passed.  Negative entries are cached too so
    repeat sweeps don't re-query items where TMDB has nothing.

    Returns ``{lang: {"title", "overview", "tagline": ""}}`` or None on 404.
    """
    cache_key = build_episode_key(series_tmdb_id, season, episode)
    hit, cached = _cache_lookup(cache, cache_key, cache_ttl)
    if hit:
        return cached

    url = f"{TMDB_BASE}/tv/{series_tmdb_id}/season/{season}/episode/{episode}"
    log_ctx = f"episode {series_tmdb_id}/S{season}E{episode}"
    out, not_found = _collect_localized_results(
        client, limiter, url, log_ctx, langs,
        payload_parser=_parse_tmdb_episode_payload,
    )
    result = None if not_found else out
    if cache is not None:
        cache[cache_key] = make_entry(result)
    return result


def fetch_tmdb_localized(
    client: httpx.Client,
    limiter: RateLimiter,
    tmdb_id: str,
    media_type: str,
    langs: tuple[str, ...] = _DEFAULT_LOCALIZED_LANGS,
    cache: Optional[dict] = None,
    cache_ttl: int = DEFAULT_TTL_SECONDS,
) -> Optional[dict]:
    """Fetch title+overview for an item in each language.

    Args:
        client: httpx client with Authorization header set.
        limiter: RateLimiter instance for TMDB.
        tmdb_id: TMDB item ID.
        media_type: ``"movie"``, ``"tv"`` or ``"collection"``.
        langs: BCP47 language codes to fetch.
        cache: Optional shared cache dict (mutated in place).  Both positive
            and negative results are stored — re-runs skip cached items.
        cache_ttl: Maximum age in seconds for a cache entry to be reused.

    Returns:
        Dict ``{lang: {"title": str, "overview": str, "tagline": str}}`` or
        None on 404.  Missing languages are silently skipped.
    """
    key_builder = _MEDIA_TYPE_KEY_BUILDERS.get(media_type)
    cache_key = key_builder(tmdb_id) if key_builder else None
    hit, cached = _cache_lookup(cache, cache_key, cache_ttl)
    if hit:
        return cached

    url = f"{TMDB_BASE}/{media_type}/{tmdb_id}"
    log_ctx = f"{media_type}/{tmdb_id}"
    out, not_found = _collect_localized_results(client, limiter, url, log_ctx, langs)
    result = None if not_found else out
    if cache is not None and cache_key is not None:
        cache[cache_key] = make_entry(result)
    return result


def pick_overview_from_localized(
    localized: dict, lang_chain: tuple[str, ...] = LANG_CHAIN_DEFAULT
) -> Optional[tuple[str, str]]:
    """Pick first non-empty overview from the chain. Returns (overview, lang) or None."""
    for lang in lang_chain:
        ov = (localized.get(lang) or {}).get("overview", "")
        if ov:
            return ov, lang
    return None


def pick_tagline_from_localized(
    localized: dict, lang_chain: tuple[str, ...] = LANG_CHAIN_DEFAULT
) -> Optional[tuple[str, str]]:
    """Pick first non-empty tagline from the chain. Returns (tagline, lang) or None."""
    for lang in lang_chain:
        tg = (localized.get(lang) or {}).get("tagline", "")
        if tg:
            return tg, lang
    return None


def pick_title_from_localized(
    current_title: str, localized: dict
) -> Optional[tuple[str, str]]:
    """Apply the title policy using language detection (lingua-py).

    Keep the current title if its detected language is Czech, Slovak, or
    English.  Otherwise replace it with the en-US TMDB title (when available
    and different).  OriginalTitle is never touched by this function.

    Args:
        current_title: The title Emby currently holds.
        localized: Output of fetch_tmdb_localized.

    Returns:
        ``(new_title, "en-US")`` to apply a replacement, or None to keep current.
    """
    if not current_title or not current_title.strip():
        return None

    detected = detect_title_language(current_title)
    if detected in _TITLE_KEEP_ISO_CODES:
        return None  # cs/sk/en — keep

    en_title = (localized.get(_TITLE_REPLACEMENT_LANG) or {}).get("title", "")
    if not en_title:
        return None  # no EN title to replace with
    if en_title.strip().casefold() == current_title.strip().casefold():
        return None  # already equals EN (modulo punctuation/case) — no-op
    return en_title, _TITLE_REPLACEMENT_LANG


def fetch_tmdb_overview(
    client: httpx.Client,
    limiter: RateLimiter,
    tmdb_id: str,
    media_type: str,
    lang_chain: tuple[str, ...] = LANG_CHAIN_DEFAULT,
) -> Optional[tuple[str, str]]:
    """Return (overview, lang) trying lang_chain in order. Thin wrapper over
    fetch_tmdb_localized for callers that only want the overview.
    """
    loc = fetch_tmdb_localized(client, limiter, tmdb_id, media_type, lang_chain)
    if loc is None:
        return None
    return pick_overview_from_localized(loc, lang_chain)


def _is_episode_candidate(it: dict) -> bool:
    """Eligible iff has SeriesId + season/episode numbers + non-Slavic Overview."""
    if not it.get("SeriesId"):
        return False
    if it.get("ParentIndexNumber") is None or it.get("IndexNumber") is None:
        return False
    ov = (it.get("Overview") or "").strip()
    return not _looks_slavic(ov)


def _is_movie_or_series_candidate(it: dict) -> bool:
    """Eligible iff has TMDB ID and Overview or Tagline still looks non-Slavic."""
    pids = it.get("ProviderIds") or {}
    if not pids.get("Tmdb"):
        return False
    ov = (it.get("Overview") or "").strip()
    tags = it.get("Taglines") or []
    tag = (tags[0] if tags else "").strip()
    return not _looks_slavic(ov) or not _looks_slavic(tag)


def collect_overview_candidates(items: list[dict]) -> list[dict]:
    """Return items eligible for overview/tagline localization.

    Eligible:
    - Movie/Series: has its own TMDB ID, at least one field looks non-Slavic.
    - Episode: has SeriesId + season/episode numbers (TMDB resolution happens
      at process time via the parent series's TMDB ID), and its Overview
      looks non-Slavic.  Episodes have no taglines.

    LockedFields is NOT checked here because Emby's batch list endpoints
    silently omit ``LockedFields`` from their responses — only the per-item
    ``/Users/{uid}/Items/{id}`` GET returns it.  The actual lock enforcement
    happens inside ``update_item_metadata`` which fetches the full item and
    skips any field already in ``LockedFields``.
    """
    out: list[dict] = []
    for it in items:
        if it.get("Type") == "Episode":
            if _is_episode_candidate(it):
                out.append(it)
        elif _is_movie_or_series_candidate(it):
            out.append(it)
    return out


def build_series_tmdb_map(items: list[dict]) -> dict[str, str]:
    """Build a {SeriesId: series_tmdb_id} lookup for fast episode resolution.

    Returns only entries where the series item actually has a TMDB ID.
    """
    return {
        it["Id"]: tmdb_id
        for it in items
        if it.get("Type") == "Series"
        and (tmdb_id := (it.get("ProviderIds") or {}).get("Tmdb"))
    }


def pick_overview_with_fallback(
    localized: dict,
    current_overview: str,
    lang_chain: tuple[str, ...] = LANG_CHAIN_DEFAULT,
) -> Optional[tuple[str, str]]:
    """Pick an overview using the chain, with EN fallback when current is empty.

    When the current Overview is empty, append "en-US" to the chain so we can
    fill the gap with an English description rather than leaving it blank.
    When the current Overview is non-empty (already English), the chain stays
    as-is so we never churn English→English.
    """
    chain = lang_chain
    if not current_overview.strip() and "en-US" not in chain:
        chain = chain + ("en-US",)
    return pick_overview_from_localized(localized, chain)


def pick_tagline_with_fallback(
    localized: dict,
    current_tagline: str,
    lang_chain: tuple[str, ...] = LANG_CHAIN_DEFAULT,
) -> Optional[tuple[str, str]]:
    """Pick a tagline using the chain, with EN fallback when current is empty."""
    chain = lang_chain
    if not current_tagline.strip() and "en-US" not in chain:
        chain = chain + ("en-US",)
    return pick_tagline_from_localized(localized, chain)


def _field_needs_update(
    new_value: Optional[str],
    current_value: Optional[str],
    lock_name: str,
    current_locked: list,
) -> bool:
    """Return True when new_value is set, field isn't locked, and value differs."""
    if new_value is None:
        return False
    if lock_name in current_locked:
        return False
    return (current_value or "") != new_value


def _apply_field_update(
    payload: dict,
    field_key: str,
    new_value,
    lock_name: str,
    locked: Optional[list],
) -> None:
    """Write the new value to payload and append the lock enum when applicable."""
    payload[field_key] = new_value
    if locked is not None and lock_name not in locked:
        locked.append(lock_name)


def _post_metadata_update(
    client: httpx.Client, base_url: str, item_id: str, payload: dict,
) -> bool:
    """POST the payload to Emby and log success/failure.  Returns True on 2xx."""
    name = payload.get("Name", item_id)
    try:
        resp = client.post(f"{base_url}/Items/{item_id}", json=payload)
        if resp.is_success:
            logger.info(f"Updated metadata for {name} ({item_id})")
            return True
        logger.error(f"Failed to update metadata for {name}: HTTP {resp.status_code}")
        return False
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"Failed to update metadata for {name}: {e}")
        return False


def update_item_metadata(
    client: httpx.Client,
    base_url: str,
    item_id: str,
    full_item: dict,
    new_overview: Optional[str] = None,
    new_title: Optional[str] = None,
    new_tagline: Optional[str] = None,
    lock: bool = True,
) -> bool:
    """Update Overview, Name, and/or Tagline for a single Emby item via atomic POST.

    Per-field policy:
    - No-ops when the new value equals the current value.
    - Skips a field when it is already in ``LockedFields`` — respecting prior
      explicit locks (set by us or the user via the Emby UI).
    - OriginalTitle is never touched.
    - When ``lock=True``, each updated field gets its corresponding
      ``LockedFields`` enum appended (``"Overview"``, ``"Name"``, ``"Tagline"``
      — note Emby uses ``"Tagline"`` singular for the lock enum even though
      the data field is ``"Taglines"`` plural).

    Returns:
        True when a non-empty payload was POSTed and accepted.
    """
    current_locked = full_item.get("LockedFields") or []
    current_tagline = (full_item.get("Taglines") or [None])[0]
    overview_changes = _field_needs_update(
        new_overview, full_item.get("Overview"), "Overview", current_locked,
    )
    title_changes = _field_needs_update(
        new_title, full_item.get("Name"), "Name", current_locked,
    )
    tagline_changes = (
        new_tagline is not None
        and "Tagline" not in current_locked
        and current_tagline != new_tagline
    )
    if not (overview_changes or title_changes or tagline_changes):
        return False

    payload = copy.deepcopy(full_item)
    locked = payload.setdefault("LockedFields", []) if lock else None

    if overview_changes:
        _apply_field_update(payload, "Overview", new_overview, "Overview", locked)
    if title_changes:
        _apply_field_update(payload, "Name", new_title, "Name", locked)
    if tagline_changes:
        # Emby's data field is "Taglines" (plural list) but the LockedFields
        # enum uses "Tagline" (singular). Empirically verified — "Taglines"
        # in LockedFields is silently rejected.
        _apply_field_update(payload, "Taglines", [new_tagline], "Tagline", locked)

    return _post_metadata_update(client, base_url, item_id, payload)


def update_item_overview(
    client: httpx.Client,
    base_url: str,
    item_id: str,
    full_item: dict,
    new_overview: str,
    lock: bool = True,
) -> bool:
    """Backwards-compatible wrapper: update Overview only."""
    return update_item_metadata(
        client, base_url, item_id, full_item,
        new_overview=new_overview, new_title=None, lock=lock,
    )
