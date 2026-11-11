"""Report wording must be literally true: plain reasons per guard rule, a next step for every
held pair, keeper reasons that match the pair's own data, and HDR labels without repeats."""

from __future__ import annotations

from tests.unit.reports.test_report_v2 import _pair, _render, _rows

UNAVAILABLE = "frame-check unavailable: the same picture is not proven, so both copies stay"
DIFFERENT = "frame-check different (0.10, 0.10)"
RUNTIME_UNSURE = ("duration-mismatch (keeper 3235s vs delete 2435s); not proven that the kept copy holds all of it: "
                  "frame-check unsure (0.30), 45s of the copy to delete not found in the kept copy")


def _held(error, n=1, series="Show"):
    return _pair("skipped_unsafe", n=n, series=series, error=error)


def test_frame_check_verdicts_get_their_own_reason_codes():
    rows = _rows([_held(UNAVAILABLE, 1, "A"), _held(DIFFERENT, 1, "B"), _held(RUNTIME_UNSURE, 1, "C")], "blocked")
    by_code = sorted(r["reason_code"] for r in rows)
    assert by_code == ["picture_check_missing", "pictures_differ", "runtime"], "runtime wins over its embedded verdict"


def test_closed_row_names_the_rule_in_plain_words():
    html = _render([_held(UNAVAILABLE, 1, "A"), _held(DIFFERENT, 1, "B")])
    assert 'class="fact-reason">picture check didn&#39;t run</span>' in html
    assert 'class="fact-reason">pictures differ</span>' in html
    assert "guard refused" not in html


def test_every_held_pair_gets_a_next_step_and_unproven_pairs_are_not_excluded():
    row = _rows([_held(UNAVAILABLE)], "blocked")[0]
    lines = {line["label"]: line["text"] for line in row["shared"]}
    assert "Guard" not in lines, "the closed row names the reason; the body gives only the next step"
    assert lines["Next step"] == "run again with the frame check; don't exclude yet"
    assert row["reason"] == UNAVAILABLE, "the raw message stays in the data"
    differ = {line["label"]: line["text"] for line in _rows([_held(DIFFERENT)], "blocked")[0]["shared"]}
    assert "exclude the pair" in differ["Next step"]


def test_keeper_reason_is_only_given_when_the_language_decided_this_pair():
    lang = {"selected_by_language_priority": True, "priority_language_used": "cs"}
    both_cs = _rows([_pair("success", keep={"rating": 28.0, **lang}, delete={"rating": 28.0})], "removed")[0]
    assert both_cs["files"][0]["keep_note"] == "", "both copies carry cs: the language did not decide"
    only_kept = _rows([_pair("success", keep={"rating": 28.0, **lang}, delete={"rating": 28.0, "langs": ("eng",)})],
                      "removed")[0]
    assert only_kept["files"][0]["keep_note"] == "kept for its cs audio", "a tie claims no better score"
    html = _render([_pair("success", keep={"rating": 20.0, **lang}, delete={"rating": 30.0, "langs": ("eng",)})])
    assert "kept for its cs audio, over a copy with the better score" in html


def test_hdr10_is_not_named_twice_and_dolby_vision_loss_is_named():
    keep = {"res": "1440p HDR 10 HEVC", "video": {"video_range": "HDR10"}, "rating": 63.9}
    delete = {"res": "1440p Dolby Vision HEVC", "rating": 0.0066,
              "video": {"video_range": "DolbyVision", "dv_profile": "dvhe.05", "is_dovi_p5": True}}
    html = _render([_pair("success", keep=keep, delete=delete)])
    assert "HDR 10 HEVC HDR10" not in html
    assert ">loses Dolby Vision</span>" in html
    assert '<span class="score-note">DV P5 penalty</span>' in html


def test_warning_line_asks_for_a_check_in_emby_not_on_disk_now():
    html = _render([_pair("success", keep={"rating": 10.0})])
    assert "1 removal had a warning sign: check the kept copy in Emby when you can." in html
    assert "on disk now" not in html


def test_bulk_exclude_offers_only_pairs_shown_to_differ():
    differ = _pair("skipped_unsafe", n=1, series="A", error=DIFFERENT, keep={"ids": {"Imdb": "tt1"}}, delete={"ids": {"Imdb": "tt1"}})
    unproven = _pair("skipped_unsafe", n=1, series="B", error=UNAVAILABLE, keep={"ids": {"Imdb": "tt2"}},
                     delete={"ids": {"Imdb": "tt2"}})
    html = _render([differ, unproven])
    assert "<code>--exclude-ids tt1</code>" in html, "only the pair whose pictures differ"
    assert "tt2</code>" not in html.split('class="run-decide"')[1].split("</details>")[0]
    assert "Not for excluding (1 pair): each row&#39;s next step says what to do instead" in html
    assert "for good" not in html


def test_unproven_rows_carry_no_exclude_hint():
    rows = _rows([_pair("skipped_unsafe", error=UNAVAILABLE, keep={"ids": {"Imdb": "tt9"}}, delete={"ids": {"Imdb": "tt9"}})],
                 "blocked")
    assert rows[0]["excludable"] is False
    html = _render([_pair("skipped_unsafe", error=UNAVAILABLE, keep={"ids": {"Imdb": "tt9"}}, delete={"ids": {"Imdb": "tt9"}})])
    assert 'class="exclude-hint"' not in html


def test_bar_counts_second_look_files_once_and_pills_show_only_in_the_mixed_section():
    html = _render([_pair("success", n=1, keep={"rating": 10.0}), _pair("success", n=2)])
    assert 'class="run-seg seg-check" style="flex-grow: 1"' in html
    assert 'class="run-seg seg-removed" style="flex-grow: 1"' in html, "2 removed = 1 second look + 1 plain"
    assert 'href="#removed"><span class="run-swatch seg-removed"></span>Removed <strong>2</strong>' in html
    assert html.count('class="pill pill-removed"') == 1, "only the second-look row carries the pill"


def test_kept_copy_link_is_a_labelled_target_and_equal_runtimes_are_marked():
    html = _render([_pair("skipped_unsafe", error=DIFFERENT)])
    assert ">Kept copy</a>" in html
    assert ">Other copy</a>" in html
    assert 'class="pf-num fact-runtime same-as-kept"' in html, "same runtime on both sides is hidden on a phone"


def test_fact_colours_are_neutral_so_amber_only_means_check():
    from emby_dedupe.reports.html import _read_report_css

    css = _read_report_css()
    for legacy in ("#bd93f9; }", "#ffb86c; }", "#50fa7b; }", "#ff79c6; }"):
        assert f".fact-runtime {{ color: {legacy}" not in css
    assert ".fact-runtime { color: var(--soft); }" in css
    assert ".run-stats .stat-value { font-size: 1.5rem; color: var(--text-color); }" in css


def test_same_languages_in_another_track_order_are_not_a_difference():
    html = _render([_pair("success", keep={"langs": ("eng", "cze")}, delete={"langs": ("cze", "eng")})])
    assert "eng cs vs cs eng" not in html
    assert '<span class="fact-audio">' not in html.split('class="crow-line2"')[1].split("</span>\n")[0]


def test_runtime_next_step_agrees_with_the_exclude_hint():
    row = _rows([_held(RUNTIME_UNSURE)], "blocked")[0]
    step = {line["label"]: line["text"] for line in row["shared"]}["Next step"]
    assert "a different cut or episode: exclude the pair" in step
    assert "really are the same" not in step


def test_a_multi_file_row_keeps_the_plain_reason_when_scores_differ():
    files = [_pair("skipped_unsafe", n=n, error=f"frame-check different (0.{n}0, 0.{n}0)") for n in (1, 2, 3)]
    row = _rows(files, "blocked")[0]
    assert row["reason_code"] == "pictures_differ"
    assert row["diff_parts"][-1]["text"] == "pictures differ", "the closed row says it once for all files"
    from emby_dedupe.reports.clusters import _guard_line

    assert _guard_line(row, row["files"]).endswith("(details per file below)")


def test_every_guard_rule_has_plain_words_and_a_next_step():
    from emby_dedupe.reports.clusters import _REASON_CODES, _SHORT_REASON, NEXT_STEP

    for _token, code in _REASON_CODES:
        assert code in NEXT_STEP, code
        assert code == "runtime" or code in _SHORT_REASON, code
    watched = "watched-copy: Jo watched the copy to delete and the kept copy shares no provider id"
    row = _rows([_held(watched)], "blocked")[0]
    assert row["reason_code"] == "watched_copy"
    assert row["excludable"] is False


def test_language_summaries_compare_variant_for_variant():
    from emby_dedupe.reports.clusters import _same_langs

    assert _same_langs("eng cs", "cs eng")
    assert not _same_langs("cs eng / cs", "eng cs"), "a file with other languages is a difference"


def test_dry_run_wording_and_print_rules():
    html = _render([_pair("would_block", error=UNAVAILABLE), _pair("not_attempted", n=2, keep={"rating": 10.0})])
    assert "Check first" in html
    assert "picture check didn&#39;t run" in html
    assert "Dry run" in html
    assert ".pairs td.same-as-kept { display: table-cell; }" not in html, "print shows all cells already"


def test_one_blocked_delete_is_not_called_all_1():
    html = _render([_pair("would_block", error=UNAVAILABLE)])
    assert "The safety guard would block the only delete." in html
    assert "all 1 delete" not in html
