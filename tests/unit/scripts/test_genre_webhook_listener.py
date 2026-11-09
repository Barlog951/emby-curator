"""The webhook listener: pipeline order, chunked CLI calls, re-queue on failure, request limits."""
from __future__ import annotations

import http.client
import importlib.util
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "genre-webhook-listener.py"


def _load():
    spec = importlib.util.spec_from_file_location("genre_webhook_listener", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def listener(monkeypatch):
    module = _load()
    armed: list[bool] = []
    monkeypatch.setattr(module, "_arm_timer", lambda: armed.append(True))
    module._armed = armed
    module._queued_for_genres = {}
    module._queued_for_descriptions = {}
    module._attempts.clear()
    return module


def test_new_items_get_the_origin_genre_after_genres_process(listener, monkeypatch):
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(listener, "_run_subprocess", lambda label, cmd: calls.append((label, cmd)) or True)
    listener._queued_for_genres = {"s1": "Arabela", "m1": "Pelíšky"}
    listener._run_pipelines()
    assert [label for label, _ in calls] == ["genres process", "genres origin"]
    origin_cmd = calls[1][1]
    assert origin_cmd[1:] == ["genres", "origin", "--doit", "--item-ids", "s1,m1"]
    assert listener._armed == []


def test_large_batches_are_split_into_chunks(listener, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(listener, "_run_subprocess", lambda label, cmd: calls.append(cmd) or True)
    listener._queued_for_descriptions = {str(i): f"t{i}" for i in range(1, 1201)}
    listener._run_pipelines()
    id_args = [cmd[-1].split(",") for cmd in calls]
    assert [len(ids) for ids in id_args] == [500, 500, 200]
    assert sum(id_args, []) == [str(i) for i in range(1, 1201)]


def test_failed_chunk_is_queued_again_until_the_attempt_limit(listener, monkeypatch):
    monkeypatch.setattr(listener, "_run_subprocess", lambda label, cmd: False)
    listener._queued_for_descriptions = {"e1": "Episode 1"}
    for attempt in range(1, listener.MAX_ATTEMPTS + 1):
        listener._run_pipelines()
        queued = listener._queued_for_descriptions
        if attempt < listener.MAX_ATTEMPTS:
            assert queued == {"e1": "Episode 1"}
        else:
            assert queued == {}
    assert len(listener._armed) == listener.MAX_ATTEMPTS - 1


def test_success_after_a_failure_does_not_requeue(listener, monkeypatch):
    results = iter([False, True])
    monkeypatch.setattr(listener, "_run_subprocess", lambda label, cmd: next(results))
    listener._queued_for_descriptions = {"e1": "Episode 1"}
    listener._run_pipelines()
    listener._run_pipelines()
    assert listener._queued_for_descriptions == {}


def test_timer_never_waits_past_the_oldest_item(monkeypatch):
    module = _load()
    started: list[float] = []

    class FakeTimer:
        def __init__(self, delay, _fn):
            started.append(delay)
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(module.threading, "Timer", FakeTimer)
    clock = iter([1000.0, 1000.0 + module.MAX_WAIT_SECONDS - 60])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    module._arm_timer()
    module._arm_timer()
    assert started[0] == module.DEBOUNCE_SECONDS
    assert started[1] == 60


def test_subprocess_timeout_counts_as_failure(listener, monkeypatch):
    def slow(*_a, **_k):
        raise listener.subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(listener.subprocess, "run", slow)
    assert listener._run_subprocess("x", ["x"]) is False


def test_listens_on_localhost_by_default():
    assert _load().LISTEN_HOST == "127.0.0.1"


def test_oversized_body_is_refused(monkeypatch):
    module = _load()
    queued: list[tuple] = []
    monkeypatch.setattr(module, "_schedule_fix", lambda *a: queued.append(a))
    server = module.http.server.ThreadingHTTPServer(("127.0.0.1", 0), module._Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.putrequest("POST", "/webhook")
        conn.putheader("Content-Length", str(module.MAX_BODY_BYTES + 1))
        conn.putheader("Content-Type", "application/json")
        conn.endheaders()
        assert conn.getresponse().status == 413

        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        body = b'{"Event": "library.new", "Item": {"Id": "9", "Type": "Movie", "Name": "Film"}}'
        conn.request("POST", "/webhook", body=body, headers={"Content-Type": "application/json"})
        assert conn.getresponse().status == 200
        assert queued == [("9", "Film", "9", "Film")]
    finally:
        server.shutdown()
