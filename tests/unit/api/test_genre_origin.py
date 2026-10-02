"""Czech/Slovak film detection for `genres origin` (cases are real library titles, 2026-10-02)."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from emby_dedupe.api.genre_origin import classify_origin, csfd_countries_by_id, fetch_tmdb_origin


def _movie(*locations: str) -> dict:
    return {"Name": "x", "ProductionLocations": list(locations)}


@pytest.mark.parametrize(("item", "tmdb", "expected"), [
    # Šialene smutná princezná — Czech original language
    (_movie("Czechoslovakia"), {"lang": "cs", "countries": ["XC"]}, True),
    # Meky — Slovak, Emby has no countries at all (common for local docs)
    (_movie(), {"lang": "sk", "countries": []}, True),
    # A Royal Affair — Danish-language co-production with Czech money: counts
    (_movie("Czech Republic", "Denmark", "Germany", "Sweden"), {"lang": "da", "countries": ["CZ", "DK", "DE", "SE"]}, True),
    # a silent/no-dialogue film made in Czechia
    (_movie("Czech Republic"), {"lang": "xx", "countries": ["CZ"]}, True),
    # The Bourne Identity / Van Helsing — Hollywood shot in Prague: English, so not
    (_movie("Czech Republic", "Germany", "United States of America"), {"lang": "en", "countries": ["CZ", "DE", "US"]}, False),
    # Anthropoid / The Glass Room — English-language, out by the same rule
    (_movie("Czech Republic", "Slovakia"), {"lang": "en", "countries": ["CZ", "SK"]}, False),
    # The Color of Magic — Emby says UK, yet the TMDb id it holds is a Czech entry: wrong id, refuse
    (_movie("United Kingdom"), {"lang": "cs", "countries": ["CZ"]}, False),
    # an ordinary foreign film
    (_movie("France"), {"lang": "fr", "countries": ["FR"]}, False),
])
def test_tmdb_rules(item, tmdb, expected):
    assert classify_origin(item, tmdb, None)[0] is expected


def test_tmdb_verdict_wins_over_csfd():
    assert classify_origin(_movie(), {"lang": "en", "countries": ["US"]}, ["Česko"])[0] is False


@pytest.mark.parametrize(("countries", "expected"), [
    (["Česko"], True),                     # 45 let Ypsilonky
    (["Slovensko"], True),                 # Tatranský durič
    (["Československo", "Nemecko"], True),
    (["Rakúsko", "Nemecko"], False),       # Třpytivé Vánoce: Czech title, Austrian film
    (["Nemecko", "Česko"], False),         # ČSFD lists the main country first
])
def test_csfd_first_country_decides_when_tmdb_has_nothing(countries, expected):
    assert classify_origin(_movie(), None, countries)[0] is expected
    assert classify_origin(_movie(), {"missing": True}, countries)[0] is expected


def test_emby_locations_only_when_nothing_else():
    assert classify_origin(_movie("Slovakia"), None, None)[0] is True  # Dano Drevo a Turnaj Mekyho Žbirku
    assert classify_origin(_movie("Slovakia", "Germany"), None, None)[0] is False
    assert classify_origin(_movie(), None, None) == (False, "no origin data")


def test_csfd_index_reads_film_entries_only():
    cache = {
        "film:https://www.csfd.sk/film/1-a/prehlad/": {"csfd_id": "1", "countries": ["Česko"]},
        "film:https://www.csfd.sk/film/2/prehlad/": {"csfd_id": 2, "countries": None},
        # cached before the series-origin parser fix (Národní klenoty, a Czech TV series)
        "film:https://www.csfd.sk/film/314255-narodni-klenoty/prehlad/": {"csfd_id": "314255", "countries": ["Česko ("]},
        "search:Meky": [{"url": "u"}],
        "people:Karel Smrž": [],
    }
    assert csfd_countries_by_id(cache) == {"1": ["Česko"], "2": [], "314255": ["Česko"]}


# --- TMDb lookup ------------------------------------------------------------------

def _response(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=httpx.Request("GET", "https://api.themoviedb.org"))


def test_fetch_reads_language_and_countries_and_caches():
    client = MagicMock()
    client.get.return_value = _response(200, {
        "original_language": "cs",
        "production_countries": [{"iso_3166_1": "CZ"}, {"iso_3166_1": "SK"}],
        "origin_country": ["CZ", "XC"],
    })
    cache: dict = {}
    first = fetch_tmdb_origin(client, MagicMock(), "42", cache)
    assert first == {"lang": "cs", "countries": ["CZ", "SK", "XC"]}
    assert fetch_tmdb_origin(client, MagicMock(), "42", cache) == first
    assert client.get.call_count == 1
    assert "tmdb_42_movie" not in cache  # never collides with fetch_tmdb_genres' list-valued keys


def test_tv_series_use_the_tv_endpoint_and_their_own_cache_key():
    """TMDb movie and TV ids overlap: movie 42 and series 42 are different titles."""
    client = MagicMock()
    client.get.return_value = _response(200, {"original_language": "cs", "origin_country": ["CZ"]})
    cache: dict = {"origin_tmdb_movie_42": {"lang": "en", "countries": ["US"]}}
    assert fetch_tmdb_origin(client, MagicMock(), "42", cache, "tv") == {"lang": "cs", "countries": ["CZ"]}
    assert client.get.call_args.args[0].endswith("/tv/42")
    assert cache["origin_tmdb_movie_42"]["lang"] == "en"


def test_fetch_caches_404_but_not_network_errors():
    client = MagicMock()
    client.get.return_value = _response(404)
    cache: dict = {}
    assert fetch_tmdb_origin(client, MagicMock(), "1", cache) == {"missing": True}
    client.get.side_effect = httpx.ConnectError("down")
    assert fetch_tmdb_origin(client, MagicMock(), "2", cache) is None
    assert "origin_tmdb_movie_2" not in cache  # retried next run


def test_the_monthly_genre_jobs_keep_the_origin_genre():
    """normalize maps only known variants and audit --suggest must not flag our own genre."""
    from emby_dedupe.api.genre_providers import compare_genres
    from emby_dedupe.api.genres import normalize_genre_name, suggest_genre_mappings
    from emby_dedupe.utils.constants import GENRE_NORMALIZATION_MAP, ORIGIN_GENRE_DEFAULT

    assert normalize_genre_name(ORIGIN_GENRE_DEFAULT, GENRE_NORMALIZATION_MAP) == ORIGIN_GENRE_DEFAULT
    assert suggest_genre_mappings({ORIGIN_GENRE_DEFAULT: 700}) == []
    assert ORIGIN_GENRE_DEFAULT in compare_genres(["Drama", ORIGIN_GENRE_DEFAULT], ["Drama", "Comedy"])["merged"]
