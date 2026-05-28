"""Unit tests for emby_dedupe.api.description_cache."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from emby_dedupe.api import description_cache as cache_mod
from emby_dedupe.api.description_cache import (
    DEFAULT_TTL_SECONDS,
    build_collection_key,
    build_episode_key,
    build_movie_key,
    build_tv_key,
    is_fresh,
    make_entry,
    read_entry,
)


class TestKeyBuilders:
    def test_movie_key(self):
        assert build_movie_key("19913") == "movie:19913"

    def test_tv_key(self):
        assert build_tv_key("93491") == "tv:93491"

    def test_collection_key(self):
        assert build_collection_key("10") == "collection:10"

    def test_episode_key(self):
        assert build_episode_key("93491", 1, 1) == "ep:93491:s1e1"

    def test_episode_key_multi_digit(self):
        assert build_episode_key("100", 10, 20) == "ep:100:s10e20"


class TestIsFresh:
    def test_none_entry_is_not_fresh(self):
        assert is_fresh(None) is False

    def test_missing_ts_is_not_fresh(self):
        assert is_fresh({"data": "foo"}) is False

    def test_recent_entry_is_fresh(self):
        e = {"_ts": int(time.time()), "data": "foo"}
        assert is_fresh(e) is True

    def test_old_entry_is_stale(self):
        old = int(time.time()) - DEFAULT_TTL_SECONDS - 1
        assert is_fresh({"_ts": old, "data": "foo"}) is False

    def test_custom_ttl(self):
        recent = int(time.time()) - 60
        # 30 second TTL → 60s-old entry is stale
        assert is_fresh({"_ts": recent, "data": "foo"}, ttl_seconds=30) is False
        # 120 second TTL → 60s-old entry is fresh
        assert is_fresh({"_ts": recent, "data": "foo"}, ttl_seconds=120) is True


class TestMakeAndReadEntry:
    def test_make_entry_wraps_data(self):
        e = make_entry({"foo": "bar"})
        assert e["data"] == {"foo": "bar"}
        assert isinstance(e["_ts"], int)

    def test_make_entry_preserves_none(self):
        """Negative results (TMDB 404) must be cacheable."""
        e = make_entry(None)
        assert e["data"] is None
        assert isinstance(e["_ts"], int)

    def test_read_entry_unwraps(self):
        assert read_entry({"_ts": 1, "data": {"x": "y"}}) == {"x": "y"}

    def test_read_entry_returns_none_for_missing(self):
        assert read_entry(None) is None

    def test_read_entry_roundtrip_for_negative(self):
        assert read_entry(make_entry(None)) is None


class TestLoadSaveCache:
    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cache_mod, "CACHE_PATH", tmp_path / "nonexistent.json")
        assert cache_mod.load_cache() == {}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cache_mod, "CACHE_PATH", tmp_path / "cache.json")
        data = {
            "movie:19913": make_entry({"en-US": {"title": "x", "overview": "y", "tagline": ""}}),
            "ep:93491:s1e1": make_entry(None),
        }
        cache_mod.save_cache(data)
        loaded = cache_mod.load_cache()
        assert loaded == data

    def test_save_atomic_via_tmp(self, tmp_path, monkeypatch):
        target = tmp_path / "cache.json"
        monkeypatch.setattr(cache_mod, "CACHE_PATH", target)
        cache_mod.save_cache({"k": {"_ts": 0, "data": None}})
        assert target.exists()
        # .tmp file is cleaned up via rename
        assert not (tmp_path / "cache.tmp").exists()

    def test_load_corrupt_returns_empty(self, tmp_path, monkeypatch):
        target = tmp_path / "cache.json"
        target.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(cache_mod, "CACHE_PATH", target)
        assert cache_mod.load_cache() == {}

    def test_save_creates_parent_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "cache.json"
        monkeypatch.setattr(cache_mod, "CACHE_PATH", nested)
        cache_mod.save_cache({"k": make_entry({"foo": "bar"})})
        assert nested.exists()

    def test_save_handles_oserror_silently(self, tmp_path, monkeypatch):
        """An OSError on disk write must not propagate — just log a warning."""
        # Force write_text to fail by making the .tmp path point at a directory.
        target = tmp_path / "cache.json"
        # Create a directory at the .tmp location → write_text raises IsADirectoryError
        (tmp_path / "cache.tmp").mkdir()
        monkeypatch.setattr(cache_mod, "CACHE_PATH", target)
        # Must not raise
        cache_mod.save_cache({"k": make_entry({"foo": "bar"})})
