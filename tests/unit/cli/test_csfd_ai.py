"""Tests for the ``csfd fill --ai-match`` fallback (TypeSafe Jev picks among ČSFD candidates)."""
from __future__ import annotations

from argparse import Namespace

import httpx
import pytest

from emby_dedupe.api.csfd import CsfdError, CsfdFilm, CsfdHit
from emby_dedupe.api.typesafe import DEFAULT_MODEL, ChoiceAnswer, TypesafeError
from emby_dedupe.cli import csfd as cli
from emby_dedupe.cli.csfd import (
    AI_NONE,
    AiMatcher,
    AiSuggestion,
    ai_candidates,
    ai_pick,
    ai_state,
    describe_candidate,
    load_manual_map,
    load_never_match,
    write_ai_review,
)

URL_A = "https://www.csfd.sk/film/100-a/prehlad/"
URL_B = "https://www.csfd.sk/film/200-b/prehlad/"


def _item(**over):
    base = {"Id": "7", "Type": "Movie", "Name": "The Count", "ProductionYear": 2023,
            "Path": "/Movies/HD/El Conde (2023) - 1080p WEB-DL CZ/El Conde (2023) - 1080p WEB-DL CZ.mkv",
            "ProviderIds": {}, "ImageTags": {}, "Genres": [], "Overview": "", "LockedFields": [],
            "People": []}
    base.update(over)
    return base


def _film(url=URL_A, csfd_id="100", title="Hrabě", **over):
    base = dict(url=url, csfd_id=csfd_id, title=title, year=2023, countries=["Čile"],
                names=["El Conde"], plot="Plot.", poster_url=None)
    base.update(over)
    return CsfdFilm(**base)


class _Csfd:
    """Fake CsfdClient: fixed hits for every query, films by URL (an Exception value raises)."""

    def __init__(self, hits, films):
        self.hits, self.films, self.searched, self.fetched = hits, films, [], []

    def search(self, query):
        self.searched.append(query)
        return self.hits

    def film(self, url):
        self.fetched.append(url)
        film = self.films[url]
        if isinstance(film, Exception):
            raise film
        return film

    def fetch_poster(self, url):
        return b"\xff\xd8", "image/jpeg"


class _Jev:
    """Fake TypesafeClient: replays answers (ChoiceAnswer or Exception) and records questions."""

    def __init__(self, *answers):
        self.answers, self.calls, self.closed = list(answers), [], False

    def choose(self, state, instructions, criteria):
        self.calls.append((state, instructions, criteria))
        nxt = self.answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def close(self):
        self.closed = True


def _hit(url=URL_A, title="Hrabě", year=2023, kind="film"):
    return CsfdHit(url, title, year, kind)


# --- candidates and the question -------------------------------------------

def test_candidates_are_deduplicated_year_guarded_and_capped(monkeypatch):
    hits = [_hit(URL_A), _hit(URL_A), _hit(URL_B, year=2010)]  # duplicate + wrong year
    got = ai_candidates(_Csfd(hits, {}), _item())
    assert [h.url for h in got] == [URL_A]
    many = [_hit(f"https://www.csfd.sk/film/{i}-x/prehlad/") for i in range(20)]
    monkeypatch.setattr(cli, "AI_MAX_CANDIDATES", 3)
    assert len(ai_candidates(_Csfd(many, {}), _item())) == 3


def test_state_sends_folder_name_never_the_full_path():
    state = ai_state(_item(OriginalTitle="El Conde"))
    assert state == {"title": "The Count", "year": 2023, "type": "Movie",
                     "folder_name": "El Conde (2023) - 1080p WEB-DL CZ", "original_title": "El Conde"}
    assert "/Movies" not in str(state)
    assert "original_title" not in ai_state(_item(OriginalTitle="The Count"))  # same as Name: omitted
    series = ai_state(_item(Type="Series", Path="/Movies/Serials/Show (2020)"))
    assert series["folder_name"] == "Show (2020)"


def test_candidate_description_carries_country_and_original_titles():
    """Regression 2026-09-23: without country/original titles Jev matched El Conde to the
    Korean sports drama *Count* (same title, same year) at 0.92."""
    korean = _film(URL_B, "200", "Count", countries=["Južná Kórea"], names=["Kaunteu", "카운트", "x", "y"])
    text = describe_candidate(_hit(URL_B, "Count"), korean)
    assert text == "Count (2023), film; country: Južná Kórea; original titles: Kaunteu, 카운트, x"
    bare = _film(countries=[], names=[])
    assert describe_candidate(_hit(), bare) == "Hrabě (2023), film"


def test_instructions_name_the_fields_actually_sent():
    """Jev reads literally: the instructions must talk about the folder name we send."""
    assert "folder name" in cli.AI_INSTRUCTIONS and "file path" not in cli.AI_INSTRUCTIONS


# --- ai_pick ----------------------------------------------------------------

def test_no_candidates_means_no_api_call():
    jev = _Jev()
    assert ai_pick(_Csfd([], {}), jev, _item()) is None
    assert jev.calls == []


def test_pick_returns_film_and_confidence_and_asks_with_none_option():
    film = _film()
    jev = _Jev(ChoiceAnswer("c1", 0.97))
    got = ai_pick(_Csfd([_hit()], {URL_A: film}), jev, _item())
    assert got == (film, 0.97)
    _, _, criteria = jev.calls[0]
    assert set(criteria) == {"c1", AI_NONE}


def test_pick_none_and_unknown_choice_return_none():
    csfd = _Csfd([_hit()], {URL_A: _film()})
    assert ai_pick(csfd, _Jev(ChoiceAnswer(AI_NONE, 0.9)), _item()) is None
    assert ai_pick(csfd, _Jev(ChoiceAnswer("c9", 0.9)), _item()) is None


def test_candidate_page_failure_skips_only_that_candidate():
    good = _film(URL_B, "200", "B")
    csfd = _Csfd([_hit(URL_A), _hit(URL_B)], {URL_A: CsfdError("blocked"), URL_B: good})
    jev = _Jev(ChoiceAnswer("c1", 0.95))
    assert ai_pick(csfd, jev, _item()) == (good, 0.95)
    assert set(jev.calls[0][2]) == {"c1", AI_NONE}  # the failed page got no option


# --- AiMatcher ---------------------------------------------------------------

def _matcher(*answers, auto=False, threshold=0.9):
    return AiMatcher(_Jev(*answers), auto=auto, threshold=threshold)


def test_review_mode_never_applies_even_at_high_confidence():
    m = _matcher(ChoiceAnswer("c1", 0.99))
    s = m.resolve(_Csfd([_hit()], {URL_A: _film()}), _item())
    assert s is not None and not s.applied and m.suggestions == [s]


def test_auto_mode_applies_only_at_or_above_threshold():
    csfd = _Csfd([_hit()], {URL_A: _film()})
    high = _matcher(ChoiceAnswer("c1", 0.9), auto=True).resolve(csfd, _item())
    low = _matcher(ChoiceAnswer("c1", 0.89), auto=True).resolve(csfd, _item())
    assert high is not None and high.applied
    assert low is not None and not low.applied


def test_picks_below_review_minimum_are_dropped():
    m = _matcher(ChoiceAnswer("c1", 0.4), auto=True)
    assert m.resolve(_Csfd([_hit()], {URL_A: _film()}), _item()) is None
    assert m.suggestions == []


def test_api_error_is_counted_and_run_continues():
    m = _matcher(TypesafeError("overloaded"), ChoiceAnswer("c1", 0.95))
    csfd = _Csfd([_hit()], {URL_A: _film()})
    assert m.resolve(csfd, _item()) is None
    assert m.errors == 1 and not m.disabled
    assert m.resolve(csfd, _item()) is not None  # next item still asked


def test_rejected_key_disables_the_fallback_for_the_run():
    m = _matcher(TypesafeError("bad key", fatal=True))
    csfd = _Csfd([_hit()], {URL_A: _film()})
    assert m.resolve(csfd, _item()) is None and m.disabled
    assert m.resolve(csfd, _item()) is None
    assert len(m.client.calls) == 1  # no second call once disabled


# --- review file and never-match ---------------------------------------------

def test_review_file_is_a_valid_map_file(tmp_path):
    """Regression: load_manual_map splits on the FIRST tab, so any trailing column would be
    glued onto the URL. Review entries must parse to clean URLs; applied ones stay comments."""
    review = AiSuggestion("1", "Gold Run", 2022, _film(URL_A, "100", "Zlatý útek"), 0.95)
    applied = AiSuggestion("2", "Disaster", 2026, _film(URL_B, "200", "Černobyl"), 0.98, applied=True)
    path = tmp_path / "review.tsv"
    write_ai_review(str(path), [review, applied], DEFAULT_MODEL)
    assert load_manual_map(str(path)) == {"1": URL_A}
    text = path.read_text(encoding="utf-8")
    assert "# 0.98 AUTO-APPLIED | Disaster (2026) -> Černobyl (2023)" in text
    assert f"# 2\t{URL_B}" in text
    assert text.index("0.98") < text.index("0.95")  # most confident first


def test_never_match_lines(tmp_path):
    path = tmp_path / "map.tsv"
    path.write_text(f"# comment\n1\t{URL_A}\n2\t-\n# 3\t-\n4\t -\n", encoding="utf-8")
    assert load_never_match(str(path)) == {"2", "4"}
    assert load_manual_map(str(path)) == {"1": URL_A}  # older readers ignore '-' lines
    assert load_never_match(None) == set()


# --- wiring: _lookup, _make_ai_matcher, _run_fill ------------------------------

def test_lookup_skips_never_match_items_without_searching():
    csfd = _Csfd([_hit()], {URL_A: _film()})
    stats = {"matched": 0, "unmatched": 0, "errors": 0}
    plan = cli._lookup(csfd, _item(), {}, stats, never={"7"})
    assert plan.film is None and plan.reason == "marked no-match in map"
    assert csfd.searched == [] and stats["unmatched"] == 1


def test_lookup_uses_ai_only_after_strict_miss():
    film = _film(names=["Unrelated"])
    csfd = _Csfd([_hit()], {URL_A: film})  # no title in common: the strict matcher misses
    stats = {"matched": 0, "unmatched": 0, "errors": 0}
    auto = cli._lookup(csfd, _item(), {}, stats, ai=_matcher(ChoiceAnswer("c1", 0.95), auto=True))
    assert auto.film is film and auto.reason == "ai match (0.95)" and stats["matched"] == 1
    review = cli._lookup(csfd, _item(), {}, stats, ai=_matcher(ChoiceAnswer("c1", 0.95)))
    assert review.film is None and review.reason.startswith("ai suggestion (0.95): review")


def test_make_ai_matcher(monkeypatch):
    assert cli._make_ai_matcher(Namespace(ai_match=False)) is None
    monkeypatch.delenv("DEDUPE_TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        cli._make_ai_matcher(Namespace(ai_match=True))
    monkeypatch.setenv("TYPESAFE_API_KEY", "your-api-key")  # vendor-name fallback
    m = cli._make_ai_matcher(Namespace(ai_match=True, ai_auto=True, ai_threshold=0.95, ai_model=None))
    assert m is not None and m.auto and m.threshold == 0.95
    assert m.client._model == DEFAULT_MODEL
    m.client.close()


def test_run_fill_with_ai_writes_review_and_applies_only_in_auto(tmp_path, monkeypatch):
    item = _item()
    posted: list[str] = []

    def emby(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(200, json={"Items": [item]})

    csfd = _Csfd([_hit()], {URL_A: _film(names=["Unrelated"])})
    monkeypatch.setattr(cli, "CsfdClient", lambda *a, **k: csfd)
    monkeypatch.setattr(cli, "load_csfd_cache", lambda: {})
    monkeypatch.setattr(cli, "save_csfd_cache", lambda cache: None)
    monkeypatch.setattr(cli, "fetch_items_with_genres", lambda *a, **k: [item])
    client = httpx.Client(transport=httpx.MockTransport(emby))
    review = tmp_path / "review.tsv"

    def run(auto: bool, doit: bool) -> _Jev:
        jev = _Jev(ChoiceAnswer("c1", 0.96))
        monkeypatch.setattr(cli, "_make_ai_matcher", lambda args: AiMatcher(jev, auto=auto))
        args = Namespace(doit=doit, flaresolverr_url="http://fs/v1", report=None,
                         ai_review_file=str(review), ai_model=None)
        cli._run_fill(client, "http://emby:8096", "u", ["lib"], args)
        return jev

    jev = run(auto=False, doit=True)  # review-only: even --doit writes nothing
    assert posted == [] and jev.closed
    assert load_manual_map(str(review)) == {"7": URL_A}

    run(auto=True, doit=True)
    assert posted == ["/Items/7"]
    assert load_manual_map(str(review)) == {}  # applied picks are recorded as comments only


def test_strict_verifier_still_wins_before_ai():
    """Emby's own OriginalTitle (El Conde) is listed on the page: the strict pass matches it and
    Jev is never asked. The same title only in the FOLDER goes to the reviewed AI path instead."""
    csfd = _Csfd([_hit()], {URL_A: _film(names=["El Conde"])})
    stats = {"matched": 0, "unmatched": 0, "errors": 0}
    jev = _Jev()
    plan = cli._lookup(csfd, _item(OriginalTitle="El Conde"), {}, stats, ai=AiMatcher(jev, auto=True))
    assert plan.film is not None and "via original title" in plan.reason and jev.calls == []
    jev = _Jev(ChoiceAnswer("c1", 0.97))
    plan = cli._lookup(csfd, _item(), {}, stats, ai=AiMatcher(jev))  # 'El Conde' only in the folder
    assert plan.film is None and plan.reason.startswith("ai suggestion (0.97)")
    assert jev.calls[0][0]["folder_name"].startswith("El Conde")

def test_episode_and_season_pages_are_never_candidates():
    """Regression 2026-09-23: an episode page carries its series' id, so it overwrote the
    series option (Eyes of Wakanda showed as 'Straty a nálezy (E03)')."""
    series = "https://www.csfd.sk/film/1020814-oci-wakandy/prehlad/"
    episode = "https://www.csfd.sk/film/1020814-oci-wakandy/1709587-straty-a-nalezy/prehlad/"
    hits = [_hit(series, "Oči Wakandy", 2025, "series"), _hit(episode, "Oči Wakandy", 2025, "series")]
    item = _item(Type="Series", Name="Eyes of Wakanda", ProductionYear=2025, Path="/Movies/Serials/Eyes of Wakanda")
    assert [h.url for h in ai_candidates(_Csfd(hits, {}), item)] == [series]


def test_option_keys_are_unique_even_when_csfd_ids_repeat():
    same_id = [_hit(URL_A), _hit("https://www.csfd.sk/film/100-a-remaster/prehlad/")]
    films = {URL_A: _film(), "https://www.csfd.sk/film/100-a-remaster/prehlad/": _film(title="Other")}
    jev = _Jev(ChoiceAnswer("c2", 0.95))
    film, _ = ai_pick(_Csfd(same_id, films), jev, _item())
    assert film.title == "Other" and set(jev.calls[0][2]) == {"c1", "c2", AI_NONE}


def test_item_fetch_requests_path_and_original_title(monkeypatch):
    """Regression 2026-09-23: Emby omits Path/OriginalTitle unless asked, so title_queries'
    folder and original-title searches never ran and only the Name was looked up."""
    assert {"Path", "OriginalTitle", "People"} <= set(cli.ITEM_EXTRA_FIELDS.split(","))
    seen: dict[str, str] = {}
    monkeypatch.setattr(cli, "fetch_items_by_ids",
                        lambda *a, fields, **k: seen.setdefault("ids", fields) and [])
    monkeypatch.setattr(cli, "fetch_items_with_genres",
                        lambda *a, extra_fields, **k: seen.setdefault("all", extra_fields) and [])
    cli._fetch_candidates(None, "http://emby", "u", ["lib"], Namespace(item_ids="1,2"))
    cli._fetch_candidates(None, "http://emby", "u", ["lib"], Namespace())
    assert "Path" in seen["ids"] and "OriginalTitle" in seen["ids"]
    assert "Path" in seen["all"] and "OriginalTitle" in seen["all"]


def test_strict_matcher_never_searches_the_folder_title():
    """Regression 2026-09-23: folder titles can name a different film than Emby's metadata
    (folder 'Peninsula (2020)', item 'Buklog: The Ritual System'), and strict matches are
    applied. The folder may only feed the reviewed AI path."""
    item = _item(Name="Buklog: The Ritual System", ProductionYear=2020,
                 Path="/Movies/Dokumenty/Peninsula (2020)/Peninsula (2020).mkv")
    peninsula = _film(title="Peninsula", year=2020, names=["Bando"])
    csfd = _Csfd([_hit(URL_A, "Peninsula", 2020)], {URL_A: peninsula})
    film, reason = cli.resolve_film(csfd, item, {})
    assert film is None and csfd.searched == ["Buklog: The Ritual System"]
    assert cli.title_queries(item) == ["Buklog: The Ritual System", "Peninsula"]  # AI path still sees it
    assert [h.url for h in ai_candidates(csfd, item)] == [URL_A]


def test_strict_matcher_uses_emby_original_title():
    item = _item(Name="Magical Albania", OriginalTitle="Zauberhaftes Albanien", ProductionYear=2016,
                 Path="/Movies/Dokumenty/Zauberhaftes Albanien (2016)/x.mkv")
    film = _film(title="Úžasná Albánie", year=2016, names=["Zauberhaftes Albanien"])
    csfd = _Csfd([_hit(URL_A, "Úžasná Albánie", 2016)], {URL_A: film})
    got, reason = cli.resolve_film(csfd, item, {})
    assert got is film and reason.endswith("via original title")
    assert "Zauberhaftes Albanien" in csfd.searched
