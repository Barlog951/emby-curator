"""Tests for emby_dedupe.api.csfd — ČSFD parsing, matching, client cache."""
from __future__ import annotations

import json

import httpx
import pytest

from emby_dedupe.api import csfd
from emby_dedupe.api.csfd import (
    CsfdClient,
    CsfdError,
    CsfdFilm,
    CsfdHit,
    normalize_title,
    parse_film,
    parse_search,
    pick_match,
)

SEARCH_HTML = """
<section class="main-box" data-search-results="films" id="films">
 <article class="article article-poster-50"><div class="article-content"><header>
  <h3 class="film-title-nooverflow"><i class="icon"></i><a href="/film/1885748-tatransky-duric/prehlad/" class="film-title-name">Tatranský durič</a> <span class="film-title-info"><span class="info">(2026)</span></span></h3>
 </header></div></article>
 <article class="article"><div class="article-content"><header>
  <h3><a href="/film/338522-macgyver/669026-x/prehlad/" class="film-title-name">MacGyver - Friends</a> <span class="film-title-info"><span class="info">(2018)</span></span></h3>
 </header></div></article>
</section>
<section class="main-box" data-search-results="series" id="series">
 <article class="article"><div class="article-content"><header>
  <h3><a href="/film/999-na-telo/prehlad/" class="film-title-name">Na telo</a> <span class="film-title-info"><span class="info">(seriál) (2026)</span></span></h3>
 </header></div></article>
</section>
"""

FILM_HTML = """
<h1>  Tatranský durič </h1>
<div class="film-about"><div class="film-posters"><a href="/x/"><img src="//image.pmgstatic.com/cache/resized/w140/files/images/film/posters/167/136/167136394_586fc2.jpg" loading="lazy"></a></div></div>
<div class="origin">Slovensko / Česko,
   2026,  31 min</div>
<div class="genres"><a href="/zanre/13/">Dokumentárny</a> <span class="bullet"></span> Krátkometrážny <span class="bullet"></span> Erotický</div>
<div class="film-rating-average rating-average-x">
  87%
</div>
<div class="plot-full">Dokumentárny film predstavuje najmladšie <a href="/x">plemeno</a> psa.

 ( STVR )</div>
"""


def test_parse_search_reads_both_sections_with_year_and_kind():
    hits = parse_search(SEARCH_HTML)
    assert [(h.title, h.year, h.kind) for h in hits] == [
        ("Tatranský durič", 2026, "film"),
        ("MacGyver - Friends", 2018, "film"),
        ("Na telo", 2026, "series"),
    ]
    assert hits[0].url == "https://www.csfd.sk/film/1885748-tatransky-duric/prehlad/"


def test_parse_film_extracts_every_field_and_upgrades_poster():
    film = parse_film(FILM_HTML, "https://www.csfd.sk/film/1885748-tatransky-duric/prehlad/")
    assert film.csfd_id == "1885748"
    assert film.title == "Tatranský durič"
    assert film.year == 2026
    assert film.countries == ["Slovensko", "Česko"]
    assert film.genres_sk == ["Dokumentárny", "Krátkometrážny", "Erotický"]
    assert film.genres_en == ["Documentary", "Short"]          # unknown label dropped
    assert film.rating_pct == 87 and film.rating_10 == 8.7
    assert film.plot == "Dokumentárny film predstavuje najmladšie plemeno psa."  # source tag stripped
    assert film.poster_url == (
        "https://image.pmgstatic.com/cache/resized/w420/files/images/film/posters/167/136/167136394_586fc2.jpg"
    )


def test_parse_film_handles_missing_rating_and_poster():
    page = "<h1>X</h1><div class=\"origin\">USA</div><div class=\"film-rating-average\">? %</div>"
    film = parse_film(page, "https://www.csfd.sk/film/5-x/prehlad/")
    assert film.rating_pct is None and film.rating_10 is None
    assert film.poster_url is None and film.year is None and film.plot == ""


def test_parse_film_placeholder_poster_is_not_a_poster():
    page = ('<h1>X</h1><div class="film-posters"><img src="data:image/gif;base64,R0lG" '
            'class="empty-image"></div>')
    assert parse_film(page, "https://www.csfd.sk/film/5-x/prehlad/").poster_url is None


def test_parse_film_without_title_raises():
    with pytest.raises(CsfdError):
        parse_film("<div>nothing</div>", "https://www.csfd.sk/film/5-x/prehlad/")


def test_normalize_title_folds_diacritics_case_and_punctuation():
    assert normalize_title("Tatranský durič") == "tatransky duric"
    assert normalize_title("Ako si vycvičiť draka: Návrat!") == normalize_title("ako si vycvicit draka navrat")


def _hit(title, year, kind="film"):
    return CsfdHit(f"https://www.csfd.sk/film/{abs(hash((title, year)))}-x/", title, year, kind)


def test_pick_match_requires_same_title_kind_and_close_year():
    hits = [_hit("Tatranský durič", 2026), _hit("MacGyver", 2018), _hit("Tatransky duric", 2026, "series")]
    assert pick_match(hits, "Tatransky Duric", 2026, "film") is hits[0]
    assert pick_match(hits, "Tatransky Duric", 2027, "film") is hits[0]      # ±1 year ok
    assert pick_match(hits, "Tatransky Duric", 2010, "film") is None         # year contradicts
    assert pick_match(hits, "Tatransky Duric", 2026, "series") is hits[2]
    assert pick_match(hits, "Unknown", 2026, "film") is None


def test_pick_match_refuses_ambiguity_and_uses_exact_year_to_break_ties():
    two = [_hit("Remake", 2019), _hit("Remake", 2020)]
    assert pick_match(two, "Remake", 2020, "film") is two[1]       # exact year wins over ±1
    assert pick_match(two, "Remake", None, "film") is None         # no year: ambiguous
    undated = [_hit("Solo", None)]
    assert pick_match(undated, "Solo", 1999, "film") is undated[0]  # unknown year on ČSFD is tolerated
    assert pick_match([_hit("Solo", None), _hit("Solo", None)], "Solo", None, "film") is None


def _flaresolverr_transport(pages: dict[str, str], calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1":
            url = json.loads(request.content)["url"]
            calls.append(url)
            page = pages.get(url)
            if page is None:
                return httpx.Response(200, json={"status": "ok", "solution": {"status": 404, "response": ""}})
            return httpx.Response(200, json={"status": "ok", "solution": {"status": 200, "response": page}})
        if "pmgstatic" in request.url.host:
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
        return httpx.Response(500)
    return httpx.MockTransport(handler)


def test_client_search_film_and_poster_round_trip_with_cache():
    calls: list[str] = []
    film_url = "https://www.csfd.sk/film/1885748-tatransky-duric/prehlad/"
    pages = {"https://www.csfd.sk/hladat/?q=Tatransk%C3%BD%20duri%C4%8D": SEARCH_HTML, film_url: FILM_HTML}
    cache: dict = {}
    client = CsfdClient(httpx.Client(transport=_flaresolverr_transport(pages, calls)),
                        "http://fs/v1", cache, calls_per_second=1000)
    hits = client.search("Tatranský durič")
    assert hits[0].title == "Tatranský durič"
    film = client.film(film_url)
    assert isinstance(film, CsfdFilm) and film.csfd_id == "1885748"
    data, ctype = client.fetch_poster(film.poster_url)
    assert data.startswith(b"\xff\xd8") and ctype == "image/jpeg"
    # second round is served from the cache: no new FlareSolverr calls
    before = len(calls)
    assert client.search("Tatranský durič")[0].url == hits[0].url
    assert client.film(film_url).plot == film.plot
    assert len(calls) == before
    assert set(cache) == {"search:Tatranský durič", f"film:{film_url}"}


def test_client_raises_csfd_error_on_bad_solution_and_transport_failure():
    calls: list[str] = []
    client = CsfdClient(httpx.Client(transport=_flaresolverr_transport({}, calls)),
                        "http://fs/v1", None, calls_per_second=1000)
    with pytest.raises(CsfdError, match="HTTP 404"):
        client.film("https://www.csfd.sk/film/1-missing/prehlad/")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    dead = CsfdClient(httpx.Client(transport=httpx.MockTransport(down)), "http://fs/v1", None, 1000)
    with pytest.raises(CsfdError, match="FlareSolverr request failed"):
        dead.search("x")
    with pytest.raises(CsfdError, match="poster download failed"):
        dead.fetch_poster("https://image.pmgstatic.com/x.jpg")


def test_cache_helpers_round_trip(tmp_path):
    path = tmp_path / "csfd-cache.json"
    csfd.save_csfd_cache({"search:x": []}, path)
    assert csfd.load_csfd_cache(path) == {"search:x": []}
    assert csfd.load_csfd_cache(tmp_path / "missing.json") == {}


NAMES_HTML = """
<h1> Vykúpenie z väznice Shawshank </h1>
<ul class="film-names">
 <li> <img src="//x/flag.svg" class="flag" title="Česko"> Vykoupení z věznice Shawshank
   <span class="normal more-name-link"> <span class="span-more-small"><a href="#" class="more">viac</a></span> </span> </li>
 <li class="more-names hidden"> <img class="flag" title="USA"> The Shawshank Redemption </li>
 <li class="more-names hidden"> <img class="flag" title="Nový Zéland"> The Shawshank Redemption
   <span class="normal less-name-link"> <span class="span-more-small"><a href="#" class="more">menej</a></span> </span> </li>
</ul>
"""


def test_parse_film_collects_alternative_names_without_ui_links():
    film = parse_film(NAMES_HTML, "https://www.csfd.sk/film/2294-x/prehlad/")
    assert film.names == ["Vykoupení z věznice Shawshank", "The Shawshank Redemption"]
    assert film.all_titles[0] == "Vykúpenie z väznice Shawshank"
    assert csfd.verify_match(film, ["The Shawshank Redemption"])
    assert csfd.verify_match(film, ["shawshank redemption, the"]) is False
    assert csfd.verify_match(film, ["", "VYKOUPENI Z VEZNICE SHAWSHANK"])


def test_candidate_hits_same_year_same_kind_or_exact_year_other_kind():
    hits = [_hit("Řecko z ptačí perspektivy", 2021, "series"), _hit("Krásy Řecka", 2013, "series"),
            _hit("Zrod impérií", 2022, "film"), _hit("Nedatované", None, "series")]
    assert csfd.candidate_hits(hits, 2021, "series") == [hits[0]]
    assert csfd.candidate_hits(hits, 2022, "series") == [hits[0], hits[2]]   # ±1 same kind, exact other kind
    # no year on the item: every same-kind hit is a candidate (page verification stays strict)
    assert csfd.candidate_hits(hits, None, "series") == [hits[0], hits[1], hits[3]]
    assert csfd.candidate_hits(hits, 1990, "film") == []


def test_client_refetches_cached_film_entries_that_predate_names():
    calls: list[str] = []
    url = "https://www.csfd.sk/film/2294-x/prehlad/"
    cache = {f"film:{url}": {"url": url, "csfd_id": "2294", "title": "old", "year": None,
                             "countries": [], "genres_sk": [], "plot": "", "rating_pct": None,
                             "poster_url": None}}
    client = CsfdClient(httpx.Client(transport=_flaresolverr_transport({url: NAMES_HTML}, calls)),
                        "http://fs/v1", cache, calls_per_second=1000)
    assert client.film(url).names == ["Vykoupení z věznice Shawshank", "The Shawshank Redemption"]
    assert calls == [url] and "names" in cache[f"film:{url}"]
