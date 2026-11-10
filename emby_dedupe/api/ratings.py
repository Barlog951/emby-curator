"""Age ratings for items whose ``OfficialRating`` Emby left empty.

Emby's TMDb provider takes only the metadata country's certificate or the US one, and never
looks again once an item is added. This module reads every country's certificates from TMDb
and chooses one rating:

1. A Slovak certificate is written as Emby writes it (``SK-12``); else a US one, bare (``R``).
2. Otherwise the certificates of nearby countries (CZ, DE, AT, PL, HU, GB) give an age,
   which is written on the Slovak scale already used on the server (``SK-7`` … ``SK-18``),
   rounded up to the next Slovak step. One country gives its age, two give the stricter one,
   three or more give the middle one.
3. A neighbour's "suitable for all" (age 0) is not written: an empty rating already passes
   every age filter, and ``SK-U`` would mark the title as made for children.
4. Without any certificate the rating stays empty; nothing is estimated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from emby_dedupe.api.genre_providers import RateLimiter
from emby_dedupe.utils.logging import logger

TMDB_BASE = "https://api.themoviedb.org/3"
METADATA_COUNTRY = "SK"
NEIGHBOURS = ("CZ", "DE", "AT", "PL", "HU", "GB")  # order breaks ties
SK_STEPS = (7, 12, 15, 18)

# Age each certificate stands for, per TMDb's official certification lists. Values outside
# these tables (NR, Unrated, free text) are ignored.
MOVIE_AGES: dict[str, dict[str, int]] = {
    "SK": {"U": 0, "7": 7, "12": 12, "15": 15, "18": 18},
    "US": {"G": 0, "PG": 7, "PG-13": 12, "R": 17, "NC-17": 18},
    "CZ": {"U": 0, "12+": 12, "15+": 15, "18+": 18},
    "DE": {"0": 0, "6": 6, "12": 12, "16": 16, "18": 18},
    "AT": {"AA": 0, "6": 6, "8": 8, "10": 10, "12": 12, "14": 14, "16": 16},
    "PL": {"AL": 0, "7": 7, "12": 12, "16": 16, "18": 18},
    "HU": {"KN": 0, "6": 6, "12": 12, "16": 16, "18": 18},
    "GB": {"U": 0, "PG": 7, "12A": 12, "12": 12, "15": 15, "18": 18, "R18": 18},
}
TV_AGES: dict[str, dict[str, int]] = {
    "SK": {"U": 0, "7": 7, "12": 12, "15": 15, "18": 18},
    "US": {"TV-Y": 0, "TV-Y7": 7, "TV-G": 0, "TV-PG": 7, "TV-14": 14, "TV-MA": 17},
    "CZ": {"0+": 0, "5+": 5, "7+": 7, "9+": 9, "12+": 12, "15+": 15, "18+": 18},
    "DE": {"0": 0, "6": 6, "12": 12, "16": 16, "18": 18},
    "AT": {"AA": 0, "6": 6, "8": 8, "10": 10, "12": 12, "14": 14, "16": 16},
    "PL": {"0": 0, "7": 7, "12": 12, "16": 16, "18": 18},
    "HU": {"KN": 0, "6": 6, "12": 12, "16": 16, "18": 18},
    "GB": {"U": 0, "PG": 7, "12A": 12, "12": 12, "15": 15, "18": 18, "R18": 18},
}
FOUND_TTL = 30 * 86400
MISSING_TTL = 7 * 86400  # certificates appear on TMDb over time


@dataclass
class RatingChoice:
    """The rating chosen for one item, or why there is none."""

    rating: str | None
    reason: str
    certificates: dict[str, list[str]] = field(default_factory=dict)


def sk_step(age: int) -> str:
    """Slovak-scale rating for an age, rounded up to the next Slovak step."""
    for step in SK_STEPS:
        if age <= step:
            return f"SK-{step}"
    return f"SK-{SK_STEPS[-1]}"


def _country_age(values: list[str], table: dict[str, int]) -> int | None:
    ages = [table[v] for v in values if v in table]
    return max(ages) if ages else None


def _combined_age(ages: list[int]) -> int:
    """One age: the stricter of two, the middle of three or more (upper middle when even)."""
    if len(ages) <= 2:
        return max(ages)
    return sorted(ages)[len(ages) // 2]


def choose_rating(certificates: dict[str, list[str]], media_type: str = "movie") -> RatingChoice:
    """Choose the rating to write from TMDb certificates per country.

    Args:
        certificates: ``{country: [certificate, ...]}`` with empty values removed.
        media_type: ``"movie"`` or ``"tv"``; selects the certification tables.
    """
    tables = TV_AGES if media_type == "tv" else MOVIE_AGES
    sk = [v for v in certificates.get(METADATA_COUNTRY, []) if v in tables[METADATA_COUNTRY]]
    if sk:
        strictest = max(sk, key=lambda v: tables[METADATA_COUNTRY][v])
        return RatingChoice(f"SK-{strictest}", "slovak certificate", certificates)
    us = [v for v in certificates.get("US", []) if v in tables["US"]]
    if us:
        return RatingChoice(max(us, key=lambda v: tables["US"][v]), "us certificate", certificates)
    ages = [age for country in NEIGHBOURS
            if (age := _country_age(certificates.get(country, []), tables[country])) is not None]
    if not ages:
        reason = "other countries only" if certificates else "no certificate"
        return RatingChoice(None, reason, certificates)
    age = _combined_age(ages)
    if age == 0:
        return RatingChoice(None, "suitable for all", certificates)
    return RatingChoice(sk_step(age), f"neighbours {len(ages)}", certificates)


class CertificateSource:
    """TMDb certificates per item, cached (found for 30 days, none for 7)."""

    def __init__(self, token: str, cache: dict | None = None, calls_per_second: float = 35.0) -> None:
        self._http = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=20)
        self._limiter = RateLimiter(calls_per_second)
        self._cache = cache if cache is not None else {}

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        self._limiter.acquire()
        response = self._http.get(f"{TMDB_BASE}{path}", params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def tmdb_id_for_imdb(self, imdb_id: str, media_type: str) -> str | None:
        """TMDb id for an IMDb id, or None when TMDb does not know it."""
        data = self._get(f"/find/{imdb_id}", {"external_source": "imdb_id"}) or {}
        hits = data.get("tv_results" if media_type == "tv" else "movie_results") or []
        return str(hits[0]["id"]) if hits else None

    def certificates(self, tmdb_id: str, media_type: str) -> dict[str, list[str]] | None:
        """``{country: [certificate, ...]}``; None when TMDb has no such item.

        Raises:
            httpx.HTTPError: when TMDb cannot be reached; nothing is cached then.
        """
        key = f"{media_type}:{tmdb_id}"
        entry = self._cache.get(key)
        if entry and time.time() - entry["at"] < (FOUND_TTL if entry["certs"] else MISSING_TTL):
            return entry["certs"]
        if media_type == "tv":
            data = self._get(f"/tv/{tmdb_id}/content_ratings")
            rows = [(r.get("iso_3166_1"), r.get("rating")) for r in (data or {}).get("results", [])]
        else:
            data = self._get(f"/movie/{tmdb_id}/release_dates")
            rows = [(r.get("iso_3166_1"), d.get("certification"))
                    for r in (data or {}).get("results", []) for d in r.get("release_dates", [])]
        if data is None:
            return None
        certs: dict[str, list[str]] = {}
        for country, value in rows:
            value = (value or "").strip()
            if country and value and value not in certs.setdefault(country, []):
                certs[country].append(value)
        certs = {c: v for c, v in certs.items() if v}
        self._cache[key] = {"at": int(time.time()), "certs": certs}
        logger.debug(f"TMDb {key}: {certs}")
        return certs
