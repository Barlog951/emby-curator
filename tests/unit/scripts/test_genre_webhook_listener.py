"""The webhook listener runs `genres origin` on new items, after `genres process`."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "genre-webhook-listener.py"


def _load():
    spec = importlib.util.spec_from_file_location("genre_webhook_listener", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_items_get_the_origin_genre_after_genres_process(monkeypatch):
    listener = _load()
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(listener, "_run_subprocess", lambda label, cmd: calls.append((label, cmd)))
    listener._queued_for_genres = {"s1": "Arabela", "m1": "Pelíšky"}
    listener._queued_for_descriptions = {}
    listener._run_pipelines()
    assert [label for label, _ in calls] == ["genres process", "genres origin"]
    origin_cmd = calls[1][1]
    assert origin_cmd[1:] == ["genres", "origin", "--doit", "--item-ids", "s1,m1"]
