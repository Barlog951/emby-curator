"""Tests for emby_dedupe/api/genres.py"""

import pytest
from unittest.mock import MagicMock

import httpx

from emby_dedupe.api.genres import (
    build_genre_audit,
    fetch_items_with_genres,
    normalize_genre_name,
    suggest_genre_mappings,
    update_item_genres,
    fetch_full_item,
    get_user_id,
)
from emby_dedupe.utils.constants import GENRE_NORMALIZATION_MAP
from emby_dedupe.utils.exceptions import EmbyServerConnectionError


class TestNormalizeGenreName:
    def test_known_variant_sci_fi(self):
        assert normalize_genre_name("Sci-Fi", GENRE_NORMALIZATION_MAP) == "Science Fiction"

    def test_unknown_unchanged(self):
        assert normalize_genre_name("Custom Genre", GENRE_NORMALIZATION_MAP) == "Custom Genre"

    def test_case_insensitive_lower(self):
        assert normalize_genre_name("sci-fi", GENRE_NORMALIZATION_MAP) == "Science Fiction"

    def test_case_insensitive_upper(self):
        assert normalize_genre_name("SCI-FI", GENRE_NORMALIZATION_MAP) == "Science Fiction"

    def test_dada_to_comedy(self):
        assert normalize_genre_name("dada", GENRE_NORMALIZATION_MAP) == "Comedy"

    def test_dada_upper_to_comedy(self):
        assert normalize_genre_name("DADA", GENRE_NORMALIZATION_MAP) == "Comedy"

    def test_suspense_to_thriller(self):
        assert normalize_genre_name("Suspense", GENRE_NORMALIZATION_MAP) == "Thriller"

    def test_vojnovy_to_war(self):
        assert normalize_genre_name("Vojnový", GENRE_NORMALIZATION_MAP) == "War"

    def test_sf_to_science_fiction(self):
        assert normalize_genre_name("SF", GENRE_NORMALIZATION_MAP) == "Science Fiction"

    def test_known_canonical_unchanged(self):
        # "Drama" is not in the map, stays as-is
        assert normalize_genre_name("Drama", GENRE_NORMALIZATION_MAP) == "Drama"

    def test_scifi_no_hyphen_to_science_fiction(self):
        assert normalize_genre_name("scifi", GENRE_NORMALIZATION_MAP) == "Science Fiction"

    def test_reality_tv_to_reality(self):
        assert normalize_genre_name("Reality-TV", GENRE_NORMALIZATION_MAP) == "Reality"

    def test_dokument_to_documentary(self):
        assert normalize_genre_name("Dokument", GENRE_NORMALIZATION_MAP) == "Documentary"

    def test_empty_string_unchanged(self):
        assert normalize_genre_name("", GENRE_NORMALIZATION_MAP) == ""


class TestBuildGenreAudit:
    def test_genre_counts_correct(self):
        items = [
            {"Id": "1", "Name": "Movie A", "Genres": ["Drama", "Thriller"]},
            {"Id": "2", "Name": "Movie B", "Genres": ["Drama"]},
            {"Id": "3", "Name": "Movie C", "Genres": []},
        ]
        audit = build_genre_audit(items)
        assert audit["genre_counts"]["Drama"] == 2
        assert audit["genre_counts"]["Thriller"] == 1

    def test_total_items(self):
        items = [
            {"Id": "1", "Name": "A", "Genres": ["Drama"]},
            {"Id": "2", "Name": "B", "Genres": []},
        ]
        audit = build_genre_audit(items)
        assert audit["total_items"] == 2

    def test_items_without_genres_count(self):
        items = [
            {"Id": "1", "Name": "A", "Genres": ["Drama"]},
            {"Id": "2", "Name": "B", "Genres": []},
            {"Id": "3", "Name": "C", "Genres": []},
        ]
        audit = build_genre_audit(items)
        assert audit["total_without_genres"] == 2
        assert len(audit["items_without_genres"]) == 2

    def test_identifies_normalization_candidates(self):
        items = [
            {"Id": "1", "Name": "Movie A", "Genres": ["Sci-Fi"]},       # needs normalization
            {"Id": "2", "Name": "Movie B", "Genres": ["SF"]},            # needs normalization
            {"Id": "3", "Name": "Movie C", "Genres": ["Science Fiction"]},  # already canonical
        ]
        audit = build_genre_audit(items)
        assert len(audit["normalization_candidates"]) == 2

    def test_no_candidates_when_all_canonical(self):
        items = [
            {"Id": "1", "Name": "Movie A", "Genres": ["Drama", "Action"]},
            {"Id": "2", "Name": "Movie B", "Genres": ["Comedy"]},
        ]
        audit = build_genre_audit(items)
        assert len(audit["normalization_candidates"]) == 0

    def test_returns_required_keys(self):
        audit = build_genre_audit([])
        required_keys = {
            "genre_counts", "items_without_genres", "normalization_candidates",
            "variant_groups", "total_items", "total_without_genres",
        }
        assert required_keys.issubset(audit.keys())

    def test_empty_items_list(self):
        audit = build_genre_audit([])
        assert audit["total_items"] == 0
        assert audit["total_without_genres"] == 0
        assert audit["genre_counts"] == {}

    def test_variant_groups_tracks_canonical_to_variants(self):
        items = [
            {"Id": "1", "Name": "Movie A", "Genres": ["Sci-Fi"]},
            {"Id": "2", "Name": "Movie B", "Genres": ["SF"]},
        ]
        audit = build_genre_audit(items)
        assert "Science Fiction" in audit["variant_groups"]
        variants = audit["variant_groups"]["Science Fiction"]
        assert "Sci-Fi" in variants
        assert "SF" in variants

    def test_variant_groups_serialisable_as_list(self):
        # variant_groups values must be lists (not sets) for JSON serialisation
        items = [{"Id": "1", "Name": "A", "Genres": ["Sci-Fi"]}]
        audit = build_genre_audit(items)
        for canonical, variants in audit["variant_groups"].items():
            assert isinstance(variants, list), f"{canonical} variants should be a list, not set"

    def test_normalization_candidate_has_expected_fields(self):
        items = [{"Id": "42", "Name": "Test Movie", "Genres": ["dada"]}]
        audit = build_genre_audit(items)
        assert len(audit["normalization_candidates"]) == 1
        candidate = audit["normalization_candidates"][0]
        assert candidate["item_id"] == "42"
        assert candidate["item_name"] == "Test Movie"
        assert candidate["current_genres"] == ["dada"]
        assert candidate["suggested_genres"] == ["Comedy"]

    def test_item_missing_genres_key_treated_as_empty(self):
        items = [{"Id": "1", "Name": "No Key"}]  # No "Genres" key
        audit = build_genre_audit(items)
        assert audit["total_without_genres"] == 1
        assert audit["total_items"] == 1


class TestUpdateItemGenres:
    def _make_mock_client(self, mocker, is_success=True, status_code=204):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.is_success = is_success
        mock_resp.status_code = status_code
        mock_client.post.return_value = mock_resp
        return mock_client

    def _get_posted_payload(self, mock_client):
        call = mock_client.post.call_args
        return call.kwargs.get("json") or call[1].get("json")

    def test_updates_genres_and_genre_items(self, mocker):
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [{"Name": "Drama", "Id": "14956"}], "LockedFields": [],
        }
        result = update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy", "Drama"])
        assert result is True
        mock_client.post.assert_called_once()
        payload = self._get_posted_payload(mock_client)
        assert "Comedy" in payload["Genres"]
        assert "Drama" in payload["Genres"]
        genre_item_names = [gi["Name"] for gi in payload["GenreItems"]]
        assert "Comedy" in genre_item_names
        assert "Drama" in genre_item_names

    def test_genre_items_have_empty_id(self, mocker):
        # Emby resolves IDs server-side; we send Id="" for new genres
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"])
        payload = self._get_posted_payload(mock_client)
        for gi in payload["GenreItems"]:
            assert gi["Id"] == ""

    def test_adds_genres_to_locked_fields_when_lock_true(self, mocker):
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"], lock=True)
        payload = self._get_posted_payload(mock_client)
        assert "Genres" in payload["LockedFields"]

    def test_preserves_existing_locked_fields(self, mocker):
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": ["Overview", "Name"],
        }
        update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"], lock=True)
        payload = self._get_posted_payload(mock_client)
        assert "Overview" in payload["LockedFields"]
        assert "Name" in payload["LockedFields"]
        assert "Genres" in payload["LockedFields"]

    def test_no_lock_when_lock_false(self, mocker):
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"], lock=False)
        payload = self._get_posted_payload(mock_client)
        assert "Genres" not in payload["LockedFields"]

    def test_noop_when_genres_unchanged(self, mocker):
        mock_client = mocker.MagicMock()
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Comedy", "Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        result = update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy", "Drama"])
        assert result is False
        mock_client.post.assert_not_called()

    def test_noop_order_insensitive(self, mocker):
        # ["Drama", "Comedy"] == ["Comedy", "Drama"] semantically — should be no-op
        mock_client = mocker.MagicMock()
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama", "Comedy"],
            "GenreItems": [], "LockedFields": [],
        }
        result = update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy", "Drama"])
        assert result is False
        mock_client.post.assert_not_called()

    def test_does_not_mutate_original_item(self, mocker):
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"])
        # Original must not be mutated — deepcopy is used internally
        assert full_item["Genres"] == ["Drama"]
        assert full_item["LockedFields"] == []

    def test_returns_false_on_http_failure(self, mocker):
        mock_client = self._make_mock_client(mocker, is_success=False, status_code=500)
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        result = update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"])
        assert result is False

    def test_returns_false_on_request_error(self, mocker):
        mock_client = mocker.MagicMock()
        mock_client.post.side_effect = httpx.RequestError("connection refused")
        full_item = {
            "Id": "123", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        result = update_item_genres(mock_client, "http://emby", "123", full_item, ["Comedy"])
        assert result is False

    def test_posts_to_correct_endpoint(self, mocker):
        mock_client = self._make_mock_client(mocker)
        full_item = {
            "Id": "abc-999", "Name": "Test", "Genres": ["Drama"],
            "GenreItems": [], "LockedFields": [],
        }
        update_item_genres(mock_client, "http://emby", "abc-999", full_item, ["Comedy"])
        call_url = mock_client.post.call_args[0][0]
        assert call_url == "http://emby/Items/abc-999"


class TestFetchItemsWithGenres:
    def test_fetches_movies_and_series(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {
            "Items": [
                {"Id": "1", "Name": "Movie", "Type": "Movie", "Genres": []},
                {"Id": "2", "Name": "Show", "Type": "Series", "Genres": ["Drama"]},
            ],
            "TotalRecordCount": 2,
        }
        mock_resp.is_success = True
        mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp

        items = fetch_items_with_genres(mock_client, "http://emby", ["lib1"])

        assert len(items) == 2
        call_url = mock_client.get.call_args[0][0]
        assert "Movie" in call_url
        assert "Series" in call_url

    def test_pagination_exhausts_all_items(self, mocker):
        mock_client = mocker.MagicMock()

        def side_effect(url):
            mock_resp = mocker.MagicMock()
            mock_resp.is_success = True
            mock_resp.raise_for_status.return_value = None
            if "StartIndex=0" in url:
                mock_resp.json.return_value = {
                    "Items": [{"Id": "1", "Genres": []}, {"Id": "2", "Genres": []}],
                    "TotalRecordCount": 3,
                }
            else:
                mock_resp.json.return_value = {
                    "Items": [{"Id": "3", "Genres": []}],
                    "TotalRecordCount": 3,
                }
            return mock_resp

        mock_client.get.side_effect = side_effect

        items = fetch_items_with_genres(mock_client, "http://emby", ["lib1"])
        assert len(items) == 3

    def test_empty_library_ids_fetches_all(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"Items": [{"Id": "1", "Genres": []}], "TotalRecordCount": 1}
        mock_resp.is_success = True
        mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp

        items = fetch_items_with_genres(mock_client, "http://emby", [])
        assert len(items) == 1
        # No ParentId in URL when library_ids is empty
        call_url = mock_client.get.call_args[0][0]
        assert "ParentId" not in call_url

    def test_returns_empty_on_request_error(self, mocker):
        mock_client = mocker.MagicMock()
        mock_client.get.side_effect = httpx.RequestError("connection refused")

        items = fetch_items_with_genres(mock_client, "http://emby", ["lib1"])
        assert items == []

    def test_multiple_libraries_fetched(self, mocker):
        mock_client = mocker.MagicMock()

        def side_effect(url):
            mock_resp = mocker.MagicMock()
            mock_resp.is_success = True
            mock_resp.raise_for_status.return_value = None
            if "lib1" in url:
                mock_resp.json.return_value = {"Items": [{"Id": "1", "Genres": []}], "TotalRecordCount": 1}
            else:
                mock_resp.json.return_value = {"Items": [{"Id": "2", "Genres": []}], "TotalRecordCount": 1}
            return mock_resp

        mock_client.get.side_effect = side_effect

        items = fetch_items_with_genres(mock_client, "http://emby", ["lib1", "lib2"])
        assert len(items) == 2


class TestFetchFullItem:
    def test_returns_item_dict(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"Id": "42", "Name": "Test Movie", "Genres": ["Drama"]}
        mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp

        item = fetch_full_item(mock_client, "http://emby", "user-123", "42")
        assert item["Id"] == "42"
        assert item["Name"] == "Test Movie"

    def test_uses_user_scoped_endpoint(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"Id": "42"}
        mock_resp.raise_for_status.return_value = None
        mock_client.get.return_value = mock_resp

        fetch_full_item(mock_client, "http://emby", "user-abc", "item-xyz")
        call_url = mock_client.get.call_args[0][0]
        assert call_url == "http://emby/Users/user-abc/Items/item-xyz"

    def test_raises_on_http_status_error(self, mocker):
        mock_client = mocker.MagicMock()
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.content = b"Not Found"
        mock_client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=mock_request, response=mock_response
        )

        with pytest.raises(EmbyServerConnectionError):
            fetch_full_item(mock_client, "http://emby", "user-abc", "item-xyz")

    def test_raises_on_request_error(self, mocker):
        mock_client = mocker.MagicMock()
        mock_client.get.side_effect = httpx.RequestError("connection refused")

        with pytest.raises(EmbyServerConnectionError):
            fetch_full_item(mock_client, "http://emby", "user-abc", "item-xyz")


class TestGetUserId:
    def test_returns_first_user_id(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [
            {"Id": "user-abc-123", "Name": "Barlog"},
            {"Id": "other-user", "Name": "Other"},
        ]
        mock_client.get.return_value = mock_resp

        user_id = get_user_id(mock_client, "http://emby")
        assert user_id == "user-abc-123"

    def test_raises_when_no_users(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        mock_client.get.return_value = mock_resp

        with pytest.raises(EmbyServerConnectionError, match="No users found"):
            get_user_id(mock_client, "http://emby")

    def test_raises_on_http_status_error(self, mocker):
        mock_client = mocker.MagicMock()
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.content = b"Unauthorized"
        mock_client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=mock_request, response=mock_response
        )

        with pytest.raises(EmbyServerConnectionError):
            get_user_id(mock_client, "http://emby")

    def test_raises_on_request_error(self, mocker):
        mock_client = mocker.MagicMock()
        mock_client.get.side_effect = httpx.RequestError("connection refused")

        with pytest.raises(EmbyServerConnectionError):
            get_user_id(mock_client, "http://emby")

    def test_calls_users_endpoint(self, mocker):
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [{"Id": "u1", "Name": "Test"}]
        mock_client.get.return_value = mock_resp

        get_user_id(mock_client, "http://emby")
        call_url = mock_client.get.call_args[0][0]
        assert call_url == "http://emby/Users"


class TestSuggestGenreMappings:
    def test_canonical_genre_not_flagged(self):
        assert suggest_genre_mappings({"Drama": 10}) == []

    def test_unknown_genre_flagged(self):
        results = suggest_genre_mappings({"Dobrodružný": 45})
        assert len(results) == 1
        assert results[0]["genre"] == "Dobrodružný"
        assert results[0]["count"] == 45

    def test_suggests_close_match(self):
        # "Horoor" is close to "Horror"
        results = suggest_genre_mappings({"Horoor": 5})
        assert results[0]["suggestions"] == ["Horror"]

    def test_already_in_normalization_map_excluded(self):
        # "suspense" is already in GENRE_NORMALIZATION_MAP → not flagged as unknown
        results = suggest_genre_mappings({"suspense": 10})
        assert results == []

    def test_sorted_by_count_descending(self):
        results = suggest_genre_mappings({"UnknownA": 5, "UnknownB": 50, "UnknownC": 1})
        counts = [r["count"] for r in results]
        assert counts == sorted(counts, reverse=True)

    def test_no_match_returns_empty_suggestions(self):
        results = suggest_genre_mappings({"XyzQwerty": 3})
        assert results[0]["suggestions"] == []
