"""Grouping guards: films of one length need the frame check, a file another group keeps is
never deleted, and ``--exclude-ids`` holds whatever the ids' spelling or Emby's answer."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

from emby_dedupe.api import deduplication as dd
from emby_dedupe.api.client import _process_provider_id
from emby_dedupe.api.content_check import FrameVerdict
from emby_dedupe.utils.constants import IGNORED_IMDB_ID
from emby_dedupe.utils.providers import normalize_provider_value

TICKS = 10_000_000
STEADY = (-3.0, -3.5, -4.1, -4.6, -5.3)


class _Frames:
    def __init__(self, verdict, offsets=STEADY):
        self.verdict = FrameVerdict(verdict, (0.9,) * 5, offsets if verdict == "same" else ())
        self.calls = 0

    def compare(self, keeper, other):
        self.calls += 1
        return self.verdict

    def covers(self, keeper, other):
        raise AssertionError("films of one length are compared, not covered")


def _film(item_id, seconds, path, provider_ids=None):
    return {"id": item_id, "runtime_ticks": int(seconds * TICKS), "frame_rate": 24.0, "is_episode": False,
            "path": path, "provider_ids": provider_ids or {"Imdb": "tt0000001"}}


KEEP = _film("k", 7000, "/M/A (2001)/A (2001).mkv")
OTHER = _film("o", 7010, "/M/B (2002)/B (2002).mkv")  # another film Emby linked by a wrong id


# --- G1: films of one length --------------------------------------------------------------

def test_a_film_of_one_length_is_deleted_only_when_the_frames_match():
    frames = _Frames("same")
    assert dd._different_content_reason(OTHER, KEEP, frames) is None
    assert frames.calls == 1


def test_a_different_film_of_one_length_is_kept():
    reason = dd._different_content_reason(OTHER, KEEP, _Frames("different"))
    assert reason is not None
    assert "frame-check different" in reason


def test_a_film_whose_frames_cannot_be_read_is_kept():
    reason = dd._different_content_reason(OTHER, KEEP, _Frames("unavailable"))
    assert reason is not None
    assert "not proven" in reason


def test_a_film_matching_far_apart_is_kept():
    far = (-200.0, -200.2, -199.9, -200.1, -200.0)  # limit for 7010 s: 2 % = 140 s
    assert dd._different_content_reason(OTHER, KEEP, _Frames("same", far)) is not None


def test_without_a_frame_checker_films_of_one_length_still_pass():
    assert dd._different_content_reason(OTHER, KEEP, None) is None


def test_films_keep_the_film_rule_for_season_folders():
    """The film frame check must not switch on the episode season-folder test."""
    keep = _film("k", 7000, "/M/Season 1/A (2001).mkv")
    other = _film("o", 7010, "/M/Season 2/A (2001).mkv")
    assert dd._different_content_reason(other, keep, _Frames("same")) is None


def test_prefetch_holds_film_pairs_of_one_length_and_pal_film_pairs():
    same_len = {"keep": KEEP, "delete": [OTHER]}
    pal_keep = {**_film("k2", 2423, "/M/C (2003)/C.mkv"), "frame_rate": 25.0}
    pal = {"keep": pal_keep, "delete": [_film("d2", 2423 * 1.0427, "/M/C (2003) 4K/C.mkv")]}
    other_cut = {"keep": _film("k3", 6000, "/M/D/D.mkv"), "delete": [_film("d3", 6600, "/M/D 4K/D.mkv")]}
    pairs, covering = dd._frame_check_pairs([same_len, pal, other_cut])
    assert [(k["id"], o["id"]) for k, o in pairs] == [("k", "o")]
    assert [(k["id"], o["id"]) for k, o in covering] == [("k2", "d2")]


# --- G2: one file, two Emby ids, two groups ----------------------------------------------------

def _crosswise():
    """The same two files in two groups with opposite keepers (two libraries over one folder)."""
    x, y = "/M/T (2001)/T (2001).mkv", "/M/T (2001) 4K/T (2001).mkv"
    first = {"keep": {"id": "a1", "path": x, "runtime_ticks": 3600 * TICKS},
             "delete": [{"id": "a2", "path": y, "runtime_ticks": 3600 * TICKS}]}
    second = {"keep": {"id": "b2", "path": y, "runtime_ticks": 3600 * TICKS},
              "delete": [{"id": "b1", "path": x, "runtime_ticks": 3600 * TICKS}]}
    return [first, second], [x, y]


def test_a_file_another_group_keeps_is_never_deleted():
    decisions, library = _crosswise()
    with patch("emby_dedupe.api.deduplication.delete_item") as delete, \
         patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        dd.process_deletion_and_generate_report(MagicMock(), "http://emby", decisions, True, "u", "p", "k",
                                                None, library)
    delete.assert_not_called()
    for decision in decisions:
        item = decision["delete"][0]
        assert item["deletion_result"]["status"] == "skipped_unsafe"
        assert item["deletion_result"]["error"].startswith("kept-file")
        assert "fold_safe_candidate" not in item  # fold-safe must leave it too


def test_the_kept_file_hold_shows_in_the_dry_run():
    decisions, library = _crosswise()
    with patch("emby_dedupe.reports.markdown.format_markdown_table", return_value="RPT"), \
         patch("emby_dedupe.api.deduplication.tqdm"):
        dd.process_deletion_and_generate_report(MagicMock(), "http://emby", decisions, False, "u", "p", "k",
                                                None, library)
    assert all("kept-file" in d["delete"][0]["dry_run_refusal"] for d in decisions)


def test_a_delete_no_group_keeps_is_not_held():
    decisions, _ = _crosswise()
    decisions[1]["keep"]["path"] = "/M/T (2001) Other/T (2001).mkv"  # nobody keeps the 4K file now
    dd._hold_kept_files(decisions)
    assert dd.KEPT_FILE_HOLD not in decisions[0]["delete"][0]
    assert dd.KEPT_FILE_HOLD in decisions[1]["delete"][0]  # the first group still keeps this file


# --- G3: exclusions -----------------------------------------------------------------------------

def test_provider_values_are_compared_trimmed_and_lowercased():
    assert normalize_provider_value(" TT0120737 ") == "tt0120737"
    assert normalize_provider_value(550) == "550"
    assert normalize_provider_value(None) == ""


def test_ids_spelled_differently_share_one_table_key():
    tables = {"imdb": {}}
    for item_id, value in (("1", "tt0120737"), ("2", "TT0120737 ")):
        _process_provider_id("imdb", "imdb", value, tables, {"Id": item_id}, "Lib", IGNORED_IMDB_ID)
    assert list(tables["imdb"]) == ["tt0120737"]
    assert [i["id"] for i in tables["imdb"]["tt0120737"]] == ["1", "2"]


def test_the_placeholder_imdb_id_is_ignored_in_any_spelling():
    tables = {"imdb": {}}
    _process_provider_id("imdb", "imdb", " TT0000000 ", tables, {"Id": "1"}, "Lib", IGNORED_IMDB_ID)
    assert tables["imdb"] == {}


def test_an_excluded_id_matches_whatever_its_spelling():
    tables = {"imdb": {"tt0120737": [{"id": "1"}, {"id": "2"}]}, "tmdb": {"550": [{"id": "3"}, {"id": "4"}]}}
    assert dd.identify_duplicates(tables, [" TT0120737", "550 "]) == {"imdb": {}, "tmdb": {}}
    exclusion = dd._build_exclusion_map([" TT0120737", "550 "])
    assert exclusion["imdb"] == ["tt0120737"]
    assert exclusion["tmdb"] == ["550"]
    item = {"Id": "1", "ProviderIds": {"IMDB": "tt0120737 "}}
    assert dd._check_group_exclusion([item], exclusion)[0] is True


@patch("emby_dedupe.api.deduplication.fetch_items_details")
@patch("emby_dedupe.api.deduplication.determine_items_to_delete")
def test_a_group_emby_answers_only_in_part_is_skipped(determine, fetch):
    """The copy Emby did not return may be the one carrying the excluded id."""
    fetch.return_value = [{"Id": "1", "ProviderIds": {"Tmdb": "550"}}]
    decisions, _ = dd.process_duplicate_groups(Mock(), "http://emby", [[{"id": "1"}, {"id": "2"}]],
                                               excluded_ids=["tt0120737"])
    assert decisions == []
    determine.assert_not_called()


@patch("emby_dedupe.api.deduplication.fetch_items_details")
@patch("emby_dedupe.api.deduplication.determine_items_to_delete", return_value=None)
def test_a_group_emby_answers_in_full_is_processed(determine, fetch):
    fetch.return_value = [{"Id": "1"}, {"Id": "2"}]
    dd.process_duplicate_groups(Mock(), "http://emby", [["1", "2"]])
    determine.assert_called_once()
