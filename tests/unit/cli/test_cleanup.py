"""
Tests for emby_dedupe.cli.cleanup — comprehensive coverage of all cleanup functions.

Covers all 15 DA fixes:
  #2  Favorite actors scoped to primary user only
  #4  _validate_cleanup_args supports --all-libraries
  #5  --exclude-ids protection
  #6  None rating treated as 0.0 (not equal to 0.0)
  #7  Empty string in protect_paths doesn't match every path
  #9  Template path uses 2x dirname
  #10 DateCreated [:10] slice
  #11 _resolve_primary_user_id by username, fallback with warning
  #12 IncludeItemTypes=Movie on all movie-fetching queries
  #13 Batch UserData check per user (100-item chunks)
  #14 _save_cleanup_html_report saves HTML + CSS to temp dir
  #15 Size=None/missing → size_bytes=0
"""
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from emby_dedupe.cli.cleanup import (
    CleanupCandidate,
    CleanupConfig,
    SeriesCleanupCandidate,
    _build_last_episode_added_map,
    _calculate_series_sizes,
    _check_play_and_interest_batch,
    _check_series_play_and_favorites,
    _compute_age_years,
    _compute_effective_rating,
    _compute_rating_threshold,
    _format_cleanup_report_console,
    _format_cleanup_report_json,
    _get_movie_actor_names,
    _is_excluded_by_provider_id,
    _is_franchise_protected,
    _is_path_protected,
    _probe_library_content,
    _resolve_primary_user_id,
    _run_cleanup_pipeline,
    _run_series_cleanup_pipeline,
    _save_cleanup_html_report,
    _validate_cleanup_args,
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


@pytest.fixture
def mock_client():
    """Mock httpx.Client."""
    return MagicMock()


def _make_response(data: dict) -> MagicMock:
    """Create a mock HTTP response returning the given data as JSON."""
    resp = MagicMock()
    resp.json.return_value = data
    return resp


def _date_years_ago(years: float) -> str:
    """Return an ISO date string approximately `years` years in the past."""
    days = int(years * 365.25)
    return (date.today() - timedelta(days=days)).isoformat() + "T00:00:00Z"


def _make_movie(
    item_id="m1",
    name="Test Movie",
    date_created=None,
    rating=None,
    critic_rating=None,
    size=1_000_000,
    path="/Movies/HD/Test Movie.mkv",
    provider_ids=None,
    people=None,
    library_name="HD & 4k",
    production_year=2015,
):
    """Create a minimal Emby movie item dict for pipeline testing."""
    return {
        "Id": item_id,
        "Name": name,
        "DateCreated": date_created or _date_years_ago(4),
        "CommunityRating": rating,
        "CriticRating": critic_rating,
        "Size": size,
        "Path": path,
        "ProviderIds": provider_ids or {},
        "People": people or [],
        "ProductionYear": production_year,
        "_library_name": library_name,
        "_library_id": "lib1",
    }


# ---------------------------------------------------------------------------
# TestComputeAgeYears
# ---------------------------------------------------------------------------


class TestComputeAgeYears:
    """Tests for _compute_age_years (DA fix #10)."""

    def test_3_years_ago(self):
        """Movie added 3 years ago returns ~3.0."""
        ds = _date_years_ago(3)
        age = _compute_age_years(ds)
        assert 2.9 < age < 3.2

    def test_5_years_ago(self):
        """Movie added 5 years ago returns ~5.0."""
        ds = _date_years_ago(5)
        age = _compute_age_years(ds)
        assert 4.9 < age < 5.2

    def test_missing_date_returns_0(self):
        """None / empty string returns 0.0 — safe default, won't flag for deletion."""
        assert _compute_age_years(None) == 0.0
        assert _compute_age_years("") == 0.0

    def test_bad_date_string_returns_0(self):
        """Unparseable date returns 0.0."""
        assert _compute_age_years("not-a-date") == 0.0
        assert _compute_age_years("2020-13-45") == 0.0

    def test_future_date_returns_near_zero(self):
        """Future DateCreated (clock skew) returns a tiny negative, clamped at ~0."""
        future = (date.today() + timedelta(days=10)).isoformat() + "T00:00:00Z"
        age = _compute_age_years(future)
        assert age < 0.1  # negative or very small positive

    def test_date_slice_10_chars(self):
        """Verifies that only the first 10 characters (date part) are used — DA fix #10."""
        # Provide a timestamp with a fake time-zone offset that would corrupt full parse
        ds = "2020-06-15T23:59:59.999Z"
        age = _compute_age_years(ds)
        # Should parse "2020-06-15" correctly
        expected = (date.today() - date(2020, 6, 15)).days / 365.25
        assert abs(age - expected) < 0.01


# ---------------------------------------------------------------------------
# TestComputeRatingThreshold
# ---------------------------------------------------------------------------


class TestComputeRatingThreshold:
    """Tests for _compute_rating_threshold."""

    def test_at_min_age_returns_base_rating(self, default_config):
        """3 years → base 6.0."""
        assert _compute_rating_threshold(3.0, default_config) == pytest.approx(6.0)

    def test_4_years(self, default_config):
        """4 years → 6.5."""
        assert _compute_rating_threshold(4.0, default_config) == pytest.approx(6.5)

    def test_5_years(self, default_config):
        """5 years → 7.0."""
        assert _compute_rating_threshold(5.0, default_config) == pytest.approx(7.0)

    def test_6_years(self, default_config):
        """6 years → 7.5."""
        assert _compute_rating_threshold(6.0, default_config) == pytest.approx(7.5)

    def test_7_plus_capped_at_max(self, default_config):
        """7+ years → capped at 8.0."""
        assert _compute_rating_threshold(7.0, default_config) == pytest.approx(8.0)
        assert _compute_rating_threshold(10.0, default_config) == pytest.approx(8.0)
        assert _compute_rating_threshold(20.0, default_config) == pytest.approx(8.0)

    def test_custom_config(self):
        """Custom base/step/max values are respected."""
        config = CleanupConfig(min_age_years=2, base_rating=5.0, decay_step=1.0, max_rating=9.0)
        assert _compute_rating_threshold(2.0, config) == pytest.approx(5.0)
        assert _compute_rating_threshold(4.0, config) == pytest.approx(7.0)
        assert _compute_rating_threshold(6.0, config) == pytest.approx(9.0)  # capped

    def test_below_min_age_returns_below_base(self, default_config):
        """Below min_age_years the formula gives < base_rating (edge case)."""
        result = _compute_rating_threshold(1.0, default_config)
        # 6.0 + (1.0 - 3.0) * 0.5 = 6.0 - 1.0 = 5.0
        assert result == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# TestComputeEffectiveRating
# ---------------------------------------------------------------------------


class TestComputeEffectiveRating:
    """Tests for _compute_effective_rating — combines CommunityRating and CriticRating."""

    def test_only_community_rating(self):
        """Only community rating present → returns it directly."""
        assert _compute_effective_rating(7.5, None) == pytest.approx(7.5)

    def test_only_critic_rating(self):
        """Only critic rating present → normalised (80 → 8.0)."""
        assert _compute_effective_rating(None, 80) == pytest.approx(8.0)

    def test_both_present_averaged(self):
        """Both present → average of community and normalised critic."""
        # community=6.0, critic=80 → normalised 8.0 → avg = (6.0 + 8.0) / 2 = 7.0
        assert _compute_effective_rating(6.0, 80) == pytest.approx(7.0)

    def test_community_higher(self):
        """Community higher than normalised critic → average still computed."""
        # community=9.0, critic=60 → normalised 6.0 → avg = (9.0 + 6.0) / 2 = 7.5
        assert _compute_effective_rating(9.0, 60) == pytest.approx(7.5)

    def test_both_absent_returns_zero(self):
        """Both None → 0.0 (safe default: always below threshold)."""
        assert _compute_effective_rating(None, None) == pytest.approx(0.0)

    def test_critic_rating_zero_is_real(self):
        """CriticRating=0 is a real score (not treated as absent)."""
        # community=5.0, critic=0 → normalised 0.0 → avg = (5.0 + 0.0) / 2 = 2.5
        assert _compute_effective_rating(5.0, 0) == pytest.approx(2.5)

    def test_community_zero_with_critic(self):
        """CommunityRating=0 is a real score → averaged with critic."""
        # community=0.0, critic=80 → normalised 8.0 → avg = (0.0 + 8.0) / 2 = 4.0
        assert _compute_effective_rating(0.0, 80) == pytest.approx(4.0)

    def test_only_critic_zero(self):
        """Only critic=0, no community → returns 0.0."""
        assert _compute_effective_rating(None, 0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestIsFranchiseProtected
# ---------------------------------------------------------------------------


class TestIsFranchiseProtected:
    """Tests for _is_franchise_protected."""

    def test_tmdbcollection_present(self):
        """Exact key 'TmdbCollection' triggers protection."""
        assert _is_franchise_protected({"TmdbCollection": "123"}) is True

    def test_tmdbcollection_case_insensitive_upper(self):
        """All-upper key is also detected."""
        assert _is_franchise_protected({"TMDBCOLLECTION": "123"}) is True

    def test_tmdbcollection_case_insensitive_lower(self):
        """All-lower key is detected."""
        assert _is_franchise_protected({"tmdbcollection": "456"}) is True

    def test_no_collection(self):
        """Regular ProviderIds without collection key is not protected."""
        assert _is_franchise_protected({"Imdb": "tt1234567", "Tmdb": "999"}) is False

    def test_empty_provider_ids(self):
        """Empty dict is not protected."""
        assert _is_franchise_protected({}) is False


# ---------------------------------------------------------------------------
# TestIsPathProtected
# ---------------------------------------------------------------------------


class TestIsPathProtected:
    """Tests for _is_path_protected (DA fix #7)."""

    def test_matching_path(self):
        """Path containing the protected substring is protected."""
        assert _is_path_protected("/Movies/Dokumenty/film.mkv", ["/Dokumenty/"]) is True

    def test_non_matching_path(self):
        """Path not containing the substring is not protected."""
        assert _is_path_protected("/Movies/HD/film.mkv", ["/Dokumenty/"]) is False

    def test_multiple_protect_paths_first_matches(self):
        """Protected if any path substring matches."""
        assert _is_path_protected("/Movies/Dokumenty/film.mkv", ["/Other/", "/Dokumenty/"]) is True

    def test_multiple_protect_paths_none_match(self):
        """Not protected if no substring matches."""
        assert _is_path_protected("/Movies/HD/film.mkv", ["/Other/", "/Dokumenty/"]) is False

    def test_empty_string_guard(self):
        """Empty string in protect_paths must NOT match every path (DA fix #7)."""
        assert _is_path_protected("/Movies/HD/film.mkv", [""]) is False
        assert _is_path_protected("/Movies/Dokumenty/film.mkv", ["", "/Dokumenty/"]) is True

    def test_empty_protect_paths_list(self):
        """Empty protect_paths list means no path is protected."""
        assert _is_path_protected("/Movies/HD/film.mkv", []) is False


# ---------------------------------------------------------------------------
# TestIsExcludedByProviderId
# ---------------------------------------------------------------------------


class TestIsExcludedByProviderId:
    """Tests for _is_excluded_by_provider_id (DA fix #5)."""

    def test_imdb_id_excluded(self):
        """IMDB ID in exclusion set triggers exclusion."""
        provider_ids = {"Imdb": "tt0120737", "Tmdb": "120"}
        assert _is_excluded_by_provider_id(provider_ids, {"tt0120737"}) is True

    def test_tmdb_id_excluded(self):
        """TMDB ID in exclusion set triggers exclusion."""
        provider_ids = {"Tmdb": "27205"}
        assert _is_excluded_by_provider_id(provider_ids, {"27205"}) is True

    def test_not_excluded(self):
        """Movie with IDs not in exclusion set is not excluded."""
        provider_ids = {"Imdb": "tt9999999"}
        assert _is_excluded_by_provider_id(provider_ids, {"tt0120737"}) is False

    def test_empty_exclude_set(self):
        """Empty exclusion set never excludes anything."""
        provider_ids = {"Imdb": "tt0120737"}
        assert _is_excluded_by_provider_id(provider_ids, set()) is False

    def test_empty_provider_ids(self):
        """Movie with no provider IDs is not excluded."""
        assert _is_excluded_by_provider_id({}, {"tt0120737"}) is False


# ---------------------------------------------------------------------------
# TestGetMovieActorNames
# ---------------------------------------------------------------------------


class TestGetMovieActorNames:
    """Tests for _get_movie_actor_names."""

    def test_actors_only(self):
        """Directors and composers are filtered out; only actors returned."""
        people = [
            {"Name": "Tom Hanks", "Type": "Actor"},
            {"Name": "Steven Spielberg", "Type": "Director"},
            {"Name": "John Williams", "Type": "Composer"},
            {"Name": "Robin Wright", "Type": "Actor"},
        ]
        result = _get_movie_actor_names(people)
        assert result == {"Tom Hanks", "Robin Wright"}

    def test_empty_people(self):
        """Empty people list returns empty set."""
        assert _get_movie_actor_names([]) == set()

    def test_missing_type_field(self):
        """Entries without a Type field are excluded (Type defaults to None)."""
        people = [
            {"Name": "Unknown Person"},
            {"Name": "Tom Hanks", "Type": "Actor"},
        ]
        result = _get_movie_actor_names(people)
        assert result == {"Tom Hanks"}


# ---------------------------------------------------------------------------
# TestResolvePrimaryUserId  (DA fix #11)
# ---------------------------------------------------------------------------


class TestResolvePrimaryUserId:
    """Tests for _resolve_primary_user_id (DA fix #11)."""

    USERS = [
        {"Id": "uid1", "Name": "Barlog"},
        {"Id": "uid2", "Name": "Alice"},
        {"Id": "uid3", "Name": "Bob"},
    ]

    def _mock_client(self, users=None):
        client = MagicMock()
        resp = _make_response(users or self.USERS)
        with patch("emby_dedupe.cli.cleanup.make_http_request", return_value=resp):
            return client, resp

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_find_by_username_exact(self, mock_req):
        """Looks up user by exact username match."""
        mock_req.return_value = _make_response(self.USERS)
        client = MagicMock()
        uid = _resolve_primary_user_id(client, "http://emby", "Barlog")
        assert uid == "uid1"

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_find_by_username_case_insensitive(self, mock_req):
        """Username lookup is case-insensitive."""
        mock_req.return_value = _make_response(self.USERS)
        client = MagicMock()
        uid = _resolve_primary_user_id(client, "http://emby", "barlog")
        assert uid == "uid1"

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_username_not_found_falls_back_to_first(self, mock_req):
        """Unknown username falls back to first user with warning."""
        mock_req.return_value = _make_response(self.USERS)
        client = MagicMock()
        with patch("emby_dedupe.cli.cleanup.logger") as mock_logger:
            uid = _resolve_primary_user_id(client, "http://emby", "Nonexistent")
            assert uid == "uid1"
            mock_logger.warning.assert_called_once()
            warn_msg = mock_logger.warning.call_args[0][0]
            assert "not found" in warn_msg.lower() or "falling back" in warn_msg.lower()

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_no_username_falls_back_to_first_with_warning(self, mock_req):
        """No username provided → first user returned with warning (DA fix #11)."""
        mock_req.return_value = _make_response(self.USERS)
        client = MagicMock()
        with patch("emby_dedupe.cli.cleanup.logger") as mock_logger:
            uid = _resolve_primary_user_id(client, "http://emby", None)
            assert uid == "uid1"
            mock_logger.warning.assert_called_once()

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_empty_users_list_returns_empty_string(self, mock_req):
        """When GET /Users returns [], function logs error and returns '' (Bug fix #2).

        An empty string prevents the malformed /Users//Items URL from being constructed.
        """
        mock_req.return_value = _make_response([])
        client = MagicMock()
        result = _resolve_primary_user_id(client, "http://emby", "Barlog")
        assert result == ""


# ---------------------------------------------------------------------------
# TestCheckPlayAndInterestBatch  (DA fix #13)
# ---------------------------------------------------------------------------


class TestCheckPlayAndInterestBatch:
    """Tests for _check_play_and_interest_batch (DA fix #13)."""

    USERS = [{"Id": "u1"}, {"Id": "u2"}]

    def _user_items_response(self, items):
        return _make_response({"Items": items})

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_movie_played_by_one_user_is_protected(self, mock_req):
        """Movie played by any user goes into played_ids."""
        mock_req.return_value = self._user_items_response([
            {"Id": "m1", "UserData": {"Played": True, "IsFavorite": False, "PlaybackPositionTicks": 0}},
        ])
        client = MagicMock()
        played, interested = _check_play_and_interest_batch(client, "http://emby", self.USERS, ["m1"])
        assert "m1" in played

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_movie_favorited_is_protected(self, mock_req):
        """Movie marked as favorite by any user goes into interested_ids."""
        mock_req.return_value = self._user_items_response([
            {"Id": "m2", "UserData": {"Played": False, "IsFavorite": True, "PlaybackPositionTicks": 0}},
        ])
        client = MagicMock()
        played, interested = _check_play_and_interest_batch(client, "http://emby", self.USERS, ["m2"])
        assert "m2" in interested
        assert "m2" not in played

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_movie_started_is_protected(self, mock_req):
        """Movie with PlaybackPositionTicks > 0 goes into interested_ids (in-progress)."""
        mock_req.return_value = self._user_items_response([
            {"Id": "m3", "UserData": {"Played": False, "IsFavorite": False, "PlaybackPositionTicks": 12345}},
        ])
        client = MagicMock()
        played, interested = _check_play_and_interest_batch(client, "http://emby", self.USERS, ["m3"])
        assert "m3" in interested

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_unplayed_uninterested_not_protected(self, mock_req):
        """Movie with no play activity not in either set."""
        mock_req.return_value = self._user_items_response([
            {"Id": "m4", "UserData": {"Played": False, "IsFavorite": False, "PlaybackPositionTicks": 0}},
        ])
        client = MagicMock()
        played, interested = _check_play_and_interest_batch(client, "http://emby", self.USERS, ["m4"])
        assert "m4" not in played
        assert "m4" not in interested

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_batch_chunking_uses_100_item_chunks(self, mock_req):
        """With 250 candidates and 1 user, should make 3 API calls (ceil(250/100))."""
        mock_req.return_value = self._user_items_response([])
        client = MagicMock()
        candidate_ids = [f"m{i}" for i in range(250)]
        users = [{"Id": "u1"}]
        _check_play_and_interest_batch(client, "http://emby", users, candidate_ids)
        # 250 candidates / 100 per chunk = 3 calls for 1 user
        assert mock_req.call_count == 3

    def test_empty_candidates_returns_empty_sets(self):
        """No candidates → no API calls, empty sets returned."""
        client = MagicMock()
        played, interested = _check_play_and_interest_batch(client, "http://emby", self.USERS, [])
        assert played == set()
        assert interested == set()

    def test_empty_users_returns_empty_sets(self):
        """No users → no API calls, empty sets returned."""
        client = MagicMock()
        played, interested = _check_play_and_interest_batch(client, "http://emby", [], ["m1"])
        assert played == set()
        assert interested == set()


# ---------------------------------------------------------------------------
# TestValidateCleanupArgs  (DA fix #4, #11)
# ---------------------------------------------------------------------------


class TestValidateCleanupArgs:
    """Tests for _validate_cleanup_args."""

    def _call(self, **kwargs):
        defaults = dict(
            host="http://emby",
            api_key="key",
            libraries=["Movies"],
            all_libraries=False,
            doit=False,
            username=None,
            password=None,
        )
        defaults.update(kwargs)
        _validate_cleanup_args(**defaults)

    def test_valid_with_library(self):
        """Valid args with explicit library name — no error."""
        self._call()  # should not raise

    def test_valid_with_all_libraries(self):
        """--all-libraries allows empty library list (DA fix #4)."""
        self._call(libraries=[], all_libraries=True)  # no error

    def test_missing_host_raises_sys_exit(self):
        """Missing host → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(host=None)

    def test_missing_api_key_raises_sys_exit(self):
        """Missing api_key → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(api_key=None)

    def test_no_library_and_no_all_libraries_exits(self):
        """No library and no --all-libraries → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(libraries=[], all_libraries=False)

    def test_doit_without_username_exits(self):
        """--doit without --username → sys.exit(1) (DA fix #11)."""
        with pytest.raises(SystemExit):
            self._call(doit=True, username=None, password="pw")

    def test_doit_without_password_exits(self):
        """--doit without --password → sys.exit(1)."""
        with pytest.raises(SystemExit):
            self._call(doit=True, username="user", password=None)

    def test_dry_run_no_username_warns_not_exits(self):
        """Dry-run without username warns but does NOT exit (DA fix #11)."""
        with patch("emby_dedupe.cli.cleanup.logger") as mock_logger:
            self._call(doit=False, username=None)  # must NOT raise
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# TestRunCleanupPipeline
# ---------------------------------------------------------------------------


class TestRunCleanupPipeline:
    """Tests for _run_cleanup_pipeline — all 7 filter layers."""

    BASE_URL = "http://emby"

    def _make_client(self):
        return MagicMock()

    def _pipeline(self, movies, config=None, played_ids=None, interested_ids=None, actors=None):
        """
        Helper: run pipeline with mocked API calls returning the given movies list.
        Returns (candidates, stats).
        """
        config = config or CleanupConfig()
        played_ids = played_ids or set()
        interested_ids = interested_ids or set()
        actors = actors if actors is not None else set()

        client = self._make_client()
        with patch("emby_dedupe.cli.cleanup._fetch_all_library_movies", return_value=movies), \
             patch("emby_dedupe.cli.cleanup._build_favorite_actors_set", return_value=actors), \
             patch("emby_dedupe.cli.cleanup._fetch_all_users", return_value=[]), \
             patch("emby_dedupe.cli.cleanup._check_play_and_interest_batch", return_value=(played_ids, interested_ids)):
            return _run_cleanup_pipeline(client, self.BASE_URL, config, ["lib1"], "uid1")

    def test_age_filter_removes_too_young(self):
        """Movies newer than min_age_years are filtered at stage 1."""
        young_movie = _make_movie(item_id="young", date_created=_date_years_ago(1))
        candidates, stats = self._pipeline([young_movie])
        assert stats["age_filtered"] == 1
        assert stats["final_candidates"] == 0
        assert len(candidates) == 0

    def test_exclusion_filter_removes_excluded(self):
        """Movies with excluded provider IDs are removed at stage 2."""
        excluded_movie = _make_movie(
            item_id="excluded",
            provider_ids={"Imdb": "tt0120737"},
            rating=1.0,  # low enough to be a candidate otherwise
        )
        config = CleanupConfig(excluded_provider_ids={"tt0120737"})
        candidates, stats = self._pipeline([excluded_movie], config=config)
        assert stats["excluded_filtered"] == 1
        assert stats["final_candidates"] == 0

    def test_played_by_any_user_protected(self):
        """Movie played by any user is removed from candidates."""
        movie = _make_movie(item_id="played_m", rating=1.0)
        candidates, stats = self._pipeline([movie], played_ids={"played_m"})
        assert stats["play_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_interested_user_protected(self):
        """Movie with IsFavorite/in-progress from any user is protected."""
        movie = _make_movie(item_id="fav_m", rating=1.0)
        candidates, stats = self._pipeline([movie], interested_ids={"fav_m"})
        assert stats["interest_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_favorite_actor_protected(self):
        """Movie starring a favorite actor is not a candidate."""
        people = [{"Name": "Tom Hanks", "Type": "Actor"}]
        movie = _make_movie(item_id="actor_m", people=people, rating=1.0)
        candidates, stats = self._pipeline([movie], actors={"Tom Hanks"})
        assert stats["actor_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_favorite_actor_protected_at_9_years(self):
        """9-year-old movie with favorite actor is still protected."""
        people = [{"Name": "Tom Hanks", "Type": "Actor"}]
        movie = _make_movie(item_id="actor_9yr", date_created=_date_years_ago(9), people=people, rating=1.0)
        candidates, stats = self._pipeline([movie], actors={"Tom Hanks"})
        assert stats["actor_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_favorite_actor_not_protected_after_10_years(self):
        """10+ year old movie loses favorite actor protection → becomes candidate."""
        people = [{"Name": "Tom Hanks", "Type": "Actor"}]
        movie = _make_movie(item_id="actor_11yr", date_created=_date_years_ago(11), people=people, rating=1.0)
        candidates, stats = self._pipeline([movie], actors={"Tom Hanks"})
        assert stats["actor_protected"] == 0
        assert stats["final_candidates"] == 1

    def test_12yr_masterpiece_protected(self):
        """12+ year old movie with 9.0+ rating is still protected."""
        movie = _make_movie(item_id="master", date_created=_date_years_ago(13), rating=9.2)
        candidates, stats = self._pipeline([movie])
        assert stats["rating_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_12yr_non_masterpiece_candidate(self):
        """12+ year old movie with rating below 9.0 loses all protection."""
        people = [{"Name": "Tom Hanks", "Type": "Actor"}]
        movie = _make_movie(
            item_id="old_good", date_created=_date_years_ago(13),
            rating=8.5, people=people,
            provider_ids={"TmdbCollection": "10"},
            path="/Movies/Dokumenty/Old.mkv",
        )
        candidates, stats = self._pipeline([movie], actors={"Tom Hanks"})
        # franchise, path, actor all bypassed at 12+ years
        assert stats["actor_protected"] == 0
        assert stats["franchise_protected"] == 0
        assert stats["path_protected"] == 0
        assert stats["final_candidates"] == 1
        assert candidates[0].threshold == 9.0

    def test_11yr_still_has_normal_protections(self):
        """11-year-old movie still uses normal protection layers."""
        movie = _make_movie(
            item_id="11yr_franchise", date_created=_date_years_ago(11),
            rating=1.0, provider_ids={"TmdbCollection": "10"},
        )
        candidates, stats = self._pipeline([movie])
        assert stats["franchise_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_franchise_protected(self):
        """Movie with TmdbCollection in ProviderIds is not a candidate."""
        movie = _make_movie(
            item_id="franchise_m",
            provider_ids={"TmdbCollection": "10"},
            rating=1.0,
        )
        candidates, stats = self._pipeline([movie])
        assert stats["franchise_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_path_protected(self):
        """Movie in a protected path directory is not a candidate."""
        movie = _make_movie(
            item_id="doc_m",
            path="/Movies/Dokumenty/film.mkv",
            rating=1.0,
        )
        candidates, stats = self._pipeline([movie])
        assert stats["path_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_high_rating_protected(self):
        """Movie with CommunityRating >= threshold is not a candidate."""
        # 4 years old → threshold = 6.5; rating 7.0 is above threshold
        movie = _make_movie(item_id="good_m", date_created=_date_years_ago(4), rating=7.0)
        candidates, stats = self._pipeline([movie])
        assert stats["rating_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_low_rating_flagged_as_candidate(self):
        """Movie with rating below threshold becomes a candidate."""
        # 4 years old → threshold = 6.5; rating 4.0 is below threshold
        movie = _make_movie(item_id="bad_m", date_created=_date_years_ago(4), rating=4.0)
        candidates, stats = self._pipeline([movie])
        assert stats["final_candidates"] == 1
        assert candidates[0].item_id == "bad_m"

    def test_unrated_treated_as_zero(self):
        """CommunityRating=None is treated as 0.0 — always below threshold (DA fix #6)."""
        movie = _make_movie(item_id="unrated_m", date_created=_date_years_ago(4), rating=None)
        candidates, stats = self._pipeline([movie])
        assert stats["final_candidates"] == 1
        assert candidates[0].rating is None  # None preserved, not converted to 0.0

    def test_candidates_sorted_by_age_desc(self):
        """Candidates are sorted by age_years descending (oldest movies first)."""
        movies = [
            _make_movie(item_id="young", date_created=_date_years_ago(4), rating=1.0, size=500_000_000),
            _make_movie(item_id="old", date_created=_date_years_ago(8), rating=1.0, size=500_000_000),
            _make_movie(item_id="mid", date_created=_date_years_ago(6), rating=1.0, size=500_000_000),
        ]
        candidates, stats = self._pipeline(movies)
        assert stats["final_candidates"] == 3
        assert candidates[0].item_id == "old"
        assert candidates[1].item_id == "mid"
        assert candidates[2].item_id == "young"

    def test_size_none_treated_as_zero(self):
        """Size=None in Emby response → size_bytes=0 (DA fix #15)."""
        movie = _make_movie(item_id="nosize", date_created=_date_years_ago(4), rating=1.0, size=None)
        candidates, stats = self._pipeline([movie])
        assert stats["final_candidates"] == 1
        assert candidates[0].size_bytes == 0

    def test_critic_rating_protects_low_community(self):
        """Movie with low community but high critic rating is protected via average."""
        # 4 years old → threshold = 6.5
        # community=5.0, critic=80 → normalised 8.0 → avg = (5.0 + 8.0) / 2 = 6.5 → protected
        movie = _make_movie(
            item_id="critic_saved", date_created=_date_years_ago(4),
            rating=5.0, critic_rating=80,
        )
        candidates, stats = self._pipeline([movie])
        assert stats["rating_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_both_ratings_low_flags_as_candidate(self):
        """Movie with both community and critic below threshold → candidate."""
        # 4 years old → threshold = 6.5
        # community=4.0, critic=55 → normalised 5.5 → avg = (4.0 + 5.5) / 2 = 4.75 < 6.5
        movie = _make_movie(
            item_id="both_low", date_created=_date_years_ago(4),
            rating=4.0, critic_rating=55,
        )
        candidates, stats = self._pipeline([movie])
        assert stats["final_candidates"] == 1
        assert candidates[0].critic_rating == 55

    def test_candidate_preserves_critic_rating(self):
        """CleanupCandidate stores the raw critic rating."""
        movie = _make_movie(
            item_id="raw_cr", date_created=_date_years_ago(4),
            rating=3.0, critic_rating=40,
        )
        candidates, stats = self._pipeline([movie])
        assert candidates[0].critic_rating == 40
        assert candidates[0].rating == 3.0


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
        with patch("emby_dedupe.cli.cleanup.shutil.copy2"), \
             patch("emby_dedupe.cli.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.cli.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html><body>test</body></html>", no_open=True)
            assert path.startswith(tempfile.gettempdir())
            assert path.endswith(".html")
            assert "emby_cleanup_report_" in path

    def test_html_file_content_written(self):
        """HTML content is written to the file."""
        with patch("emby_dedupe.cli.cleanup.shutil.copy2"), \
             patch("emby_dedupe.cli.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.cli.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html><body>hello world</body></html>", no_open=True)
            with open(path) as f:
                content = f.read()
            assert "hello world" in content
            os.unlink(path)  # cleanup

    def test_css_copied_alongside_html(self):
        """report.css is copied to the same temp directory as the HTML file."""
        with patch("emby_dedupe.cli.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.cli.cleanup.shutil.copy2") as mock_copy, \
             patch("emby_dedupe.cli.cleanup.webbrowser.open"):
            path = _save_cleanup_html_report("<html/>", no_open=True)
            assert mock_copy.called
            copy_dst = mock_copy.call_args[0][1]
            assert copy_dst == os.path.join(tempfile.gettempdir(), "report.css")
            os.unlink(path)  # cleanup

    def test_returns_file_path(self):
        """Return value is the path string of the saved file."""
        with patch("emby_dedupe.cli.cleanup.shutil.copy2"), \
             patch("emby_dedupe.cli.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.cli.cleanup.webbrowser.open"):
            result = _save_cleanup_html_report("<html/>", no_open=True)
            assert isinstance(result, str)
            assert os.path.basename(result).startswith("emby_cleanup_report_")
            os.unlink(result)  # cleanup

    def test_no_open_flag_prevents_browser_open(self):
        """no_open=True prevents webbrowser.open() call."""
        with patch("emby_dedupe.cli.cleanup.shutil.copy2"), \
             patch("emby_dedupe.cli.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.cli.cleanup.webbrowser.open") as mock_open:
            path = _save_cleanup_html_report("<html/>", no_open=True)
            mock_open.assert_not_called()
            os.unlink(path)  # cleanup

    def test_browser_opened_when_no_open_false(self):
        """no_open=False triggers webbrowser.open()."""
        with patch("emby_dedupe.cli.cleanup.shutil.copy2"), \
             patch("emby_dedupe.cli.cleanup.os.path.exists", return_value=True), \
             patch("emby_dedupe.cli.cleanup.webbrowser.open") as mock_open:
            path = _save_cleanup_html_report("<html/>", no_open=False)
            mock_open.assert_called_once()
            os.unlink(path)  # cleanup


# ---------------------------------------------------------------------------
# TestRunCleanupCommand  (Bug fix #2: empty primary_user_id abort)
# ---------------------------------------------------------------------------


class TestRunCleanupCommand:
    """Integration-level tests for run_cleanup_command entry point."""

    def _minimal_args(self, **overrides):
        """Build minimal valid args Namespace for run_cleanup_command."""
        from argparse import Namespace

        defaults = dict(
            host="http://emby",
            port=None,
            api_key="key",
            library=["Movies"],
            all_libraries=False,
            doit=False,
            username="Barlog",
            password=None,
            min_age_years=3,
            protect_paths=["/Dokumenty/"],
            base_rating=6.0,
            decay_step=0.5,
            max_rating=8.0,
    
            exclude_ids="",
            format="console",
            html_report=False,
            html_only=False,
            no_open=True,
        )
        defaults.update(overrides)
        return Namespace(**defaults)

    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup._fetch_all_library_movies")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_empty_primary_user_id_aborts_cleanly(
        self, mock_client_cls, mock_fetch, mock_check, mock_resolve
    ):
        """When _resolve_primary_user_id returns '', run_cleanup_command returns early.

        Prevents malformed /Users//Items URL from being constructed (Bug fix #2).
        """
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        args = self._minimal_args()
        run_cleanup_command(args)

        # _fetch_all_library_movies must NOT have been called — we aborted before pipeline
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------


def _make_series(
    item_id="s1",
    name="Test Series",
    date_created=None,
    rating=None,
    critic_rating=None,
    path="/Movies/Serials/Test Series",
    provider_ids=None,
    library_name="SERIALS",
    production_year=2020,
    recursive_item_count=10,
):
    """Create a minimal Emby series item dict for pipeline testing."""
    return {
        "Id": item_id,
        "Name": name,
        "DateCreated": date_created or _date_years_ago(4),
        "CommunityRating": rating,
        "CriticRating": critic_rating,
        "Path": path,
        "ProviderIds": provider_ids or {},
        "ProductionYear": production_year,
        "RecursiveItemCount": recursive_item_count,
        "_library_name": library_name,
        "_library_id": "lib_series",
    }


# ---------------------------------------------------------------------------
# TestSeriesCleanupCandidate
# ---------------------------------------------------------------------------


class TestSeriesCleanupCandidate:
    """Tests for SeriesCleanupCandidate dataclass."""

    def test_basic_creation(self):
        """SeriesCleanupCandidate can be created with all required fields."""
        c = SeriesCleanupCandidate(
            item_id="s1",
            name="Test Series",
            year=2020,
            rating=7.5,
            critic_rating=None,
            threshold=6.5,
            stale_years=4.0,
            last_episode_added="2022-01-15T00:00:00Z",
            episode_count=24,
            library="SERIALS",
            size_bytes=5_000_000_000,
            path="/Movies/Serials/Test Series",
        )
        assert c.item_id == "s1"
        assert c.name == "Test Series"
        assert c.year == 2020
        assert c.rating == 7.5
        assert c.stale_years == 4.0
        assert c.episode_count == 24
        assert c.size_bytes == 5_000_000_000

    def test_default_deletion_result_is_none(self):
        """deletion_result defaults to None."""
        c = SeriesCleanupCandidate(
            item_id="s2", name="Another", year=None, rating=None,
            critic_rating=None, threshold=6.0, stale_years=3.0, last_episode_added=None,
            episode_count=0, library="SERIALS", size_bytes=0,
            path="/series/path",
        )
        assert c.deletion_result is None

    def test_none_rating_preserved(self):
        """None rating is not converted to 0.0."""
        c = SeriesCleanupCandidate(
            item_id="s3", name="Unrated", year=2019, rating=None,
            critic_rating=None, threshold=6.5, stale_years=4.0, last_episode_added=None,
            episode_count=5, library="SERIALS", size_bytes=100,
            path="/series",
        )
        assert c.rating is None


# ---------------------------------------------------------------------------
# TestBuildLastEpisodeAddedMap
# ---------------------------------------------------------------------------


class TestBuildLastEpisodeAddedMap:
    """Tests for _build_last_episode_added_map."""

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_groups_by_series_id_and_takes_max(self, mock_req):
        """Multiple episodes per series — max DateCreated is kept."""
        mock_req.return_value = _make_response({
            "Items": [
                {"SeriesId": "s1", "DateCreated": "2021-01-01T00:00:00Z"},
                {"SeriesId": "s1", "DateCreated": "2022-06-15T00:00:00Z"},
                {"SeriesId": "s1", "DateCreated": "2020-03-10T00:00:00Z"},
                {"SeriesId": "s2", "DateCreated": "2023-01-01T00:00:00Z"},
            ],
            "TotalRecordCount": 4,
        })
        client = MagicMock()
        result = _build_last_episode_added_map(client, "http://emby", ["lib1"])
        assert result["s1"] == "2022-06-15T00:00:00Z"
        assert result["s2"] == "2023-01-01T00:00:00Z"

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_empty_response(self, mock_req):
        """Empty episode list returns empty map."""
        mock_req.return_value = _make_response({"Items": [], "TotalRecordCount": 0})
        client = MagicMock()
        result = _build_last_episode_added_map(client, "http://emby", ["lib1"])
        assert result == {}

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_skips_episodes_without_series_id(self, mock_req):
        """Episodes with empty/missing SeriesId are ignored."""
        mock_req.return_value = _make_response({
            "Items": [
                {"SeriesId": "", "DateCreated": "2021-01-01T00:00:00Z"},
                {"DateCreated": "2021-01-01T00:00:00Z"},
                {"SeriesId": "s1", "DateCreated": "2022-01-01T00:00:00Z"},
            ],
            "TotalRecordCount": 3,
        })
        client = MagicMock()
        result = _build_last_episode_added_map(client, "http://emby", ["lib1"])
        assert len(result) == 1
        assert "s1" in result

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_pagination(self, mock_req):
        """Handles paginated responses correctly."""
        # First page: 2 items, total 3
        page1 = _make_response({
            "Items": [
                {"SeriesId": "s1", "DateCreated": "2021-01-01T00:00:00Z"},
                {"SeriesId": "s2", "DateCreated": "2022-01-01T00:00:00Z"},
            ],
            "TotalRecordCount": 3,
        })
        # Second page: 1 item
        page2 = _make_response({
            "Items": [
                {"SeriesId": "s1", "DateCreated": "2023-01-01T00:00:00Z"},
            ],
            "TotalRecordCount": 3,
        })
        mock_req.side_effect = [page1, page2]
        client = MagicMock()
        result = _build_last_episode_added_map(client, "http://emby", ["lib1"])
        assert result["s1"] == "2023-01-01T00:00:00Z"
        assert result["s2"] == "2022-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# TestCheckSeriesPlayAndFavorites
# ---------------------------------------------------------------------------


class TestCheckSeriesPlayAndFavorites:
    """Tests for _check_series_play_and_favorites."""

    USERS = [{"Id": "u1", "Name": "User1"}, {"Id": "u2", "Name": "User2"}]

    def _series_items_response(self, items):
        return _make_response({"Items": items})

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_played_via_played_flag(self, mock_req):
        """Series with Played=True is detected as played."""
        mock_req.return_value = self._series_items_response([
            {
                "Id": "s1",
                "RecursiveItemCount": 10,
                "UserData": {"Played": True, "IsFavorite": False, "UnplayedItemCount": 0},
            },
        ])
        client = MagicMock()
        played, fav = _check_series_play_and_favorites(client, "http://emby", self.USERS, ["s1"])
        assert "s1" in played

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_played_via_unplayed_less_than_total(self, mock_req):
        """Series with UnplayedItemCount < RecursiveItemCount is partially watched."""
        mock_req.return_value = self._series_items_response([
            {
                "Id": "s2",
                "RecursiveItemCount": 10,
                "UserData": {"Played": False, "IsFavorite": False, "UnplayedItemCount": 7},
            },
        ])
        client = MagicMock()
        played, fav = _check_series_play_and_favorites(client, "http://emby", self.USERS, ["s2"])
        assert "s2" in played

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_fully_unplayed_not_in_played(self, mock_req):
        """Series with all episodes unplayed is NOT in played_ids."""
        mock_req.return_value = self._series_items_response([
            {
                "Id": "s3",
                "RecursiveItemCount": 10,
                "UserData": {"Played": False, "IsFavorite": False, "UnplayedItemCount": 10},
            },
        ])
        client = MagicMock()
        played, fav = _check_series_play_and_favorites(client, "http://emby", self.USERS, ["s3"])
        assert "s3" not in played

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_favorite_detected(self, mock_req):
        """Series marked as favorite by any user is in favorited_ids."""
        mock_req.return_value = self._series_items_response([
            {
                "Id": "s4",
                "RecursiveItemCount": 5,
                "UserData": {"Played": False, "IsFavorite": True, "UnplayedItemCount": 5},
            },
        ])
        client = MagicMock()
        played, fav = _check_series_play_and_favorites(client, "http://emby", self.USERS, ["s4"])
        assert "s4" in fav
        assert "s4" not in played

    def test_empty_candidates_returns_empty(self):
        """No candidates → empty sets, no API calls."""
        client = MagicMock()
        played, fav = _check_series_play_and_favorites(client, "http://emby", self.USERS, [])
        assert played == set()
        assert fav == set()

    def test_empty_users_returns_empty(self):
        """No users → empty sets."""
        client = MagicMock()
        played, fav = _check_series_play_and_favorites(client, "http://emby", [], ["s1"])
        assert played == set()
        assert fav == set()

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_chunk_size_50(self, mock_req):
        """With 120 candidates and 1 user, should make 3 API calls (ceil(120/50))."""
        mock_req.return_value = self._series_items_response([])
        client = MagicMock()
        ids = [f"s{i}" for i in range(120)]
        users = [{"Id": "u1", "Name": "User1"}]
        _check_series_play_and_favorites(client, "http://emby", users, ids)
        assert mock_req.call_count == 3  # 50+50+20


# ---------------------------------------------------------------------------
# TestCalculateSeriesSizes
# ---------------------------------------------------------------------------


class TestCalculateSeriesSizes:
    """Tests for _calculate_series_sizes."""

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_sums_episode_sizes(self, mock_req):
        """Total size is sum of all episode Size fields."""
        mock_req.return_value = _make_response({
            "Items": [
                {"Size": 1_000_000_000},
                {"Size": 2_000_000_000},
                {"Size": 500_000_000},
            ],
            "TotalRecordCount": 3,
        })
        client = MagicMock()
        result = _calculate_series_sizes(client, "http://emby", ["s1"])
        assert result["s1"] == 3_500_000_000

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_none_size_treated_as_zero(self, mock_req):
        """Size=None in episode → treated as 0."""
        mock_req.return_value = _make_response({
            "Items": [
                {"Size": 1_000_000},
                {"Size": None},
                {},
            ],
            "TotalRecordCount": 3,
        })
        client = MagicMock()
        result = _calculate_series_sizes(client, "http://emby", ["s1"])
        assert result["s1"] == 1_000_000

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_empty_series(self, mock_req):
        """Series with no episodes returns 0 bytes."""
        mock_req.return_value = _make_response({"Items": [], "TotalRecordCount": 0})
        client = MagicMock()
        result = _calculate_series_sizes(client, "http://emby", ["s1"])
        assert result["s1"] == 0


# ---------------------------------------------------------------------------
# TestProbeLibraryContent
# ---------------------------------------------------------------------------


class TestProbeLibraryContent:
    """Tests for _probe_library_content."""

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_movie_only_library(self, mock_req):
        """Library with only movies returns (N, 0)."""
        # First call: Movie count, Second call: Series count
        mock_req.side_effect = [
            _make_response({"TotalRecordCount": 500}),
            _make_response({"TotalRecordCount": 0}),
        ]
        client = MagicMock()
        movies, series = _probe_library_content(client, "http://emby", "lib1")
        assert movies == 500
        assert series == 0

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_series_only_library(self, mock_req):
        """Library with only series returns (0, N)."""
        mock_req.side_effect = [
            _make_response({"TotalRecordCount": 0}),
            _make_response({"TotalRecordCount": 200}),
        ]
        client = MagicMock()
        movies, series = _probe_library_content(client, "http://emby", "lib1")
        assert movies == 0
        assert series == 200

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_mixed_library(self, mock_req):
        """Library with both movies and series returns both counts."""
        mock_req.side_effect = [
            _make_response({"TotalRecordCount": 100}),
            _make_response({"TotalRecordCount": 50}),
        ]
        client = MagicMock()
        movies, series = _probe_library_content(client, "http://emby", "lib1")
        assert movies == 100
        assert series == 50

    @patch("emby_dedupe.cli.cleanup.make_http_request")
    def test_empty_library(self, mock_req):
        """Empty library returns (0, 0)."""
        mock_req.side_effect = [
            _make_response({"TotalRecordCount": 0}),
            _make_response({"TotalRecordCount": 0}),
        ]
        client = MagicMock()
        movies, series = _probe_library_content(client, "http://emby", "lib1")
        assert movies == 0
        assert series == 0


# ---------------------------------------------------------------------------
# TestRunSeriesCleanupPipeline
# ---------------------------------------------------------------------------


class TestRunSeriesCleanupPipeline:
    """Tests for _run_series_cleanup_pipeline — all 5 filter layers."""

    BASE_URL = "http://emby"

    def _pipeline(
        self,
        series_list,
        episode_map=None,
        config=None,
        played_ids=None,
        favorited_ids=None,
        size_map=None,
    ):
        """Helper: run series pipeline with mocked dependencies."""
        config = config or CleanupConfig()
        played_ids = played_ids or set()
        favorited_ids = favorited_ids or set()
        episode_map = episode_map or {}
        size_map = size_map or {}

        client = MagicMock()
        with patch("emby_dedupe.cli.cleanup._fetch_all_library_series", return_value=series_list), \
             patch("emby_dedupe.cli.cleanup._build_last_episode_added_map", return_value=episode_map), \
             patch("emby_dedupe.cli.cleanup._fetch_all_users", return_value=[]), \
             patch("emby_dedupe.cli.cleanup._check_series_play_and_favorites", return_value=(played_ids, favorited_ids)), \
             patch("emby_dedupe.cli.cleanup._calculate_series_sizes", return_value=size_map):
            return _run_series_cleanup_pipeline(
                client, self.BASE_URL, config, ["lib1"], "uid1"
            )

    def test_staleness_filter_removes_recent(self):
        """Series with recent episodes (< min_age_years) are filtered out."""
        series = _make_series(item_id="recent")
        # Episode added 1 year ago → not stale enough
        episode_map = {"recent": _date_years_ago(1)}
        candidates, stats = self._pipeline([series], episode_map=episode_map)
        assert stats["stale_filtered"] == 1
        assert stats["final_candidates"] == 0

    def test_staleness_filter_keeps_old(self):
        """Series with stale episodes (>= min_age_years) passes staleness filter."""
        series = _make_series(item_id="old", rating=1.0)
        episode_map = {"old": _date_years_ago(5)}
        candidates, stats = self._pipeline(
            [series], episode_map=episode_map, size_map={"old": 1000}
        )
        assert stats["stale_filtered"] == 0
        assert stats["final_candidates"] == 1

    def test_series_without_episodes_filtered_as_recent(self):
        """Series not in episode map → stale_years=0.0 → filtered as recent."""
        series = _make_series(item_id="no_eps")
        candidates, stats = self._pipeline([series], episode_map={})
        assert stats["stale_filtered"] == 1

    def test_exclusion_filter(self):
        """Series with excluded provider IDs are filtered."""
        series = _make_series(item_id="exc", provider_ids={"Imdb": "tt1111111"}, rating=1.0)
        episode_map = {"exc": _date_years_ago(5)}
        config = CleanupConfig(excluded_provider_ids={"tt1111111"})
        candidates, stats = self._pipeline([series], episode_map=episode_map, config=config)
        assert stats["excluded_filtered"] == 1
        assert stats["final_candidates"] == 0

    def test_play_protection(self):
        """Series watched by any user is protected."""
        series = _make_series(item_id="watched", rating=1.0)
        episode_map = {"watched": _date_years_ago(5)}
        candidates, stats = self._pipeline(
            [series], episode_map=episode_map, played_ids={"watched"}
        )
        assert stats["play_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_favorite_protection(self):
        """Series favorited by any user is protected."""
        series = _make_series(item_id="fav", rating=1.0)
        episode_map = {"fav": _date_years_ago(5)}
        candidates, stats = self._pipeline(
            [series], episode_map=episode_map, favorited_ids={"fav"}
        )
        assert stats["favorite_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_path_protection(self):
        """Series in a protected path is not a candidate."""
        series = _make_series(item_id="doc", path="/Movies/Dokumenty/Doc Series", rating=1.0)
        episode_map = {"doc": _date_years_ago(5)}
        candidates, stats = self._pipeline([series], episode_map=episode_map)
        assert stats["path_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_rating_protection(self):
        """Series with rating >= threshold is protected."""
        # 5 years stale → threshold = 7.0; rating 8.0 is above
        series = _make_series(item_id="good", rating=8.0)
        episode_map = {"good": _date_years_ago(5)}
        candidates, stats = self._pipeline([series], episode_map=episode_map)
        assert stats["rating_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_low_rating_flagged(self):
        """Series with rating below threshold becomes a candidate."""
        # 5 years stale → threshold = 7.0; rating 4.0 is below
        series = _make_series(item_id="bad", rating=4.0)
        episode_map = {"bad": _date_years_ago(5)}
        candidates, stats = self._pipeline(
            [series], episode_map=episode_map, size_map={"bad": 5000}
        )
        assert stats["final_candidates"] == 1
        assert candidates[0].item_id == "bad"
        assert candidates[0].size_bytes == 5000

    def test_unrated_treated_as_zero(self):
        """Series with CommunityRating=None treated as 0.0 — always below threshold."""
        series = _make_series(item_id="unrated", rating=None)
        episode_map = {"unrated": _date_years_ago(5)}
        candidates, stats = self._pipeline(
            [series], episode_map=episode_map, size_map={"unrated": 100}
        )
        assert stats["final_candidates"] == 1
        assert candidates[0].rating is None

    def test_candidates_sorted_by_staleness_desc(self):
        """Candidates are sorted by stale_years descending."""
        s1 = _make_series(item_id="young_s", rating=1.0)
        s2 = _make_series(item_id="old_s", rating=1.0)
        s3 = _make_series(item_id="mid_s", rating=1.0)
        episode_map = {
            "young_s": _date_years_ago(4),
            "old_s": _date_years_ago(8),
            "mid_s": _date_years_ago(6),
        }
        size_map = {"young_s": 100, "old_s": 200, "mid_s": 300}
        candidates, stats = self._pipeline(
            [s1, s2, s3], episode_map=episode_map, size_map=size_map
        )
        assert stats["final_candidates"] == 3
        assert candidates[0].item_id == "old_s"
        assert candidates[1].item_id == "mid_s"
        assert candidates[2].item_id == "young_s"

    def test_end_to_end_multiple_filters(self):
        """Multiple series pass through all filters correctly."""
        series_list = [
            _make_series(item_id="recent_s"),          # staleness < 3yr
            _make_series(item_id="excluded_s", provider_ids={"Imdb": "tt999"}, rating=1.0),
            _make_series(item_id="watched_s", rating=1.0),
            _make_series(item_id="fav_s", rating=1.0),
            _make_series(item_id="doc_s", path="/Movies/Dokumenty/Doc", rating=1.0),
            _make_series(item_id="good_s", rating=9.0),
            _make_series(item_id="candidate_s", rating=2.0),
        ]
        episode_map = {
            "recent_s": _date_years_ago(1),
            "excluded_s": _date_years_ago(5),
            "watched_s": _date_years_ago(5),
            "fav_s": _date_years_ago(5),
            "doc_s": _date_years_ago(5),
            "good_s": _date_years_ago(5),
            "candidate_s": _date_years_ago(5),
        }
        config = CleanupConfig(excluded_provider_ids={"tt999"})
        size_map = {"candidate_s": 10_000}

        candidates, stats = self._pipeline(
            series_list,
            episode_map=episode_map,
            config=config,
            played_ids={"watched_s"},
            favorited_ids={"fav_s"},
            size_map=size_map,
        )

        assert stats["total_analyzed"] == 7
        assert stats["stale_filtered"] == 1
        assert stats["excluded_filtered"] == 1
        assert stats["play_protected"] == 1
        assert stats["favorite_protected"] == 1
        assert stats["path_protected"] == 1
        assert stats["rating_protected"] == 1
        assert stats["final_candidates"] == 1
        assert candidates[0].item_id == "candidate_s"

    def test_empty_series_list(self):
        """Empty series list returns empty candidates and zero stats."""
        candidates, stats = self._pipeline([])
        assert candidates == []
        assert stats["total_analyzed"] == 0
        assert stats["final_candidates"] == 0

    def test_critic_rating_protects_series(self):
        """Series with low community but high critic rating is protected via average."""
        # 5 years stale → threshold = 7.0
        # community=5.0, critic=90 → normalised 9.0 → avg = (5.0 + 9.0) / 2 = 7.0 → protected
        series = _make_series(item_id="critic_s", rating=5.0, critic_rating=90)
        episode_map = {"critic_s": _date_years_ago(5)}
        candidates, stats = self._pipeline([series], episode_map=episode_map)
        assert stats["rating_protected"] == 1
        assert stats["final_candidates"] == 0

    def test_series_candidate_preserves_critic_rating(self):
        """SeriesCleanupCandidate stores raw critic rating."""
        series = _make_series(item_id="cr_s", rating=2.0, critic_rating=30)
        episode_map = {"cr_s": _date_years_ago(5)}
        candidates, stats = self._pipeline(
            [series], episode_map=episode_map, size_map={"cr_s": 5000}
        )
        assert stats["final_candidates"] == 1
        assert candidates[0].critic_rating == 30
        assert candidates[0].rating == 2.0


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
# TestRunCleanupCommandWithSeries
# ---------------------------------------------------------------------------


class TestRunCleanupCommandWithSeries:
    """Integration tests for run_cleanup_command with series pipeline."""

    def _minimal_args(self, **overrides):
        """Build minimal valid args Namespace for run_cleanup_command."""
        from argparse import Namespace

        defaults = dict(
            host="http://emby",
            port=None,
            api_key="key",
            library=["SERIALS"],
            all_libraries=False,
            doit=False,
            username="Barlog",
            password=None,
            min_age_years=3,
            protect_paths=["/Dokumenty/"],
            base_rating=6.0,
            decay_step=0.5,
            max_rating=8.0,
            exclude_ids="",
            format="console",
            html_report=False,
            html_only=False,
            no_open=True,
        )
        defaults.update(overrides)
        return Namespace(**defaults)

    @patch("emby_dedupe.cli.cleanup._run_series_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._run_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._probe_library_content", return_value=(0, 100))
    @patch("emby_dedupe.cli.cleanup.get_library_ids_by_name", return_value=["lib_series"])
    @patch("emby_dedupe.cli.cleanup.get_all_library_ids", return_value=[{"id": "lib_series", "name": "SERIALS"}])
    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="uid1")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup.make_http_request")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_series_only_library_runs_series_pipeline(
        self, mock_client_cls, mock_http, mock_check, mock_resolve,
        mock_all_libs, mock_lib_ids, mock_probe, mock_movie_pipeline, mock_series_pipeline
    ):
        """When library contains only series, series pipeline runs and movie pipeline does not."""
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_http.return_value = _make_response({"Id": "server1"})
        mock_series_pipeline.return_value = ([], {
            "total_analyzed": 0, "stale_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "favorite_protected": 0, "path_protected": 0,
            "rating_protected": 0, "final_candidates": 0,
        })

        args = self._minimal_args()
        run_cleanup_command(args)

        mock_movie_pipeline.assert_not_called()
        mock_series_pipeline.assert_called_once()

    @patch("emby_dedupe.cli.cleanup._run_series_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._run_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._probe_library_content", return_value=(500, 200))
    @patch("emby_dedupe.cli.cleanup.get_library_ids_by_name", return_value=["lib_mixed"])
    @patch("emby_dedupe.cli.cleanup.get_all_library_ids", return_value=[{"id": "lib_mixed", "name": "Mixed"}])
    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="uid1")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup.make_http_request")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_mixed_library_runs_both_pipelines(
        self, mock_client_cls, mock_http, mock_check, mock_resolve,
        mock_all_libs, mock_lib_ids, mock_probe, mock_movie_pipeline, mock_series_pipeline
    ):
        """Mixed library (movies + series) runs both pipelines."""
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_http.return_value = _make_response({"Id": "server1"})
        mock_movie_pipeline.return_value = ([], {
            "total_analyzed": 0, "age_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "interest_protected": 0, "actor_protected": 0,
            "franchise_protected": 0, "path_protected": 0, "rating_protected": 0,
            "final_candidates": 0,
        })
        mock_series_pipeline.return_value = ([], {
            "total_analyzed": 0, "stale_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "favorite_protected": 0, "path_protected": 0,
            "rating_protected": 0, "final_candidates": 0,
        })

        args = self._minimal_args(library=["Mixed"])
        run_cleanup_command(args)

        mock_movie_pipeline.assert_called_once()
        mock_series_pipeline.assert_called_once()

    @patch("emby_dedupe.cli.cleanup._run_series_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._run_cleanup_pipeline")
    @patch("emby_dedupe.cli.cleanup._probe_library_content", return_value=(500, 0))
    @patch("emby_dedupe.cli.cleanup.get_library_ids_by_name", return_value=["lib_movies"])
    @patch("emby_dedupe.cli.cleanup.get_all_library_ids", return_value=[{"id": "lib_movies", "name": "Movies"}])
    @patch("emby_dedupe.cli.cleanup._resolve_primary_user_id", return_value="uid1")
    @patch("emby_dedupe.cli.cleanup.check_emby_connection")
    @patch("emby_dedupe.cli.cleanup.make_http_request")
    @patch("emby_dedupe.cli.cleanup.httpx.Client")
    def test_movie_only_library_skips_series_pipeline(
        self, mock_client_cls, mock_http, mock_check, mock_resolve,
        mock_all_libs, mock_lib_ids, mock_probe, mock_movie_pipeline, mock_series_pipeline
    ):
        """Movie-only library runs movie pipeline, skips series."""
        from emby_dedupe.cli.cleanup import run_cleanup_command

        mock_http.return_value = _make_response({"Id": "server1"})
        mock_movie_pipeline.return_value = ([], {
            "total_analyzed": 0, "age_filtered": 0, "excluded_filtered": 0,
            "play_protected": 0, "interest_protected": 0, "actor_protected": 0,
            "franchise_protected": 0, "path_protected": 0, "rating_protected": 0,
            "final_candidates": 0,
        })

        args = self._minimal_args(library=["Movies"])
        run_cleanup_command(args)

        mock_movie_pipeline.assert_called_once()
        mock_series_pipeline.assert_not_called()
