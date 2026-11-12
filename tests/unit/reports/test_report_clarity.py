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
    assert lines["Next step"] == "Run again with the frame check. Don't exclude yet."
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
    assert "score 0.0 · Dolby Vision profile 5, scored down" in html, "the near-zero score says why"


def test_warning_line_states_the_loss_once_and_leaves_the_count_to_the_legend():
    html = _render([_pair("success", keep={"rating": 10.0})])
    assert "Files were deleted and cannot be restored." in html
    assert "had a warning sign" not in html, "the legend already counts the second-look files"
    assert "of them <strong>1</strong> worth a second look" in html
    assert '<p class="run-line">Real run' not in html, "the badge under it already names the mode"
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


def test_bar_counts_second_look_files_once_and_a_pill_never_repeats_its_heading():
    html = _render([_pair("success", n=1, keep={"rating": 10.0}), _pair("success", n=2)])
    assert 'class="run-seg seg-check" style="flex-grow: 1"' in html
    assert 'class="run-seg seg-removed" style="flex-grow: 1"' in html, "2 removed = 1 second look + 1 plain"
    assert 'href="#removed"><span class="run-swatch seg-removed"></span>Removed <strong>2</strong>' in html
    assert 'class="pill pill-removed"' not in html, "the section heading already says Removed"
    assert "row above" not in html, "the legend already counts the second-look rows"
    dry = _render([_pair("not_attempted", n=1, keep={"rating": 10.0})])
    assert dry.count('class="pill pill-would_remove"') == 1, "Check first does not name the outcome, the pill does"


def test_kept_copy_link_is_a_labelled_target_and_equal_runtimes_are_marked():
    html = _render([_pair("skipped_unsafe", error=DIFFERENT)])
    assert 'aria-label="Open the kept copy of S01E01 in Emby">Open in Emby</a>' in html
    assert 'aria-label="Open the other copy of S01E01 in Emby">Open in Emby</a>' in html
    assert "runtime 45:00 on every file" in html, "an equal runtime is said once, not per file"
    assert "(kept 45:00)" not in html


def test_fact_colours_are_neutral_so_amber_only_means_check():
    from emby_dedupe.reports.html import _read_report_css

    css = _read_report_css()
    for legacy in ("#bd93f9; }", "#ffb86c; }", "#50fa7b; }", "#ff79c6; }"):
        assert f".fact-runtime {{ color: {legacy}" not in css
    assert ".fact-runtime { color: var(--soft); }" in css
    assert ".run-stats .stat-value { font-size: 1.5rem; color: var(--text-color); }" in css


def test_same_languages_in_another_track_order_name_only_the_default_track():
    """Same languages in another order are no language loss, but the default track changed."""
    html = _render([_pair("success", keep={"langs": ("eng", "cze")}, delete={"langs": ("cze", "eng")})])
    assert "eng cs vs cs eng" not in html
    assert '<span class="fact-audio">first audio track eng vs cs</span>' in html


def test_audio_line_never_shows_two_identical_values():
    """One of two removed copies has fewer tracks: the first variants match, so show both summaries."""
    files = [_pair("success", n=1, keep={"langs": ("cze", "fre")}, delete={"langs": ("cze", "fre")}),
             _pair("success", n=2, keep={"langs": ("cze", "fre")}, delete={"langs": ("cze",)})]
    row = _rows(files, "removed")[0]
    audio = next(p["text"] for p in row["diff_parts"] if p["kind"] == "audio")
    left, right = audio.split(" vs ")
    assert left != right


def test_runtime_gap_with_unsure_pictures_is_never_offered_for_excluding():
    """The frame check could not prove the copies differ, so excluding would hide a real duplicate."""
    row = _rows([_held(RUNTIME_UNSURE)], "blocked")[0]
    step = {line["label"]: line["text"] for line in row["shared"]}["Next step"]
    assert step == "Compare both in Emby before deciding. Don't exclude yet."
    assert row["excludable"] is False
    assert "pictures unclear" in row["diff_text"]
    proven = _rows([_held("duration-mismatch (x); frame-check different (0.10)")], "blocked")[0]
    assert proven["excludable"] is True
    assert "exclude the pair" in {line["label"]: line["text"] for line in proven["shared"]}["Next step"]


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


def test_same_format_names_a_large_size_gap():
    """A 12x size gap behind an identical format label must not read as "same quality"."""
    row = _rows([_pair("success", keep={"size_gb": 72.98}, delete={"size_gb": 6.21})], "removed")[0]
    assert row["diff_parts"][0]["text"] == "same format, kept copy 12× larger"
    close = _rows([_pair("success", keep={"size_gb": 7.0}, delete={"size_gb": 6.0})], "removed")[0]
    assert close["diff_parts"][0]["text"] == "same format"


def test_removed_row_names_a_pal_speed_up():
    """A PAL pair passes the guard; the closed row still says why the runtimes differ."""
    row = _rows([_pair("success", keep={"secs": 6279}, delete={"secs": 6019})], "removed")[0]
    assert "(PAL speed-up)" in row["diff_text"]
    plain = _rows([_pair("success", keep={"secs": 2700}, delete={"secs": 2690})], "removed")[0]
    assert "min" not in plain["diff_text"]


def test_excluded_group_links_to_the_provider_that_owns_the_id():
    """A bare number is a TMDb or TVDB id; only the item's own ProviderIds say which."""
    from emby_dedupe.api.deduplication import _provider_link

    film = {"Type": "Movie", "ProviderIds": {"Imdb": "tt0111", "Tmdb": "42"}}
    assert _provider_link(film, "tt0111") == "https://www.imdb.com/title/tt0111"
    assert _provider_link(film, "42") == "https://www.themoviedb.org/movie/42"
    show = {"Type": "Series", "ProviderIds": {"Tmdb": "77"}}
    assert _provider_link(show, "77") == "https://www.themoviedb.org/tv/77"
    episode = {"Type": "Episode", "ProviderIds": {"Tvdb": "2548471"}}
    assert _provider_link(episode, "2548471") == "", "a TVDB episode id has no TMDb page"
    tmdb_episode = {"Type": "Episode", "ProviderIds": {"Tmdb": "5"}}
    assert _provider_link(tmdb_episode, "5") == "", "a TMDb episode id is not a show id"
    assert _provider_link({"ProviderIds": {"Tmdb": "6"}}, "6") == "", "no type, no guess"


def test_a_kept_file_shared_by_two_removed_copies_is_shown_once():
    """Two copies of one episode removed: the second line says "same kept file", not a second keeper."""
    a = _pair("success", n=1)
    b = _pair("success", n=1, delete={"size_gb": 2.0})
    b["delete"][0]["id"] = "d-second"
    b["delete"][0]["path"] = b["delete"][0]["path"].replace("HDTV", "WEB")
    html = _render([{"keep": a["keep"], "delete": a["delete"] + b["delete"]}])
    assert html.count('class="pair-line pair-keep"') == 1, "the kept file is shown once for both copies"
    assert html.count('class="pair-line pair-other"') == 2
    assert 'class="pair pair-cont"' in html


def test_identical_sides_are_described_once():
    """Two copies with the same format and audio: one "Both" line, not the same text twice."""
    row = _rows([_held(DIFFERENT)], "blocked")[0]
    labels = [line["label"] for line in row["shared"]]
    assert labels[0] == "Both"
    assert "Kept" not in labels


def test_header_time_is_local_like_the_cleanup_report(monkeypatch):
    import time

    from emby_dedupe.reports.html import _human_time

    monkeypatch.setenv("TZ", "Europe/Bratislava")
    time.tzset()
    try:
        assert _human_time("2026-11-12T08:36:57+00:00") == "12 Nov 2026 09:36"
        assert _human_time("2026-07-01T08:05:00+00:00") == "1 Jul 2026 10:05", "summer time, no leading zero"
        assert _human_time("not a stamp") == "not a stamp"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_a_long_server_error_is_cut_on_the_row_but_kept_whole_in_the_data():
    body = "HTTP 500: " + "<html><body>Internal Server Error</body></html>\n" * 40
    d = _pair("failed", error=body)
    row = _rows([d], "failed")[0]
    shown = {line["label"]: line["text"] for line in row["shared"]}["Error"]
    assert len(shown) <= 240
    assert shown.endswith("…")
    assert "\n" not in shown
    assert row["reason"] == body, "the raw message stays in the data"


def test_unbroken_names_wrap_instead_of_widening_the_page():
    html = _render([_pair("success", series="A" * 180)])
    body_rule = html[html.index("body {"):html.index("}", html.index("body {"))]
    assert "overflow-wrap: anywhere;" in body_rule
