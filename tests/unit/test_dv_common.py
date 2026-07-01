"""Tests for scripts/dv/dv_common.py — the shared DV Profile-5 criterion.

dv_common is the single source of truth shared by dv-scan and dv-convert, so the
scanner and the converter can never disagree on what 'problematic' means.
"""
import importlib.util
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dv" / "dv_common.py"
_spec = importlib.util.spec_from_file_location("dv_common", _PATH)
dvc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dvc)


def test_is_problematic_only_profile_5():
    assert dvc.is_problematic(5) is True
    for p in (None, 4, 7, 8):  # no-DV / P4 / P7 / P8 all carry a usable base
        assert dvc.is_problematic(p) is False


def test_dv_profile_of_full_probe_dict():
    probe = {"streams": [{"side_data_list": [
        {"side_data_type": "DOVI", "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}]}]}
    assert dvc.dv_profile_of(probe) == (5, 0)


def test_dv_profile_of_single_stream_dict():
    stream = {"side_data_list": [{"dv_profile": 8, "dv_bl_signal_compatibility_id": 1}]}
    assert dvc.dv_profile_of(stream) == (8, 1)


def test_dv_profile_of_no_dv():
    assert dvc.dv_profile_of({"streams": [{"side_data_list": []}]}) == (None, None)
    assert dvc.dv_profile_of({}) == (None, None)


def test_utc_now_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", dvc.utc_now())


# --- safe_output_path: the converted HDR10 must never land in a folder Emby would
# fold-delete when the dedupe later removes the P5 original (mirrors the 44 baseline). ---
import os  # noqa: E402


def _mk(root, rel):
    """Create an empty file at root/rel and return its absolute path."""
    p = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def _owning_folder_or_none(inp):
    """The folder Emby would fold-delete for `inp` (its dirname if it owns the
    folder), else None."""
    return os.path.dirname(inp) if dvc._owns_folder(inp) else None


def _assert_safe(inp, out):
    """Core invariant: the keeper (out) is never inside the folder Emby deletes."""
    owned = _owning_folder_or_none(inp)
    if owned is not None:
        assert not (out == owned or out.startswith(owned + os.sep)), \
            f"UNSAFE: keeper {out!r} is inside fold-deleted folder {owned!r}"


def test_movie_in_own_folder_goes_to_sibling_folder(tmp_path):
    inp = _mk(tmp_path, "4K/Movie Name (2020) - 2160p DoVi/Movie Name (2020) - 2160p DoVi.mkv")
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(
        str(tmp_path), "4K",
        "Movie Name (2020) - 2160p DoVi [HDR10]",
        "Movie Name (2020) - 2160p DoVi [HDR10].mkv")
    _assert_safe(inp, out)


def test_movie_with_colocated_other_version_still_gets_new_folder(tmp_path):
    """Regression for the Marty Supreme data loss (2026-07-01). The P5 shared its
    per-title folder `Marty Supreme (2025)/` with a 73.6 GB REMUX keeper. The old rule
    saw the REMUX as "another distinct video → shared → drop the HDR10 loose here",
    which inflated the folder to 3 files so the dedupe guard mis-cleared the P5 delete
    → Emby fold-deleted the whole directory and destroyed the REMUX. A per-title folder
    is a fold-delete trap even with co-located versions, so the HDR10 must go to its own
    NEW sibling folder, and the source folder must stay at keeper+delete only."""
    inp = _mk(tmp_path, "4K/Marty Supreme (2025)/Marty Supreme (2025) - 2160p WEB-DL DoVi CZ.mkv")
    _mk(tmp_path, "4K/Marty Supreme (2025)/Marty Supreme (2025) - 2160p Remux DoVi CZ.mkv")  # REMUX keeper co-located
    assert dvc._owns_folder(inp) is True
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(
        str(tmp_path), "4K",
        "Marty Supreme (2025) - 2160p WEB-DL DoVi CZ [HDR10]",
        "Marty Supreme (2025) - 2160p WEB-DL DoVi CZ [HDR10].mkv")
    _assert_safe(inp, out)


def test_episode_in_per_episode_subfolder_goes_to_season_folder(tmp_path):
    inp = _mk(tmp_path, "Show/S03/Show S03E02 - 2160p DoVi/Show S03E02 - 2160p DoVi.mkv")
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(str(tmp_path), "Show", "S03",
                               "Show S03E02 - 2160p DoVi [HDR10].mkv")
    _assert_safe(inp, out)


def test_episode_loose_in_shared_season_folder_stays_beside(tmp_path):
    inp = _mk(tmp_path, "Show/S03/Show S03E01 - 2160p DoVi.mkv")
    _mk(tmp_path, "Show/S03/Show S03E03 - 2160p DoVi.mkv")  # sibling makes the folder shared
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(str(tmp_path), "Show", "S03",
                               "Show S03E01 - 2160p DoVi [HDR10].mkv")
    assert dvc._owns_folder(inp) is False
    _assert_safe(inp, out)


def test_episode_in_multi_episode_named_subfolder_goes_to_season_folder(tmp_path):
    # e.g. ".../S03/Show S03E01-E03 .../Show S03E01 ....mkv" — subfolder named for a range
    inp = _mk(tmp_path, "Show/S03/Show S03E01-E03 - 2160p DoVi/Show S03E01 - 2160p DoVi.mkv")
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(str(tmp_path), "Show", "S03",
                               "Show S03E01 - 2160p DoVi [HDR10].mkv")
    _assert_safe(inp, out)


def test_movie_loose_in_shared_folder_stays_beside(tmp_path):
    inp = _mk(tmp_path, "Loose/Movie A (2019).mkv")
    _mk(tmp_path, "Loose/Movie B (2021).mkv")  # other distinct movie → shared folder
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(str(tmp_path), "Loose", "Movie A (2019) [HDR10].mkv")
    assert dvc._owns_folder(inp) is False
    _assert_safe(inp, out)


def test_owns_folder_ignores_own_hdr10_sibling_and_extras(tmp_path):
    # a re-run (HDR10 already beside) + a trailer must NOT count as "other media"
    inp = _mk(tmp_path, "4K/Movie (2020)/Movie (2020).mkv")
    _mk(tmp_path, "4K/Movie (2020)/Movie (2020) [HDR10].mkv")
    _mk(tmp_path, "4K/Movie (2020)/Movie (2020)-trailer.mkv")
    assert dvc._owns_folder(inp) is True
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(str(tmp_path), "4K",
                               "Movie (2020) [HDR10]", "Movie (2020) [HDR10].mkv")
    _assert_safe(inp, out)


def test_doc_series_loose_episode_stays_in_season_folder(tmp_path):
    # Documentary-library series structure (/Dokumenty/<Show>/S01/<Show> S01E01...)
    inp = _mk(tmp_path, "Dokumenty/DocShow (2023)/S01/DocShow S01E01 - 1080p.mkv")
    _mk(tmp_path, "Dokumenty/DocShow (2023)/S01/DocShow S01E02 - 1080p.mkv")
    out = dvc.safe_output_path(inp)
    assert out == os.path.join(str(tmp_path), "Dokumenty", "DocShow (2023)", "S01",
                               "DocShow S01E01 - 1080p [HDR10].mkv")
    _assert_safe(inp, out)


def test_season_folder_detection(tmp_path):
    inp = _mk(tmp_path, "Show/S03/ep/Show S03E02.mkv")
    assert os.path.basename(dvc._season_folder(inp)) == "S03"
    inp2 = _mk(tmp_path, "Show/Season 2/Show S02E01.mkv")
    assert os.path.basename(dvc._season_folder(inp2)) == "Season 2"
    movie = _mk(tmp_path, "4K/Movie (2020)/Movie (2020).mkv")
    assert dvc._season_folder(movie) is None


def test_per_episode_subfolder_name_is_not_mistaken_for_a_season(tmp_path):
    # "Show S03E02 - ..." contains s03 but must NOT match the season pattern
    assert dvc._SEASON_RE.match("Show S03E02 - 2160p DoVi") is None
    assert dvc._SEASON_RE.match("S03") is not None
