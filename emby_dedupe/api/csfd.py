"""ČSFD (csfd.sk) lookup for titles the big providers do not know.

Czech and Slovak documentaries, regional TV productions and stand-up specials are
routinely absent from TMDb/TVDb but present on ČSFD. This module resolves an Emby
item to a ČSFD film page and extracts what Emby is missing: Slovak overview,
genres, year, community rating and a poster.

ČSFD sits behind a Cloudflare bot check, so page fetches go through a local
FlareSolverr instance (``http://localhost:8191/v1``). Poster images live on
``image.pmgstatic.com`` which is not protected and is fetched directly.

Matching is deliberately strict: a hit is accepted only when its normalized
title equals the normalized query and its year (when both are known) is within
one year. Anything looser silently attaches a wrong film's poster and plot.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from emby_dedupe.api.genre_providers import RateLimiter
from emby_dedupe.utils.json_cache import load_json_cache, save_json_cache
from emby_dedupe.utils.logging import logger

FLARESOLVERR_URL_DEFAULT = "http://localhost:8191/v1"
CSFD_BASE = "https://www.csfd.sk"
CACHE_PATH = Path.home() / ".cache" / "emby-dedupe" / "csfd-cache.json"
FLARESOLVERR_TIMEOUT_MS = 60_000
YEAR_TOLERANCE = 1
POSTER_WIDTH = 1080  # ČSFD's resized cache serves up to w1080 (1080x1600); the raw file path is 403

KIND_FILM = "film"
KIND_SERIES = "series"

# ČSFD genre labels (Slovak UI) -> the English TMDb-style names the library uses.
# Labels with no sensible English counterpart are dropped rather than guessed.
GENRE_MAP_SK_EN: dict[str, str] = {
    "Dokumentárny": "Documentary",
    "Krátkometrážny": "Short",
    "Dráma": "Drama",
    "Komédia": "Comedy",
    "Krimi": "Crime",
    "Thriller": "Thriller",
    "Horor": "Horror",
    "Akčný": "Action",
    "Dobrodružný": "Adventure",
    "Animovaný": "Animation",
    "Rodinný": "Family",
    "Fantasy": "Fantasy",
    "Sci-Fi": "Science Fiction",
    "Romantický": "Romance",
    "Vojnový": "War",
    "Western": "Western",
    "Historický": "History",
    "Životopisný": "Biography",
    "Hudobný": "Music",
    "Muzikál": "Music",
    "Mysteriózny": "Mystery",
    "Športový": "Sport",
    "Rozprávka": "Family",
    "Detský": "Kids",
    "Talk-show": "Talk Show",
    "Reality-TV": "Reality",
    "Súťažný": "Reality",
    "Publicistický": "Documentary",
    "Stand-up": "Comedy",
    "Telenovela": "Soap",
}


class CsfdError(RuntimeError):
    """Raised when ČSFD or FlareSolverr cannot serve a page."""


@dataclass
class CsfdHit:
    """One row of a ČSFD search result page."""

    url: str
    title: str
    year: int | None
    kind: str


@dataclass
class CsfdFilm:
    """The metadata extracted from one ČSFD film/series page."""

    url: str
    csfd_id: str
    title: str
    year: int | None
    countries: list[str] = field(default_factory=list)
    genres_sk: list[str] = field(default_factory=list)
    plot: str = ""
    rating_pct: int | None = None
    poster_url: str | None = None
    names: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    cast: list[list[str]] = field(default_factory=list)  # [name, role] pairs (role may be "")

    @property
    def all_titles(self) -> list[str]:
        """Display title plus every alternative/original title ČSFD lists."""
        return [self.title, *self.names]

    @property
    def genres_en(self) -> list[str]:
        """Library genre names for this film, unknown ČSFD labels dropped."""
        seen: list[str] = []
        for label in self.genres_sk:
            mapped = GENRE_MAP_SK_EN.get(label)
            if mapped and mapped not in seen:
                seen.append(mapped)
        return seen

    @property
    def rating_10(self) -> float | None:
        """ČSFD percentage on Emby's 0-10 CommunityRating scale."""
        return None if self.rating_pct is None else round(self.rating_pct / 10, 1)


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_title(title: str) -> str:
    """Fold a title for comparison: no diacritics, case, or punctuation."""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z]+", " ", ascii_only.casefold()).strip()


def _first_int(text: str) -> int | None:
    match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# parsers (pure functions over page HTML)
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r'<section[^>]+data-search-results="(films|series)"[^>]*>(.*?)</section>', re.S
)
_ARTICLE_RE = re.compile(r"<article\b.*?</article>", re.S)
_TITLE_LINK_RE = re.compile(
    r'<a href="(/film/[^"]+)"[^>]*class="film-title-name"[^>]*>(.*?)</a>', re.S
)
_TITLE_INFO_RE = re.compile(r'<span class="film-title-info">(.*?)</span>\s*</h3>', re.S)


def parse_search(page: str) -> list[CsfdHit]:
    """Extract film and series hits from a ČSFD search page."""
    hits: list[CsfdHit] = []
    for section_kind, body in _SECTION_RE.findall(page):
        kind = KIND_FILM if section_kind == "films" else KIND_SERIES
        for article in _ARTICLE_RE.findall(body):
            link = _TITLE_LINK_RE.search(article)
            if not link:
                continue
            info = _TITLE_INFO_RE.search(article)
            year = _first_int(_strip_tags(info.group(1))) if info else None
            hits.append(CsfdHit(CSFD_BASE + link.group(1), _strip_tags(link.group(2)), year, kind))
    return hits


_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_ORIGIN_RE = re.compile(r'<div class="origin">(.*?)</div>', re.S)
_GENRES_RE = re.compile(r'<div class="genres">(.*?)</div>', re.S)
_PLOT_RE = re.compile(r'<div class="plot-(?:full|preview)">(.*?)</div>', re.S)
_RATING_RE = re.compile(r'class="film-rating-average[^"]*"[^>]*>(.*?)</', re.S)
_POSTER_RE = re.compile(r'<div class="film-posters">.*?<img[^>]+src="([^"]+)"', re.S)
_NAMES_RE = re.compile(r'<ul class="film-names">(.*?)</ul>', re.S)
_NAME_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
_CREATORS_RE = re.compile(r'<div class="creators"[^>]*>(.*?)</div>\s*</div>\s*</div>', re.S)
_CREATOR_LINK_RE = re.compile(
    r'<a href="/tvorca/[^"]+">([^<]+)</a>(?:&nbsp;|\s)*(?:<span class="span-more-small"[^>]*>\(([^)]*)\)</span>)?'
)
DIRECTOR_HEADINGS = {"Réžia", "Režie"}
CAST_HEADINGS = {"Hrajú", "Hrají"}
_NAME_LINK_RE = re.compile(r'<span class="normal (?:more|less)-name-link">.*?</span>\s*</span>', re.S)
_ID_RE = re.compile(r"/film/(\d+)-")


def _parse_origin(fragment: str) -> tuple[list[str], int | None]:
    """'Slovensko / Česko, 2026, 31 min' -> (countries, year)."""
    text = _strip_tags(fragment)
    year = _first_int(text)
    head = text.split(str(year))[0] if year else text
    countries = [c.strip() for c in re.split(r"[,/]", head) if c.strip()]
    return countries, year


def _parse_plot(page: str) -> str:
    match = _PLOT_RE.search(page)
    if not match:
        return ""
    plot = _strip_tags(match.group(1))
    return re.sub(r"\s*\([^()]*\)\s*$", "", plot).strip()  # drop trailing "( STVR )" source


def _parse_genres(page: str) -> list[str]:
    """ČSFD separates genre labels with bullet spans (or '/' on search rows)."""
    match = _GENRES_RE.search(page)
    if not match:
        return []
    text = _strip_tags(re.sub(r'<span class="bullet">\s*</span>', " | ", match.group(1)))
    return [g.strip() for g in re.split(r"\s*[|/]\s*", text) if g.strip()]


def _parse_names(page: str) -> list[str]:
    """Alternative titles (original title included) from the film-names list."""
    block = _NAMES_RE.search(page)
    if not block:
        return []
    names: list[str] = []
    for item in _NAME_LI_RE.findall(block.group(1)):
        text = _strip_tags(_NAME_LINK_RE.sub("", item))
        if text and text not in names:
            names.append(text)
    return names


def _parse_creators(page: str) -> tuple[list[str], list[list[str]]]:
    """Directors and cast (name, role) from the creators block; other professions ignored."""
    block = _CREATORS_RE.search(page)
    if not block:
        return [], []
    directors: list[str] = []
    cast: list[list[str]] = []
    for chunk in block.group(1).split("<h4>")[1:]:          # one chunk per profession heading
        heading, _, body = chunk.partition("</h4>")
        heading = heading.rstrip(":").strip()
        for name, role in _CREATOR_LINK_RE.findall(body):
            name = html.unescape(name).strip()
            if heading in DIRECTOR_HEADINGS and name not in directors:
                directors.append(name)
            elif heading in CAST_HEADINGS and name not in [c[0] for c in cast]:
                cast.append([name, html.unescape(role).strip()])
    return directors, cast


def _parse_poster(page: str) -> str | None:
    match = _POSTER_RE.search(page)
    if not match or match.group(1).startswith("data:"):
        return None  # lazy-load placeholder, the film has no poster
    src = match.group(1)
    url = "https:" + src if src.startswith("//") else src
    return re.sub(r"/cache/resized/w\d+/", f"/cache/resized/w{POSTER_WIDTH}/", url)


def parse_film(page: str, url: str) -> CsfdFilm:
    """Extract the metadata block from a ČSFD film or series page."""
    title_match = _H1_RE.search(page)
    if not title_match:
        raise CsfdError(f"no <h1> title on {url}")
    origin = _ORIGIN_RE.search(page)
    countries, year = _parse_origin(origin.group(1)) if origin else ([], None)
    genres = _parse_genres(page)
    rating_match = _RATING_RE.search(page)
    rating_text = _strip_tags(rating_match.group(1)) if rating_match else ""
    rating = int(rating_text.rstrip("%")) if rating_text.rstrip("%").isdigit() else None
    id_match = _ID_RE.search(url)
    directors, cast = _parse_creators(page)
    return CsfdFilm(
        url=url,
        csfd_id=id_match.group(1) if id_match else "",
        title=_strip_tags(title_match.group(1)),
        year=year,
        countries=countries,
        genres_sk=genres,
        plot=_parse_plot(page),
        rating_pct=rating,
        poster_url=_parse_poster(page),
        names=_parse_names(page),
        directors=directors,
        cast=cast,
    )


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def pick_match(
    hits: list[CsfdHit], title: str, year: int | None, kind: str
) -> CsfdHit | None:
    """Choose the one hit that unambiguously is ``title`` (``year``).

    Rules: same kind, normalized titles equal, and when both years are known
    they differ by at most ``YEAR_TOLERANCE``. If several hits survive and the
    year cannot break the tie, return None — ambiguity is a manual decision.
    """
    wanted = normalize_title(title)
    same_title = [h for h in hits if h.kind == kind and normalize_title(h.title) == wanted]
    if year is not None:
        close = [
            h for h in same_title
            if h.year is not None and abs(h.year - year) <= YEAR_TOLERANCE
        ]
        if len(close) == 1:
            return close[0]
        if close:
            exact = [h for h in close if h.year == year]
            return exact[0] if len(exact) == 1 else None
        same_title = [h for h in same_title if h.year is None]
    return same_title[0] if len(same_title) == 1 else None


def candidate_hits(hits: list[CsfdHit], year: int | None, kind: str) -> list[CsfdHit]:
    """Hits worth a page fetch when no title matched outright.

    ČSFD finds foreign titles by their original name but displays the Czech or
    Slovak one, so a same-year hit of the same kind is a plausible candidate that
    ``verify_match`` must confirm against the page's full title list. A hit of
    the other kind is only considered when its year is exact — Emby and ČSFD do
    disagree on film-versus-series for TV documentaries.
    """
    out: list[CsfdHit] = []
    for hit in hits:
        if year is None or hit.year is None:
            close = hit.kind == kind and year is None
        elif hit.kind == kind:
            close = abs(hit.year - year) <= YEAR_TOLERANCE
        else:
            close = hit.year == year
        if close:
            out.append(hit)
    return out


def verify_match(film: CsfdFilm, queries: list[str]) -> bool:
    """True when one of the film's titles equals one of the item's titles."""
    wanted = {normalize_title(q) for q in queries if q}
    return any(normalize_title(t) in wanted for t in film.all_titles)


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------

class CsfdClient:
    """Fetch ČSFD pages through FlareSolverr with a small on-disk cache."""

    def __init__(
        self,
        http: httpx.Client,
        flaresolverr_url: str = FLARESOLVERR_URL_DEFAULT,
        cache: dict | None = None,
        calls_per_second: float = 0.7,
    ) -> None:
        self._http = http
        self._flaresolverr_url = flaresolverr_url
        self._cache = cache
        self._limiter = RateLimiter(calls_per_second)

    def _get_page(self, url: str) -> str:
        self._limiter.acquire()
        try:
            resp = self._http.post(
                self._flaresolverr_url,
                json={"cmd": "request.get", "url": url, "maxTimeout": FLARESOLVERR_TIMEOUT_MS},
                timeout=FLARESOLVERR_TIMEOUT_MS / 1000 + 15,
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CsfdError(f"FlareSolverr request failed for {url}: {exc}") from exc
        if body.get("status") != "ok":
            raise CsfdError(f"FlareSolverr could not solve {url}: {body.get('message')}")
        solution = body.get("solution") or {}
        if solution.get("status") != 200:
            raise CsfdError(f"ČSFD returned HTTP {solution.get('status')} for {url}")
        return str(solution.get("response") or "")

    def search(self, query: str) -> list[CsfdHit]:
        """Search ČSFD; results are cached per query."""
        key = f"search:{query}"
        if self._cache is not None and key in self._cache:
            return [CsfdHit(**h) for h in self._cache[key]]
        page = self._get_page(f"{CSFD_BASE}/hladat/?q={quote(query, safe='')}")
        hits = parse_search(page)
        if self._cache is not None:
            self._cache[key] = [asdict(h) for h in hits]
        return hits

    def film(self, url: str) -> CsfdFilm:
        """Fetch and parse one film/series page; cached per URL."""
        key = f"film:{url}"
        cached = self._cache.get(key) if self._cache is not None else None
        if cached is not None and "cast" in cached:  # entries from before cast was parsed refetch
            return CsfdFilm(**cached)
        film = parse_film(self._get_page(url), url)
        if self._cache is not None:
            self._cache[key] = asdict(film)
        return film

    def fetch_poster(self, url: str) -> tuple[bytes, str]:
        """Download a poster directly (the image CDN has no bot check)."""
        try:
            resp = self._http.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CsfdError(f"poster download failed for {url}: {exc}") from exc
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        return resp.content, content_type


def film_url(csfd_id: str) -> str:
    """Canonical page URL for a ČSFD id (the site redirects to the slugged form)."""
    return f"{CSFD_BASE}/film/{csfd_id}/prehlad/"


def load_csfd_cache(path: Path = CACHE_PATH) -> dict:
    """Load the ČSFD page cache (empty dict when absent)."""
    return load_json_cache(path, label="ČSFD cache")


def save_csfd_cache(cache: dict, path: Path = CACHE_PATH) -> None:
    """Persist the ČSFD page cache atomically."""
    save_json_cache(path, cache, label="ČSFD cache")
    logger.debug(f"ČSFD cache saved: {len(cache)} entries -> {path}")
