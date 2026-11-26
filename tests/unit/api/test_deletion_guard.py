"""Tests for the deletion safety guard (emby_dedupe/api/deletion_guard.py).

The guard refuses any Emby delete that would fold-delete a folder containing the
keeper — the exact failure that destroyed 19 keepers in the DV Profile-5 incident.
Scenarios below mirror the real 44-item baseline.
"""
from unittest.mock import MagicMock, patch

from emby_dedupe.api.deduplication import process_deletion_and_generate_report
from emby_dedupe.api.deletion_guard import (
    collect_known_paths,
    is_delete_safe,
)

# --- shorthands for the real layouts ------------------------------------------------
MOVIE_P5 = "/Movies/4K/Alexander (2025) - DoVi SK/Alexander (2025) - DoVi SK.mkv"
MOVIE_KEEP_SIBLING = "/Movies/4K/Alexander (2025) - DoVi SK [HDR10]/Alexander (2025) - DoVi SK [HDR10].mkv"
MOVIE_KEEP_COLOCATED = "/Movies/4K/Alexander (2025) - DoVi SK/Alexander (2025) - DoVi SK [HDR10].mkv"

EP_SUBFOLDER_P5 = "/Movies/Serials/Reacher/S03/Reacher S03E02 - DoVi/Reacher S03E02 - DoVi.mkv"
EP_KEEP_IN_SEASON = "/Movies/Serials/Reacher/S03/Reacher S03E02 - DoVi [HDR10].mkv"
EP_KEEP_COLOCATED = "/Movies/Serials/Reacher/S03/Reacher S03E02 - DoVi/Reacher S03E02 - DoVi [HDR10].mkv"

EP_LOOSE_P5 = "/Movies/Serials/Reacher/S03/Reacher S03E01 - DoVi.mkv"
# The keeper's name must not start with the duplicate's stem: Emby would also delete the
# keeper's sidecars, which the name-prefix rule refuses (tests at the end of this file).
EP_LOOSE_KEEP = "/Movies/Serials/Reacher/S03/Reacher S03E01 - HDR10.mkv"
EP_PREFIX_KEEP = "/Movies/Serials/Reacher/S03/Reacher S03E01 - DoVi [HDR10].mkv"
EP_SIBLING = "/Movies/Serials/Reacher/S03/Reacher S03E03 - DoVi [HDR10].mkv"


# --- the dangerous cases the guard MUST refuse (the 19 lost) -------------------------
def test_movie_keeper_colocated_in_dedicated_folder_is_unsafe():
    safe, reason = is_delete_safe(MOVIE_KEEP_COLOCATED, MOVIE_P5, [MOVIE_KEEP_COLOCATED, MOVIE_P5])
    assert safe is False
    assert "dedicated folder" in reason


def test_episode_keeper_colocated_in_subfolder_is_unsafe():
    paths = [EP_KEEP_COLOCATED, EP_SUBFOLDER_P5]
    safe, reason = is_delete_safe(EP_KEEP_COLOCATED, EP_SUBFOLDER_P5, paths)
    assert safe is False
    assert "dedicated folder" in reason


# --- the post-converter-fix safe layouts (keeper in a different folder) --------------
def test_movie_keeper_in_sibling_folder_is_safe():
    safe, reason = is_delete_safe(MOVIE_KEEP_SIBLING, MOVIE_P5, [MOVIE_KEEP_SIBLING, MOVIE_P5])
    assert safe is True
    assert "different folder" in reason


def test_episode_keeper_in_season_folder_p5_in_subfolder_is_safe():
    paths = [EP_KEEP_IN_SEASON, EP_SUBFOLDER_P5]
    safe, reason = is_delete_safe(EP_KEEP_IN_SEASON, EP_SUBFOLDER_P5, paths)
    assert safe is True
    assert "different folder" in reason


# --- the loose case: file-only delete, keeper survives, must be ALLOWED --------------
def test_loose_pair_in_shared_season_folder_is_safe():
    # other episodes present in the same season folder → Emby deletes only the file
    known = [EP_LOOSE_KEEP, EP_LOOSE_P5, EP_SIBLING]
    safe, reason = is_delete_safe(EP_LOOSE_KEEP, EP_LOOSE_P5, known)
    assert safe is True
    assert "shared folder" in reason


def test_loose_singleton_without_known_siblings_is_conservatively_refused():
    # only the pair itself is known in the folder → can't prove it's shared → refuse (safe default)
    known = [EP_LOOSE_KEEP, EP_LOOSE_P5]
    safe, _ = is_delete_safe(EP_LOOSE_KEEP, EP_LOOSE_P5, known)
    assert safe is False


# --- degenerate inputs ---------------------------------------------------------------
def test_missing_paths_do_not_crash():
    # An unknown DELETE path is REFUSED (changed 2026-08-20). Without it the guard cannot
    # evaluate any of its rules, so allowing was the guard silently switching itself off —
    # 3 of 15 deletes went through unguarded on the 2026-08-20 run because Emby returned
    # no Path. Refusing costs one deferred delete; allowing costs a keeper.
    assert is_delete_safe("", "", [])[0] is False
    assert is_delete_safe(MOVIE_KEEP_SIBLING, None, [])[0] is False
    # An unknown KEEPER path still allows: there is no identified keeper to protect, and
    # the delete path is known so the caller's other rules still applied.
    assert is_delete_safe(None, MOVIE_P5, [])[0] is True


def test_windows_style_paths_normalised():
    keep = r"C:\Media\Movie\Movie [HDR10].mkv"   # same folder as delete
    delete = r"C:\Media\Movie\Movie.mkv"
    safe, _ = is_delete_safe(keep, delete, [keep, delete])
    assert safe is False


# --- the fix: full library visibility must keep the refusal AND lift false positives ---
def test_dedicated_folder_still_refused_with_full_library_visibility():
    """SAFETY (the data-loss direction): even when ``known_paths`` is the *whole* library,
    a keeper co-located alone with the delete in a dedicated folder must STILL be refused.
    Extra paths in OTHER folders (and nested under the dedicated one) must never be
    mistaken for same-folder siblings → no under-refusal."""
    full_library = [
        MOVIE_KEEP_COLOCATED, MOVIE_P5,                 # the dedicated pair
        MOVIE_KEEP_SIBLING,                             # a DIFFERENT folder
        EP_LOOSE_P5, EP_LOOSE_KEEP, EP_SIBLING,         # an unrelated season folder
        "/Movies/4K/Alexander (2025) - DoVi SK/extras/featurette.mkv",  # NESTED, must not count
    ]
    safe, reason = is_delete_safe(MOVIE_KEEP_COLOCATED, MOVIE_P5, full_library)
    assert safe is False
    assert "dedicated folder" in reason


def test_single_duplicate_season_folder_is_safe_with_library_siblings():
    """THE FIX (Dutton S01E07 / Proud S01E03): a loose duplicate pair is the only
    duplicated item in its season folder, so the decision paths alone make the folder
    look dedicated. With the season's other episodes visible (full library paths) the
    guard correctly sees a shared folder → file-only delete → safe."""
    known = [EP_LOOSE_KEEP, EP_LOOSE_P5, EP_SIBLING]  # EP_SIBLING only known via library paths
    safe, reason = is_delete_safe(EP_LOOSE_KEEP, EP_LOOSE_P5, known)
    assert safe is True
    assert "shared folder" in reason


# --- backstop: co-deleted siblings must not vouch a folder "shared" (Marty Supreme) ---
def test_marty_supreme_per_title_folder_refused_regardless_of_siblings():
    """The Marty Supreme data loss (2026-07-01): keeper (REMUX) co-located in a per-title
    folder `Marty Supreme (2025)/` with a P5 + HDR10. Emby fold-deletes the whole directory
    on ANY delete (log-proven), so every co-located delete must be refused — with OR
    without delete_paths, because the per-title trap check is unconditional."""
    keeper = "/Movies/4K/Marty Supreme (2025)/Marty Supreme (2025) - 2160p Remux DoVi CZ.mkv"
    p5 = "/Movies/4K/Marty Supreme (2025)/Marty Supreme (2025) - 2160p WEB-DL DoVi CZ.mkv"
    hdr10 = "/Movies/4K/Marty Supreme (2025)/Marty Supreme (2025) - 2160p WEB-DL DoVi CZ [HDR10].mkv"
    known = [keeper, p5, hdr10]
    safe, reason = is_delete_safe(keeper, p5, known, delete_paths=[p5, hdr10])
    assert safe is False
    assert "dedicated folder" in reason
    assert is_delete_safe(keeper, hdr10, known, delete_paths=[p5, hdr10])[0] is False
    # per-title trap is unconditional — refused even with no delete/run context at all
    assert is_delete_safe(keeper, p5, known)[0] is False


def test_backstop_refuses_non_per_title_folder_when_only_sibling_is_co_deleted():
    """Backstop for a NON-per-title container (folder not named for the movie): keeper
    co-located with a delete, and the only other sibling is ALSO a delete this run → no
    survivor remains → can't prove "shared" → refuse. Without the delete-set info the old
    guard let the co-deleted sibling vouch "shared" and wrongly allowed it."""
    keeper = "/Movies/4K/Collection/Movie A - 2160p.mkv"
    delete = "/Movies/4K/Collection/Movie A - 1080p.mkv"
    codel = "/Movies/4K/Collection/Movie B - 1080p.mkv"  # also being deleted → not a survivor
    known = [keeper, delete, codel]
    assert is_delete_safe(keeper, delete, known, delete_paths=[delete, codel])[0] is False
    assert is_delete_safe(keeper, delete, known)[0] is True  # legacy over-allow the backstop closes


def test_surviving_non_deleted_sibling_still_marks_folder_shared():
    """A neighbour that is NOT being deleted still proves a shared folder → file-only
    delete allowed — BUT only in a non-per-title container (a season folder here, so the
    per-title trap check doesn't fire). The backstop only excludes co-deleted siblings."""
    keeper = "/Movies/Serials/Show/S01/Show S01E01 - 2160p.mkv"
    delete = "/Movies/Serials/Show/S01/Show S01E01 - 1080p.mkv"
    survivor = "/Movies/Serials/Show/S01/Show S01E02 - 2160p.mkv"  # not being deleted
    known = [keeper, delete, survivor]
    safe, reason = is_delete_safe(keeper, delete, known, delete_paths=[delete])
    assert safe is True
    assert "shared folder" in reason


def test_per_title_movie_folder_refused_even_with_surviving_sibling():
    """The Marty class, fully closed: Emby fold-deletes a per-title movie folder on ANY
    delete regardless of what survives in it (log-proven 2026-07-01). A keeper co-located
    in a folder NAMED for the movie must be refused even when a non-deleted sibling is
    present — the surviving-sibling 'shared' test must NOT apply to per-title folders."""
    keeper = "/Movies/4K/Movie (2020)/Movie (2020) - 2160p REMUX.mkv"   # kept
    delete = "/Movies/4K/Movie (2020)/Movie (2020) - 1080p.mkv"          # deleted
    survivor = "/Movies/4K/Movie (2020)/Movie (2020) - 720p.mkv"         # NOT deleted this run
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, survivor], delete_paths=[delete])
    assert safe is False
    assert "dedicated folder" in reason


# --- collect_known_paths -------------------------------------------------------------
def test_collect_delete_paths_gathers_only_deletes():
    from emby_dedupe.api.deletion_guard import collect_delete_paths
    decisions = [
        {"keep": {"path": "/a/keep.mkv"}, "delete": [{"path": "/a/d1.mkv"}, {"path": "/a/d2.mkv"}]},
        {"keep": {"path": "/b/keep2.mkv"}, "delete": [{"path": None}]},
    ]
    assert set(collect_delete_paths(decisions)) == {"/a/d1.mkv", "/a/d2.mkv"}


def test_collect_known_paths_gathers_keep_and_delete():
    decisions = [
        {"keep": {"path": "/a/keep1.mkv"}, "delete": [{"path": "/a/del1.mkv"}, {"path": "/a/del2.mkv"}]},
        {"keep": {"path": "/b/keep2.mkv"}, "delete": [{"path": None}]},
        {"keep": {}, "delete": []},
    ]
    paths = collect_known_paths(decisions)
    assert set(paths) == {"/a/keep1.mkv", "/a/del1.mkv", "/a/del2.mkv", "/b/keep2.mkv"}


_HOUR_TICKS = 3600 * 10_000_000


def _probed(decisions):
    """Give every copy a running time, as Emby does once it has probed a file.

    Unprobed copies (no runtime) are refused by the content guard since 2026-10-12, which
    is not what these tests are about.
    """
    for decision in decisions:
        for item in [decision["keep"], *decision["delete"]]:
            item.setdefault("runtime_ticks", _HOUR_TICKS)
    return decisions


# --- integration: the guard must stop the actual Emby delete call (the incident) -----
def test_process_deletion_skips_colocated_delete_but_runs_safe_one():
    """Regression for the DV-P5 data loss: a co-located keeper must NOT be deleted,
    while a duplicate whose keeper lives in a different folder still is."""
    decisions = [
        {  # UNSAFE: keeper co-located with the P5 in one dedicated movie folder
            "keep": {"id": "kA", "name": "A", "path": "/Movies/4K/Movie/Movie [HDR10].mkv"},
            "delete": [{"id": "dA", "name": "A p5", "path": "/Movies/4K/Movie/Movie.mkv"}],
        },
        {  # SAFE: keeper in a sibling folder, P5 in its own folder
            "keep": {"id": "kB", "name": "B", "path": "/Movies/4K/Film [HDR10]/Film [HDR10].mkv"},
            "delete": [{"id": "dB", "name": "B p5", "path": "/Movies/4K/Film/Film.mkv"}],
        },
    ]
    _probed(decisions)
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        mock_del.return_value = {"id": "dB", "status": "success", "error": None}
        result = process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k",
            None, collect_known_paths(decisions),
        )

    assert result == "RPT"
    # delete_item was called for the SAFE duplicate only (positional item_id == args[2])
    called_ids = [call.args[2] for call in mock_del.call_args_list]
    assert called_ids == ["dB"]
    # the co-located one was refused by the guard, not deleted
    assert decisions[0]["delete"][0]["deletion_result"]["status"] == "skipped_unsafe"
    assert decisions[1]["delete"][0]["deletion_result"]["status"] == "success"


def _single_duplicate_season_decisions():
    """A loose duplicate pair that is the ONLY duplicated item in its season folder —
    the Dutton/Proud shape that the decision paths alone make look 'dedicated'."""
    return _probed([
        {
            "keep": {"id": "k", "name": "ep", "path": EP_LOOSE_KEEP},
            "delete": [{"id": "d", "name": "ep dup", "path": EP_LOOSE_P5}],
        },
    ])


def test_process_deletion_over_refuses_single_duplicate_folder_without_library_paths():
    """Regression guard for the bug: WITHOUT full library visibility the loose pair's
    folder looks dedicated and the safe file-only delete is wrongly skipped."""
    decisions = _single_duplicate_season_decisions()
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k"
        )
    mock_del.assert_not_called()
    assert decisions[0]["delete"][0]["deletion_result"]["status"] == "skipped_unsafe"


def test_process_deletion_library_paths_lift_single_duplicate_over_refusal():
    """THE FIX end-to-end: passing the season's other episodes (library_paths) makes the
    guard see a shared folder, so the previously-blocked safe delete now runs."""
    decisions = _single_duplicate_season_decisions()
    library_paths = [EP_LOOSE_KEEP, EP_LOOSE_P5, EP_SIBLING]  # EP_SIBLING = the neighbour
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        mock_del.return_value = {"id": "d", "status": "success", "error": None}
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k",
            None, library_paths,
        )
    assert [call.args[2] for call in mock_del.call_args_list] == ["d"]
    assert decisions[0]["delete"][0]["deletion_result"]["status"] == "success"


def test_process_deletion_backstop_protects_multi_version_movie_folder():
    """End-to-end backstop for the Marty Supreme loss: a REMUX keeper + P5 + HDR10 all in
    one movie folder, plus a 1080p in its own folder. The two co-located deletes must be
    REFUSED (each other's only sibling is also a delete → folder is effectively dedicated →
    Emby would fold-delete the keeper); the isolated 1080p in a different folder still deletes."""
    folder = "/Movies/4K/Marty Supreme (2025)"
    keeper = f"{folder}/Marty Supreme (2025) - 2160p Remux DoVi CZ.mkv"
    p5 = f"{folder}/Marty Supreme (2025) - 2160p WEB-DL DoVi CZ.mkv"
    hdr10 = f"{folder}/Marty Supreme (2025) - 2160p WEB-DL DoVi CZ [HDR10].mkv"
    hd1080 = "/Movies/HD/Marty Supreme (2025) - 1080p WEB-DL CZ/Marty Supreme (2025) - 1080p WEB-DL CZ.mkv"
    decisions = [{
        "keep": {"id": "k", "name": "Marty", "path": keeper},
        "delete": [
            {"id": "p5", "name": "p5", "path": p5},
            {"id": "hdr", "name": "hdr10", "path": hdr10},
            {"id": "hd", "name": "1080p", "path": hd1080},
        ],
    }]
    _probed(decisions)
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        mock_del.return_value = {"id": "hd", "status": "success", "error": None}
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k",
            None, collect_known_paths(decisions),
        )
    status = {d["id"]: d["deletion_result"]["status"] for d in decisions[0]["delete"]}
    assert status["p5"] == "skipped_unsafe"   # co-located; only sibling (HDR10) is co-deleted
    assert status["hdr"] == "skipped_unsafe"  # co-located; only sibling (P5) is co-deleted
    assert status["hd"] == "success"          # isolated in its own folder → safe
    assert [call.args[2] for call in mock_del.call_args_list] == ["hd"]


def test_converter_and_guard_agree_a_per_title_folder_is_a_trap(tmp_path):
    """Cross-module drift canary. The converter (`dv_common._owns_folder`) and the guard
    (`is_delete_safe`) encode the SAME per-title rule (`stem.startswith(folder)`) in two
    separate files — the converter decides where to place the HDR10, the guard decides
    whether to clear a delete. If one drifts, the converter places into / the guard clears
    a folder the other treats as safe → another Marty-class fold-delete. This fails loudly
    if they ever disagree on the exact Marty layout (a comment can't; a test can)."""
    import importlib.util
    from pathlib import Path

    dv_path = Path(__file__).resolve().parents[3] / "scripts" / "dv" / "dv_common.py"
    spec = importlib.util.spec_from_file_location("dv_common_canary", dv_path)
    dvc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dvc)

    folder = tmp_path / "4K" / "Marty Supreme (2025)"
    folder.mkdir(parents=True)
    p5 = folder / "Marty Supreme (2025) - 2160p WEB-DL DoVi CZ.mkv"
    keeper = folder / "Marty Supreme (2025) - 2160p Remux DoVi CZ.mkv"
    p5.touch()
    keeper.touch()

    # converter: this per-title folder is "owned" → HDR10 must go to a NEW folder, not loose here
    assert dvc._owns_folder(str(p5)) is True
    # guard: keeper co-located in that same per-title folder → refuse the delete
    safe, reason = is_delete_safe(str(keeper), str(p5), [str(keeper), str(p5)])
    assert safe is False
    assert "dedicated folder" in reason

    # The same layout with a folder name that differs only in case: both still see a trap.
    folder2 = tmp_path / "HD" / "marty supreme (2025)"
    folder2.mkdir(parents=True)
    p5b, keeperb = folder2 / p5.name, folder2 / keeper.name
    p5b.touch()
    keeperb.touch()
    assert dvc._owns_folder(str(p5b)) is True
    safe, reason = is_delete_safe(str(keeperb), str(p5b), [str(keeperb), str(p5b)])
    assert safe is False
    assert "dedicated folder" in reason


class TestCleanupDeleteGuard:
    """The cleanup path had NO fold-delete guard: cli/cleanup.py called delete_item()
    directly, so deleting a movie that shares its folder with keepers could fold-delete
    the whole directory. Live case 2026-07-28: Victoria's Secret 2014 sat loose in a
    folder holding 13 editions, 9 of them keepers.
    """

    VS = "/Movies/Dokumenty/The Victoria's Secret Fashion Show - 720p x264"

    def test_loose_file_in_shared_folder_is_file_only_delete(self):
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        delete = f"{self.VS}/The Victoria's Secret Fashion Show (2014) - 720p x264.mp4"
        known = [
            delete,
            f"{self.VS}/The Victoria's Secret Fashion Show (2005) - 720p x264.mp4",
            f"{self.VS}/The Victoria's Secret Fashion Show (2010) - 720p x264.mkv",
            f"{self.VS}/The Victoria's Secret Fashion Show 2017/x.mkv",
        ]
        safe, reason = is_cleanup_delete_safe(delete, known, [delete])
        assert safe, reason
        assert "file only" in reason

    def test_per_title_folder_with_a_survivor_is_refused(self):
        """A folder named for the title is fold-deleted wholesale — never safe."""
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        d = "/Movies/Dokumenty/Some Doc (2019)"
        delete = f"{d}/Some Doc (2019) - 1080p.mkv"
        known = [delete, f"{d}/Some Doc (2019) - extras.mkv"]
        safe, reason = is_cleanup_delete_safe(delete, known, [delete])
        assert not safe
        assert "fold-delete" in reason or "dedicated folder" in reason

    def test_nested_survivor_blocks_the_delete(self):
        """A survivor in a SUBfolder doesn't stop Emby fold-deleting the parent."""
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        d = "/Movies/Dokumenty/Container"
        delete = f"{d}/loose.mkv"
        known = [delete, f"{d}/Sub/keeper.mkv"]
        safe, _ = is_cleanup_delete_safe(delete, known, [delete])
        assert not safe

    def test_folder_where_everything_is_being_deleted_is_safe(self):
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        d = "/Movies/Dokumenty/Doomed"
        a, b = f"{d}/a.mkv", f"{d}/b.mkv"
        safe, reason = is_cleanup_delete_safe(a, [a, b], [a, b])
        assert safe
        assert "survives" in reason

    def test_sibling_that_is_also_a_delete_target_does_not_vouch_shared(self):
        """Two co-located deletes must not vouch for each other while a keeper sits there."""
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        d = "/Movies/Dokumenty/Mixed"
        keep = f"{d}/Mixed/keeper.mkv"          # nested keeper
        a, b = f"{d}/a.mkv", f"{d}/b.mkv"
        safe, _ = is_cleanup_delete_safe(a, [a, b, keep], [a, b])
        assert not safe

    def test_missing_path_is_refused(self):
        """Changed 2026-08-20: an unusable path is refused, not waved through.

        It used to return safe, which meant a candidate whose Path Emby did not return
        was deleted with no fold check at all.
        """
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        assert is_cleanup_delete_safe(None, [], [])[0] is False
        assert is_cleanup_delete_safe("", ["/x/y.mkv"], [])[0] is False
        assert is_cleanup_delete_safe("unknown", ["/x/y.mkv"], [])[0] is False

    def test_incomplete_path_list_refuses_a_file_delete(self):
        """With a short list, "nothing in this folder survives" is not proven."""
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        delete = "/Movies/DiViX/Alone (2022) - 1080p/Alone (2022) - 1080p.mkv"
        assert is_cleanup_delete_safe(delete, [delete], [delete])[0] is True
        safe, reason = is_cleanup_delete_safe(delete, [delete], [delete], paths_complete=False)
        assert not safe
        assert reason.startswith("library-incomplete:")

    def test_incomplete_listing_refuses_a_series_folder_too(self):
        """A series delete takes the whole folder, so an item the listing misses could go with it."""
        from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

        show = "/Movies/Serials/--- UKONCENE ---/Some Show (2019)"
        safe, reason = is_cleanup_delete_safe(show, [], [show], delete_is_container=True, paths_complete=False,
                                              owners={}, series_id="1")
        assert not safe
        assert reason.startswith("library-incomplete:")
        assert is_cleanup_delete_safe(show, [], [show], delete_is_container=True, owners={}, series_id="1")[0]



def test_unprobed_copy_is_never_deleted():
    """Regression 2026-10-12: a download still landing has no runtime in Emby, scored as
    "unknown quality" and was picked for deletion against an old SD copy (NCIS S03).
    Only the folder rule stopped it; the content guard now refuses it on its own."""
    decisions = [{
        "keep": {"id": "k", "name": "old SD", "path": "/S/NCIS/S03/NCIS.s03e01.SK.avi", "runtime_ticks": _HOUR_TICKS},
        "delete": [{"id": "d", "name": "new 1080p", "path": "/S/NCIS/S03-new/NCIS S03E01 - 1080p SK.mkv"}],
    }]
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k",
            None, ["/S/NCIS/S03/NCIS.s03e01.SK.avi", "/S/NCIS/S03-new/NCIS S03E01 - 1080p SK.mkv", "/S/NCIS/S03-new/x.mkv"],
        )
    mock_del.assert_not_called()
    result = decisions[0]["delete"][0]["deletion_result"]
    assert result["status"] == "skipped_unsafe"
    assert "runtime-unknown" in result["error"]
    assert "delete" in result["error"]


# --- name prefix: Emby's file-only delete takes every file starting with the name -----
# Deleting "X.mkv" through Emby also removes "X CZ.nfo" and "X CZ.cs.srt" beside it
# (proven against a disposable Emby 4.10.2.0), so a surviving file whose name starts with
# the duplicate's stem loses its metadata and subtitles. The guard refuses; fold-safe
# delete then removes the single file.
def test_keeper_named_with_the_duplicate_prefix_is_refused():
    known = [EP_PREFIX_KEEP, EP_LOOSE_P5, EP_SIBLING]
    safe, reason = is_delete_safe(EP_PREFIX_KEEP, EP_LOOSE_P5, known)
    assert safe is False
    assert reason.startswith("name-prefix")
    assert "co-located" not in reason  # the report would advise moving a copy out


def test_name_prefix_ignores_case():
    keeper = "/Movies/Serials/Reacher/S03/reacher s03e01 - dovi CZ.mkv"
    safe, reason = is_delete_safe(keeper, EP_LOOSE_P5, [keeper, EP_LOOSE_P5, EP_SIBLING])
    assert safe is False
    assert "name-prefix" in reason


def test_reverse_prefix_is_safe():
    # The keeper's name is a prefix of the duplicate's: Emby's delete matches only names
    # starting with the duplicate's stem, so the keeper's sidecars are untouched.
    known = [EP_PREFIX_KEEP, EP_LOOSE_P5, EP_SIBLING]
    safe, reason = is_delete_safe(EP_LOOSE_P5, EP_PREFIX_KEEP, known)
    assert safe is True
    assert "shared folder" in reason


def test_prefix_neighbour_that_is_not_the_keeper_is_refused():
    delete = "/S/Show/S01/Show S01E1.mkv"
    keeper = "/S/Show/S01/Show - S01E01 - 1080p.mkv"
    neighbour = "/S/Show/S01/Show S01E10.mkv"
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, neighbour])
    assert safe is False
    assert "Show S01E10.mkv" in reason


def test_prefix_neighbour_refused_when_the_keeper_is_elsewhere():
    delete = "/S/Show/S01/Show S01E1.mkv"
    keeper = "/S/Show/S01 other/Show S01E01 - 1080p.mkv"
    neighbour = "/S/Show/S01/Show S01E10.mkv"
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, neighbour])
    assert safe is False
    assert "name-prefix" in reason


def test_prefix_neighbour_deleted_in_the_same_run_still_refuses():
    # The neighbour's own delete can still be refused or fail later in the run; it would
    # then survive without its .nfo and subtitles.
    delete = "/S/Show/S01/Show S01E01.mkv"
    also_deleted = "/S/Show/S01/Show S01E01 (2).mkv"
    keeper = "/S/Show/S01 other/Show S01E01 - 1080p.mkv"
    known = [keeper, delete, also_deleted, "/S/Show/S01/Show S01E02.mkv"]
    safe, reason = is_delete_safe(keeper, delete, known, [delete, also_deleted])
    assert safe is False
    assert "Show S01E01 (2).mkv" in reason


def test_prefix_neighbour_refused_without_a_keeper_path():
    delete = "/S/Show/S01/Show S01E01.mkv"
    known = [delete, "/S/Show/S01/Show S01E01 CZ.mkv", "/S/Show/S01/Show S01E02.mkv"]
    safe, reason = is_delete_safe(None, delete, known)
    assert safe is False
    assert "name-prefix" in reason
    assert is_delete_safe(None, delete, [delete, "/S/Show/S01/Show S01E02.mkv"]) == (
        True, "no keeper path to protect")


def test_prefix_neighbour_in_a_subfolder_does_not_count():
    known = [EP_LOOSE_KEEP, EP_LOOSE_P5, EP_SIBLING,
             "/Movies/Serials/Reacher/S03/extras/Reacher S03E01 - DoVi featurette.mkv"]
    safe, reason = is_delete_safe(EP_LOOSE_KEEP, EP_LOOSE_P5, known)
    assert safe is True
    assert "shared folder" in reason


def test_per_title_folder_holding_only_the_duplicate_goes_whole():
    delete = "/Movies/HD/Film (2020)/Film (2020).mkv"
    keeper = "/Movies/4K/Film (2020) - 2160p/Film (2020) - 2160p.mkv"
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, "/Movies/HD/Other (2019)/Other (2019).mkv"])
    assert safe is True
    assert "different folder" in reason


def test_process_deletion_hands_a_prefix_pair_to_fold_safe():
    decisions = _probed([{
        "keep": {"id": "k", "name": "ep", "path": EP_PREFIX_KEEP},
        "delete": [{"id": "d", "name": "ep dup", "path": EP_LOOSE_P5}],
    }])
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k",
            None, [EP_PREFIX_KEEP, EP_LOOSE_P5, EP_SIBLING],
        )
    mock_del.assert_not_called()
    item = decisions[0]["delete"][0]
    assert item["deletion_result"]["status"] == "skipped_unsafe"
    assert item["fold_safe_candidate"]["keeper"] == EP_PREFIX_KEEP
    assert "name-prefix" in item["fold_safe_candidate"]["reason"]


def test_cleanup_refuses_a_delete_whose_name_prefixes_a_survivor():
    from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

    delete = "/Movies/Kolekcia/Shrek (2001).mkv"
    sequel = "/Movies/Kolekcia/Shrek (2001) CZ.mkv"
    others = ["/Movies/Kolekcia/Madagascar (2005).mkv", "/Movies/Kolekcia/Zootopia (2016).mkv"]
    known = [delete, sequel, *others]
    safe, reason = is_cleanup_delete_safe(delete, known, [delete])
    assert safe is False
    assert "name-prefix" in reason
    # Deleting both still refuses: the second delete may be refused or fail.
    assert is_cleanup_delete_safe(delete, known, [delete, sequel])[0] is False
    assert is_cleanup_delete_safe(sequel, known, [delete, sequel])[0] is True


# --- group size: more copies to delete than a title has versions ---------------------
def _misnamed_season(n):
    """A keeper and ``n`` misnamed copies of one episode in a season folder shared with
    other episodes, all with the same runtime: without a frame check nothing tells them
    apart except their number."""
    folder = "/S/Show/S01"
    keep = {"id": "k", "name": "E01", "path": f"{folder}/Show S01E01.mkv"}
    deletes = [{"id": f"d{i}", "name": f"E01 ({i})", "path": f"{folder}/Show S01E01 ({i}).mkv"}
               for i in range(2, 2 + n)]
    library = [keep["path"], *(d["path"] for d in deletes), f"{folder}/Show S01E02.mkv"]
    return _probed([{"keep": keep, "delete": deletes}]), library


def _run(decisions, library, doit=True):
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        mock_del.return_value = {"id": "x", "status": "success", "error": None}
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, doit, "u", "p", "k", None, library,
        )
    return mock_del


def test_oversized_group_is_held_on_the_emby_path():
    decisions, library = _misnamed_season(6)
    mock_del = _run(decisions, library)
    mock_del.assert_not_called()
    for item in decisions[0]["delete"]:
        assert item["deletion_result"]["status"] == "skipped_unsafe"
        assert item["deletion_result"]["error"].startswith("group-too-large")
        assert "fold_safe_candidate" not in item  # fold-safe must not remove them either


def test_oversized_group_is_shown_as_held_in_the_dry_run():
    decisions, library = _misnamed_season(6)
    _run(decisions, library, doit=False)
    for item in decisions[0]["delete"]:
        assert item["deletion_result"]["status"] == "would_block"
        assert "group-too-large" in item["deletion_result"]["error"]


def test_group_at_the_limit_is_not_held():
    decisions, library = _misnamed_season(5)
    mock_del = _run(decisions, library)
    assert mock_del.call_count == 5


# --- title folder: Emby deletes it whole, so nothing else may be inside --------------
TITLE_DIR = "/M/T (2001)"
TITLE_DEL = f"{TITLE_DIR}/T (2001).mkv"
TITLE_KEEP = "/M/Collection/T (2001).mkv"


def test_title_folder_with_another_video_is_refused():
    other = f"{TITLE_DIR}/T (2001) CZ.mkv"
    safe, reason = is_delete_safe(TITLE_KEEP, TITLE_DEL, [TITLE_KEEP, TITLE_DEL, other])
    assert safe is False
    assert reason.startswith("title-folder-shared")
    assert "T (2001) CZ.mkv" in reason
    assert "co-located" not in reason


def test_title_folder_refusal_counts_nested_videos_and_other_delete_targets():
    nested = f"{TITLE_DIR}/extras/Making of.mkv"
    assert is_delete_safe(TITLE_KEEP, TITLE_DEL, [TITLE_KEEP, TITLE_DEL, nested])[0] is False
    also_deleted = f"{TITLE_DIR}/T (2001) - 720p.mkv"
    known = [TITLE_KEEP, TITLE_DEL, also_deleted]
    assert is_delete_safe(TITLE_KEEP, TITLE_DEL, known, [TITLE_DEL, also_deleted])[0] is False


def test_title_folder_named_in_another_case_is_still_a_title_folder():
    # "show s01e01/" is the folder of "Show S01E01 CZ.mkv" for a server that ignores case;
    # deleting there would take the held "Show S01E01.mkv" with the folder.
    folder = "/S/Show/S01/show s01e01"
    delete, held = f"{folder}/Show S01E01 CZ.mkv", f"{folder}/Show S01E01.mkv"
    keeper = "/S/Show/S01/Show S01E01 - 1080p.mkv"
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, held])
    assert safe is False
    assert reason.startswith("title-folder-shared")
    assert "Show S01E01.mkv" in reason


def test_title_folder_case_match_covers_non_ascii_letters():
    folder = "/M/čert (2001)"
    delete, other = f"{folder}/Čert (2001).mkv", f"{folder}/Čert (2001) CZ.mkv"
    keeper = "/M/Collection/Čert (2001).mkv"
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, other])
    assert safe is False
    assert reason.startswith("title-folder-shared")


def test_title_folder_in_another_case_holding_only_the_duplicate_goes_whole():
    # The wider match must not refuse a folder that really holds nothing else.
    folder = "/S/Show/S01/show s01e01"
    delete = f"{folder}/Show S01E01 CZ.mkv"
    keeper = "/S/Show/S01/Show S01E01 - 1080p.mkv"
    safe, _ = is_delete_safe(keeper, delete, [keeper, delete])
    assert safe is True


def test_episode_subfolder_with_another_copy_is_refused():
    folder = "/S/Show/S01/Show S01E01"
    delete, other = f"{folder}/Show S01E01.mkv", f"{folder}/Show S01E01 - 576p.mkv"
    keeper = "/S/Show/S01/Show S01E01 - 1080p.mkv"
    safe, reason = is_delete_safe(keeper, delete, [keeper, delete, other, "/S/Show/S01/Show S01E02.mkv"])
    assert safe is False
    assert "title-folder-shared" in reason


def test_process_deletion_keeps_a_held_copy_in_the_title_folder():
    held = f"{TITLE_DIR}/T (2001) CZ.mkv"
    decisions = _probed([{
        "keep": {"id": "k", "name": "T", "path": TITLE_KEEP},
        "delete": [{"id": "d", "name": "T", "path": TITLE_DEL},
                   {"id": "h", "name": "T CZ", "path": held, "runtime_ticks": 2 * _HOUR_TICKS}],
    }])
    mock_del = _run(decisions, [TITLE_KEEP, TITLE_DEL, held])
    mock_del.assert_not_called()
    first, second = decisions[0]["delete"]
    assert first["deletion_result"]["status"] == "skipped_unsafe"
    assert "title-folder-shared" in first["fold_safe_candidate"]["reason"]  # removed file-only instead
    assert "duration-mismatch" in second["deletion_result"]["error"]


# --- an incomplete library list clears no Emby delete ---------------------------------
def _shared_pair():
    keep = {"id": "k", "name": "E01", "path": "/S/Show/S01/Show S01E01 - 1080p.mkv"}
    dup = {"id": "d", "name": "E01", "path": "/S/Show/S01/Show - S01E01.mkv"}
    return _probed([{"keep": keep, "delete": [dup]}]), [keep["path"], dup["path"], "/S/Show/S01/Show S01E02.mkv"]


def test_complete_library_list_allows_a_shared_folder_delete():
    decisions, library = _shared_pair()
    assert _run(decisions, library).call_count == 1


def test_missing_library_list_refuses_every_emby_delete():
    # Decision paths alone would clear this (keeper elsewhere) and miss a neighbour such
    # as "Show S01E01 CZ.mkv" whose sidecars Emby's delete would take.
    keep = {"id": "k", "name": "E01", "path": "/S/Show/S01 1080p/Show S01E01.mkv"}
    dup = {"id": "d", "name": "E01", "path": "/S/Show/S01/Show S01E01.mkv"}
    decisions = _probed([{"keep": keep, "delete": [dup]}])
    mock_del = _run(decisions, None)
    mock_del.assert_not_called()
    item = decisions[0]["delete"][0]
    assert item["deletion_result"]["error"].startswith("library-incomplete")
    assert item["fold_safe_candidate"]  # fold-safe sees the real folder and may still remove it


def test_partial_library_list_refuses_every_emby_delete():
    decisions, library = _shared_pair()
    with patch("emby_dedupe.api.deduplication.delete_item") as mock_del, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        process_deletion_and_generate_report(
            MagicMock(), "http://emby", decisions, True, "u", "p", "k", None, library,
            library_complete=False,
        )
    mock_del.assert_not_called()
    assert "library-incomplete" in decisions[0]["delete"][0]["deletion_result"]["error"]


def test_incomplete_list_never_turns_a_refusal_into_a_pass():
    safe, reason = is_delete_safe(EP_PREFIX_KEEP, EP_LOOSE_P5, [EP_PREFIX_KEEP, EP_LOOSE_P5, EP_SIBLING],
                                  paths_complete=False)
    assert safe is False
    assert reason.startswith("name-prefix")


# --- sidecar owner: Emby lists "X.CZ.cs.srt" under "X.mkv" too ----------------------
DOT_DIR = "/M/Collection"
DOT_KEEP, DOT_DEL = f"{DOT_DIR}/T (2001).mkv", f"{DOT_DIR}/T (2001).CZ.mkv"
DOT_OTHER = f"{DOT_DIR}/U (2002).mkv"


def test_delete_whose_name_extends_the_keeper_after_a_dot_is_refused():
    safe, reason = is_delete_safe(DOT_KEEP, DOT_DEL, [DOT_KEEP, DOT_DEL, DOT_OTHER])
    assert safe is False
    assert reason.startswith("sidecar-owner")
    assert "T (2001).mkv" in reason


def test_sidecar_owner_outside_every_group_or_due_for_deletion_counts():
    keeper = "/M/Elsewhere/T (2001).mkv"
    safe, reason = is_delete_safe(keeper, DOT_DEL, [keeper, DOT_DEL, f"{DOT_DIR}/T (2001).mkv", DOT_OTHER])
    assert (safe, reason.split(":")[0]) == (False, "sidecar-owner")
    also_deleted = f"{DOT_DIR}/T (2001).mkv"
    safe, _ = is_delete_safe(keeper, DOT_DEL, [keeper, DOT_DEL, also_deleted, DOT_OTHER], [DOT_DEL, also_deleted])
    assert safe is False


def test_sidecar_owner_ignores_case_and_needs_the_dot():
    assert is_delete_safe(DOT_KEEP, f"{DOT_DIR}/t (2001).cz.mkv", [DOT_KEEP, DOT_OTHER])[0] is False
    spaced = f"{DOT_DIR}/T (2001) CZ.mkv"  # no dot after the stem: Emby does not list it under T (2001).mkv
    assert is_delete_safe(DOT_KEEP, spaced, [DOT_KEEP, spaced, DOT_OTHER]) == (
        True, "shared folder (1 surviving item(s)) → Emby deletes file only")


def test_cleanup_holds_a_delete_whose_subtitles_a_survivor_shows():
    from emby_dedupe.api.deletion_guard import is_cleanup_delete_safe

    safe, reason = is_cleanup_delete_safe(DOT_DEL, [DOT_KEEP, DOT_DEL, DOT_OTHER], [DOT_DEL])
    assert safe is False
    assert "sidecar-owner" in reason


def test_process_deletion_hands_a_dot_named_copy_to_fold_safe():
    decisions = _probed([{"keep": {"id": "k", "name": "T", "path": DOT_KEEP},
                          "delete": [{"id": "d", "name": "T CZ", "path": DOT_DEL}]}])
    mock_del = _run(decisions, [DOT_KEEP, DOT_DEL, DOT_OTHER])
    mock_del.assert_not_called()
    assert "sidecar-owner" in decisions[0]["delete"][0]["fold_safe_candidate"]["reason"]
