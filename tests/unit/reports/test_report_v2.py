"""Report design v2 (2026-10-11, four-reviewer pass on the real output).

Every rule here comes from a finding on a shipped report: the verdict card is the only
statistics block, risk flags show on refused pairs as notes, summaries never hide a
copy, the --exclude-ids hint names an IMDb id, the page carries run provenance, ids,
data attributes and a JSON twin, and refused pairs remember how often they were seen.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from emby_dedupe.reports.clusters import _reason_text, _summarise, build_clusters
from emby_dedupe.reports.history import load_history, pair_key, record_run
from emby_dedupe.reports.html import build_report_context, format_html_report, generate_html_report

BASE = "https://emby.example.com:443"
GB = 1024**3
SEC = 10_000_000


def _item(item_id, path, *, rating=50.0, langs=("cze",), res="1080p", secs=2700, size_gb=1.0,
          video=None, series="", season=None, episode=None, status=None, error=None, ids=None, **extra):
    item = {
        "id": item_id, "name": f"name-{item_id}", "path": path, "serverid": "s1", "rating": rating,
        "runtime_ticks": secs * SEC, "provider_ids": ids or {},
        "quality_description": {"size": int(size_gb * GB), "path": path, "date_added": "2026-10-01",
                                "video": {"codec": "h264", "resolution": res, **(video or {})},
                                "audio": {"codec": "ac3", "channels": 6, "languages": list(langs)},
                                "source_quality_multiplier": 1.0, "score_summary": f"score {rating:.1f} = res 1 + bitrate 1"},
        **extra,
    }
    if series:
        item.update(is_episode=True, series_name=series, season_number=season, episode_number=episode)
    if status:
        item["deletion_result"] = {"status": status, "error": error}
    return item


def _pair(status="success", *, keep=None, delete=None, error=None, n=1, series="Show", season=1):
    folder = f"/S/{series}/S{season:02d}"
    keep_kw = {"rating": 90.0, **(keep or {})}
    delete_kw = {"rating": 50.0, **(delete or {})}
    k = _item(f"k{season}{n}", f"{folder}/{series} S{season:02d}E{n:02d} - CZ.mkv", series=series, season=season,
              episode=n, **keep_kw)
    d = _item(f"d{season}{n}", f"{folder}/{series} S{season:02d}E{n:02d} - HDTV.mkv", series=series, season=season,
              episode=n, status=status, error=error, **delete_kw)
    return {"keep": k, "delete": [d]}


def _render(decisions, metadata=None):
    with patch("emby_dedupe.reports.images.inline_poster_urls", side_effect=lambda urls, *a, **k: {}):
        return format_html_report(BASE, decisions, metadata or {})


def _rows(decisions, section, history=None):
    return build_clusters(decisions, BASE, history)["sections"][section]


# ---- flags on every outcome ---------------------------------------------------------

def test_refused_pair_shows_its_flags_as_notes_not_checks():
    """Imma Tataranni's '(2)' names were invisible on blocked rows (data review, finding 2)."""
    d = _pair("skipped_unsafe", error="duration-mismatch (keeper 3127s vs delete 3506s)",
              delete={"langs": ("eng",), "secs": 3506})
    d["delete"][0]["path"] = d["delete"][0]["path"].replace(".mkv", " (2).mkv")
    row = _rows([d], "blocked")[0]

    assert row["risk"] is False
    assert set(row["flags"]) == {"language_loss", "dup_suffix"}
    assert "other copy has a language the kept one lacks" in row["risk_text"]
    html = _render([d])
    assert 'class="chip chip-note" title="other copy has a language the kept one lacks">loses eng</span>' in html
    assert 'class="chip chip-note" title="&#39;(n)&#39; file name">&#39;(n)&#39; name</span>' in html
    assert 'class="chip chip-check"' not in html


def test_refused_pairs_with_different_flags_stay_one_row():
    """Notes are aggregated; only a deletion is split by its flags."""
    a = _pair("skipped_unsafe", n=1, error="duration-mismatch (x)", delete={"langs": ("eng",)})
    b = _pair("skipped_unsafe", n=2, error="duration-mismatch (x)")
    rows = _rows([a, b], "blocked")

    assert len(rows) == 1 and rows[0]["count"] == 2
    assert rows[0]["flags"] == ["language_loss"]


def test_hdr_in_the_quality_label_and_hdr_loss_flag():
    d = _pair("success", keep={"video": {"video_range": "SDR"}},
              delete={"video": {"video_range": "HDR10", "resolution": "2160p"}, "res": "2160p"})
    row = _rows([d], "removed")[0]

    assert row["delete_quality"] == "2160p HDR10 WEB-DL"
    assert "hdr_loss" in row["flags"] and row["risk"]


def test_dolby_vision_label_is_not_doubled():
    d = _pair("success", keep={"res": "4K Dolby Vision HEVC", "video": {"video_range": "DolbyVision", "dv_profile": "DoviProfile8"}})
    assert _rows([d], "removed")[0]["keep_quality"] == "4K Dolby Vision HEVC WEB-DL"


def test_a_file_name_that_lies_about_its_resolution_is_flagged():
    """Call the Midwife S13: 480p copies named '1080p HDTV.mkv' hid behind '+3 more (mixed)'."""
    d = _pair("skipped_unsafe", error="duration-mismatch (x)", delete={"res": "480p"})
    assert d["delete"][0]["path"].endswith("1080p HDTV.mkv") is False  # fixture sanity
    d["delete"][0]["path"] = "/S/Show/S01/Show S01E01 - 1080p HDTV.mkv"
    row = _rows([d], "blocked")[0]

    assert "name_lies" in row["flags"]
    assert "file name claims a resolution the video does not have" in row["risk_text"]


# ---- summaries that hide nothing ----------------------------------------------------

def test_summary_lists_every_distinct_value_most_common_first():
    assert _summarise(["480p HDTV"] + ["1080i HDTV"] * 7) == "1080i HDTV / 480p HDTV"
    assert _summarise(["1080p"] * 3) == "1080p"


def test_runtime_chip_names_the_ratio_and_its_usual_cause():
    files = [{"keep": {"seconds": 3127}, "delete": {"seconds": 3506}}]
    assert _reason_text("runtime", files, "") == "runtime 52 vs 58 min (×1.12, different cut)"
    pal = [{"keep": {"seconds": 2500}, "delete": {"seconds": 2604}}]
    assert "PAL speed-up" in _reason_text("runtime", pal, "")
    double = [{"keep": {"seconds": 720}, "delete": {"seconds": 1335}}]
    assert "half or double episode" in _reason_text("runtime", double, "")
    extras = [{"keep": {"seconds": 2700}, "delete": {"seconds": 2790}}]
    assert "extras or recap" in _reason_text("runtime", extras, "")


def test_row_shows_the_narrowest_score_margin_and_lost_languages():
    a = _pair("success", n=1, keep={"rating": 30.0}, delete={"rating": 29.0, "langs": ("eng", "cze")})
    b = _pair("success", n=2, keep={"rating": 90.0}, delete={"rating": 10.0})
    rows = _rows([a, b], "removed")
    risky = [r for r in rows if r["risk"]][0]

    assert risky["score_text"] == "30.0 vs 29.0"
    assert risky["lang_loss"] == "eng"
    assert risky["langs_text"] == "cs / eng cs"


def test_files_inside_a_row_are_sorted_by_episode():
    rows = _rows([_pair("success", n=n) for n in (2, 1, 4, 3)], "removed")
    assert [f["episode"] for f in rows[0]["files"]] == ["S01E01", "S01E02", "S01E03", "S01E04"]


def test_removed_rows_sort_by_size_freed_after_the_risky_ones():
    small = _pair("success", n=1, delete={"size_gb": 0.5})
    big = _pair("success", n=2, series="Other", delete={"size_gb": 8.0})
    rows = _rows([small, big], "removed")
    assert [r["name"] for r in rows] == ["Other", "Show"]


# ---- the exclude hint -----------------------------------------------------------------

def test_exclude_hint_prefers_the_imdb_id_and_appears_on_every_outcome():
    """A bare number matches TMDB and TVDB alike (data review, finding 5)."""
    ids = {"Tvdb": "9514669", "Imdb": "tt24786748", "Tmdb": "176986"}
    blocked = _pair("skipped_unsafe", n=1, error="duration-mismatch (x)", keep={"ids": ids}, delete={"ids": ids})
    removed = _pair("not_attempted", n=2, keep={"ids": ids}, delete={"ids": ids})
    clusters = build_clusters([blocked, removed], BASE)

    assert clusters["sections"]["blocked"][0]["exclude_ids"] == ["tt24786748"]
    assert clusters["sections"]["would_remove"][0]["exclude_ids"] == ["tt24786748"]
    html = _render([blocked, removed])
    assert html.count('<code class="ids"><span>tt24786748</span></code>') == 2
    assert "Don't want this removed?" in html and "Keep both copies for good?" in html


def test_numeric_only_ids_come_with_a_collision_warning():
    ids = {"Tvdb": "9514669"}
    d = _pair("skipped_unsafe", error="duration-mismatch (x)", keep={"ids": ids}, delete={"ids": ids})
    html = _render([d])
    assert "a bare number matches TMDB and TVDB alike" in html


def test_decide_block_aggregates_the_guarded_pairs():
    ids_a, ids_b = {"Imdb": "tt1"}, {"Imdb": "tt2"}
    decisions = [_pair("skipped_unsafe", n=1, error="x", keep={"ids": ids_a}, delete={"ids": ids_a}),
                 _pair("skipped_unsafe", n=2, series="B", error="x", keep={"ids": ids_b}, delete={"ids": ids_b})]
    html = _render(decisions)
    assert "<code>--exclude-ids tt1,tt2</code>" in html


# ---- the verdict card is the only statistics block -----------------------------------

def test_summary_statistics_cards_are_gone():
    html = _render([_pair("success")])
    assert "Summary Statistics" not in html
    assert 'class="stats-container"' not in html
    assert "Items to Remove" not in html


def test_reconcile_sentence_uses_one_vocabulary():
    html = _render([_pair("success", n=1, delete={"size_gb": 1.0}),
                    _pair("skipped_unsafe", n=2, error="x", delete={"size_gb": 2.0})])
    assert "2 duplicate copies in 2 groups: 1 removed, 1 blocked by the guard, 0 failed." in html
    assert "(4.00 GB with the copies that remain)" in html, "keepers 2 GB + the blocked 2 GB copy"


def test_dry_run_with_nothing_to_remove_says_so():
    html = _render([_pair("would_block", error="duration-mismatch (x)")])
    assert "<h1 id=\"verdict\" class=\"run-headline\">Nothing would be removed.</h1>" in html
    assert "Dry run · nothing deleted" in html and 'class="run-warning"' not in html
    assert "<title>Dry run: Nothing would be removed.</title>" in html


def test_failures_lead_the_headline_and_the_bar():
    html = _render([_pair("success", n=1), _pair("failed", n=2, error="HTTP 500")])
    assert "1 delete failed. 1 file removed," in html
    assert html.index('class="run-seg seg-failed"') < html.index('class="run-seg seg-removed"')
    assert "Files were deleted and cannot be restored." in html


def test_risky_rows_open_first_in_their_own_section():
    html = _render([_pair("success", n=1, keep={"rating": 10.0}), _pair("success", n=2)])
    check = html.index('id="check"')
    assert check < html.index('id="removed"')
    assert 'class="crow risky" id="row-removed-show-s01-1" data-outcome="removed" data-reason="" data-flags="score_inversion" open>' in html
    assert "Removed · 2 files" in html and "1 row above, worth a second look" in html
    assert "Removed, worth a second look · 1 file · 1 row" in html and "Already removed." in html
    dry = _render([_pair("not_attempted", n=1, keep={"rating": 10.0})])
    assert "Check first · 1 file · 1 row" in dry and "before --doit" in dry


def test_empty_sections_are_one_line_and_zero_state_has_no_bar():
    html = _render([], {"excluded_ids": [], "excluded_groups_count": 0, "excluded_titles": {}})
    assert "No duplicates found." in html
    assert 'class="run-bar"' not in html
    assert "No ids excluded." in html


# ---- provenance, anchors, machine readability --------------------------------------

def test_header_and_footer_carry_the_run_provenance():
    run = {"generated_at": "2026-10-11T13:33:00+00:00", "version": "3.4.0", "host": "emby-gpu",
           "argv": "emby-curator dedupe --library Movies", "libraries": ["Movies"], "run_id": "1791726000"}
    html = _render([_pair("success")], {"run": run})
    assert "2026-10-11T13:33:00+00:00" in html and "library Movies" in html
    assert "emby-curator 3.4.0 · run 1791726000 · host emby-gpu" in html
    assert '<meta name="generator" content="emby-curator 3.4.0">' in html
    assert '<p class="eyebrow">Emby Curator · dedupe report</p>' in html


def test_every_section_and_row_has_an_anchor_and_data_attributes():
    html = _render([_pair("success")])
    assert 'id="removed"' in html and 'href="#removed"' in html
    assert 'id="row-removed-show-s01-1"' in html
    assert 'data-keep-id="k11"' in html and 'data-item-id="d11"' in html
    assert 'rel="noopener"' in html and 'target="_blank" class' not in html


def test_json_island_matches_the_twin_and_carries_no_credential():
    ctx = build_report_context(BASE, [_pair("success")], {})
    island = ctx["report_json_island"]
    data = json.loads(island)

    assert data == json.loads(ctx["report_json"])
    assert data["sections"][2]["outcome"] == "removed"
    row = data["sections"][2]["rows"][0]
    assert row["files"][0]["keep"]["path"].endswith("S01E01 - CZ.mkv")
    assert "api_key" not in island and "image" not in json.dumps(row)
    assert "</" not in island


def test_per_file_evidence_is_in_the_row_body():
    """Size, added date, audio codec and the score breakdown lived only in the folded cards."""
    html = _render([_pair("success")])
    body = html[html.index('class="crow-body"'):]
    assert "1.00 GB" in body and '<span class="fact-audio">ac3 5.1 cs</span> · <span class="fact-date">added 2026-10-01</span>' in body
    assert "score 90.0 = res 1 + bitrate 1" in body
    assert '<dt>Folder</dt><dd><span class="fact-path">/S/Show/S01</span></dd>' in body
    assert 'title="/S/Show/S01/Show S01E01 - CZ.mkv">CZ.mkv</span>' in body
    assert 'class="legacy-groups"' not in html


def test_print_opens_every_row_and_paths_wrap_at_separators():
    html = _render([_pair("success")])
    assert ".crow::details-content { content-visibility: visible;" in html
    assert ".pf-name { font-family: var(--mono); font-size: 0.82rem; color: #e6e3db; overflow-wrap: anywhere; }" in html


# ---- blocked in N runs ----------------------------------------------------------------

def test_history_counts_consecutive_refusals_and_forgets_resolved_pairs(tmp_path):
    path = tmp_path / "h.json"
    blocked = _pair("skipped_unsafe", error="x")
    assert record_run([blocked], path) == {pair_key(blocked["keep"], blocked["delete"][0]): 1}
    assert record_run([blocked], path)[pair_key(blocked["keep"], blocked["delete"][0])] == 2
    assert load_history(path) == {pair_key(blocked["keep"], blocked["delete"][0]): 2}
    assert record_run([_pair("success")], path) == {}
    assert load_history(path) == {}


def test_rows_show_blocked_in_n_runs():
    blocked = _pair("skipped_unsafe", error="duration-mismatch (x)")
    key = pair_key(blocked["keep"], blocked["delete"][0])
    assert _rows([blocked], "blocked", {key: 3})[0]["blocked_runs"] == 3
    assert "blocked in 3 runs" in _render([blocked], {"history": {key: 3}})


def test_generate_writes_the_twin_beside_the_page_and_records_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUPE_HISTORY_FILE", str(tmp_path / "history.json"))
    with patch("tempfile.gettempdir", return_value=str(tmp_path)), \
         patch("emby_dedupe.reports.images.inline_poster_urls", side_effect=lambda urls, *a, **k: {}):
        html_path = generate_html_report(BASE, [_pair("skipped_unsafe", error="x")], {})

    json_path = html_path[:-5] + ".json"
    twin = json.loads(open(json_path, encoding="utf-8").read())
    assert twin["sections"][4]["files"] == 1
    assert json_path.rsplit("/", 1)[1] in open(html_path, encoding="utf-8").read()
    assert load_history(tmp_path / "history.json") != {}


def test_unreadable_history_is_ignored(tmp_path):
    bad = tmp_path / "h.json"
    bad.write_text("{not json")
    assert load_history(bad) == {}


# ---- posters ----------------------------------------------------------------------------

def test_series_poster_wins_over_the_episode_still():
    d = _pair("success")
    d["keep"]["image_class"] = "img-episode"
    d["keep"]["series_poster"] = {"image_url": "data:image/jpeg;base64,AAAA", "image_class": "img-series"}
    assert _rows([d], "removed")[0]["image_class"] == "img-series"


def test_other_copy_shows_its_own_poster_only_when_it_differs():
    d = _pair("success")
    d["keep"]["image_class"] = "img-a"
    d["delete"][0]["image_class"] = "img-b"
    html = _render([d])
    assert 'class="thumb thumb-xs poster img-b"' in html
    d["delete"][0]["image_class"] = "img-a"
    assert 'class="thumb thumb-xs' not in _render([d])


@pytest.mark.parametrize("status,outcome", [("success", "removed"), ("skipped_unsafe", "blocked")])
def test_counts_reconcile_for_every_outcome(status, outcome):
    clusters = build_clusters([_pair(status, error="x")], BASE)
    assert clusters["totals"][outcome] == 1 and clusters["sizes"][outcome] == GB


# ---- series poster from the scan -------------------------------------------------------

def test_enrich_keep_item_attaches_the_series_poster_as_an_image_holder():
    """The row for a season shows the series art; the inliner must find it (image_url key)."""
    from emby_dedupe.api.deduplication import _enrich_keep_item

    keep = {"id": "e1", "serverid": "s1"}
    details = [{"Id": "e1", "Name": "Pilot", "ImageTags": {"Primary": "still"}, "SeriesName": "Show",
                "SeriesId": "series9", "SeriesPrimaryImageTag": "art7", "ParentIndexNumber": 1, "IndexNumber": 1}]
    _enrich_keep_item(keep, details, "http://emby", "k")

    assert keep["series_poster"]["image_url"] == "http://emby/Items/series9/Images/Primary?tag=art7&quality=80&maxHeight=300&api_key=k"
    assert "api_key" in keep["image_url"]


def test_enrich_keep_item_without_series_art_has_no_series_poster():
    from emby_dedupe.api.deduplication import _enrich_keep_item

    keep = {"id": "m1", "serverid": "s1"}
    _enrich_keep_item(keep, [{"Id": "m1", "Name": "Film", "ImageTags": {"Primary": "p"}}], "http://emby", "k")

    assert "series_poster" not in keep


def test_language_priority_is_mentioned_only_when_it_changed_a_decision():
    quiet = _render([_pair("success")], {"language_priorities": ["sk", "cs", "eng"]})
    assert "Language priority" not in quiet
    d = _pair("success")
    d["keep"]["changed_by_language_priority"] = True
    loud = _render([d], {"language_priorities": ["sk", "cs", "eng"]})
    assert "Language priority sk, cs, eng changed 1 decision." in loud


# ---- row body: shared facts once, one line per episode ----------------------------------

def test_row_body_says_constant_facts_once_and_marks_the_odd_file():
    """Fuki's review of V3: eight episodes printed the same folder, verdict and facts eight times."""
    files = [_pair("skipped_unsafe", n=n, error="duration-mismatch (x)", delete={"res": "1080i" if n != 5 else "1080p"})
             for n in range(1, 9)]
    row = _rows(files, "blocked")[0]
    labels = [line["label"] for line in row["shared"]]

    assert labels == ["Folder", "Kept", "Other", "Guard"]
    assert row["shared"][0]["text"] == "/S/Show/S01"
    assert row["shared"][1]["text"] == "1080p WEB-DL · ac3 5.1 cs · added 2026-10-01"
    assert row["shared"][2]["text"] == "1080i WEB-DL · ac3 5.1 cs · added 2026-10-01", "the majority value is the row's fact"
    assert row["shared"][3]["text"] == "runtime 45 vs 45 min (×1.00, different cut), beyond the guard's tolerance"
    odd = [f for f in row["files"] if f["delete"]["vary"]]
    assert len(odd) == 1 and odd[0]["episode"] == "S01E05" and odd[0]["delete"]["vary"] == {"video": "1080p WEB-DL"}
    html = _render(files)
    page = html[:html.index('<script type="application/json"')]
    assert page.count("/S/Show/S01") == 1 + 16, "folder once in the shared block, then only in title tooltips"
    assert '<span class="pf-vary vary-video" title="video differs from the rest of the row">1080p WEB-DL</span>' in html


def test_short_file_names_drop_the_episode_prefix():
    from emby_dedupe.reports.clusters import _short_name

    assert _short_name("Call the Midwife S12E01 - 1080p x264 CZ.mkv", True) == "1080p x264 CZ.mkv"
    assert _short_name("Show S01E01.mkv", True) == "Show S01E01.mkv"
    assert _short_name("Mrs Doubtfire (1993) - 1080p x264 CZ.mkv", False) == "Mrs Doubtfire (1993) - 1080p x264 CZ.mkv"


def test_mixed_statuses_in_a_row_are_shown_per_file():
    a = _pair("success", n=1)
    b = _pair("fold_safe_removed", n=2)
    row = _rows([a, b], "removed")[0]
    assert row["per_file_status"] is True
    html = _render([a, b])
    assert '<span class="pf-reason">Deleted (file only, over SSH)</span>' in html


def test_closed_row_second_line_says_only_what_differs():
    """Fuki: the line "1080p H264 HDTV vs 1080i H264 HDTV / 1080p H264 HDTV · cs orig vs eng audio ·
    runtime 52 vs 58 min (×1.12, different cut)" was too long."""
    from emby_dedupe.reports.clusters import _quality_diff

    assert _quality_diff("1080p H264 HDTV", "1080i H264 HDTV") == "1080p vs 1080i"
    assert _quality_diff("4K Dolby Vision HEVC BluRay", "1080p H264 HDTV") == "4K Dolby Vision HEVC BluRay vs 1080p H264 HDTV"
    assert _quality_diff("1080p H264 HDTV", "1080p H264 HDTV") == "same quality"
    files = [_pair("would_block", n=n, error="duration-mismatch (x)",
                   keep={"res": "1080p", "secs": 3127}, delete={"res": "1080i" if n != 5 else "1080p", "secs": 3506, "langs": ("eng",)})
             for n in range(1, 9)]
    row = _rows(files, "would_block")[0]
    assert row["diff_text"] == "1080p vs 1080i · cs vs eng · 52 vs 58 min"
    half = _pair("would_block", error="duration-mismatch (x)", keep={"secs": 720}, delete={"secs": 1335})
    assert _rows([half], "would_block")[0]["diff_text"].endswith("12 vs 22 min (half or double episode)")


def test_facts_carry_their_kind_so_each_gets_one_colour():
    """Fuki: V4 looked drained next to the cleanup report's coloured badges."""
    d = _pair("would_block", error="duration-mismatch (x)", keep={"res": "1080p", "secs": 3127},
              delete={"res": "1080i", "secs": 3506, "langs": ("eng",)})
    row = _rows([d], "would_block")[0]
    assert [p["kind"] for p in row["diff_parts"]] == ["quality", "audio", "runtime"]
    assert [p["kind"] for p in row["shared"][1]["parts"]] == ["quality", "audio", "date"]
    html = _render([d])
    assert '<span class="fact-quality">1080p vs 1080i</span> · <span class="fact-audio">cs vs eng</span>' in html
    assert '<td class="pf-num fact-size" data-l="Size">1.00 GB</td>' in html


def test_stats_row_is_back_under_the_legend():
    """Fuki asked where the previous version's statistics went: one tile row, the cleanup look."""
    html = _render([_pair("success", n=1, delete={"size_gb": 1.0}),
                    _pair("skipped_unsafe", n=2, error="x", delete={"size_gb": 2.0})])
    for label, value in (("Duplicate groups", "2"), ("Copies found", "2"), ("Removed", "1"), ("Blocked by guard", "1"),
                         ("Failed", "0"), ("Freed", "1.00 GB"), ("Keepers on disk", "2.00 GB"), ("Space saved", "20.0%")):
        assert f'<div class="stat-value">{value}</div><div class="stat-label">{label}</div>' in html, label
    dry = _render([_pair("not_attempted")])
    assert '<div class="stat-label">Would remove</div>' in dry and "Failed</div>" not in dry
