"""
Tests for emby_dedupe.reports.cleanup — console, JSON and HTML report
formatting for the cleanup command.

Covers the report-related DA fixes:
  #6  None rating treated as 0.0 (not equal to 0.0)
  #9  Template path uses 2x dirname
  #14 _save_cleanup_html_report saves HTML + CSS to temp dir
"""
import os
from unittest.mock import patch

import pytest

from emby_dedupe.models.cleanup import (
    CleanupCandidate,
    CleanupConfig,
    SeriesCleanupCandidate,
)
from emby_dedupe.reports.cleanup import (
    _format_cleanup_report_console,
    _format_cleanup_report_json,
    _format_days_left,
    _format_rating_str,
    _generate_cleanup_html_report,
    _movie_candidate_to_dict,
    _print_near_miss_movie_table,
    _print_near_miss_series_table,
    _save_cleanup_html_report,
    _series_candidate_to_dict,
)
from emby_dedupe.reports.common import report_dir

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config():
    """Default CleanupConfig for tests."""
    return CleanupConfig(
        min_age_years=3,
        protect_paths=["/Dokumenty/"],
        base_rating=6.0,
        decay_step=0.5,
        max_rating=8.0,

        excluded_provider_ids=set(),
    )


# ---------------------------------------------------------------------------
# TestFormatReports
# ---------------------------------------------------------------------------


class TestFormatReports:
    """Tests for _format_cleanup_report_console and _format_cleanup_report_json."""

    def _sample_candidates(self):
        return [
            CleanupCandidate(
                item_id="m1",
                name="Dead Movie",
                year=2015,
                rating=3.5,
                critic_rating=None,
                threshold=7.0,
                age_years=5.0,
                library="HD & 4k",
                size_bytes=4_000_000_000,
                path="/Movies/HD/Dead Movie.mkv",
            ),
        ]

    def _sample_stats(self):
        return {
            "total_analyzed": 100,
            "age_filtered": 10,
            "excluded_filtered": 2,
            "play_protected": 30,
            "interest_protected": 5,
            "actor_protected": 8,
            "franchise_protected": 4,
            "path_protected": 1,
            "rating_protected": 40,
            "final_candidates": 1,
        }

    def test_console_output_has_header(self, capsys, default_config):
        """Console report starts with '=== Cleanup Report ==='."""
        _format_cleanup_report_console([], self._sample_stats(), default_config)
        captured = capsys.readouterr().out
        assert "=== Cleanup Report ===" in captured

    def test_console_output_has_candidates(self, capsys, default_config):
        """Console report includes candidate movie name."""
        _format_cleanup_report_console(self._sample_candidates(), self._sample_stats(), default_config)
        captured = capsys.readouterr().out
        assert "Dead Movie" in captured

    def test_console_no_candidates_message(self, capsys, default_config):
        """Console report with no candidates shows informative message."""
        stats = self._sample_stats()
        stats["final_candidates"] = 0
        _format_cleanup_report_console([], stats, default_config)
        captured = capsys.readouterr().out
        assert "No movie cleanup candidates" in captured

    def test_json_output_valid(self, default_config):
        """JSON report returns a dict (valid JSON-serializable)."""
        result = _format_cleanup_report_json(self._sample_candidates(), self._sample_stats(), default_config)
        import json
        serialized = json.dumps(result)  # must not raise
        assert isinstance(serialized, str)

    def test_json_has_all_fields(self, default_config):
        """JSON output has expected top-level keys."""
        result = _format_cleanup_report_json(self._sample_candidates(), self._sample_stats(), default_config)
        assert "candidates" in result
        assert "protection_stats" in result
        assert "config" in result
        assert "total_size_bytes" in result
        assert "total_size_human" in result

    def test_json_candidate_fields(self, default_config):
        """Each JSON candidate has all required fields."""
        result = _format_cleanup_report_json(self._sample_candidates(), self._sample_stats(), default_config)
        c = result["candidates"][0]
        assert c["item_id"] == "m1"
        assert c["name"] == "Dead Movie"
        assert c["rating"] == 3.5
        assert c["size_bytes"] == 4_000_000_000
        assert "size_human" in c

    def test_json_unrated_candidate(self, default_config):
        """None rating is preserved in JSON output (DA fix #6)."""
        candidates = [
            CleanupCandidate(
                item_id="u1", name="Unrated", year=2015, rating=None,
                critic_rating=None, threshold=6.5, age_years=4.0, library="Movies",
                size_bytes=1_000_000, path="/Movies/Unrated.mkv",
            )
        ]
        result = _format_cleanup_report_json(candidates, self._sample_stats(), default_config)
        assert result["candidates"][0]["rating"] is None


# ---------------------------------------------------------------------------
# TestSaveCleanupHtmlReport  (DA fix #14)
# ---------------------------------------------------------------------------


class TestSaveCleanupHtmlReport:
    """Tests for _save_cleanup_html_report (DA fix #14)."""

    def test_html_file_created_in_report_dir(self):
        """HTML file is saved to the report directory (never the shared temp directory)."""
        with patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html><body>test</body></html>", no_open=True)
            assert path.startswith(report_dir())
            assert path.endswith(".html")
            assert "emby_cleanup_report_" in path

    def test_html_file_content_written(self):
        """HTML content is written to the file."""
        with patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html><body>hello world</body></html>", no_open=True)
            with open(path) as f:
                content = f.read()
            assert "hello world" in content
            os.unlink(path)  # cleanup

    def test_no_stylesheet_copied_beside_the_report(self, tmp_path):
        """The stylesheet is embedded in the page, so saving writes the HTML file only."""
        with patch("emby_dedupe.reports.cleanup.report_dir", return_value=str(tmp_path)), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html/>", no_open=True)
        assert os.listdir(tmp_path) == [os.path.basename(path)]

    def test_returns_file_path(self):
        """Return value is the path string of the saved file."""
        with patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            result = _save_cleanup_html_report("<html/>", no_open=True)
            assert isinstance(result, str)
            assert os.path.basename(result).startswith("emby_cleanup_report_")
            os.unlink(result)  # cleanup

    def test_no_open_flag_prevents_browser_open(self):
        """no_open=True prevents webbrowser.open() call."""
        with patch("emby_dedupe.reports.cleanup.webbrowser.open") as mock_open:
            path = _save_cleanup_html_report("<html/>", no_open=True)
            mock_open.assert_not_called()
            os.unlink(path)  # cleanup

    def test_reports_in_same_second_do_not_overwrite(self, tmp_path):
        """Regression: the name had 1-second resolution, so two reports in the same second
        (e.g. parallel test workers) shared one file and overwrote each other."""
        with patch("emby_dedupe.reports.common.time.time", return_value=1234567890), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            first = _save_cleanup_html_report("<html>first</html>", no_open=True)
            second = _save_cleanup_html_report("<html>second</html>", no_open=True)
        assert first != second
        assert os.path.basename(first).startswith("emby_cleanup_report_1234567890_")
        with open(first, encoding="utf-8") as f:
            assert f.read() == "<html>first</html>"
        with open(second, encoding="utf-8") as f:
            assert f.read() == "<html>second</html>"

    def test_browser_opened_when_no_open_false(self):
        """no_open=False triggers webbrowser.open()."""
        with patch("emby_dedupe.reports.cleanup.webbrowser.open") as mock_open:
            path = _save_cleanup_html_report("<html/>", no_open=False)
            mock_open.assert_called_once()
            os.unlink(path)  # cleanup


# ---------------------------------------------------------------------------
# TestFormatReportsWithSeries
# ---------------------------------------------------------------------------


class TestFormatReportsWithSeries:
    """Tests for report functions with series data."""

    def _sample_series_candidates(self):
        return [
            SeriesCleanupCandidate(
                item_id="s1",
                name="Dead Series",
                year=2020,
                rating=4.0,
                critic_rating=None,
                threshold=7.0,
                stale_years=5.0,
                last_episode_added="2021-03-01T00:00:00Z",
                episode_count=12,
                library="SERIALS",
                size_bytes=8_000_000_000,
                path="/Movies/Serials/Dead Series",
            ),
        ]

    def _sample_series_stats(self):
        return {
            "total_analyzed": 50,
            "stale_filtered": 10,
            "excluded_filtered": 2,
            "play_protected": 15,
            "favorite_protected": 3,
            "path_protected": 1,
            "rating_protected": 18,
            "final_candidates": 1,
        }

    def _sample_movie_stats(self):
        return {
            "total_analyzed": 100,
            "age_filtered": 10,
            "excluded_filtered": 2,
            "play_protected": 30,
            "interest_protected": 5,
            "actor_protected": 8,
            "franchise_protected": 4,
            "path_protected": 1,
            "rating_protected": 40,
            "final_candidates": 0,
        }

    def test_console_with_series_section(self, capsys):
        """Console report includes series section when series data provided."""
        config = CleanupConfig()
        _format_cleanup_report_console(
            [], self._sample_movie_stats(), config,
            series_candidates=self._sample_series_candidates(),
            series_stats=self._sample_series_stats(),
        )
        captured = capsys.readouterr().out
        assert "=== Series Cleanup Report ===" in captured
        assert "Dead Series" in captured
        assert "Stale filtered" in captured

    def test_console_without_series_omits_section(self, capsys):
        """Console report without series data does not print series section."""
        config = CleanupConfig()
        _format_cleanup_report_console([], self._sample_movie_stats(), config)
        captured = capsys.readouterr().out
        assert "Series Cleanup Report" not in captured

    def test_json_includes_series_keys(self):
        """JSON report includes series_candidates and series_stats when provided."""
        config = CleanupConfig()
        result = _format_cleanup_report_json(
            [], self._sample_movie_stats(), config,
            series_candidates=self._sample_series_candidates(),
            series_stats=self._sample_series_stats(),
        )
        assert "series_candidates" in result
        assert "series_stats" in result
        assert "series_total_size_bytes" in result
        assert "series_total_size_human" in result
        assert len(result["series_candidates"]) == 1
        assert result["series_candidates"][0]["item_id"] == "s1"
        assert result["series_candidates"][0]["stale_years"] == 5.0

    def test_json_without_series_omits_keys(self):
        """JSON report without series data does not include series keys."""
        config = CleanupConfig()
        result = _format_cleanup_report_json([], self._sample_movie_stats(), config)
        assert "series_candidates" not in result
        assert "series_stats" not in result


# ---------------------------------------------------------------------------
# Candidate helpers for near-miss / HTML report tests
# ---------------------------------------------------------------------------


def _make_movie_candidate(
    item_id="m1",
    name="Dead Movie",
    year=2015,
    rating=3.5,
    critic_rating=None,
    threshold=7.0,
    age_years=5.0,
    library="HD",
    size_bytes=4_000_000_000,
    path="/Movies/HD/Dead Movie.mkv",
    days_left=None,
):
    """Create a CleanupCandidate with sensible defaults for report testing."""
    return CleanupCandidate(
        item_id=item_id,
        name=name,
        year=year,
        rating=rating,
        critic_rating=critic_rating,
        threshold=threshold,
        age_years=age_years,
        library=library,
        size_bytes=size_bytes,
        path=path,
        days_left=days_left,
    )


def _make_series_cleanup_candidate(
    item_id="s1",
    name="Dead Series",
    year=2020,
    rating=4.0,
    critic_rating=None,
    threshold=7.0,
    stale_years=5.0,
    last_episode_added="2021-03-01T00:00:00Z",
    episode_count=12,
    library="SERIALS",
    size_bytes=8_000_000_000,
    path="/Movies/Serials/Dead Series",
    days_left=None,
):
    """Create a SeriesCleanupCandidate with sensible defaults for report testing."""
    return SeriesCleanupCandidate(
        item_id=item_id,
        name=name,
        year=year,
        rating=rating,
        critic_rating=critic_rating,
        threshold=threshold,
        stale_years=stale_years,
        last_episode_added=last_episode_added,
        episode_count=episode_count,
        library=library,
        size_bytes=size_bytes,
        path=path,
        days_left=days_left,
    )


# ---------------------------------------------------------------------------
# TestFormatRatingStr
# ---------------------------------------------------------------------------


class TestFormatRatingStr:
    """Tests for _format_rating_str — all four formatting branches."""

    def test_both_ratings_combined(self):
        """Both present → 'community/critic-normalised' (critic 0-100 → 0-10)."""
        assert _format_rating_str(5.0, 80) == "5.0/8.0"

    def test_community_only(self):
        """Only community rating present → shown alone."""
        assert _format_rating_str(6.5, None) == "6.5"

    def test_critic_only(self):
        """Only critic rating present → 'RT:' prefix with normalised value."""
        assert _format_rating_str(None, 75) == "RT:7.5"

    def test_neither_rating(self):
        """Both absent → 'none'."""
        assert _format_rating_str(None, None) == "none"

    def test_critic_zero_is_real_score(self):
        """CriticRating=0 is a real score, not treated as absent."""
        assert _format_rating_str(None, 0) == "RT:0.0"


# ---------------------------------------------------------------------------
# TestFormatDaysLeft
# ---------------------------------------------------------------------------


class TestFormatDaysLeft:
    """Tests for _format_days_left — all branches."""

    def test_none_means_never(self):
        """days=None → 'never' (masterpiece, protected forever)."""
        assert _format_days_left(None) == "never"

    def test_zero_means_now(self):
        """days=0 → 'now'."""
        assert _format_days_left(0) == "now"

    def test_days_under_30(self):
        """1-29 days → 'Nd'."""
        assert _format_days_left(1) == "1d"
        assert _format_days_left(29) == "29d"

    def test_months_under_year(self):
        """30-364 days → 'Nmo' (floor division by 30)."""
        assert _format_days_left(30) == "1mo"
        assert _format_days_left(90) == "3mo"
        assert _format_days_left(364) == "12mo"

    def test_years(self):
        """365+ days → 'N.Nyr' (divided by 365.25)."""
        assert _format_days_left(365) == "1.0yr"
        assert _format_days_left(731) == "2.0yr"


# ---------------------------------------------------------------------------
# TestPrintNearMissMovieTable
# ---------------------------------------------------------------------------


class TestPrintNearMissMovieTable:
    """Tests for _print_near_miss_movie_table."""

    def test_header_and_row_printed(self, capsys):
        """Table header includes 'Days Left'; row includes name, rating, days."""
        near_miss = [
            _make_movie_candidate(
                name="Close Call", rating=7.0, critic_rating=80, days_left=15,
            ),
        ]
        _print_near_miss_movie_table(near_miss)
        captured = capsys.readouterr().out
        assert "Days Left" in captured
        assert "Close Call" in captured
        assert "7.0/8.0" in captured
        assert "15d" in captured

    def test_never_shown_for_protected_forever(self, capsys):
        """days_left=None renders as 'never'."""
        _print_near_miss_movie_table([_make_movie_candidate(rating=9.5, days_left=None)])
        captured = capsys.readouterr().out
        assert "never" in captured

    def test_long_name_truncated_to_40_chars(self, capsys):
        """Names longer than the column width are truncated."""
        _print_near_miss_movie_table([_make_movie_candidate(name="X" * 60, days_left=0)])
        captured = capsys.readouterr().out
        assert "X" * 40 in captured
        assert "X" * 41 not in captured

    def test_missing_year_rendered_empty(self, capsys):
        """year=None prints as empty string, not 'None'."""
        _print_near_miss_movie_table([_make_movie_candidate(year=None, days_left=0)])
        captured = capsys.readouterr().out
        assert "None" not in captured


# ---------------------------------------------------------------------------
# TestPrintNearMissSeriesTable
# ---------------------------------------------------------------------------


class TestPrintNearMissSeriesTable:
    """Tests for _print_near_miss_series_table."""

    def test_header_and_row_printed(self, capsys):
        """Table header includes 'Days Left' and 'Eps'; row includes series data."""
        near_miss = [
            _make_series_cleanup_candidate(
                name="Fading Show", rating=7.5, days_left=0, episode_count=24,
            ),
        ]
        _print_near_miss_series_table(near_miss)
        captured = capsys.readouterr().out
        assert "Days Left" in captured
        assert "Eps" in captured
        assert "Fading Show" in captured
        assert "now" in captured
        assert "24" in captured

    def test_days_left_in_months(self, capsys):
        """days_left=200 renders as '6mo' (200 // 30)."""
        _print_near_miss_series_table([_make_series_cleanup_candidate(days_left=200)])
        captured = capsys.readouterr().out
        assert "6mo" in captured

    def test_critic_only_rating(self, capsys):
        """Series with only critic rating shows 'RT:' prefix."""
        _print_near_miss_series_table([
            _make_series_cleanup_candidate(rating=None, critic_rating=60, days_left=None),
        ])
        captured = capsys.readouterr().out
        assert "RT:6.0" in captured


# ---------------------------------------------------------------------------
# TestConsoleNearMissSections
# ---------------------------------------------------------------------------


class TestConsoleNearMissSections:
    """Tests for near-miss sections in _format_cleanup_report_console."""

    def test_movie_near_miss_section_printed(self, capsys, default_config):
        """Movie near-miss list prints its own header and total size."""
        near = [_make_movie_candidate(name="Almost Gone", days_left=15, size_bytes=2_000_000_000)]
        _format_cleanup_report_console([], {}, default_config, movie_near_miss=near)
        captured = capsys.readouterr().out
        assert "=== Next 1 Movie Candidates (protected only by rating) ===" in captured
        assert "Almost Gone" in captured
        assert "1.86 GiB" in captured

    def test_series_near_miss_section_printed(self, capsys, default_config):
        """Series near-miss list prints its own header with days-left values."""
        near = [_make_series_cleanup_candidate(name="Almost Gone Show", days_left=90)]
        _format_cleanup_report_console([], {}, default_config, series_near_miss=near)
        captured = capsys.readouterr().out
        assert "=== Next 1 Series Candidates (protected only by rating) ===" in captured
        assert "Almost Gone Show" in captured
        assert "3mo" in captured

    def test_no_near_miss_sections_when_absent(self, capsys, default_config):
        """Without near-miss data the sections are omitted entirely."""
        _format_cleanup_report_console([], {}, default_config)
        captured = capsys.readouterr().out
        assert "protected only by rating" not in captured


# ---------------------------------------------------------------------------
# TestCandidateToDictHelpers
# ---------------------------------------------------------------------------


class TestCandidateToDictHelpers:
    """Tests for _movie_candidate_to_dict and _series_candidate_to_dict."""

    def test_movie_dict_fields(self):
        """Movie dict has formatted rating/threshold strings and image URL."""
        c = _make_movie_candidate(rating=3.5, critic_rating=40, size_bytes=1_073_741_824)
        d = _movie_candidate_to_dict(c, "http://emby", "KEY")
        assert d["item_id"] == "m1"
        assert d["rating_str"] == "3.5"
        assert d["critic_rating_str"] == "40%"
        assert d["threshold_str"] == "7.00"
        assert d["age_years"] == 5.0
        assert d["size_human"] == "1.00 GiB"
        assert d["image_url"] == (
            "http://emby/Items/m1/Images/Primary?maxWidth=200"
        )

    def test_movie_dict_unrated_and_no_api_key(self):
        """None ratings render as 'unrated'/None; no API key → empty image URL."""
        c = _make_movie_candidate(rating=None, critic_rating=None)
        d = _movie_candidate_to_dict(c, "http://emby", "")
        assert d["rating_str"] == "unrated"
        assert d["critic_rating_str"] is None
        assert d["image_url"] == ""

    def test_series_dict_fields(self):
        """Series dict has staleness, episode count and image URL."""
        c = _make_series_cleanup_candidate(rating=4.0, critic_rating=55, episode_count=12)
        d = _series_candidate_to_dict(c, "http://emby", "KEY")
        assert d["item_id"] == "s1"
        assert d["rating_str"] == "4.0"
        assert d["critic_rating_str"] == "55%"
        assert d["stale_years"] == 5.0
        assert d["episode_count"] == 12
        assert d["last_episode_added"] == "2021-03-01T00:00:00Z"
        assert d["image_url"].startswith("http://emby/Items/s1/Images/Primary")

    def test_series_dict_unrated_and_no_api_key(self):
        """None rating renders 'unrated'; no API key → empty image URL."""
        c = _make_series_cleanup_candidate(rating=None, critic_rating=None)
        d = _series_candidate_to_dict(c, "http://emby", "")
        assert d["rating_str"] == "unrated"
        assert d["critic_rating_str"] is None
        assert d["image_url"] == ""


# ---------------------------------------------------------------------------
# TestGenerateCleanupHtmlReport
# ---------------------------------------------------------------------------


class TestGenerateCleanupHtmlReport:
    """Tests for _generate_cleanup_html_report — real Jinja2 rendering."""

    def _movie_stats(self):
        return {
            "total_analyzed": 100,
            "age_filtered": 10,
            "excluded_filtered": 2,
            "play_protected": 30,
            "interest_protected": 5,
            "actor_protected": 8,
            "franchise_protected": 4,
            "path_protected": 1,
            "rating_protected": 39,
            "final_candidates": 1,
        }

    def _series_stats(self):
        return {
            "total_analyzed": 50,
            "stale_filtered": 10,
            "excluded_filtered": 2,
            "play_protected": 15,
            "favorite_protected": 3,
            "path_protected": 1,
            "rating_protected": 18,
            "final_candidates": 1,
        }

    def test_renders_movie_candidates(self, default_config):
        """A dry run leads with what would go and how much space it frees."""
        html = _generate_cleanup_html_report(
            "http://emby",
            [_make_movie_candidate(name="Dead Movie", size_bytes=4_000_000_000)],
            self._movie_stats(),
            default_config,
            doit=False,
            api_key="KEY",
        )
        assert "Dry run · nothing deleted" in html
        assert html.count("Dry run") == 1, "only the badge names the mode; masthead and footer do not repeat it"
        assert 'add its id to <code class="flag">--exclude-ids</code>' in html  # markup, not escaped text
        assert "1 film would be removed, 3.73 GiB to free." in html
        assert "Dead Movie" in html
        # The poster fetch fails here, so the row shows the placeholder, never a remote URL.
        assert "Items/m1/Images/Primary" not in html
        assert '<span class="thumb" aria-hidden="true"></span>' in html

    def test_renders_series_section(self, default_config):
        """Series candidates share the outcome section with films."""
        html = _generate_cleanup_html_report(
            "http://emby",
            [],
            self._movie_stats(),
            default_config,
            doit=False,
            series_candidates=[_make_series_cleanup_candidate(name="Dead Series")],
            series_stats=self._series_stats(),
        )
        assert "1 series would be removed, 7.45 GiB to free." in html
        assert "Would be removed · 1 title</h2>" in html, "the headline gives the size; the heading only the count"
        assert "Dead Series" in html
        assert "no new episode for 5.0 years · 12 episodes" in html
        assert "2021-03-01" in html  # last episode added, not the raw timestamp
        assert "2021-03-01T00:00:00Z" not in html

    def test_renders_near_miss_sections_with_days_left(self, default_config):
        """Next-in-line rows say when each title goes; the first dated one leads the subline."""
        html = _generate_cleanup_html_report(
            "http://emby",
            [],
            self._movie_stats(),
            default_config,
            doit=False,
            movie_near_miss=[_make_movie_candidate(name="Almost Movie", days_left=15)],
            series_near_miss=[_make_series_cleanup_candidate(name="Almost Series", days_left=None)],
        )
        assert "Next to go: Almost Movie in 15 days, 3.73 GiB." in html
        assert "goes in 15 days" in html
        assert "Almost Series" in html
        assert "no date: its rating keeps it" in html
        assert "Next in line · 1 film and 1 series" in html

    def test_no_candidates_message(self, default_config):
        """An empty run says so in the headline and renders no empty outcome section."""
        html = _generate_cleanup_html_report(
            "http://emby", [], self._movie_stats(), default_config, doit=False,
        )
        assert "Nothing to clean up." in html
        assert 'class="out-section"' not in html
        assert 'class="run-bar"' not in html

    def test_protection_stats_in_funnel(self, default_config):
        """The folded rules list each protection count once and skip the zero ones."""
        stats = {**self._movie_stats(), "interest_protected": 0}
        html = _generate_cleanup_html_report("http://emby", [], stats, default_config, doit=False)
        assert "How titles were chosen" in html
        assert "100 checked, 1 picked for removal." in html
        assert "<dt>30</dt><dd>watched by someone</dd>" in html
        assert "first reason that applies" in html
        assert "favourited or started" not in html
        assert "films: 12 years and more" in html  # the masterpiece rule is films only
        assert "7 years and more" in html  # the decay cap
        assert "so 3.8 years needs 6.4" in html  # the rating rises evenly between rows
        assert "Filter Funnel" not in html

    def test_decay_rows_for_a_short_config(self):
        """A one-year minimum and a quick cap read as plain years, then the masterpiece rule."""
        from emby_dedupe.reports.cleanup import _rules

        cfg = CleanupConfig(min_age_years=1, base_rating=6.0, decay_step=1.0, max_rating=7.0)
        decay = _rules(cfg, {}, None)["decay"]
        assert [d["age"] for d in decay] == ["at 1 year", "2 years and more", "films: 12 years and more"]
        assert [d["need"] for d in decay] == ["6.0", "7.0", "9.0"]

    def test_real_run_counts_only_successful_deletes(self, default_config):
        """Freed space and the headline count successful deletes; held ones are named."""
        films = [
            _make_movie_candidate(item_id="ok", name="Gone", size_bytes=1024**3),
            _make_movie_candidate(item_id="bad", name="Stuck", size_bytes=2 * 1024**3),
            _make_movie_candidate(item_id="held", name="Kept", size_bytes=4 * 1024**3),
        ]
        films[0].deletion_result = {"status": "success", "error": None}
        films[1].deletion_result = {"status": "failed", "error": "HTTP 500"}
        films[2].deletion_result = {"status": "skipped_unsafe", "error": "keeper co-located in a dedicated folder"}
        html = _generate_cleanup_html_report("http://emby", films, self._movie_stats(), default_config, doit=True)
        assert "1 film removed, 1.00 GiB freed." in html
        assert "1 blocked by the safety guard, 1 failed." in html
        for section in ("removed", "blocked", "failed"):
            assert f'id="{section}"' in html
        assert "Emby did not delete it (HTTP 500)." in html
        assert "Its folder also holds media you keep, and Emby would delete the whole folder." in html
        removed = html[html.index('id="removed"'):html.index('id="blocked"')]
        assert "Open in Emby" not in removed, "a deleted title has no Emby page any more"
        assert 'data-reason="keeper co-located in a dedicated folder"' in html

    def test_real_run_with_nothing_deleted(self, default_config):
        """When every delete was held or failed the headline does not claim space."""
        film = _make_movie_candidate()
        film.deletion_result = {"status": "failed", "error": None}
        html = _generate_cleanup_html_report("http://emby", [film], self._movie_stats(), default_config, doit=True)
        assert "Nothing removed." in html
        assert "freed" not in html

    def test_report_is_self_contained_and_links_are_safe(self, default_config):
        """The stylesheet is embedded and every new-tab link carries rel=noopener."""
        import re

        html = _generate_cleanup_html_report(
            "http://emby", [_make_movie_candidate()], self._movie_stats(), default_config, doit=False,
            movie_near_miss=[_make_movie_candidate(item_id="n1", days_left=3)],
        )
        assert "<link" not in html
        assert ".crow" in html
        blank = re.findall(r"<a [^>]*target=\"_blank\"[^>]*>", html)
        assert blank
        assert all('rel="noopener noreferrer"' in a for a in blank)
        assert "emby-curator" in html
        assert "emby-dedupe" not in html

    def test_exclude_hint_prefers_imdb(self, default_config):
        """Rows that can still be kept show the id to pass to --exclude-ids."""
        imdb = _make_movie_candidate(item_id="a", name="With Imdb", days_left=5)
        imdb.provider_ids = {"Tmdb": "5", "Imdb": "tt0123"}
        tmdb = _make_movie_candidate(item_id="b", name="Tmdb Only", days_left=9)
        tmdb.provider_ids = {"Tmdb": "48121"}
        html = _generate_cleanup_html_report(
            "http://emby", [], self._movie_stats(), default_config, doit=False, movie_near_miss=[imdb, tmdb],
        )
        assert '<code class="ids">tt0123</code>' in html
        assert '<code class="ids">48121</code>' in html
        assert html.count("a bare number matches TMDB and TVDB alike") == 1


class TestCleanupHtmlHelpers:
    """Plain-language helpers behind the cleanup report rows."""

    def test_rating_text_shows_the_compared_value(self):
        from emby_dedupe.reports.cleanup import _rating_text

        assert _rating_text(5.1, 31.0, 7.5) == "rated 4.1 (users 5.1, critics 3.1), needs 7.5"
        assert _rating_text(None, None, 9.0) == "no rating, needs 9.0"
        assert _rating_text(None, 62.0, 6.0) == "rated 6.2 (critics 6.2), needs 6.0"
        assert _rating_text(3.8, None, 6.0) == "rated 3.8, needs 6.0"
        # Equal after rounding: the deciding digits are shown, so the two numbers never look equal.
        assert _rating_text(5.3, 75.0, 6.4033) == "rated 6.400 (users 5.3, critics 7.5), needs 6.403"
        assert _rating_text(6.5, None, 6.49) == "rated 6.50, needs 6.49"
        assert _rating_text(7.0, None, 7.0) == "rated 7.000, exactly the 7.000 it needs"

    def test_when_text(self):
        from emby_dedupe.reports.cleanup import _when_text

        assert _when_text(None) == ""
        assert _when_text(0) == "on the next run"
        assert _when_text(1) == "in 1 day"
        assert _when_text(40) == "in about 1 month"
        assert _when_text(57) == "in about 2 months"
        assert _when_text(500) == "in about 1.4 years"

    def test_date_text(self):
        from emby_dedupe.reports.cleanup import _date_text

        assert _date_text("2023-02-09T15:20:30.0000000Z") == "2023-02-09"
        assert _date_text(None) == ""
        assert _date_text("someday") == "someday"

    def test_titles_phrase(self):
        from emby_dedupe.reports.cleanup import _titles_phrase

        assert _titles_phrase(2, 1) == "2 films and 1 series"
        assert _titles_phrase(1, 0) == "1 film"
        assert _titles_phrase(0, 3) == "3 series"

    def test_collection_reasons_are_plain(self):
        from emby_dedupe.reports.cleanup import _reason_lines

        for raw in ("collection kept: deleting 'A' failed, the other parts stay",
                    "collection kept whole: the safety guard holds 'A'"):
            why, step = _reason_lines("blocked", raw)
            assert why.startswith("Another part of its collection")
            assert step
        why, _ = _reason_lines("blocked", "delete path unknown — cannot verify fold safety, refusing")
        assert "no file path" in why


    def test_collection_hold_names_the_other_part(self):
        from emby_dedupe.reports.cleanup import _reason_lines

        why, step = _reason_lines("blocked", "collection kept: deleting 'Duo One' failed, the other parts stay")
        assert why == "Another part of its collection (Duo One) could not be deleted, so the whole collection stays."
        assert step.startswith("Sort out Duo One first")

    def test_large_counts_carry_thousands_separators(self):
        from emby_dedupe.reports.cleanup import _rules

        rules = _rules(CleanupConfig(), {"total_analyzed": 7304, "age_filtered": 4854}, None)
        assert rules["funnels"][0]["checked"] == "7,304"
        assert rules["funnels"][0]["steps"][0]["count"] == "4,854"


class TestCleanupPolish:
    """Copy-paste ids, the collection sibling link and the bar rule."""

    STATS = {"total_analyzed": 10, "final_candidates": 2}

    def _film(self, item_id, name, ids=None, days=None, result=None, collection=None):
        c = _make_movie_candidate(item_id=item_id, name=name, days_left=days)
        c.provider_ids = ids or {}
        c.deletion_result = result
        c.collection = collection
        c.collection_size = 2 if collection else 0
        return c

    def test_dry_run_offers_the_ids_to_keep_in_one_line(self, default_config):
        films = [self._film("a", "A", {"Imdb": "tt01"}), self._film("b", "B", {"Tmdb": "22"})]
        near = [self._film("n", "N", {"Imdb": "tt09"}, days=3)]
        html = _generate_cleanup_html_report("http://emby", films, self.STATS, default_config, doit=False,
                                             movie_near_miss=near)
        assert "Next steps: copy-paste values" in html
        assert "<code>tt01,22</code>" in html
        assert "<code>tt09</code>" in html

    def test_a_held_collection_part_links_to_the_part_it_waits_for(self, default_config):
        failed = self._film("one", "Duo One", result={"status": "failed", "error": "HTTP 500"}, collection="Duo")
        held = self._film("two", "Duo Two", collection="Duo",
                          result={"status": "skipped_unsafe",
                                  "error": "collection kept: deleting 'Duo One' failed, the other parts stay"})
        html = _generate_cleanup_html_report("http://emby", [failed, held], self.STATS, default_config, doit=True)
        assert '<a href="#item-one">Go to Duo One</a>' in html
        assert 'id="item-one"' in html

    def test_a_single_outcome_draws_no_bar(self, default_config):
        html = _generate_cleanup_html_report("http://emby", [self._film("a", "A")], self.STATS, default_config,
                                             doit=False)
        assert 'class="run-bar"' not in html
        assert "Would be removed <strong>1</strong>" in html

    def test_one_title_reads_as_one(self, default_config):
        from emby_dedupe.reports.cleanup import _decide

        one = [{"outcome": "would_remove", "exclude_id": "tt01"}]
        assert _decide(one, [], doit=False)[0]["label"].startswith("To keep the title that would be removed")
