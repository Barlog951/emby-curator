"""Tests for emby_dedupe/cli/descriptions.py.

These tests focus on the small helper functions extracted from ``_run_fill``
during the complexity-reduction refactor.  Each helper is independently
testable with mocks — together they cover the ``_run_fill`` orchestration
paths without needing a live Emby/TMDB.
"""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest

from emby_dedupe.cli import descriptions as desc_cli
from emby_dedupe.cli.descriptions import (
    _apply_or_preview,
    _collect_candidates,
    _fetch_input_items,
    _fetch_localized_for_episode,
    _fetch_localized_for_item,
    _fetch_localized_for_movie_or_series,
    _new_run_stats,
    _parse_lang_chain,
    _pick_updates,
    _preview_change,
    _print_fill_summary,
    _process_item,
    _resolve_cache,
    _resolve_episode_series_tmdb,
    _resolve_tmdb_key,
    _run_fill,
    _truncate,
    _validate_args,
    run_descriptions_command,
)

# ---------------------------------------------------------------------------
# _parse_lang_chain
# ---------------------------------------------------------------------------


class TestParseLangChain:
    def test_none_returns_default(self):
        from emby_dedupe.api.descriptions import LANG_CHAIN_DEFAULT
        assert _parse_lang_chain(None) == LANG_CHAIN_DEFAULT

    def test_empty_string_returns_default(self):
        from emby_dedupe.api.descriptions import LANG_CHAIN_DEFAULT
        assert _parse_lang_chain("") == LANG_CHAIN_DEFAULT

    def test_whitespace_only_returns_default(self):
        from emby_dedupe.api.descriptions import LANG_CHAIN_DEFAULT
        # All entries whitespace-only -> empty tuple -> fallback to default
        assert _parse_lang_chain(" , , ") == LANG_CHAIN_DEFAULT

    def test_comma_separated(self):
        assert _parse_lang_chain("sk-SK,cs-CZ,en-US") == ("sk-SK", "cs-CZ", "en-US")

    def test_strips_whitespace(self):
        assert _parse_lang_chain(" sk-SK , cs-CZ ") == ("sk-SK", "cs-CZ")


# ---------------------------------------------------------------------------
# _new_run_stats
# ---------------------------------------------------------------------------


class TestNewRunStats:
    def test_all_zeros(self):
        s = _new_run_stats()
        assert all(v == 0 for v in s.values())
        assert set(s.keys()) == {
            "found_overview", "found_tagline", "found_title", "found_year",
            "updated", "skipped_no_data", "errors", "cache_hits",
        }


# ---------------------------------------------------------------------------
# _resolve_tmdb_key
# ---------------------------------------------------------------------------


class TestResolveTmdbKey:
    def test_uses_args_value(self):
        args = argparse.Namespace(tmdb_api_key="from-args")
        assert _resolve_tmdb_key(args) == "from-args"

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("DEDUPE_TMDB_API_KEY", "from-env")
        args = argparse.Namespace(tmdb_api_key=None)
        assert _resolve_tmdb_key(args) == "from-env"

    def test_exits_when_missing(self, monkeypatch):
        monkeypatch.delenv("DEDUPE_TMDB_API_KEY", raising=False)
        args = argparse.Namespace(tmdb_api_key=None)
        with pytest.raises(SystemExit):
            _resolve_tmdb_key(args)


# ---------------------------------------------------------------------------
# _resolve_cache
# ---------------------------------------------------------------------------


class TestResolveCache:
    def test_no_cache_returns_none(self):
        args = argparse.Namespace(no_cache=True, cache_ttl_days=None)
        cache, ttl = _resolve_cache(args)
        assert cache is None
        # Default ttl seconds = 30 days
        assert ttl == 30 * 86400

    def test_default_ttl(self, monkeypatch):
        monkeypatch.setattr(desc_cli, "load_cache", lambda: {})
        args = argparse.Namespace(no_cache=False, cache_ttl_days=None)
        cache, ttl = _resolve_cache(args)
        assert cache == {}
        assert ttl == 30 * 86400

    def test_custom_ttl(self, monkeypatch):
        monkeypatch.setattr(desc_cli, "load_cache", lambda: {"k": "v"})
        args = argparse.Namespace(no_cache=False, cache_ttl_days=7)
        cache, ttl = _resolve_cache(args)
        assert cache == {"k": "v"}
        assert ttl == 7 * 86400


# ---------------------------------------------------------------------------
# _validate_args
# ---------------------------------------------------------------------------


class TestValidateArgs:
    def test_missing_host_exits(self):
        with pytest.raises(SystemExit):
            _validate_args(None, "key", ["lib"], False, None)

    def test_missing_api_key_exits(self):
        with pytest.raises(SystemExit):
            _validate_args("http://emby", None, ["lib"], False, None)

    def test_missing_lib_and_no_alternates_exits(self):
        with pytest.raises(SystemExit):
            _validate_args("http://emby", "key", [], False, None)

    def test_all_libraries_satisfies_lib_requirement(self):
        # No SystemExit
        _validate_args("http://emby", "key", [], True, None)

    def test_item_ids_satisfies_lib_requirement(self):
        _validate_args("http://emby", "key", [], False, "1,2,3")

    def test_valid_args(self):
        _validate_args("http://emby", "key", ["Movies"], False, None)


# ---------------------------------------------------------------------------
# _fetch_input_items
# ---------------------------------------------------------------------------


class TestFetchInputItems:
    def test_item_ids_routes_through_fetch_items_by_ids(self, monkeypatch):
        called = {}

        def fake_by_ids(client, base_url, user_id, item_ids, fields=None):
            called["item_ids"] = item_ids
            called["fields"] = fields
            return [{"Id": "1"}]

        monkeypatch.setattr(desc_cli, "fetch_items_by_ids", fake_by_ids)
        out = _fetch_input_items(MagicMock(), "u", "uid", [], "1, 2 ,3")
        assert out == [{"Id": "1"}]
        assert called["item_ids"] == ["1", "2", "3"]
        assert "Overview" in called["fields"]
        assert "SeriesId" in called["fields"]

    def test_no_item_ids_routes_through_fetch_items_with_genres(self, monkeypatch):
        called = {}

        def fake_full(client, base_url, library_ids, item_types):
            called["library_ids"] = library_ids
            called["item_types"] = item_types
            return [{"Id": "x"}]

        monkeypatch.setattr(desc_cli, "fetch_items_with_genres", fake_full)
        out = _fetch_input_items(MagicMock(), "u", "uid", ["lib1"], None)
        assert out == [{"Id": "x"}]
        assert called["library_ids"] == ["lib1"]
        # Episodes must be included for per-episode overview localization
        assert "Episode" in called["item_types"]


# ---------------------------------------------------------------------------
# _resolve_episode_series_tmdb
# ---------------------------------------------------------------------------


class TestResolveEpisodeSeriesTmdb:
    def test_none_series_id(self):
        assert _resolve_episode_series_tmdb(MagicMock(), "u", "uid", None, {}) is None

    def test_cache_hit(self):
        m = {"s1": "100"}
        assert _resolve_episode_series_tmdb(MagicMock(), "u", "uid", "s1", m) == "100"

    def test_cache_miss_fetches_and_stores(self, monkeypatch):
        m: dict = {}

        def fake_full(client, base_url, user_id, item_id):
            return {"ProviderIds": {"Tmdb": "200"}}

        monkeypatch.setattr(desc_cli, "fetch_full_item", fake_full)
        result = _resolve_episode_series_tmdb(
            MagicMock(), "u", "uid", "s99", m,
        )
        assert result == "200"
        assert m["s99"] == "200"

    def test_cache_miss_returns_none_when_no_tmdb(self, monkeypatch):
        m: dict = {}

        def fake_full(client, base_url, user_id, item_id):
            return {"ProviderIds": {"Tvdb": "abc"}}  # no Tmdb

        monkeypatch.setattr(desc_cli, "fetch_full_item", fake_full)
        result = _resolve_episode_series_tmdb(
            MagicMock(), "u", "uid", "s99", m,
        )
        assert result is None
        assert "s99" not in m

    def test_fetch_exception_returns_none(self, monkeypatch):
        def fake_full(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(desc_cli, "fetch_full_item", fake_full)
        result = _resolve_episode_series_tmdb(
            MagicMock(), "u", "uid", "s99", {},
        )
        assert result is None


# ---------------------------------------------------------------------------
# _fetch_localized_for_episode / _movie_or_series / _for_item
# ---------------------------------------------------------------------------


class TestFetchLocalizedForEpisode:
    def test_records_cache_hit_when_pre_existed(self, monkeypatch):
        cache = {"ep:99:s1e1": {"_ts": 0, "data": {"x": 1}}}
        stats = _new_run_stats()
        monkeypatch.setattr(
            desc_cli, "fetch_tmdb_episode_localized",
            lambda *a, **kw: {"x": 1},
        )
        item = {"ParentIndexNumber": 1, "IndexNumber": 1}
        out = _fetch_localized_for_episode(
            item, "99", MagicMock(), MagicMock(), cache, 100, stats,
        )
        assert out == {"x": 1}
        assert stats["cache_hits"] == 1

    def test_no_cache_hit_when_miss(self, monkeypatch):
        cache: dict = {}
        stats = _new_run_stats()
        monkeypatch.setattr(
            desc_cli, "fetch_tmdb_episode_localized",
            lambda *a, **kw: {"x": 2},
        )
        item = {"ParentIndexNumber": 1, "IndexNumber": 1}
        out = _fetch_localized_for_episode(
            item, "99", MagicMock(), MagicMock(), cache, 100, stats,
        )
        assert out == {"x": 2}
        assert stats["cache_hits"] == 0

    def test_cache_none_skips_hit_tracking(self, monkeypatch):
        stats = _new_run_stats()
        monkeypatch.setattr(
            desc_cli, "fetch_tmdb_episode_localized",
            lambda *a, **kw: {"x": 3},
        )
        item = {"ParentIndexNumber": 1, "IndexNumber": 1}
        out = _fetch_localized_for_episode(
            item, "99", MagicMock(), MagicMock(), None, 100, stats,
        )
        assert out == {"x": 3}
        assert stats["cache_hits"] == 0


class TestFetchLocalizedForMovieOrSeries:
    def test_movie_default_media_type(self, monkeypatch):
        captured = {}

        def fake_fetch(client, limiter, tmdb_id, media, cache, cache_ttl):
            captured["media"] = media
            captured["tmdb_id"] = tmdb_id
            return {"out": 1}

        monkeypatch.setattr(desc_cli, "fetch_tmdb_localized", fake_fetch)
        item = {"Type": "Movie", "ProviderIds": {"Tmdb": "42"}}
        out = _fetch_localized_for_movie_or_series(
            item, MagicMock(), MagicMock(), None, 100, _new_run_stats(),
        )
        assert out == {"out": 1}
        assert captured["media"] == "movie"
        assert captured["tmdb_id"] == "42"

    def test_series_maps_to_tv(self, monkeypatch):
        captured = {}

        def fake_fetch(client, limiter, tmdb_id, media, cache, cache_ttl):
            captured["media"] = media
            return None

        monkeypatch.setattr(desc_cli, "fetch_tmdb_localized", fake_fetch)
        item = {"Type": "Series", "ProviderIds": {"Tmdb": "100"}}
        _fetch_localized_for_movie_or_series(
            item, MagicMock(), MagicMock(), None, 100, _new_run_stats(),
        )
        assert captured["media"] == "tv"

    def test_boxset_maps_to_collection(self, monkeypatch):
        captured = {}

        def fake_fetch(client, limiter, tmdb_id, media, cache, cache_ttl):
            captured["media"] = media
            return None

        monkeypatch.setattr(desc_cli, "fetch_tmdb_localized", fake_fetch)
        item = {"Type": "BoxSet", "ProviderIds": {"Tmdb": "5"}}
        _fetch_localized_for_movie_or_series(
            item, MagicMock(), MagicMock(), None, 100, _new_run_stats(),
        )
        assert captured["media"] == "collection"

    def test_cache_hit_tracked(self, monkeypatch):
        cache = {"movie:42": {"_ts": 0, "data": {"x": 1}}}
        stats = _new_run_stats()
        monkeypatch.setattr(
            desc_cli, "fetch_tmdb_localized", lambda *a, **kw: {"x": 1}
        )
        item = {"Type": "Movie", "ProviderIds": {"Tmdb": "42"}}
        _fetch_localized_for_movie_or_series(
            item, MagicMock(), MagicMock(), cache, 100, stats,
        )
        assert stats["cache_hits"] == 1


class TestFetchLocalizedForItem:
    def test_routes_episode_to_episode_helper(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_episode",
            lambda *a, **kw: {"from": "episode"},
        )
        item = {"Type": "Episode", "SeriesId": "s1",
                "ParentIndexNumber": 1, "IndexNumber": 1}
        out = _fetch_localized_for_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {"s1": "100"},
            None, 100, _new_run_stats(),
        )
        assert out == {"from": "episode"}

    def test_episode_without_resolvable_series_returns_none(self, monkeypatch):
        # series_id present but no Tmdb in the map → episode helper not called
        item = {"Type": "Episode", "SeriesId": "no-such",
                "ParentIndexNumber": 1, "IndexNumber": 1}
        # _resolve_episode_series_tmdb fallback fetch returns no Tmdb
        monkeypatch.setattr(
            desc_cli, "fetch_full_item",
            lambda *a, **kw: {"ProviderIds": {}},
        )
        out = _fetch_localized_for_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, _new_run_stats(),
        )
        assert out is None

    def test_routes_movie_to_movie_helper(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_movie_or_series",
            lambda *a, **kw: {"from": "movie"},
        )
        item = {"Type": "Movie", "ProviderIds": {"Tmdb": "42"}}
        out = _fetch_localized_for_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, _new_run_stats(),
        )
        assert out == {"from": "movie"}


# ---------------------------------------------------------------------------
# _pick_updates
# ---------------------------------------------------------------------------


class TestPickUpdates:
    def test_returns_picks_when_all_available(self):
        # English movie title + English overview/tagline.  TMDB has cs-CZ data.
        item = {
            "Name": "El Niño",
            "Overview": "An English overview that is the and of with this",
            "Taglines": ["English tagline of with the"],
            "ProductionYear": 2014,  # has a year -> year pick must be None
        }
        loc = {
            "sk-SK": {"title": "", "overview": "Slovenský", "tagline": "Slovenský tag"},
            "cs-CZ": {"title": "", "overview": "", "tagline": ""},
            "en-US": {"title": "The Kid"},
        }
        chain = ("sk-SK", "cs-CZ")
        ov, tag, title, year = _pick_updates(item, loc, chain, update_title=True)
        assert ov == ("Slovenský", "sk-SK")
        assert tag == ("Slovenský tag", "sk-SK")
        assert title == ("The Kid", "en-US")
        assert year is None  # item already has a ProductionYear

    def test_title_pick_skipped_when_disabled(self):
        item = {"Name": "El Niño", "Overview": "x", "Taglines": ["y"], "ProductionYear": 2014}
        loc = {
            "sk-SK": {"overview": "Slovenský", "tagline": ""},
            "cs-CZ": {"overview": "", "tagline": ""},
            "en-US": {"title": "The Kid"},
        }
        _, _, title, _ = _pick_updates(item, loc, ("sk-SK", "cs-CZ"), update_title=False)
        assert title is None

    def test_all_none_when_nothing_to_translate(self):
        # Empty overview but no Slavic data; title is English (kept); year present.
        item = {"Name": "Fool's Paradise", "Overview": "", "Taglines": [], "ProductionYear": 2023}
        loc = {
            "sk-SK": {"overview": "", "tagline": "", "title": "", "year": ""},
            "cs-CZ": {"overview": "", "tagline": "", "title": "", "year": ""},
        }
        ov, tag, title, year = _pick_updates(item, loc, ("sk-SK", "cs-CZ"), update_title=True)
        # No EN fallback present → no overview/tagline; title is English so None.
        assert ov is None
        assert tag is None
        assert title is None
        assert year is None

    def test_year_picked_when_missing_and_tmdb_has_it(self):
        item = {"Name": "Yearless", "Overview": "", "Taglines": []}  # no ProductionYear
        loc = {"en-US": {"overview": "", "tagline": "", "title": "", "year": "2022"}}
        _, _, _, year = _pick_updates(item, loc, ("sk-SK", "cs-CZ"), update_title=False)
        assert year == 2022

    def test_year_not_picked_when_item_already_has_year(self):
        item = {"Name": "Has Year", "Overview": "", "Taglines": [], "ProductionYear": 1999}
        loc = {"en-US": {"overview": "", "tagline": "", "title": "", "year": "2022"}}
        _, _, _, year = _pick_updates(item, loc, ("sk-SK", "cs-CZ"), update_title=False)
        assert year is None


# ---------------------------------------------------------------------------
# _apply_or_preview
# ---------------------------------------------------------------------------


class TestApplyOrPreview:
    def test_dry_run_does_not_call_update(self, monkeypatch, capsys):
        called = {}

        def fake_update(*a, **kw):
            called["update"] = True
            return True

        monkeypatch.setattr(desc_cli, "update_item_metadata", fake_update)
        stats = _new_run_stats()
        args = argparse.Namespace(doit=False, lock=True)
        item = {"Id": "1", "Name": "X"}
        _apply_or_preview(
            MagicMock(), "u", "uid", item,
            "ov", "sk-SK", None, None, None, None, args, stats,
        )
        assert "update" not in called
        assert stats["updated"] == 0
        # Preview goes to stdout
        out = capsys.readouterr().out
        assert "X" in out

    def test_doit_calls_update_and_counts(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "fetch_full_item",
            lambda *a, **kw: {"Overview": "old", "Name": "X", "LockedFields": []},
        )
        monkeypatch.setattr(
            desc_cli, "update_item_metadata",
            lambda *a, **kw: True,
        )
        stats = _new_run_stats()
        args = argparse.Namespace(doit=True, lock=True)
        item = {"Id": "1", "Name": "X"}
        _apply_or_preview(
            MagicMock(), "u", "uid", item,
            "ov", "sk-SK", None, None, None, None, args, stats,
        )
        assert stats["updated"] == 1

    def test_doit_handles_exception(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("nope")

        monkeypatch.setattr(desc_cli, "fetch_full_item", boom)
        stats = _new_run_stats()
        args = argparse.Namespace(doit=True, lock=True)
        item = {"Id": "1", "Name": "X"}
        _apply_or_preview(
            MagicMock(), "u", "uid", item,
            "ov", "sk-SK", None, None, None, None, args, stats,
        )
        assert stats["errors"] == 1
        assert stats["updated"] == 0

    def test_doit_update_returns_false_does_not_count(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "fetch_full_item",
            lambda *a, **kw: {"Overview": "x"},
        )
        monkeypatch.setattr(
            desc_cli, "update_item_metadata",
            lambda *a, **kw: False,
        )
        stats = _new_run_stats()
        args = argparse.Namespace(doit=True, lock=True)
        item = {"Id": "1", "Name": "X"}
        _apply_or_preview(
            MagicMock(), "u", "uid", item,
            "ov", "sk-SK", None, None, None, None, args, stats,
        )
        assert stats["updated"] == 0
        assert stats["errors"] == 0

    def test_doit_forwards_new_year(self, monkeypatch):
        captured = {}

        def fake_update(*a, **kw):
            captured.update(kw)
            return True

        monkeypatch.setattr(
            desc_cli, "fetch_full_item",
            lambda *a, **kw: {"Name": "X", "LockedFields": []},
        )
        monkeypatch.setattr(desc_cli, "update_item_metadata", fake_update)
        stats = _new_run_stats()
        args = argparse.Namespace(doit=True, lock=True)
        item = {"Id": "1", "Name": "X"}
        _apply_or_preview(
            MagicMock(), "u", "uid", item,
            None, None, None, None, None, 2022, args, stats,
        )
        assert captured.get("new_year") == 2022
        assert stats["updated"] == 1


# ---------------------------------------------------------------------------
# _preview_change (smoke test — pure stdout helper)
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_strips_whitespace(self):
        assert _truncate("  hi  ") == "hi"

    def test_long_text_truncated_with_ellipsis(self):
        out = _truncate("x" * 500)
        assert out == "x" * 240 + "…"
        assert len(out) == 241

    def test_empty_and_none(self):
        assert _truncate("") == ""
        assert _truncate(None) == ""  # type: ignore[arg-type]


class TestPreviewChange:
    def test_only_overview(self, capsys):
        item = {"Name": "Movie", "ProductionYear": 2020, "Overview": "old"}
        _preview_change(item, "new", "sk-SK", None, None, None)
        out = capsys.readouterr().out
        assert "Movie (2020)" in out
        assert "sk-SK" in out
        assert "new" in out

    def test_only_title(self, capsys):
        item = {"Name": "Old Title", "ProductionYear": 2020}
        _preview_change(item, None, None, "New Title", None, None)
        out = capsys.readouterr().out
        assert "New Title" in out

    def test_only_tagline(self, capsys):
        item = {"Name": "Movie", "Taglines": ["old tag"]}
        _preview_change(item, None, None, None, "nový", "sk-SK")
        out = capsys.readouterr().out
        assert "nový" in out
        assert "old tag" in out

    def test_long_overview_truncated(self, capsys):
        item = {"Name": "Movie", "Overview": "x" * 500}
        _preview_change(item, "y" * 500, "sk-SK", None, None, None)
        out = capsys.readouterr().out
        # Truncation marker present
        assert "…" in out

    def test_no_year(self, capsys):
        item = {"Name": "NoYear"}
        _preview_change(item, "x", "sk-SK", None, None, None)
        out = capsys.readouterr().out
        # No year parens
        assert "NoYear" in out

    def test_year_backfill_shown(self, capsys):
        item = {"Name": "Yearless", "Overview": ""}  # no ProductionYear
        _preview_change(item, None, None, None, None, None, new_year=2022)
        out = capsys.readouterr().out
        assert "Yearless" in out
        assert "2022" in out
        assert "(none)" in out  # current year shown as none


# ---------------------------------------------------------------------------
# _print_fill_summary
# ---------------------------------------------------------------------------


class TestPrintFillSummary:
    def test_prints_all_counters(self, capsys):
        stats = _new_run_stats()
        stats["found_overview"] = 5
        stats["updated"] = 3
        _print_fill_summary(stats, doit=True)
        out = capsys.readouterr().out
        assert "5 overview translations" in out
        assert "3 items updated" in out
        # No dry-run hint when doit=True
        assert "Dry-run" not in out

    def test_dry_run_hint_when_findings(self, capsys):
        stats = _new_run_stats()
        stats["found_overview"] = 1
        _print_fill_summary(stats, doit=False)
        out = capsys.readouterr().out
        assert "Dry-run" in out

    def test_no_dry_run_hint_when_no_findings(self, capsys):
        stats = _new_run_stats()
        _print_fill_summary(stats, doit=False)
        out = capsys.readouterr().out
        assert "Dry-run" not in out


# ---------------------------------------------------------------------------
# _collect_candidates
# ---------------------------------------------------------------------------


class TestCollectCandidates:
    def test_limit_caps_results(self, monkeypatch, capsys):
        items = [
            {"Type": "Movie", "Name": str(i),
             "Overview": "The and of with this", "ProviderIds": {"Tmdb": str(i)}}
            for i in range(5)
        ]
        monkeypatch.setattr(desc_cli, "_fetch_input_items", lambda *a, **kw: items)
        args = argparse.Namespace(item_ids=None, limit=2)
        all_items, candidates = _collect_candidates(
            MagicMock(), "u", "uid", ["lib"], args,
        )
        assert len(all_items) == 5
        assert len(candidates) == 2
        out = capsys.readouterr().out
        assert "Capped" in out

    def test_limit_none_keeps_all(self, monkeypatch):
        items = [
            {"Type": "Movie", "Name": "A",
             "Overview": "The and of with this", "ProviderIds": {"Tmdb": "1"}},
        ]
        monkeypatch.setattr(desc_cli, "_fetch_input_items", lambda *a, **kw: items)
        args = argparse.Namespace(item_ids=None, limit=None)
        _, candidates = _collect_candidates(
            MagicMock(), "u", "uid", ["lib"], args,
        )
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# _process_item (end-to-end orchestrator)
# ---------------------------------------------------------------------------


class TestProcessItem:
    def test_fetch_exception_records_error(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("oops")

        monkeypatch.setattr(desc_cli, "_fetch_localized_for_item", boom)
        stats = _new_run_stats()
        item = {"Name": "X", "Type": "Movie", "ProviderIds": {"Tmdb": "1"}}
        args = argparse.Namespace(doit=False, lock=True)
        _process_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, ("sk-SK",), False, args, stats,
        )
        assert stats["errors"] == 1

    def test_localized_none_records_skipped(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_item",
            lambda *a, **kw: None,
        )
        stats = _new_run_stats()
        item = {"Name": "X", "Type": "Movie", "ProviderIds": {"Tmdb": "1"}}
        args = argparse.Namespace(doit=False, lock=True)
        _process_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, ("sk-SK",), False, args, stats,
        )
        assert stats["skipped_no_data"] == 1

    def test_no_picks_records_skipped(self, monkeypatch):
        # localized non-None but yields no picks → skipped_no_data++
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_item",
            lambda *a, **kw: {"sk-SK": {"overview": "", "tagline": "", "title": ""}},
        )
        stats = _new_run_stats()
        # Item has non-empty Overview so no EN fallback is used; nothing picks.
        item = {
            "Name": "X", "Type": "Movie",
            "Overview": "Existing", "Taglines": ["Existing"],
            "ProviderIds": {"Tmdb": "1"},
        }
        args = argparse.Namespace(doit=False, lock=True)
        _process_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, ("sk-SK",), False, args, stats,
        )
        assert stats["skipped_no_data"] == 1

    def test_overview_pick_increments_counter(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_item",
            lambda *a, **kw: {"sk-SK": {"overview": "Slovenský", "tagline": "", "title": ""}},
        )
        stats = _new_run_stats()
        item = {
            "Name": "X", "Type": "Movie",
            "Overview": "Existing", "Taglines": [],
            "ProviderIds": {"Tmdb": "1"},
        }
        args = argparse.Namespace(doit=False, lock=True)
        _process_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, ("sk-SK",), False, args, stats,
        )
        assert stats["found_overview"] == 1
        assert stats["skipped_no_data"] == 0

    def test_tagline_pick_increments_counter(self, monkeypatch):
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_item",
            lambda *a, **kw: {"sk-SK": {"overview": "", "tagline": "Slovenský slogan", "title": ""}},
        )
        stats = _new_run_stats()
        item = {
            "Name": "X", "Type": "Movie",
            "Overview": "Existing", "Taglines": ["Existing tag"],
            "ProviderIds": {"Tmdb": "1"},
        }
        args = argparse.Namespace(doit=False, lock=True)
        _process_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, ("sk-SK",), False, args, stats,
        )
        assert stats["found_tagline"] == 1

    def test_title_pick_increments_counter(self, monkeypatch):
        # Non-EN/CZ/SK title gets replaced with EN
        monkeypatch.setattr(
            desc_cli, "_fetch_localized_for_item",
            lambda *a, **kw: {
                "sk-SK": {"overview": "", "tagline": "", "title": ""},
                "en-US": {"title": "The Kid", "overview": "", "tagline": ""},
            },
        )
        stats = _new_run_stats()
        item = {
            "Name": "El Niño", "Type": "Movie",
            "Overview": "Existing", "Taglines": ["Existing"],
            "ProviderIds": {"Tmdb": "1"},
        }
        args = argparse.Namespace(doit=False, lock=True)
        _process_item(
            item, MagicMock(), "u", "uid",
            MagicMock(), MagicMock(), {},
            None, 100, ("sk-SK",), True, args, stats,
        )
        assert stats["found_title"] == 1


# ---------------------------------------------------------------------------
# _run_fill — orchestrator smoke test
# ---------------------------------------------------------------------------


class TestRunFill:
    def _make_args(self, **overrides):
        args = argparse.Namespace(
            tmdb_api_key="k", overview_langs=None,
            limit=None, item_ids=None,
            update_title=False, no_cache=True, cache_ttl_days=None,
            doit=False, lock=True,
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_no_candidates_short_circuits(self, monkeypatch, capsys):
        monkeypatch.setattr(
            desc_cli, "_fetch_input_items", lambda *a, **kw: [],
        )
        args = self._make_args()
        _run_fill(MagicMock(), "u", "uid", ["lib"], args)
        out = capsys.readouterr().out
        assert "Nothing to do" in out

    def test_dry_run_no_updates(self, monkeypatch, capsys):
        items = [{
            "Id": "1", "Type": "Movie", "Name": "Movie One",
            "Overview": "The and of with this",
            "ProviderIds": {"Tmdb": "100"},
        }]
        monkeypatch.setattr(desc_cli, "_fetch_input_items", lambda *a, **kw: items)
        monkeypatch.setattr(
            desc_cli, "fetch_tmdb_localized",
            lambda *a, **kw: {"sk-SK": {"overview": "Slovenský", "tagline": "", "title": ""}},
        )
        # update_item_metadata must NOT be called in dry-run
        called = {"updates": 0}

        def must_not_call(*a, **kw):
            called["updates"] += 1
            return True

        monkeypatch.setattr(desc_cli, "update_item_metadata", must_not_call)
        args = self._make_args()
        _run_fill(MagicMock(), "u", "uid", ["lib"], args)
        assert called["updates"] == 0
        out = capsys.readouterr().out
        assert "Dry-run" in out

    def test_saves_cache_when_enabled(self, monkeypatch, capsys):
        """When --no-cache is False (default), cache is loaded and saved."""
        items = [{
            "Id": "1", "Type": "Movie", "Name": "Movie One",
            "Overview": "The and of with this",
            "ProviderIds": {"Tmdb": "100"},
        }]
        monkeypatch.setattr(desc_cli, "_fetch_input_items", lambda *a, **kw: items)
        monkeypatch.setattr(
            desc_cli, "fetch_tmdb_localized",
            lambda *a, **kw: {"sk-SK": {"overview": "x", "tagline": "", "title": ""}},
        )
        saved = {"called": False}

        def fake_save(c):
            saved["called"] = True

        monkeypatch.setattr(desc_cli, "load_cache", lambda: {"warm": "entry"})
        monkeypatch.setattr(desc_cli, "save_cache", fake_save)
        args = self._make_args(no_cache=False)
        _run_fill(MagicMock(), "u", "uid", ["lib"], args)
        assert saved["called"] is True
        out = capsys.readouterr().out
        assert "Cache saved" in out


# ---------------------------------------------------------------------------
# run_descriptions_command — additional happy-path
# ---------------------------------------------------------------------------


class TestRunDescriptionsCommandFlow:
    def test_happy_path_routes_through_run_fill(self, monkeypatch):
        """End-to-end: valid args, successful connection, _run_fill invoked."""
        monkeypatch.setattr(
            desc_cli, "handle_host_and_port",
            lambda h, p: (h, p or 8096),
        )
        monkeypatch.setattr(desc_cli, "check_emby_connection", lambda c, u: True)
        monkeypatch.setattr(desc_cli, "get_user_id", lambda c, u: "uid")
        monkeypatch.setattr(
            desc_cli, "_resolve_library_ids",
            lambda c, b, k, libs, all_libs: ["lib1"],
        )
        called = {"ran": False}

        def fake_run(client, base_url, user_id, library_ids, args):
            called["ran"] = True

        monkeypatch.setattr(desc_cli, "_run_fill", fake_run)
        monkeypatch.setattr(desc_cli.httpx, "Client", lambda **kw: MagicMock())

        args = argparse.Namespace(
            host="http://emby", port=8096, api_key="k",
            library=["Movies"], all_libraries=False, item_ids=None,
            verbosity=0,
        )
        run_descriptions_command(args)
        assert called["ran"] is True

    def test_timeout_exception_exits(self, monkeypatch):
        import httpx as real_httpx
        monkeypatch.setattr(
            desc_cli, "handle_host_and_port",
            lambda h, p: (h, p or 8096),
        )

        def boom(c, u):
            raise real_httpx.TimeoutException("slow")

        monkeypatch.setattr(desc_cli, "check_emby_connection", boom)
        monkeypatch.setattr(desc_cli.httpx, "Client", lambda **kw: MagicMock())
        args = argparse.Namespace(
            host="http://emby", port=8096, api_key="k",
            library=["Movies"], all_libraries=False, item_ids=None,
            verbosity=0,
        )
        with pytest.raises(SystemExit):
            run_descriptions_command(args)

    def test_emby_server_connection_error_exits(self, monkeypatch):
        from emby_dedupe.utils.exceptions import EmbyServerConnectionError
        monkeypatch.setattr(
            desc_cli, "handle_host_and_port",
            lambda h, p: (h, p or 8096),
        )

        def boom(c, u):
            raise EmbyServerConnectionError("nope")

        monkeypatch.setattr(desc_cli, "check_emby_connection", boom)
        monkeypatch.setattr(desc_cli.httpx, "Client", lambda **kw: MagicMock())
        args = argparse.Namespace(
            host="http://emby", port=8096, api_key="k",
            library=["Movies"], all_libraries=False, item_ids=None,
            verbosity=0,
        )
        with pytest.raises(SystemExit):
            run_descriptions_command(args)


# ---------------------------------------------------------------------------
# run_descriptions_command — top-level
# ---------------------------------------------------------------------------


class TestRunDescriptionsCommand:
    def test_missing_host_exits(self):
        args = argparse.Namespace(
            host=None, port=None, api_key="k", library=["Movies"],
            all_libraries=False, item_ids=None, verbosity=0,
        )
        with pytest.raises(SystemExit):
            run_descriptions_command(args)

    def test_connection_failure_exits(self, monkeypatch):
        # Args valid, but check_emby_connection returns False
        monkeypatch.setattr(
            desc_cli, "handle_host_and_port",
            lambda h, p: (h, p or 8096),
        )
        monkeypatch.setattr(
            desc_cli, "check_emby_connection", lambda c, u: False,
        )
        # httpx.Client used internally; replace with a MagicMock
        monkeypatch.setattr(desc_cli.httpx, "Client", lambda **kw: MagicMock())

        args = argparse.Namespace(
            host="http://emby", port=8096, api_key="k",
            library=["Movies"], all_libraries=False, item_ids=None,
            verbosity=0,
        )
        with pytest.raises(SystemExit):
            run_descriptions_command(args)
