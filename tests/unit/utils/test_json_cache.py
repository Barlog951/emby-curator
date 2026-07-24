"""Tests for the atomic JSON file-cache primitives (utils.json_cache)."""

from emby_dedupe.utils.json_cache import load_json_cache, save_json_cache


class TestJsonCacheRoundTrip:
    def test_save_then_load(self, tmp_path):
        path = tmp_path / "cache.json"
        save_json_cache(path, {"timestamp": 1.0, "tables": {"imdb": {"tt1": "x"}}})
        assert load_json_cache(path) == {"timestamp": 1.0, "tables": {"imdb": {"tt1": "x"}}}

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_json_cache(tmp_path / "nope.json") == {}

    def test_corrupt_file_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_json_cache(path) == {}


class TestCompactOption:
    def test_default_is_indented(self, tmp_path):
        path = tmp_path / "pretty.json"
        save_json_cache(path, {"a": 1, "b": 2})
        text = path.read_text(encoding="utf-8")
        assert "\n" in text  # indent=2 spans multiple lines

    def test_compact_has_no_indentation(self, tmp_path):
        path = tmp_path / "compact.json"
        save_json_cache(path, {"a": 1, "b": 2}, compact=True)
        text = path.read_text(encoding="utf-8")
        assert "\n" not in text  # indent=None → single line
        # Still a valid round-trip.
        assert load_json_cache(path) == {"a": 1, "b": 2}

    def test_compact_is_smaller_than_indented(self, tmp_path):
        payload = {"tables": {"imdb": {f"tt{i}": f"id{i}" for i in range(200)}}}
        pretty = tmp_path / "pretty.json"
        compact = tmp_path / "compact.json"
        save_json_cache(pretty, payload)
        save_json_cache(compact, payload, compact=True)
        assert compact.stat().st_size < pretty.stat().st_size
        assert load_json_cache(compact) == payload
