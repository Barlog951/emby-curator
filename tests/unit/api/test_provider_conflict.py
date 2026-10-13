"""Two different films that share one provider id are never paired (2026-10-13).

TVDB gave Squared Love Everlasting and Squared Love All Over Again (2023) the same id
348427, so dedupe grouped them although their IMDb and TMDb ids differ; only the runtime
gap kept the delete back. Films get no frame check, so two such films of the same length
would have lost one of them.
"""

from __future__ import annotations

from emby_dedupe.api import deduplication as dd
from emby_dedupe.api.client import _process_all_provider_ids
from emby_dedupe.utils.providers import provider_id_conflict

EVERLASTING = {"Tmdb": "1150215", "Imdb": "tt28496500", "Tvdb": "348427"}
ALL_OVER_AGAIN = {"Tmdb": "1074656", "Imdb": "tt26369131", "Tvdb": "348427"}


def _movie(item_id: str, ids: dict) -> dict:
    return {"Id": item_id, "ProviderIds": ids}


def _tables(*items: dict) -> dict:
    tables: dict = {"imdb": {}, "tmdb": {}, "tvdb": {}}
    for item in items:
        _process_all_provider_ids(item, tables, "HD")
    return tables


def test_different_imdb_or_tmdb_is_a_conflict():
    assert "imdb" in provider_id_conflict(EVERLASTING, ALL_OVER_AGAIN)
    assert "tmdb" in provider_id_conflict({"tmdb": "1"}, {"TMDB": "2"})


def test_an_id_only_one_side_has_is_no_conflict():
    assert provider_id_conflict({"Imdb": "tt1", "Tvdb": "5"}, {"Tmdb": "9", "Tvdb": "5"}) is None
    assert provider_id_conflict({"Imdb": "TT1 "}, {"imdb": "tt1"}) is None
    assert provider_id_conflict(None, {"Imdb": "tt1"}) is None


def test_films_sharing_only_a_tvdb_id_are_not_grouped():
    ds = dd.build_disjoint_set(_tables(_movie("a", EVERLASTING), _movie("b", ALL_OVER_AGAIN)))
    assert ds.find("a") != ds.find("b")


def test_real_copies_still_group_through_any_shared_id():
    copy_without_imdb = {"Tmdb": "1150215", "Tvdb": "348427"}
    ds = dd.build_disjoint_set(_tables(_movie("a", EVERLASTING), _movie("b", ALL_OVER_AGAIN),
                                       _movie("c", copy_without_imdb)))
    assert ds.find("a") == ds.find("c") and ds.find("b") != ds.find("a")


def test_the_guard_refuses_a_delete_between_different_titles():
    keeper = {"path": "/M/A (2023)/a.mkv", "runtime_ticks": 6007 * 10**7, "provider_ids": ALL_OVER_AGAIN}
    item = {"path": "/M/B (2023)/b.mkv", "runtime_ticks": 6007 * 10**7, "provider_ids": EVERLASTING}
    reason = dd._different_content_reason(item, keeper)
    assert reason and reason.startswith("provider-id-conflict")
    assert dd._frame_check_pairs([{"keep": {**keeper, "is_episode": True},
                                   "delete": [{**item, "is_episode": True}]}]) == ([], [])
