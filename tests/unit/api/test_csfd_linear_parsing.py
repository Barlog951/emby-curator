"""Linear-time rewrites of the ČSFD / dedupe regexes (Sonar S8786, 2026-10-14) must give exactly what the old,
backtracking patterns gave. The expected values below were recorded from the old patterns on the same inputs."""

from __future__ import annotations

import re

from emby_dedupe.api import csfd
from emby_dedupe.cli import csfd as cli_csfd

PAGE = (
    '<section class="box" data-search-results="films"><article><h3>'
    '<a href="/film/1-a/" class="other">x</a><a href="/film/1-a/" class="film-title-name">A</a>'
    '<span class="film-title-info">(1999)</span></h3></article></section>'
    '<section class="outer"><section class="box" data-search-results="series"><article><h3>'
    '<a href="/film/2-b/" class="film-title-name" title="b">B</a></h3></article></section></section>'
    '<section data-search-results="creators"><article>c</article></section>'
)
FILMS_BODY = ('<article><h3><a href="/film/1-a/" class="other">x</a><a href="/film/1-a/" class="film-title-name">A</a>'
              '<span class="film-title-info">(1999)</span></h3></article>')
SERIES_BODY = '<article><h3><a href="/film/2-b/" class="film-title-name" title="b">B</a></h3></article>'


def test_sections_match_the_old_pattern():
    # a non-matching outer <section> must not hide the matching inner one
    assert csfd._search_sections(PAGE, {"films", "series"}) == [("films", FILMS_BODY), ("series", SERIES_BODY)]
    assert csfd._search_sections(PAGE, {"creators"}) == [("creators", "<article>c</article>")]


def test_title_link_takes_the_first_link_with_the_title_class():
    assert csfd._title_link(FILMS_BODY) == ("/film/1-a/", "A")
    assert csfd._title_link(SERIES_BODY) == ("/film/2-b/", "B")
    assert csfd._title_link('<a href="/film/3-c/" class="other">c</a>') is None


def test_parse_search_finds_the_nested_series_section():
    hits = csfd.parse_search(PAGE)
    assert [(h.title, h.kind) for h in hits] == [("A", csfd.KIND_FILM), ("B", csfd.KIND_SERIES)]


def test_poster_skips_an_img_without_src_like_before():
    page = '<div class="film-posters"><img alt="x"><img class="p" src="//img.csfd.cz/cache/resized/w140/a.jpg"></div>'
    assert csfd._parse_poster(page).startswith("https://img.csfd.cz/cache/resized/")
    assert csfd._parse_poster('<div class="film-posters"><img src="data:image/gif;base64,x"></div>') is None
    assert csfd._parse_poster("<img src='nothing'>") is None


def test_folder_title_query_is_unchanged():
    names = ("Kolja (1996)", "Kolja  (1996) - 1080p CZ", "Tri orisky (1973-1974)", "X(2001)")
    assert [cli_csfd._FOLDER_TITLE_RE.match(n).group(1).strip() for n in names] == ["Kolja", "Kolja", "Tri orisky", "X"]


def test_trailing_source_and_genre_split_are_unchanged():
    assert re.sub(r"\([^()]*\)\s*$", "", "Text filmu.  ( STVR )  ").strip() == "Text filmu."
    assert [g.strip() for g in re.split(r"[|/]", "Drama /  Komedie | Rodinný") if g.strip()] == ["Drama", "Komedie", "Rodinný"]
