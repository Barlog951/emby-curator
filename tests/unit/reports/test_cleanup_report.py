"""
Tests for emby_dedupe.reports.cleanup — console, JSON and HTML report
formatting for the cleanup command.

Covers the report-related DA fixes:
  #6  None rating treated as 0.0 (not equal to 0.0)
  #9  Template path uses 2x dirname
  #14 _save_cleanup_html_report saves HTML + CSS to temp dir
"""
import os
import tempfile
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

    def test_html_file_created_in_temp_dir(self):
        """HTML file is saved to the system temp directory."""
        with patch("emby_dedupe.reports.cleanup.shutil.copy2"), \
             patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html><body>test</body></html>", no_open=True)
            assert path.startswith(tempfile.gettempdir())
            assert path.endswith(".html")
            assert "emby_cleanup_report_" in path

    def test_html_file_content_written(self):
        """HTML content is written to the file."""
        with patch("emby_dedupe.reports.cleanup.shutil.copy2"), \
             patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html><body>hello world</body></html>", no_open=True)
            with open(path) as f:
                content = f.read()
            assert "hello world" in content
            os.unlink(path)  # cleanup

    def test_css_copied_alongside_html(self):
        """report.css is copied to the same temp directory as the HTML file."""
        with patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.reports.cleanup.shutil.copy2") as mock_copy, \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html/>", no_open=True)
            assert mock_copy.called
            copy_dst = mock_copy.call_args[0][1]
            assert copy_dst == os.path.join(tempfile.gettempdir(), "report.css")
            os.unlink(path)  # cleanup

    def test_returns_file_path(self):
        """Return value is the path string of the saved file."""
        with patch("emby_dedupe.reports.cleanup.shutil.copy2"), \
             patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"):
            result = _save_cleanup_html_report("<html/>", no_open=True)
            assert isinstance(result, str)
            assert os.path.basename(result).startswith("emby_cleanup_report_")
            os.unlink(result)  # cleanup

    def test_no_open_flag_prevents_browser_open(self):
        """no_open=True prevents webbrowser.open() call."""
        with patch("emby_dedupe.reports.cleanup.shutil.copy2"), \
             patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open") as mock_open:
            path = _save_cleanup_html_report("<html/>", no_open=True)
            mock_open.assert_not_called()
            os.unlink(path)  # cleanup

    def test_browser_opened_when_no_open_false(self):
        """no_open=False triggers webbrowser.open()."""
        with patch("emby_dedupe.reports.cleanup.shutil.copy2"), \
             patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.reports.cleanup.webbrowser.open") as mock_open:
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
        assert "1.86 GB" in captured

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
        assert d["size_human"] == "1.00 GB"
        assert d["image_url"] == (
            "http://emby/Items/m1/Images/Primary?maxWidth=200&api_key=KEY"
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
        """Rendered HTML contains title, candidate name, total size and poster URL."""
        html = _generate_cleanup_html_report(
            "http://emby",
            [_make_movie_candidate(name="Dead Movie", size_bytes=4_000_000_000)],
            self._movie_stats(),
            default_config,
            doit=False,
            api_key="KEY",
        )
        assert "Library Cleanup Report" in html
        assert "Dead Movie" in html
        assert "3.73 GB" in html  # total_size_human for 4 GB candidate
        assert "Items/m1/Images/Primary" in html  # poster image URL

    def test_renders_series_section(self, default_config):
        """Series candidates render their own section with name and size."""
        html = _generate_cleanup_html_report(
            "http://emby",
            [],
            self._movie_stats(),
            default_config,
            doit=False,
            series_candidates=[_make_series_cleanup_candidate(name="Dead Series")],
            series_stats=self._series_stats(),
        )
        assert "Series Cleanup Candidates" in html
        assert "Dead Series" in html
        assert "7.45 GB" in html  # series_total_size_human for 8 GB candidate

    def test_renders_near_miss_sections_with_days_left(self, default_config):
        """Near-miss sections show days_left_str badges for movies and series."""
        html = _generate_cleanup_html_report(
            "http://emby",
            [],
            self._movie_stats(),
            default_config,
            doit=False,
            movie_near_miss=[_make_movie_candidate(name="Almost Movie", days_left=15)],
            series_near_miss=[_make_series_cleanup_candidate(name="Almost Series", days_left=None)],
        )
        assert "Almost Movie" in html
        assert "15d left" in html
        assert "Almost Series" in html
        assert "never left" in html

    def test_no_candidates_message(self, default_config):
        """Empty candidate list renders the clean-library message."""
        html = _generate_cleanup_html_report(
            "http://emby", [], self._movie_stats(), default_config, doit=False,
        )
        assert "No cleanup candidates found" in html

    def test_protection_stats_in_funnel(self, default_config):
        """Filter funnel shows the protection stat counts."""
        html = _generate_cleanup_html_report(
            "http://emby", [], self._movie_stats(), default_config, doit=False,
        )
        assert "100" in html  # total_analyzed
        assert "Filter Funnel" in html


# ---------------------------------------------------------------------------
# TestSaveCleanupHtmlReportCssMissing
# ---------------------------------------------------------------------------


class TestSaveCleanupHtmlReportCssMissing:
    """CSS-missing branch of _save_cleanup_html_report."""

    def test_missing_css_logs_warning_and_skips_copy(self):
        """When report.css is absent, a warning is logged and no copy happens."""
        with patch("emby_dedupe.reports.cleanup.os.path.exists", return_value=False), \
             patch("emby_dedupe.reports.cleanup.shutil.copy2") as mock_copy, \
             patch("emby_dedupe.reports.cleanup.webbrowser.open"), \
             patch("emby_dedupe.reports.cleanup.logger") as mock_logger:
            path = _save_cleanup_html_report("<html/>", no_open=True)
            mock_copy.assert_not_called()
            mock_logger.warning.assert_called_once()
            warn_msg = mock_logger.warning.call_args[0][0]
            assert "CSS file not found" in warn_msg
            os.unlink(path)  # cleanup
