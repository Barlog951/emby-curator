"""Cleanup reports must show the posters of the items they deleted (fetched before the delete)."""
from __future__ import annotations

from types import SimpleNamespace

from emby_dedupe.cli import cleanup as cli
from emby_dedupe.reports import cleanup as rep
from emby_dedupe.reports import images

API_KEY = "your-api-key"  # gitleaks-allowlisted placeholder shape
DATA = "data:image/jpeg;base64,AAAA"


def test_poster_url_has_no_url_without_a_key():
    assert rep.poster_url("http://emby", "7", "") == ""
    assert rep.poster_url("http://emby", "7", API_KEY).endswith("/Items/7/Images/Primary?maxWidth=200&api_key=your-api-key")


def test_prefetch_keeps_only_real_images(monkeypatch):
    seen: list[list[str]] = []

    def fake_inline(urls, api_key=None, timeout=10.0):
        urls = list(urls)
        seen.append(urls)
        return {urls[0]: DATA, urls[1]: "http://emby/Items/2/Images/Primary?maxWidth=200"}  # 2nd failed

    monkeypatch.setattr(rep, "inline_poster_urls", fake_inline)
    got = rep.prefetch_cleanup_posters("http://emby", [SimpleNamespace(item_id="1"), SimpleNamespace(item_id="2")], API_KEY)
    assert list(got.values()) == [DATA] and len(seen[0]) == 2


def test_inline_uses_prefetched_posters_and_fetches_only_the_rest(monkeypatch):
    fetched: list[str] = []

    def fake_inline(urls, api_key=None, timeout=10.0):
        fetched.extend(urls)
        return {u: "data:image/png;base64,BBBB" for u in urls}

    monkeypatch.setattr(images, "inline_poster_urls", fake_inline)
    gone = rep.poster_url("http://emby", "deleted", API_KEY)
    alive = rep.poster_url("http://emby", "alive", API_KEY)
    context = {"cards": [{"image_url": gone}, {"image_url": alive}]}
    images.inline_images_in_place(context, API_KEY, prefetched={gone: DATA})
    assert context["cards"][0]["image_url"] == DATA          # from before the delete
    assert fetched == [alive]                                 # the deleted one is not re-fetched
    assert "api_key" not in str(context)


def test_execute_cleanup_fetches_posters_before_deleting(monkeypatch):
    """Regression 2026-09-23: the report was rendered after the deletions, so the deleted
    film's poster (V/H/S/99) no longer existed in Emby and showed as broken."""
    calls: list[str] = []
    candidate = SimpleNamespace(item_id="21407884", name="V/H/S/99")
    monkeypatch.setattr(cli, "make_http_request", lambda *a, **k: SimpleNamespace(json=lambda: {"Id": "srv"}))
    monkeypatch.setattr(cli, "_resolve_primary_user_id", lambda *a, **k: "u")
    monkeypatch.setattr(cli, "_resolve_library_ids", lambda *a, **k: (["m"], {"m": "LQ"}))
    monkeypatch.setattr(cli, "_probe_and_split_libraries", lambda *a, **k: (["m"], []))
    monkeypatch.setattr(cli, "_run_cleanup_pipeline", lambda *a, **k: ([candidate], {}, []))
    monkeypatch.setattr(cli, "_output_report", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prefetch_cleanup_posters",
                        lambda base, cands, key: calls.append(f"prefetch:{len(cands)}") or {"u": DATA})
    monkeypatch.setattr(cli, "_perform_deletions", lambda *a, **k: calls.append("delete"))
    rendered: dict = {}
    monkeypatch.setattr(cli, "_generate_cleanup_html_report",
                        lambda *a, **k: rendered.update(k) or calls.append("render") or "<html/>")
    monkeypatch.setattr(cli, "_save_cleanup_html_report", lambda *a, **k: "/tmp/r.html")

    cli._execute_cleanup(None, "http://emby", SimpleNamespace(), API_KEY, ["LQ"], False,
                         "user", "pass", "console", True, True, True, doit=True)
    assert calls == ["prefetch:1", "delete", "render"]
    assert rendered["prefetched_posters"] == {"u": DATA}


def test_dry_run_does_not_prefetch(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "make_http_request", lambda *a, **k: SimpleNamespace(json=lambda: {"Id": "srv"}))
    monkeypatch.setattr(cli, "_resolve_primary_user_id", lambda *a, **k: "u")
    monkeypatch.setattr(cli, "_resolve_library_ids", lambda *a, **k: (["m"], {}))
    monkeypatch.setattr(cli, "_probe_and_split_libraries", lambda *a, **k: (["m"], []))
    monkeypatch.setattr(cli, "_run_cleanup_pipeline", lambda *a, **k: ([SimpleNamespace(item_id="1")], {}, []))
    monkeypatch.setattr(cli, "_output_report", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prefetch_cleanup_posters", lambda *a: calls.append("prefetch") or {})
    monkeypatch.setattr(cli, "_generate_cleanup_html_report", lambda *a, **k: "<html/>")
    monkeypatch.setattr(cli, "_save_cleanup_html_report", lambda *a, **k: "/tmp/r.html")
    cli._execute_cleanup(None, "http://emby", SimpleNamespace(), API_KEY, ["LQ"], False,
                         None, None, "console", True, True, True, doit=False)
    assert calls == []  # nothing is deleted, so the report fetches normally
