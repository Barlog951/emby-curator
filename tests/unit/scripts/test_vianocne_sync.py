"""Christmas genre sync (scripts/host/vianocne_sync.py): add cap, state recovery, guarded removal."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from emby_dedupe.utils.exceptions import EmbyServerConnectionError

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "host" / "vianocne_sync.py"


def _load():
    spec = importlib.util.spec_from_file_location("vianocne_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeUsers:
    def raise_for_status(self):
        return self

    def json(self):
        return [{"Id": "u", "Policy": {"IsAdministrator": True}}]


class FakeClient:
    def __init__(self, *_a, **_k):
        pass

    def get(self, _url):
        return FakeUsers()


@pytest.fixture
def sync(tmp_path, monkeypatch):
    module = _load()
    monkeypatch.setattr(module, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("DEDUPE_EMBY_HOST", "http://emby")
    monkeypatch.setenv("DEDUPE_EMBY_API_KEY", "k")
    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    module.items = {}
    module.writes = []
    monkeypatch.setattr(module, "fetch", lambda _c, _b, _u, iid: module.items.get(iid))

    def update(_client, _base, iid, _item, genres, lock):
        module.writes.append((iid, genres))
        module.items[iid] = {**module.items[iid], "Genres": genres}
        return True
    monkeypatch.setattr(module, "update_item_genres", update)
    return module


def _film(iid, genres=()):
    return {"Id": iid, "Type": "Movie", "Name": f"Film {iid}", "Genres": list(genres)}


def _run(module, monkeypatch, listed, *flags):
    built = datetime.date.today().isoformat()  # a fresh list, so removals are not held as stale
    monkeypatch.setattr(module, "load_list", lambda: {"genre_ids": listed, "built_at": built})
    monkeypatch.setattr(sys, "argv", ["vianocne_sync.py", *flags])
    module.main()


def test_normal_growth_is_added(sync, monkeypatch):
    sync.items = {str(i): _film(str(i), [sync.GENRE]) for i in range(1, 101)}
    sync.items["new"] = _film("new", ["Comedy"])
    _run(sync, monkeypatch, [*map(str, range(1, 101)), "new"], "--doit")
    assert sync.writes == [("new", ["Comedy", sync.GENRE])]


def test_a_list_that_suddenly_grows_is_refused(sync, monkeypatch, capsys):
    sync.items = {str(i): _film(str(i), [sync.GENRE]) for i in range(1, 11)}
    sync.items.update({f"x{i}": _film(f"x{i}") for i in range(60)})
    _run(sync, monkeypatch, [*map(str, range(1, 11)), *(f"x{i}" for i in range(60))], "--doit")
    assert sync.writes == []
    assert "more than the cap of 50" in capsys.readouterr().out


def test_force_adds_past_the_cap(sync, monkeypatch):
    sync.items = {f"x{i}": _film(f"x{i}") for i in range(60)}
    _run(sync, monkeypatch, [f"x{i}" for i in range(60)], "--doit", "--force")
    assert len(sync.writes) == 60


def test_corrupt_state_is_moved_aside_and_rebuilt(sync, monkeypatch, tmp_path):
    Path(sync.STATE).write_text("{not json", encoding="utf-8")
    sync.items = {"1": _film("1", [sync.GENRE])}
    _run(sync, monkeypatch, ["1"], "--doit")
    assert list(tmp_path.glob("state.json.corrupt-*"))
    assert json.loads(Path(sync.STATE).read_text(encoding="utf-8"))["ids"] == ["1"]


def test_a_failed_read_while_removing_is_counted_not_fatal(sync, monkeypatch):
    Path(sync.STATE).write_text(json.dumps({"ids": ["1", "gone"]}), encoding="utf-8")
    sync.items = {"1": _film("1", [sync.GENRE])}

    def fetch(_c, _b, _u, iid):
        if iid == "gone":
            raise EmbyServerConnectionError("HTTP 500")
        return sync.items.get(iid)
    monkeypatch.setattr(sync, "fetch", fetch)
    with pytest.raises(SystemExit) as exc:
        _run(sync, monkeypatch, ["1"], "--doit", "--remove")
    assert exc.value.code == 1  # the error is reported through the exit code


def test_add_cap_scales_with_the_holders():
    module = _load()
    assert module.add_cap(10) == 50
    assert module.add_cap(400) == 100
