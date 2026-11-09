"""Episode numbering audit: classification of Emby numbers that disagree with the file names."""
from __future__ import annotations

from emby_dedupe.api import numbering as nb


def ep(item_id, path, season, episode, series="S1", series_name="Show", name="Title"):
    return {"Id": item_id, "Path": path, "ParentIndexNumber": season, "IndexNumber": episode,
            "SeriesId": series, "SeriesName": series_name, "Name": name}


def kinds(findings):
    return {f.item_id: f.kind for f in findings}


class TestFileEpisodeNumber:
    def test_reads_sxxeyy_from_the_file_name_only(self):
        assert nb.file_episode_number("/tv/Show S03/Show S03E07 - 1080p.mkv") == (3, 7)
        assert nb.file_episode_number("/tv/Show.S01E02.720p.mkv") == (1, 2)
        assert nb.file_episode_number("/tv/S02/Show s02 e05.mkv") == (2, 5)

    def test_no_code_or_a_folder_code_only(self):
        assert nb.file_episode_number("/tv/Show S02/Pilot.mkv") is None
        assert nb.file_episode_number(None) is None

    def test_multi_episode_file_uses_the_first_number(self):
        assert nb.file_episode_number("/tv/Show S01E01-E02.mkv") == (1, 1)

    def test_code_inside_a_word_is_ignored(self):
        assert nb.file_episode_number("/tv/CLASS1E2 thing.mkv") is None


class TestLoadAllowlist:
    def test_ids_with_comments_and_blank_lines(self):
        text = "# provider orders\n99858  # year seasons\n\n21444982\nnot-an-id\n"
        assert nb.load_allowlist(text) == {"99858", "21444982"}


class TestAuditNumbering:
    def test_agreeing_numbers_give_no_findings(self):
        assert nb.audit_numbering([ep("1", "/S01/Show S01E01.mkv", 1, 1), ep("2", "/S01/Show S01E02.mkv", 1, 2)]) == []

    def test_new_season_matched_onto_season_one_is_shadowed(self):
        """A new season's files named S04Exx that Emby numbered S1Ex hide behind the real S1Ex."""
        episodes = [ep(f"p1-{n}", f"/Show/S01/Show S01E0{n}.mkv", 1, n) for n in range(1, 4)]
        episodes += [ep(f"p4-{n}", f"/Show/S04/Show S04E0{n}.mkv", 1, n) for n in range(1, 4)]
        found = kinds(nb.audit_numbering(episodes))
        assert found == {"p4-1": nb.SHADOWED, "p4-2": nb.SHADOWED, "p4-3": nb.SHADOWED}

    def test_two_copies_of_one_episode_are_not_a_numbering_problem(self):
        episodes = [ep("a", "/S01/Show S01E01 - 720p.mkv", 1, 1), ep("b", "/S01/Show S01E01 - 1080p.mkv", 1, 1)]
        assert nb.audit_numbering(episodes) == []

    def test_unnumbered_episode_is_unmatched(self):
        found = nb.audit_numbering([ep("x", "/S23/Specials/Show S23E00 - 1080p.mp4", 0, None, name="Show S23E00")])
        assert kinds(found) == {"x": nb.UNMATCHED}
        assert found[0].emby_number == (0, None)

    def test_season_folder_spread_over_two_seasons_is_mixed(self):
        """A season folder whose files land in two Emby seasons: every mis-numbered one is mixed."""
        episodes = [ep("e1", "/S01/Show S01E01.mkv", 1, 1)]
        episodes += [ep(f"e{n}", f"/S01/Show S01E0{n}.mkv", 2, n) for n in range(2, 5)]
        assert kinds(nb.audit_numbering(episodes)) == {"e2": nb.MIXED, "e3": nb.MIXED, "e4": nb.MIXED}

    def test_split_season_with_part_left_behind_is_mixed(self):
        """S06E11-E19 that belong to S7: some moved, some stayed under S6."""
        episodes = [ep(f"s6-{n}", f"/S06/Show S06E{n:02d}.mkv", 6, n) for n in (10, 11, 12)]
        episodes += [ep("s6-14", "/S06/Show S06E14.mkv", 7, 4)]
        assert kinds(nb.audit_numbering(episodes)) == {"s6-14": nb.MIXED}

    def test_constant_offset_for_the_whole_folder_is_remapped(self):
        episodes = [ep(f"m{n}", f"/S04/Show S04E{n:02d}.mkv", 2006, n) for n in range(1, 4)]
        found = nb.audit_numbering(episodes)
        assert {f.kind for f in found} == {nb.REMAPPED}
        assert len(found) == 3

    def test_offsets_that_differ_are_a_mismatch(self):
        episodes = [ep("a", "/S03/Show S03E02.mkv", 3, 4), ep("b", "/S03/Show S03E03.mkv", 3, 2)]
        assert set(kinds(nb.audit_numbering(episodes)).values()) == {nb.MISMATCH}

    def test_part_of_a_folder_off_by_one_is_a_mismatch(self):
        episodes = [ep("ok", "/S02/Show S02E01.mkv", 2, 1), ep("off", "/S02/Show S02E14.mkv", 2, 13)]
        assert kinds(nb.audit_numbering(episodes)) == {"off": nb.MISMATCH}

    def test_missing_season_number_is_a_mismatch_not_a_crash(self):
        found = nb.audit_numbering([ep("a", "/S01/Show S01E03.mkv", None, 4)])
        assert kinds(found) == {"a": nb.MISMATCH}

    def test_allowlisted_series_are_skipped(self):
        episodes = [ep("m1", "/S04/Show S04E01.mkv", 2006, 1, series="99858")]
        assert nb.audit_numbering(episodes, {"99858"}) == []

    def test_files_without_a_code_are_ignored(self):
        assert nb.audit_numbering([ep("a", "/Show/Pilot.mkv", 1, 5)]) == []

    def test_sorted_most_serious_first(self):
        episodes = [ep("r", "/S04/B S04E01.mkv", 2006, 1, series="B", series_name="B"),
                    ep("u", "/S00/A S00E05.mkv", 0, None, series="A", series_name="A")]
        assert [f.kind for f in nb.audit_numbering(episodes)] == [nb.UNMATCHED, nb.REMAPPED]

    def test_as_dict_is_json_ready(self):
        finding = nb.audit_numbering([ep("u", "/S00/A S00E05.mkv", 0, None)])[0]
        data = finding.as_dict()
        assert data["file_number"] == [0, 5]
        assert data["emby_number"] == [0, None]
        assert data["kind"] == nb.UNMATCHED
