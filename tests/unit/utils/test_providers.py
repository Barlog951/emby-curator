"""Tests for the shared provider-ID helpers (utils.providers)."""

from emby_dedupe.utils.providers import (
    PROVIDER_PRIORITY,
    iter_provider_ids,
    normalize_provider_ids,
)


def test_provider_priority_order():
    assert PROVIDER_PRIORITY == ("imdb", "tmdb", "tvdb")


class TestNormalizeProviderIds:
    def test_lowercases_keys(self):
        assert normalize_provider_ids({"Imdb": "tt1", "TMDB": "5", "Tvdb": "9"}) == {
            "imdb": "tt1",
            "tmdb": "5",
            "tvdb": "9",
        }

    def test_none_safe(self):
        assert normalize_provider_ids(None) == {}
        assert normalize_provider_ids({}) == {}


class TestIterProviderIds:
    def test_priority_order_skips_missing(self):
        assert list(iter_provider_ids("tt1", None, "9")) == [("imdb", "tt1"), ("tvdb", "9")]

    def test_all_present(self):
        assert list(iter_provider_ids("tt1", "5", "9")) == [
            ("imdb", "tt1"),
            ("tmdb", "5"),
            ("tvdb", "9"),
        ]

    def test_none_yields_nothing(self):
        assert list(iter_provider_ids(None, None, None)) == []

    def test_capitalize_gives_emby_api_form(self):
        # Callers use provider.capitalize() for Emby API params.
        assert [p.capitalize() for p, _ in iter_provider_ids("tt1", "5", "9")] == [
            "Imdb",
            "Tmdb",
            "Tvdb",
        ]
