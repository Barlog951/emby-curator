"""`genres origin` runner: dry run writes nothing, --doit only ever adds the genre."""
from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from emby_dedupe.api.csfd import CsfdError
from emby_dedupe.cli import genre_origin as go

GENRE = "Česko-slovenský"
CZ = {"Id": "1", "Type": "Movie", "Name": "Pelíšky", "Genres": ["Comedy", "Drama"], "ProviderIds": {"Tmdb": "10"}, "ProductionLocations": ["Czech Republic"]}
US = {"Id": "2", "Type": "Movie", "Name": "EuroTrip", "Genres": ["Comedy"], "ProviderIds": {"Tmdb": "20"}, "ProductionLocations": ["Czech Republic", "United States of America"]}
DONE = {"Id": "3", "Type": "Movie", "Name": "Kolja", "Genres": ["Drama", GENRE], "ProviderIds": {"Tmdb": "30"}}
TMDB = {"10": {"lang": "cs", "countries": ["CZ"]}, "20": {"lang": "en", "countries": ["CZ", "US"]}}


@pytest.fixture
def env(mocker):
    mocker.patch.object(go, "load_genre_cache", return_value={})
    mocker.patch.object(go, "save_genre_cache")
    mocker.patch.object(go, "load_csfd_cache", return_value={})
    save_csfd = mocker.patch.object(go, "save_csfd_cache")
    mocker.patch.object(go, "fetch_tmdb_origin", side_effect=lambda _c, _l, tmdb_id, _cache, _type: TMDB.get(tmdb_id))
    mocker.patch.object(go, "fetch_items_with_genres", return_value=[CZ, US, DONE, CZ])  # CZ again via a collection
    full = {"Id": "1", "Name": "Pelíšky", "Genres": ["Comedy", "Drama", "Family"], "Overview": "o"}
    mocker.patch.object(go, "fetch_full_item", return_value=full)
    update = mocker.patch.object(go, "update_item_genres", return_value=True)
    return Namespace(update=update, save_csfd=save_csfd, full=full)


def _args(doit: bool) -> Namespace:
    return Namespace(doit=doit, lock=True, genre_name=GENRE, tmdb_api_key="k", flaresolverr_url="http://fs")


def test_dry_run_never_writes(env, capsys):
    go.run_origin(MagicMock(), "http://emby", "u", ["lib"], _args(False))
    env.update.assert_not_called()
    out = capsys.readouterr().out
    assert "would add: Pelíšky" in out and "EuroTrip" not in out
    assert "1 of 3 movies/series already have it" in out


def test_doit_adds_to_the_current_full_item_genres(env):
    go.run_origin(MagicMock(), "http://emby", "u", ["lib"], _args(True))
    env.update.assert_called_once()
    _client, _url, item_id, full_item, genres = env.update.call_args.args
    assert item_id == "1" and full_item is env.full
    # the fresh full item's genres (incl. "Family", absent from the batch fetch) are all kept
    assert genres == ["Comedy", "Drama", "Family", GENRE]
    assert env.update.call_args.kwargs == {"lock": True}


def test_csfd_only_title_uses_cache_then_fetch_and_gives_up_after_repeated_failures(mocker, env):
    items = [{"Id": str(i), "Type": "Movie", "Name": f"doc {i}", "Genres": [], "ProviderIds": {"Csfd": str(i)}} for i in range(6)]
    mocker.patch.object(go, "fetch_items_with_genres", return_value=items)
    mocker.patch.object(go, "load_csfd_cache", return_value={"film:u": {"csfd_id": "0", "countries": ["Slovensko"]}})
    film = mocker.patch.object(go.CsfdClient, "film", side_effect=CsfdError("FlareSolverr down"))
    go.run_origin(MagicMock(), "http://emby", "u", ["lib"], _args(True))
    assert [c.args[2] for c in env.update.call_args_list] == ["0"]  # the cached Slovak doc
    assert film.call_count == go._CSFD_MAX_FAILURES  # not one 75-second timeout per title
    env.save_csfd.assert_not_called()  # nothing new fetched: don't rewrite the shared 16 MB cache


def test_movies_and_series_but_never_episodes_with_item_ids(mocker, env):
    episode = {"Id": "9", "Type": "Episode", "Name": "e", "ProviderIds": {"Tmdb": "10"}}
    series = {"Id": "5", "Type": "Series", "Name": "Arabela", "Genres": ["Family"], "ProviderIds": {"Tmdb": "10"}}
    by_ids = mocker.patch.object(go, "fetch_items_by_ids", return_value=[CZ, episode, series])
    go.run_origin(MagicMock(), "http://emby", "u", [], _args(True), item_ids=["1", "9", "5"])
    assert by_ids.call_args.kwargs["fields"] == go._ORIGIN_FIELDS
    assert [c.args[2] for c in env.update.call_args_list] == ["1", "5"]
    # a series is looked up on TMDb's TV endpoint
    assert [c.args[-1] for c in go.fetch_tmdb_origin.call_args_list] == ["movie", "tv"]


def test_tagged_titles_the_rules_no_longer_back_are_listed_not_removed(mocker, env, capsys):
    """e.g. Maska (2005): Emby filed it as a Czech documentary, it got the genre, then got re-identified."""
    reidentified = {"Id": "7", "Type": "Movie", "Name": "Son of the Mask", "Genres": ["Comedy", GENRE], "ProviderIds": {"Tmdb": "20"}}
    mocker.patch.object(go, "fetch_items_with_genres", return_value=[reidentified, DONE])
    TMDB["30"] = {"lang": "cs", "countries": ["CZ"]}
    try:
        go.run_origin(MagicMock(), "http://emby", "u", ["lib"], _args(True))
    finally:
        del TMDB["30"]
    out = capsys.readouterr().out
    assert "1 title(s) have" in out and "check: Son of the Mask" in out and "Kolja" not in out
    env.update.assert_not_called()


def test_missing_tmdb_key_exits(mocker, env):
    mocker.patch.object(go, "get_env_variable", return_value=None)
    with pytest.raises(SystemExit):
        go.run_origin(MagicMock(), "http://emby", "u", ["lib"], Namespace(**{**vars(_args(True)), "tmdb_api_key": None}))


def test_a_refused_update_counts_as_an_error(env, capsys):
    env.update.return_value = False
    go.run_origin(MagicMock(), "http://emby", "u", ["lib"], _args(True))
    assert "added 0, not CZ/SK 1, errors 1" in capsys.readouterr().out
