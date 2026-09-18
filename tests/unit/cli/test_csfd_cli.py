"""Tests for emby_dedupe.cli.csfd — candidate selection, planning, payload, apply."""
from __future__ import annotations

import json
from argparse import Namespace

import httpx

from emby_dedupe.api.csfd import CsfdCreator, CsfdFilm, CsfdHit
from emby_dedupe.cli import csfd as cli
from emby_dedupe.cli.csfd import (
    ItemPlan,
    build_payload,
    is_candidate,
    load_manual_map,
    missing_fields,
    plan_item,
    resolve_film,
    title_queries,
)


def _item(**over):
    base = {"Id": "1", "Type": "Movie", "Name": "Tatranský durič", "ProductionYear": 2026,
            "Path": "/Movies/Dokumenty/Tatransky duric (2026)/Tatransky duric (2026) - 1080p.mkv",
            "ProviderIds": {}, "ImageTags": {}, "Genres": [], "Overview": "", "LockedFields": [],
            "People": [{"Name": "Some Actor", "Type": "Actor"}]}
    base.update(over)
    return base


def _film(**over):
    base = dict(url="https://www.csfd.sk/film/1885748-x/prehlad/", csfd_id="1885748",
                title="Tatranský durič", year=2026, countries=["Slovensko"],
                genres_sk=["Dokumentárny"], plot="Plot.", rating_pct=87,
                poster_url="https://image.pmgstatic.com/p.jpg")
    base.update(over)
    return CsfdFilm(**base)


def test_missing_fields_and_candidate_rules():
    assert missing_fields(_item()) == ["poster", "overview", "genres"]
    full = _item(ImageTags={"Primary": "t"}, Overview="x", Genres=["Documentary"])
    assert missing_fields(full) == []
    assert is_candidate(_item(), only_unmatched=False)
    assert is_candidate(full, only_unmatched=False)                       # no provider id
    assert not is_candidate(_item(ProviderIds={"Tmdb": "5"}, ImageTags={"Primary": "t"},
                                  Overview="x", Genres=["D"]), only_unmatched=False)
    assert is_candidate(_item(ProviderIds={"Tmdb": "5"}), only_unmatched=False)   # matched, no poster
    assert not is_candidate(_item(ProviderIds={"Tmdb": "5"}), only_unmatched=True)
    assert is_candidate(_item(ProviderIds={"Csfd": "9"}), only_unmatched=False)   # stamped but still has gaps
    assert not is_candidate(_item(Type="Episode"), only_unmatched=False)


def test_title_queries_use_name_folder_title_and_original_title_once():
    item = _item(Name="Zkaza SOC", OriginalTitle="Zkaza SOC",
                 Path="/Movies/Dokumenty/Zkaza Svetoveho obchodniho centra (2021) - 720p/Z.mkv")
    assert title_queries(item) == ["Zkaza SOC", "Zkaza Svetoveho obchodniho centra"]
    series = _item(Type="Series", Name="Na telo", Path="/Movies/Serials/Na telo (2026)")
    assert title_queries(series) == ["Na telo"]


def test_plan_item_fills_only_empty_fields():
    plan = plan_item(_item(), _film())
    assert plan.fields == {"Overview": "Plot.", "Genres": ["Documentary"], "CommunityRating": 8.7}
    assert plan.poster is True and plan.has_changes
    kept = _item(Overview="Existing", Genres=["Drama"], ImageTags={"Primary": "t"},
                 CommunityRating=6.1, ProductionYear=None)
    plan2 = plan_item(kept, _film(year=2024))
    assert plan2.fields == {"ProductionYear": 2024} and plan2.poster is False
    bare = plan_item(kept, _film(year=None, poster_url=None))
    assert bare.fields == {} and bare.poster is False
    assert bare.has_changes                      # still worth a write: the Csfd id stamp
    assert cli._describe(bare) == "csfd id only"
    assert not ItemPlan("1", "x").has_changes    # unmatched: nothing to write


def test_build_payload_sets_fields_locks_and_provider_id():
    item = _item(LockedFields=["Name"])
    plan = plan_item(item, _film())
    payload = build_payload(item, plan)
    assert payload["Overview"] == "Plot." and payload["Genres"] == ["Documentary"]
    assert payload["GenreItems"] == [{"Name": "Documentary", "Id": ""}]
    assert payload["CommunityRating"] == 8.7
    assert payload["ProviderIds"] == {"Csfd": "1885748"}
    assert payload["LockedFields"] == ["Name", "Overview", "Genres"]
    assert item["LockedFields"] == ["Name"]            # input not mutated


def test_load_manual_map_parses_tsv(tmp_path):
    f = tmp_path / "map.tsv"
    f.write_text("# id\turl\n\n12\thttps://www.csfd.sk/film/1-x/\nbad line\n", encoding="utf-8")
    assert load_manual_map(str(f)) == {"12": "https://www.csfd.sk/film/1-x/"}
    assert load_manual_map(None) == {}


class _FakeCsfd:
    def __init__(self, hits, film):
        self.hits, self._film, self.searched, self.fetched = hits, film, [], []

    def search(self, query):
        self.searched.append(query)
        return self.hits

    def film(self, url):
        self.fetched.append(url)
        return self._film

    def fetch_poster(self, url):
        return b"\xff\xd8", "image/jpeg"


def test_resolve_film_prefers_manual_map_then_strict_search():
    film = _film()
    fake = _FakeCsfd([CsfdHit(film.url, "Tatranský durič", 2026, "film")], film)
    got, reason = resolve_film(fake, _item(), {"1": "https://www.csfd.sk/film/manual/"})
    assert got is film and reason == "manual map" and fake.fetched == ["https://www.csfd.sk/film/manual/"]
    got, reason = resolve_film(fake, _item(), {})
    assert got is film and reason == "matched 'Tatranský durič'"
    none, reason = resolve_film(_FakeCsfd([], film), _item(), {})
    assert none is None and "no unambiguous" in reason


def test_run_fill_dry_run_and_doit_end_to_end(tmp_path, monkeypatch):
    """Dry run posts nothing; --doit posts the item update and the poster."""
    item = _item()
    posted: list[tuple[str, bytes]] = []

    def emby(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append((request.url.path, request.content))
            return httpx.Response(204)
        return httpx.Response(200, json={"Items": [item], "TotalRecordCount": 1})

    film = _film()
    fake = _FakeCsfd([CsfdHit(film.url, "Tatranský durič", 2026, "film")], film)
    monkeypatch.setattr(cli, "CsfdClient", lambda *a, **k: fake)
    monkeypatch.setattr(cli, "load_csfd_cache", lambda: {})
    monkeypatch.setattr(cli, "save_csfd_cache", lambda cache: None)
    monkeypatch.setattr(cli, "fetch_items_with_genres", lambda *a, **k: [item])
    client = httpx.Client(transport=httpx.MockTransport(emby))
    report = tmp_path / "r.tsv"
    args = Namespace(doit=False, flaresolverr_url="http://fs/v1", report=str(report),
                     library=["Dokumenty"], all_libraries=False)
    cli._run_fill(client, "http://emby:8096", "u", ["lib"], args)
    assert posted == []
    assert "matched 'Tatranský durič'" in report.read_text(encoding="utf-8")

    args.doit = True
    cli._run_fill(client, "http://emby:8096", "u", ["lib"], args)
    assert [p for p, _ in posted] == ["/Items/1", "/Items/1/Images/Primary"]
    body = json.loads(posted[0][1])
    assert body["Overview"] == "Plot." and body["ProviderIds"]["Csfd"] == "1885748"


def test_item_plan_describe_and_apply_failure_path():
    plan = ItemPlan("1", "X", film=_film(), fields={"Overview": "long"}, poster=True)
    assert cli._describe(plan) == "Overview=…, poster"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert cli._apply(client, "http://emby:8096", _FakeCsfd([], _film()), _item(), plan) is False


def test_run_fill_saves_cache_periodically(monkeypatch):
    items = [_item(Id=str(i), Name=f"T{i}") for i in range(1, 22)]
    saves: list[int] = []
    monkeypatch.setattr(cli, "CACHE_SAVE_EVERY", 10)
    monkeypatch.setattr(cli, "CsfdClient", lambda *a, **k: _FakeCsfd([], _film()))
    monkeypatch.setattr(cli, "load_csfd_cache", lambda: {})
    monkeypatch.setattr(cli, "save_csfd_cache", lambda cache: saves.append(1))
    monkeypatch.setattr(cli, "fetch_items_with_genres", lambda *a, **k: items)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"Items": []})))
    cli._run_fill(client, "http://emby:8096", "u", ["lib"],
                  Namespace(doit=False, flaresolverr_url="http://fs/v1", report=None))
    assert len(saves) == 3   # after item 10, item 20, and the final save


def test_resolve_film_second_pass_verifies_original_title_on_page():
    """Search shows the localized title; the page lists the original title we hold."""
    localized = _film(title="Řecko z ptačí perspektivy", names=["Aerial Greece"], year=2021)
    hits = [CsfdHit(localized.url, "Řecko z ptačí perspektivy", 2021, "series"),
            CsfdHit("https://www.csfd.sk/film/2-krasy/prehlad/", "Krásy Řecka", 2013, "series")]
    fake = _FakeCsfd(hits, localized)
    item = _item(Type="Series", Name="Aerial Greece", ProductionYear=2021,
                 Path="/Movies/Dokumenty/Aerial Greece (2021)")
    got, reason = resolve_film(fake, item, {})
    assert got is localized and reason == "verified 'Aerial Greece' via original title"
    assert fake.fetched == [localized.url]          # only the same-year hit was fetched
    # a same-year hit whose page does NOT list our title is rejected
    other = _film(title="Něco jiného", names=["Something Else"], year=2021)
    got, reason = resolve_film(_FakeCsfd(hits, other), item, {})
    assert got is None and "no unambiguous" in reason


def test_plan_item_overwrite_poster_replaces_existing_art():
    item = _item(ImageTags={"Primary": "frame"}, Overview="x", Genres=["Documentary"])
    assert plan_item(item, _film()).poster is False                      # default: keep what is there
    assert plan_item(item, _film(), overwrite_poster=True).poster is True
    assert plan_item(item, _film(poster_url=None), overwrite_poster=True).poster is False


def test_cast_gap_fills_people_and_locks_cast():
    film = _film(directors=["Pavol Baláž"], cast=[["Peter Rúfus", "rozprávač"], ["Jana Nová", ""], ["Adolf Hitler", "a.z."]])
    item = _item(People=[])
    assert "cast" in missing_fields(item)
    plan = plan_item(item, film)
    assert plan.fields["People"] == [{"Name": "Pavol Baláž", "Type": "Director"},
                                     {"Name": "Peter Rúfus", "Type": "Actor", "Role": "rozprávač"},
                                     {"Name": "Jana Nová", "Type": "Actor"},
                                     {"Name": "Adolf Hitler", "Type": "Actor", "Role": "archívne zábery"}]
    assert "Cast" not in build_payload(item, plan)["LockedFields"]   # no such lock enum in Emby
    has_cast = _item(People=[{"Name": "X", "Type": "Actor"}])
    assert "People" not in plan_item(has_cast, film).fields               # existing cast kept


def test_stamped_items_are_recandidated_only_when_gaps_remain_and_use_stored_id():
    done = _item(ProviderIds={"Csfd": "9"}, ImageTags={"Primary": "t"}, Overview="x", Genres=["D"],
                 People=[{"Name": "X", "Type": "Actor"}])
    assert not is_candidate(done, only_unmatched=False)
    gap = _item(ProviderIds={"Csfd": "1885748"}, ImageTags={"Primary": "t"}, Overview="x", Genres=["D"], People=[])
    assert is_candidate(gap, only_unmatched=False)
    fake = _FakeCsfd([], _film())
    got, reason = resolve_film(fake, gap, {})
    assert reason == "stored csfd id" and fake.fetched == ["https://www.csfd.sk/film/1885748/prehlad/"]
    assert fake.searched == []


def test_photo_candidates_are_referenced_photoless_actors_most_used_first():
    items = [_item(Id="1", People=[{"Id": "p1", "Name": "A", "Type": "Actor"}, {"Id": "p2", "Name": "B", "Type": "Actor"},
                                   {"Id": "d1", "Name": "D", "Type": "Director"}]),
             _item(Id="2", People=[{"Id": "p1", "Name": "A", "Type": "Actor"}, {"Id": "p3", "Name": "C", "Type": "Actor"}])]
    persons = {"p1": {"Id": "p1", "Name": "A", "ImageTags": {}},
               "p2": {"Id": "p2", "Name": "B", "ImageTags": {"Primary": "x"}, "Overview": "has a bio"},
               "p3": {"Id": "p3", "Name": "C", "ImageTags": {"Primary": "x"}}}
    assert cli.photo_candidates(items, persons, min_refs=1) == [
        {"Id": "p1", "Name": "A", "refs": 2, "gaps": ["photo", "bio"]}, {"Id": "p3", "Name": "C", "refs": 1, "gaps": ["bio"]}]
    assert cli.photo_candidates(items, persons, min_refs=2) == [{"Id": "p1", "Name": "A", "refs": 2, "gaps": ["photo", "bio"]}]


def test_run_people_uploads_only_matched_real_photos(tmp_path, monkeypatch):
    items = [_item(Id="1", People=[{"Id": "p1", "Name": "Milan Lasica", "Type": "Actor"},
                                   {"Id": "p2", "Name": "Milan Vašica", "Type": "Actor"},
                                   {"Id": "p3", "Name": "Milan Lasica", "Type": "Actor"}])]
    persons = {"p1": {"Id": "p1", "Name": "Milan Lasica", "ImageTags": {}}, "p2": {"Id": "p2", "Name": "Milan Vašica", "ImageTags": {}},
               "p3": {"Id": "p3", "Name": "Milan Lasica", "ImageTags": {"Primary": "tmdb"}}}   # has photo, no bio
    lasica = CsfdCreator("https://www.csfd.sk/tvorca/980/", "Milan Lasica", "herec", 1940, "https://image.pmgstatic.com/p.jpg")
    vasica = CsfdCreator("https://www.csfd.sk/tvorca/673248/", "Milan Vašica", "skladateľ", None, None)

    class FakePeople:
        def __init__(self, *a, **k):
            pass

        def search_creators(self, q):
            return [lasica] if "Lasica" in q else [vasica]

        def fetch_poster(self, url):
            return b"\xff\xd8", "image/jpeg"

        def creator(self, url):
            from emby_dedupe.api.csfd import CsfdCreatorProfile
            return CsfdCreatorProfile("1940-02-03", "Zvolen", "2021-07-18",
                                      "Narozen 3. února 1940 ve Zvolenu na Slovensku. Dramatik, prozaik a herec.")
    uploads: list[str] = []

    def emby(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            uploads.append(request.url.path)
            return httpx.Response(204)
        if "/Users/" in request.url.path:                       # fetch_full_item for the bio
            pid = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"Id": pid, "Name": "Milan Lasica", "Overview": ""})
        return httpx.Response(200, json={"Items": list(persons.values())})
    monkeypatch.setattr(cli, "CsfdClient", FakePeople)
    monkeypatch.setattr(cli, "load_csfd_cache", lambda: {})
    monkeypatch.setattr(cli, "save_csfd_cache", lambda cache: None)
    monkeypatch.setattr(cli, "fetch_items_with_genres", lambda *a, **k: items)
    client = httpx.Client(transport=httpx.MockTransport(emby))
    report = tmp_path / "people.tsv"
    args = Namespace(doit=True, flaresolverr_url="http://fs/v1", report=str(report), min_refs=1, limit=None)
    cli._run_people(client, "http://emby:8096", "u", ["lib"], args)
    # p1: portrait then bio; p2: nothing (no photo, no bio on ČSFD); p3: bio only — its photo is kept
    assert uploads == ["/Items/p1/Images/Primary", "/Items/p1", "/Items/p3"]
    text = report.read_text(encoding="utf-8")
    assert "p1\tMilan Lasica\t1\tmatched+bio" in text and "p2\tMilan Vašica\t1\tno_photo" in text
    assert "p3\tMilan Lasica\t1\tmatched+bio" in text


def test_item_fetch_requests_people(monkeypatch):
    seen: dict = {}

    def fake_fetch(client, base_url, library_ids, user_id, extra_fields=""):
        seen["extra"] = extra_fields
        return []
    monkeypatch.setattr(cli, "fetch_items_with_genres", fake_fetch)
    args = Namespace(item_ids=None, only_unmatched=False, limit=None)
    assert cli._fetch_candidates(httpx.Client(), "http://emby:8096", "u", ["lib"], args) == []
    assert "People" in seen["extra"]


def test_person_updates_fill_only_empty_biographical_fields():
    from emby_dedupe.api.csfd import CsfdCreatorProfile
    profile = CsfdCreatorProfile("1940-02-03", "Zvolen, Slovenský štát", "2021-07-18",
                                 "Narozen 3. února 1940 ve Zvolenu na Slovensku. Dramatik, prozaik a herec.")
    empty = {"Overview": "", "PremiereDate": None, "EndDate": None, "ProductionLocations": []}
    assert cli.person_updates(empty, profile) == {
        "Overview": profile.bio, "PremiereDate": "1940-02-03T00:00:00.0000000Z",
        "EndDate": "2021-07-18T00:00:00.0000000Z", "ProductionLocations": ["Zvolen, Slovenský štát"]}
    filled = {"Overview": "TMDb bio", "PremiereDate": "1940-02-03T00:00:00Z", "EndDate": "x", "ProductionLocations": ["Zvolen"]}
    assert cli.person_updates(filled, profile) == {}
    short = CsfdCreatorProfile(bio="Too short.")
    assert cli.person_updates(empty, short) == {}
