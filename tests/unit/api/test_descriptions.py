"""Unit tests for emby_dedupe.api.descriptions — overview/title policy."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from emby_dedupe.api.descriptions import (
    LANG_CHAIN_DEFAULT,
    build_series_tmdb_map,
    collect_overview_candidates,
    detect_title_language,
    fetch_tmdb_episode_localized,
    fetch_tmdb_localized,
    is_english_overview,
    pick_overview_from_localized,
    pick_overview_with_fallback,
    pick_tagline_from_localized,
    pick_tagline_with_fallback,
    pick_title_from_localized,
    update_item_metadata,
)


class TestIsEnglishOverview:
    def test_empty(self):
        assert is_english_overview("") is False
        assert is_english_overview("   ") is False

    def test_english(self):
        assert is_english_overview(
            "When their dad unexpectedly dies, two estranged sisters are brought together."
        )

    def test_czech_with_diacritics(self):
        assert (
            is_english_overview("Dvě sestry se dlouhé roky nestýkaly. Po otcově smrti.")
            is False
        )

    def test_slovak_no_diacritics_function_words(self):
        # Slovak text without diacritics still rejected via function-word hits.
        assert is_english_overview("sa do toho je ale ako") is False


class TestDetectTitleLanguage:
    """These tests prove lingua produces the expected discrimination
    on the six real failure cases that drove the redesign."""

    def test_english(self):
        assert detect_title_language("Anna Nicole Smith: You Don't Know Me") == "en"
        assert detect_title_language("Girl You Know It's True") == "en"
        assert detect_title_language("Fool's Paradise") == "en"

    def test_czech(self):
        assert detect_title_language("Mám se dobře, neboj") == "cs"
        assert detect_title_language("Přišel čas na lásku") == "cs"

    def test_slovak(self):
        assert detect_title_language("Ahojte priatelia") == "sk"
        assert detect_title_language("Agent z Hongkongu") == "sk"

    def test_other_languages(self):
        assert detect_title_language("El Niño") == "es"
        assert detect_title_language("Mañana es hoy") == "es"
        assert detect_title_language("Köln 75") == "de"
        assert detect_title_language("Папа, сдохни") == "ru"
        assert detect_title_language("악마를 보았다") == "ko"

    def test_empty(self):
        assert detect_title_language("") is None
        assert detect_title_language("   ") is None


class TestPickTitleFromLocalized:
    """The title policy itself."""

    def test_keep_english(self):
        assert (
            pick_title_from_localized(
                "Fool's Paradise", {"en-US": {"title": "Fool's Paradise"}}
            )
            is None
        )

    def test_keep_czech(self):
        # The bug case: TMDB cs-CZ leaks French original, but the Czech
        # title detected on the input alone is enough to keep it.
        assert (
            pick_title_from_localized(
                "Mám se dobře, neboj",
                {"en-US": {"title": "Don't Worry, I'm Fine"}},
            )
            is None
        )

    def test_keep_slovak(self):
        assert (
            pick_title_from_localized(
                "Ahojte priatelia", {"en-US": {"title": "Hello Friends"}}
            )
            is None
        )

    def test_replace_spanish_with_english(self):
        result = pick_title_from_localized(
            "El Niño", {"en-US": {"title": "The Kid"}}
        )
        assert result == ("The Kid", "en-US")

    def test_replace_german_with_english(self):
        result = pick_title_from_localized(
            "Köln 75", {"en-US": {"title": "Cologne 75"}}
        )
        assert result == ("Cologne 75", "en-US")

    def test_noop_when_en_equals_current(self):
        # Detected as Spanish, but TMDB EN equals the current title
        # (proper-noun case) — nothing to do.
        assert (
            pick_title_from_localized("El Niño", {"en-US": {"title": "El Niño"}})
            is None
        )

    def test_noop_typography_difference_only_uses_casefold(self):
        # casefold treats curly+straight apostrophes as different — the
        # language detector should keep English titles anyway via lang
        # detection, so the policy returns None.
        assert (
            pick_title_from_localized(
                "Fool's Paradise",
                {"en-US": {"title": "Fool's Paradise"}},
            )
            is None
        )

    def test_no_replacement_when_en_missing(self):
        # Detected non-cs/sk/en but TMDB has no en-US title — keep current.
        assert (
            pick_title_from_localized("El Niño", {"en-US": {"title": ""}}) is None
        )

    def test_empty_input(self):
        assert pick_title_from_localized("", {"en-US": {"title": "Foo"}}) is None


class TestPickOverviewFromLocalized:
    def test_picks_first_non_empty_in_chain(self):
        loc = {
            "sk-SK": {"title": "", "overview": ""},
            "cs-CZ": {"title": "", "overview": "Český text"},
        }
        assert pick_overview_from_localized(loc, ("sk-SK", "cs-CZ")) == (
            "Český text",
            "cs-CZ",
        )

    def test_returns_none_when_all_empty(self):
        loc = {
            "sk-SK": {"title": "", "overview": ""},
            "cs-CZ": {"title": "", "overview": ""},
        }
        assert pick_overview_from_localized(loc) is None

    def test_uses_default_chain(self):
        # sk-SK,cs-CZ — sk has content, picked first.
        loc = {
            "sk-SK": {"title": "", "overview": "Slovenský text"},
            "cs-CZ": {"title": "", "overview": "Český text"},
        }
        assert pick_overview_from_localized(loc) == ("Slovenský text", "sk-SK")
        # confirm default chain is what's claimed
        assert LANG_CHAIN_DEFAULT == ("sk-SK", "cs-CZ")


class TestPickWithFallback:
    """When the current field is empty, the chain extends to include en-US."""

    def test_overview_empty_falls_back_to_english(self):
        loc = {
            "sk-SK": {"overview": ""},
            "cs-CZ": {"overview": ""},
            "en-US": {"overview": "English description"},
        }
        assert pick_overview_with_fallback(loc, current_overview="") == (
            "English description", "en-US",
        )

    def test_overview_empty_still_prefers_slavic(self):
        loc = {
            "sk-SK": {"overview": "Slovenský text"},
            "cs-CZ": {"overview": "Český text"},
            "en-US": {"overview": "English text"},
        }
        assert pick_overview_with_fallback(loc, current_overview="") == (
            "Slovenský text", "sk-SK",
        )

    def test_overview_nonempty_skips_english_fallback(self):
        """When current is non-empty English, we never replace with TMDB English."""
        loc = {
            "sk-SK": {"overview": ""},
            "cs-CZ": {"overview": ""},
            "en-US": {"overview": "English description"},
        }
        result = pick_overview_with_fallback(loc, current_overview="Some English text")
        assert result is None

    def test_tagline_empty_falls_back_to_english(self):
        loc = {
            "sk-SK": {"tagline": ""},
            "cs-CZ": {"tagline": ""},
            "en-US": {"tagline": "He's wearing trouble."},
        }
        assert pick_tagline_with_fallback(loc, current_tagline="") == (
            "He's wearing trouble.", "en-US",
        )


class TestPickTaglineFromLocalized:
    def test_picks_sk_first(self):
        loc = {
            "sk-SK": {"tagline": "Slovenský slogan"},
            "cs-CZ": {"tagline": "Český slogan"},
        }
        assert pick_tagline_from_localized(loc) == ("Slovenský slogan", "sk-SK")

    def test_falls_back_to_cs(self):
        loc = {
            "sk-SK": {"tagline": ""},
            "cs-CZ": {"tagline": "Český slogan"},
        }
        assert pick_tagline_from_localized(loc) == ("Český slogan", "cs-CZ")

    def test_returns_none_when_all_empty(self):
        loc = {"sk-SK": {"tagline": ""}, "cs-CZ": {"tagline": ""}}
        assert pick_tagline_from_localized(loc) is None


class TestCollectOverviewCandidates:
    def test_skips_items_without_tmdb_id(self):
        items = [
            {"Name": "x", "Overview": "The and of with this", "ProviderIds": {}},
        ]
        assert collect_overview_candidates(items) == []

    def test_picks_english_overview(self):
        items = [
            {
                "Name": "x",
                "Overview": "When their dad unexpectedly dies and the sisters meet",
                "ProviderIds": {"Tmdb": "1"},
            }
        ]
        assert len(collect_overview_candidates(items)) == 1

    def test_picks_empty_overview(self):
        """The 'The Tuxedo' case: empty Overview should be eligible."""
        items = [
            {
                "Name": "The Tuxedo",
                "Overview": None,
                "Taglines": ["He's not looking for trouble..."],
                "ProviderIds": {"Tmdb": "10771"},
            }
        ]
        assert len(collect_overview_candidates(items)) == 1

    def test_picks_item_with_english_tagline_only(self):
        """Item with non-English Overview but English Tagline is still eligible
        for tagline localization."""
        items = [
            {
                "Name": "x",
                "Overview": "Dvě sestry se dlouhé roky nestýkaly",
                "Taglines": ["This is not a love story."],
                "ProviderIds": {"Tmdb": "1"},
            }
        ]
        assert len(collect_overview_candidates(items)) == 1

    def test_skips_when_both_fields_done(self):
        """Czech Overview + Czech Tagline = nothing to improve."""
        items = [
            {
                "Name": "x",
                "Overview": "Dvě sestry se dlouhé roky nestýkaly",
                "Taglines": ["Neotřelá romantická komedie"],
                "ProviderIds": {"Tmdb": "1"},
            }
        ]
        assert collect_overview_candidates(items) == []

    def test_picks_episode_with_english_overview(self):
        items = [
            {
                "Id": "ep1",
                "Type": "Episode",
                "SeriesId": "s1",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "Overview": "Josh Morgerman and his team chase Hurricane Michael.",
                "ProviderIds": {"Tvdb": "abc"},  # episodes have no Tmdb ID, that's expected
            }
        ]
        assert len(collect_overview_candidates(items)) == 1

    def test_skips_episode_without_seriesid(self):
        items = [
            {
                "Id": "ep1", "Type": "Episode",
                "ParentIndexNumber": 1, "IndexNumber": 1,
                "Overview": "english text",
            }
        ]
        assert collect_overview_candidates(items) == []

    def test_skips_episode_without_season_or_episode_number(self):
        items = [
            {"Id": "ep1", "Type": "Episode", "SeriesId": "s1",
             "ParentIndexNumber": 1, "Overview": "x"},  # missing IndexNumber
            {"Id": "ep2", "Type": "Episode", "SeriesId": "s1",
             "IndexNumber": 1, "Overview": "x"},  # missing ParentIndexNumber
        ]
        assert collect_overview_candidates(items) == []

    def test_skips_episode_with_slavic_overview(self):
        items = [
            {
                "Type": "Episode", "SeriesId": "s1",
                "ParentIndexNumber": 1, "IndexNumber": 1,
                "Overview": "Dvě sestry se dlouhé roky nestýkaly. Po otcově smrti.",
            }
        ]
        assert collect_overview_candidates(items) == []


class TestFetchWithCache:
    """fetch_tmdb_localized / fetch_tmdb_episode_localized must honor the cache."""

    def _make_limiter(self):
        limiter = MagicMock()
        limiter.acquire = MagicMock()
        return limiter

    def _make_client(self, response_json, status_code=200):
        client = MagicMock()
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = response_json
        response.raise_for_status = MagicMock()
        client.get.return_value = response
        return client

    def test_movie_cache_hit_skips_network(self):
        from emby_dedupe.api.description_cache import make_entry
        cache = {"movie:19913": make_entry({"en-US": {"title": "(500) Days of Summer",
                                                    "overview": "x", "tagline": "y"}})}
        client = self._make_client({})
        limiter = self._make_limiter()
        result = fetch_tmdb_localized(client, limiter, "19913", "movie", cache=cache)
        assert result == {"en-US": {"title": "(500) Days of Summer", "overview": "x", "tagline": "y"}}
        client.get.assert_not_called()
        limiter.acquire.assert_not_called()

    def test_movie_cache_miss_fetches_and_stores(self):
        cache = {}
        client = self._make_client({"title": "X", "overview": "ov", "tagline": ""})
        limiter = self._make_limiter()
        result = fetch_tmdb_localized(
            client, limiter, "999", "movie",
            langs=("en-US",), cache=cache,
        )
        assert "movie:999" in cache
        assert cache["movie:999"]["data"] == result
        client.get.assert_called()

    def test_negative_result_is_cached_so_404_not_re_queried(self):
        cache = {}
        client = self._make_client({}, status_code=404)
        limiter = self._make_limiter()
        result = fetch_tmdb_localized(
            client, limiter, "404", "movie",
            langs=("en-US",), cache=cache,
        )
        assert result is None
        assert cache["movie:404"]["data"] is None
        # Second call: cache hit, no network
        client.get.reset_mock()
        result2 = fetch_tmdb_localized(
            client, limiter, "404", "movie",
            langs=("en-US",), cache=cache,
        )
        assert result2 is None
        client.get.assert_not_called()

    def test_episode_cache_hit_skips_network(self):
        from emby_dedupe.api.description_cache import make_entry
        payload = {"en-US": {"title": "Pilot", "overview": "ep ov", "tagline": ""}}
        cache = {"ep:93491:s1e1": make_entry(payload)}
        client = self._make_client({})
        limiter = self._make_limiter()
        result = fetch_tmdb_episode_localized(client, limiter, "93491", 1, 1, cache=cache)
        assert result == payload
        client.get.assert_not_called()

    def test_episode_cache_miss_stores(self):
        cache = {}
        client = self._make_client({"name": "Pilot", "overview": "ov"})
        limiter = self._make_limiter()
        result = fetch_tmdb_episode_localized(
            client, limiter, "93491", 1, 1,
            langs=("en-US",), cache=cache,
        )
        assert "ep:93491:s1e1" in cache
        assert cache["ep:93491:s1e1"]["data"] == result

    def test_collection_uses_collection_key(self):
        cache = {}
        client = self._make_client({"name": "Star Wars Collection", "overview": "x"})
        limiter = self._make_limiter()
        fetch_tmdb_localized(
            client, limiter, "10", "collection",
            langs=("en-US",), cache=cache,
        )
        assert "collection:10" in cache


class TestBuildSeriesTmdbMap:
    def test_maps_series_only(self):
        items = [
            {"Id": "s1", "Type": "Series", "ProviderIds": {"Tmdb": "100"}},
            {"Id": "s2", "Type": "Series", "ProviderIds": {"Tmdb": "200"}},
            {"Id": "m1", "Type": "Movie", "ProviderIds": {"Tmdb": "300"}},
            {"Id": "e1", "Type": "Episode", "SeriesId": "s1"},
        ]
        assert build_series_tmdb_map(items) == {"s1": "100", "s2": "200"}

    def test_skips_series_without_tmdb(self):
        items = [
            {"Id": "s1", "Type": "Series", "ProviderIds": {"Tvdb": "abc"}},
            {"Id": "s2", "Type": "Series"},  # no ProviderIds at all
        ]
        assert build_series_tmdb_map(items) == {}


class TestExistingCollectBehaviorStillWorks:
    def test_does_not_filter_by_lockedfields(self):
        """Emby's batch endpoints don't return LockedFields, so we can't filter
        on it here. Lock enforcement is now in update_item_metadata."""
        items = [
            {
                "Name": "x",
                "Overview": "When their dad and the sisters meet of with this",
                "Taglines": ["This is not a love story."],
                "ProviderIds": {"Tmdb": "1"},
                "LockedFields": ["Overview", "Tagline"],
            }
        ]
        # Still included — actual lock enforcement is in update_item_metadata
        assert len(collect_overview_candidates(items)) == 1


class TestUpdateItemMetadata:
    def test_noop_when_nothing_changes(self):
        client = MagicMock()
        full = {"Overview": "x", "Name": "y", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_overview="x", new_title="y"
        ) is False
        client.post.assert_not_called()

    def test_overview_only_change(self):
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "old", "Name": "kept", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_overview="new", new_title=None, lock=True
        ) is True
        sent = client.post.call_args.kwargs["json"]
        assert sent["Overview"] == "new"
        assert sent["Name"] == "kept"
        assert "Overview" in sent["LockedFields"]
        assert "Name" not in sent["LockedFields"]

    def test_title_only_change(self):
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "kept", "Name": "old", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_overview=None, new_title="new", lock=True
        ) is True
        sent = client.post.call_args.kwargs["json"]
        assert sent["Name"] == "new"
        assert sent["Overview"] == "kept"
        assert "Name" in sent["LockedFields"]
        assert "Overview" not in sent["LockedFields"]

    def test_both_change(self):
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "old-ov", "Name": "old-name", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_overview="new-ov", new_title="new-name"
        ) is True
        sent = client.post.call_args.kwargs["json"]
        assert sent["Overview"] == "new-ov"
        assert sent["Name"] == "new-name"
        assert set(sent["LockedFields"]) == {"Overview", "Name"}

    def test_tagline_only_change_uses_singular_lock_enum(self):
        """Emby's data field is 'Taglines' (plural list) but LockedFields uses 'Tagline'."""
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "kept", "Name": "kept", "Taglines": ["old tag"], "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_tagline="nový tagline", lock=True
        ) is True
        sent = client.post.call_args.kwargs["json"]
        assert sent["Taglines"] == ["nový tagline"]
        assert "Tagline" in sent["LockedFields"]
        assert "Taglines" not in sent["LockedFields"]

    def test_tagline_noop_when_unchanged(self):
        client = MagicMock()
        full = {"Taglines": ["same"], "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_tagline="same"
        ) is False
        client.post.assert_not_called()

    def test_tagline_handles_missing_taglines_field(self):
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "kept", "Name": "kept", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_tagline="nový"
        ) is True
        sent = client.post.call_args.kwargs["json"]
        assert sent["Taglines"] == ["nový"]

    def test_all_three_fields_together(self):
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "o", "Name": "n", "Taglines": ["t"], "LockedFields": []}
        update_item_metadata(
            client, "u", "1", full,
            new_overview="OV", new_title="NM", new_tagline="TG",
        )
        sent = client.post.call_args.kwargs["json"]
        assert sent["Overview"] == "OV"
        assert sent["Name"] == "NM"
        assert sent["Taglines"] == ["TG"]
        assert set(sent["LockedFields"]) == {"Overview", "Name", "Tagline"}

    def test_respects_locked_overview(self):
        """update_item_metadata must skip Overview when it's already locked."""
        client = MagicMock()
        full = {
            "Overview": "old EN text",
            "Name": "n",
            "Taglines": ["t"],
            "LockedFields": ["Overview"],
        }
        assert update_item_metadata(
            client, "u", "1", full, new_overview="new SK text"
        ) is False
        client.post.assert_not_called()

    def test_respects_locked_tagline(self):
        client = MagicMock()
        full = {
            "Overview": "o", "Name": "n",
            "Taglines": ["old EN tagline"],
            "LockedFields": ["Tagline"],
        }
        assert update_item_metadata(
            client, "u", "1", full, new_tagline="new SK tagline"
        ) is False
        client.post.assert_not_called()

    def test_respects_locked_name(self):
        client = MagicMock()
        full = {"Overview": "o", "Name": "old name", "LockedFields": ["Name"]}
        assert update_item_metadata(
            client, "u", "1", full, new_title="new name"
        ) is False
        client.post.assert_not_called()

    def test_partial_lock_still_updates_other_fields(self):
        """When Overview is locked but Tagline isn't, Tagline still updates."""
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {
            "Overview": "old",
            "Name": "n",
            "Taglines": ["old EN"],
            "LockedFields": ["Overview"],
        }
        assert update_item_metadata(
            client, "u", "1", full,
            new_overview="new ov", new_tagline="new tag",
        ) is True
        sent = client.post.call_args.kwargs["json"]
        # Overview kept (locked); Tagline updated
        assert sent["Overview"] == "old"
        assert sent["Taglines"] == ["new tag"]
        assert "Tagline" in sent["LockedFields"]

    def test_original_title_never_touched(self):
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {
            "Overview": "old",
            "Name": "old",
            "OriginalTitle": "original",
            "LockedFields": [],
        }
        update_item_metadata(
            client, "u", "1", full, new_overview="new", new_title="new"
        )
        sent = client.post.call_args.kwargs["json"]
        assert sent["OriginalTitle"] == "original"

    def test_http_non_success_returns_false(self):
        client = MagicMock()
        client.post.return_value.is_success = False
        client.post.return_value.status_code = 400
        full = {"Overview": "old", "Name": "x", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_overview="new",
        ) is False

    def test_request_error_returns_false(self):
        import httpx
        client = MagicMock()
        client.post.side_effect = httpx.RequestError("conn refused")
        full = {"Overview": "old", "Name": "x", "LockedFields": []}
        assert update_item_metadata(
            client, "u", "1", full, new_overview="new",
        ) is False


class TestUpdateItemOverviewWrapper:
    """Backwards-compatible thin wrapper around update_item_metadata."""

    def test_delegates_to_update_item_metadata(self):
        from emby_dedupe.api.descriptions import update_item_overview
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "old", "Name": "kept", "LockedFields": []}
        assert update_item_overview(client, "u", "1", full, "new") is True
        sent = client.post.call_args.kwargs["json"]
        assert sent["Overview"] == "new"
        assert sent["Name"] == "kept"  # Name untouched

    def test_lock_false_does_not_add_lockedfields(self):
        from emby_dedupe.api.descriptions import update_item_overview
        client = MagicMock()
        client.post.return_value.is_success = True
        full = {"Overview": "old", "Name": "kept", "LockedFields": []}
        update_item_overview(client, "u", "1", full, "new", lock=False)
        sent = client.post.call_args.kwargs["json"]
        assert "Overview" not in sent["LockedFields"]


class TestFetchTmdbOneLangErrors:
    """The exception path in _fetch_tmdb_one_lang (logged warning, returns None)."""

    def test_exception_returns_none_silently(self):
        import httpx
        from emby_dedupe.api.descriptions import fetch_tmdb_localized
        # Build a client whose .get raises a RequestError every call.
        client = MagicMock()
        client.get.side_effect = httpx.RequestError("conn refused")
        limiter = MagicMock()
        result = fetch_tmdb_localized(
            client, limiter, "1", "movie", langs=("en-US",),
        )
        # All langs errored → empty dict (not None — only 404 returns None)
        assert result == {}

    def test_episode_exception_returns_empty(self):
        import httpx
        from emby_dedupe.api.descriptions import fetch_tmdb_episode_localized
        client = MagicMock()
        client.get.side_effect = httpx.RequestError("oops")
        limiter = MagicMock()
        result = fetch_tmdb_episode_localized(
            client, limiter, "1", 1, 1, langs=("en-US",),
        )
        assert result == {}

    def test_episode_404_returns_none(self):
        from emby_dedupe.api.descriptions import fetch_tmdb_episode_localized
        client = MagicMock()
        response = MagicMock()
        response.status_code = 404
        client.get.return_value = response
        limiter = MagicMock()
        result = fetch_tmdb_episode_localized(
            client, limiter, "1", 1, 1, langs=("en-US",),
        )
        assert result is None


class TestFetchTmdbOverviewWrapper:
    """Thin overview-only wrapper around fetch_tmdb_localized."""

    def test_returns_overview_when_found(self):
        from emby_dedupe.api.descriptions import fetch_tmdb_overview
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"title": "X", "overview": "Slovenský", "tagline": ""}
        client.get.return_value = response
        limiter = MagicMock()
        result = fetch_tmdb_overview(
            client, limiter, "1", "movie", lang_chain=("sk-SK",),
        )
        assert result == ("Slovenský", "sk-SK")

    def test_returns_none_on_404(self):
        from emby_dedupe.api.descriptions import fetch_tmdb_overview
        client = MagicMock()
        response = MagicMock()
        response.status_code = 404
        client.get.return_value = response
        limiter = MagicMock()
        result = fetch_tmdb_overview(
            client, limiter, "1", "movie", lang_chain=("sk-SK",),
        )
        assert result is None
