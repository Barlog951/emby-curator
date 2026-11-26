"""Cleanup deletes run in a safe order and are judged again against what is still on disk.

Emby's delete of one file can take part of another candidate in the same folder: its .nfo
and subtitles through the name-prefix rule, or subtitles Emby lists under both files. That
is harmless only once the other candidate is gone, so that candidate is deleted first, and
a delete is refused when the candidate it would reach failed or was held. A series delete
takes its whole folder, so every item under the folder must belong to that series.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from emby_dedupe.cli import cleanup as cli
from emby_dedupe.models.cleanup import CleanupCandidate

FOLDER = "/Movies/HD/Collection"
SHOWS = "/Movies/Serials/--- UKONCENE ---"


def _film(item_id, file_name, collection=None, folder=FOLDER):
    return CleanupCandidate(
        item_id=item_id, name=file_name.rsplit(".", 1)[0], year=2001, rating=4.0, critic_rating=None,
        threshold=6.0, age_years=5.0, library="HD", size_bytes=2**30, path=f"{folder}/{file_name}",
        collection=collection, collection_size=2 if collection else 0,
    )


def _show(item_id, folder):
    return CleanupCandidate(
        item_id=item_id, name=folder.rsplit("/", 1)[-1], year=2001, rating=4.0, critic_rating=None,
        threshold=6.0, age_years=5.0, library="Serials", size_bytes=2**30, path=folder,
    )


def _run(candidates, fail=(), label="movies", owners=None, complete=True):
    """Run the real delete loop with every path known; return the ids Emby was asked to delete."""
    paths = [c.path for c in candidates]

    def delete(client, base_url, item_id, **kw):
        return {"status": "failed" if item_id in fail else "success"}

    with patch.object(cli, "fetch_library_paths", return_value=(paths, complete)), \
         patch.object(cli, "fetch_library_owners", return_value=(owners or {}, complete)), \
         patch.object(cli, "delete_item", side_effect=delete) as deleted:
        cli._perform_deletions(MagicMock(), "http://emby", candidates, "u", "p", "k", label)
    return [call.args[2] for call in deleted.call_args_list]


def _error(candidate):
    return candidate.deletion_result.get("error", "")


@pytest.mark.parametrize("reverse", [False, True])
def test_a_delete_that_would_take_a_candidates_sidecars_runs_after_it(reverse):
    """Deleting "X.mkv" removes every "X*" file, so "X CZ.mkv" goes first; both go."""
    films = [_film("x", "X (2001).mkv"), _film("xcz", "X (2001) CZ.mkv")]
    assert _run(films[::-1] if reverse else films) == ["xcz", "x"]


@pytest.mark.parametrize("reverse", [False, True])
def test_a_failed_delete_stops_the_delete_that_would_take_its_sidecars(reverse):
    films = [_film("x", "X (2001).mkv"), _film("xcz", "X (2001) CZ.mkv")]
    assert _run(films[::-1] if reverse else films, fail=("xcz",)) == ["xcz"]
    assert films[0].deletion_result["status"] == "skipped_unsafe"
    assert _error(films[0]).startswith("re-check:")
    assert "name-prefix" in _error(films[0])


@pytest.mark.parametrize("reverse", [False, True])
def test_two_unrelated_films_in_one_folder_both_go(reverse):
    films = [_film("a", "Alpha (2001).mkv"), _film("b", "Beta (2002).mkv")]
    ordered = films[::-1] if reverse else films
    assert _run(ordered) == [c.item_id for c in ordered]


@pytest.mark.parametrize("reverse", [False, True])
def test_a_pair_that_would_each_reach_the_other_is_kept(reverse):
    """Deliberate over-refusal: neither "T (2001).mkv" nor "T (2001).CZ.mkv" can go first.

    T's delete removes every "T (2001)*" file (name-prefix), and Emby lists
    "T (2001).CZ.cs.srt" under T as well, so CZ's delete takes a subtitle T shows
    (sidecar-owner). Whichever goes first harms the other if the second delete fails.
    """
    films = [_film("t", "T (2001).mkv"), _film("tcz", "T (2001).CZ.mkv")]
    assert _run(films[::-1] if reverse else films) == []
    assert _error(films[0]).startswith("delete-order:")
    assert "name-prefix" in _error(films[0])
    assert _error(films[1]).startswith("delete-order:")
    assert "sidecar-owner" in _error(films[1])


def test_a_part_held_after_its_collection_failed_stops_the_delete_that_would_reach_it():
    """"X CZ" belongs to a collection whose other part fails first, so "X CZ" stays held,
    and "X" (whose delete would take X CZ's sidecars) must stay too."""
    other = _film("other", "Other (2003).mkv", collection="Saga", folder="/Movies/HD/Other (2003)")
    x = _film("x", "X (2001).mkv")
    xcz = _film("xcz", "X (2001) CZ.mkv", collection="Saga")
    assert _run([other, x, xcz], fail=("other",)) == ["other"]
    assert xcz.deletion_result["error"].startswith("collection kept: deleting")
    assert _error(x).startswith("re-check:")


def test_a_part_already_gone_when_its_collection_fails_lets_the_rest_finish():
    """In the other order the part goes first; its collection fails later, which cannot be
    undone, and the delete that waited for the part is then safe."""
    other = _film("other", "Other (2003).mkv", collection="Saga", folder="/Movies/HD/Other (2003)")
    x = _film("x", "X (2001).mkv")
    xcz = _film("xcz", "X (2001) CZ.mkv", collection="Saga")
    assert _run([xcz, x, other], fail=("other",)) == ["xcz", "x", "other"]


def test_a_failed_delete_leaves_a_survivor_for_its_folder_neighbour():
    """Planned as if both go; once "Alpha" fails it stays, and the guard judges "Beta" with it."""
    films = [_film("a", "Alpha (2001).mkv"), _film("b", "Beta (2002).mkv")]
    assert _run(films, fail=("a",)) == ["a"]
    assert _error(films[1]).startswith("re-check:")


def test_a_cycle_inside_a_collection_holds_the_whole_collection_before_any_delete():
    t = _film("t", "T (2001).mkv", collection="Saga")
    tcz = _film("tcz", "T (2001).CZ.mkv")
    part = _film("part", "Part (2004).mkv", collection="Saga", folder="/Movies/HD/Part (2004)")
    assert _run([part, t, tcz]) == []
    assert part.deletion_result["error"].startswith("collection kept whole")


# ---- series -----------------------------------------------------------------------

def _episodes(folder, series_id, *names):
    return {f"{folder}/S01/{n}.mkv": series_id for n in names}


def test_a_series_alone_in_its_folder_is_deleted():
    show = _show("s1", f"{SHOWS}/Show (2001)")
    owners = _episodes(show.path, "s1", "Show S01E01", "Show S01E02")
    assert _run([show], label="series", owners=owners) == ["s1"]


def test_a_series_folder_holding_a_twin_series_item_is_refused():
    show = _show("s1", f"{SHOWS}/Twin (2005)")
    owners = {**_episodes(show.path, "s1", "Twin S01E01"), **_episodes(show.path, "s2", "Twin S01E01 (2)")}
    assert _run([show], label="series", owners=owners) == []
    assert _error(show).startswith("series-folder-shared:")


@pytest.mark.parametrize("reverse", [False, True])
def test_twin_series_items_that_are_both_candidates_are_both_kept(reverse):
    """Deliberate over-refusal: each twin's folder holds the other's episodes."""
    first, second = _show("s1", f"{SHOWS}/Twin (2005)"), _show("s2", f"{SHOWS}/Twin (2005)")
    owners = {**_episodes(first.path, "s1", "Twin S01E01"), **_episodes(first.path, "s2", "Twin S01E01 (2)")}
    shows = [second, first] if reverse else [first, second]
    assert _run(shows, label="series", owners=owners) == []
    assert all(_error(s).startswith("series-folder-shared:") for s in shows)


@pytest.mark.parametrize("reverse", [False, True])
def test_twin_series_records_listing_the_same_files_are_both_refused(reverse):
    """The live shape: two Series items for one folder list every file once each, so the
    real listing gives those files no single owner, and neither twin's delete is cleared."""
    from emby_dedupe.api.client import fetch_library_owners
    from emby_dedupe.models.cleanup import SeriesCleanupCandidate

    folder = f"{SHOWS}/Into the West (2005)"
    files = [f"{folder}/S01/Into the West (2005) S01E0{n}.mkv" for n in (1, 2)]
    rows = [{"Path": p, "SeriesId": sid} for sid in ("805", "807") for p in files]
    page = MagicMock()
    page.json.return_value = {"Items": rows[::-1] if reverse else rows, "TotalRecordCount": len(rows)}
    with patch("emby_dedupe.api.pagination.make_http_request", side_effect=[page]):
        owners, complete = fetch_library_owners(MagicMock(), "http://emby")
    twins = [
        SeriesCleanupCandidate(item_id=sid, name="Into the West", year=2005, rating=4.0, critic_rating=None,
                               threshold=6.0, stale_years=5.0, last_episode_added=None, episode_count=2,
                               library="Serials", size_bytes=2**30, path=folder)
        for sid in ("805", "807")
    ]
    assert _run(twins, label="series", owners=owners, complete=complete) == []
    assert all(_error(t).startswith("series-folder-shared:") for t in twins)


def test_a_series_folder_holding_a_movie_is_refused():
    show = _show("s1", f"{SHOWS}/Show (2001)")
    owners = {**_episodes(show.path, "s1", "Show S01E01"), f"{show.path}/Show - The Movie (2003).mkv": None}
    assert _run([show], label="series", owners=owners) == []


def test_a_series_folder_holding_an_episode_without_a_series_is_refused():
    show = _show("s1", f"{SHOWS}/Show (2001)")
    owners = {**_episodes(show.path, "s1", "Show S01E01"), **_episodes(show.path, None, "Show S01E02")}
    assert _run([show], label="series", owners=owners) == []


def test_an_incomplete_listing_refuses_every_series_delete():
    show = _show("s1", f"{SHOWS}/Show (2001)")
    owners = _episodes(show.path, "s1", "Show S01E01")
    assert _run([show], label="series", owners=owners, complete=False) == []
    assert _error(show).startswith("library-incomplete:")
