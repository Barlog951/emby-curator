"""Tests for fold-safe deletion — the SSH file-only removal of guard-refused duplicates."""
import importlib
import os
import pathlib
from unittest import mock

from emby_dedupe.api.fold_safe_delete import (
    _build_remote_script,
    execute_fold_safe_deletes,
    plan_fold_safe_deletes,
)

KEEP = "/Movies/Dokumenty/Hammerhead (2026)/Hammerhead (2026) - 2160p WEB-DL DoVi CZ.mkv"
DEL = "/Movies/Dokumenty/Hammerhead (2026)/Hammerhead (2026) - 1080p WEB-DL CZ.mkv"

# 96 minutes in Emby ticks (10^7/s) — same content in two encodes has the same runtime.
TICKS_96_MIN = 96 * 60 * 10_000_000


def _decision_with_refused(keeper=KEEP, delete=DEL, marker=True,
                           keeper_ticks=TICKS_96_MIN, item_ticks=TICKS_96_MIN):
    item = {"id": "1", "path": delete, "runtime_ticks": item_ticks}
    if marker:
        item["fold_safe_candidate"] = {"keeper": keeper, "delete": delete, "reason": "co-located"}
    return {
        "title": "Hammerhead",
        "keep": {"path": keeper, "runtime_ticks": keeper_ticks},
        "delete": [item],
    }


# ---- plan_fold_safe_deletes -------------------------------------------------

def test_plan_includes_guard_refused_item():
    plans = plan_fold_safe_deletes([_decision_with_refused()])
    assert len(plans) == 1
    assert plans[0]["keeper"] == KEEP
    assert plans[0]["delete"] == DEL
    assert plans[0]["id"] == "1"
    assert plans[0]["group"] == "Hammerhead"
    assert plans[0]["status"] == "ok"
    assert plans[0]["review_reason"] == ""


def test_plan_ignores_items_without_marker():
    # A normal (non-refused) delete has no fold_safe_candidate marker.
    assert plan_fold_safe_deletes([_decision_with_refused(marker=False)]) == []


def test_plan_skips_when_keeper_equals_delete():
    # Never plan an rm where the keeper resolves to the same path as the delete target.
    assert plan_fold_safe_deletes([_decision_with_refused(keeper=DEL, delete=DEL)]) == []


def test_plan_handles_empty_and_none():
    assert plan_fold_safe_deletes([]) == []
    assert plan_fold_safe_deletes(None) == []


# ---- same-content sanity gate (regression for the 2026-07-09 data loss) ----

def test_plan_marks_unknown_duration_needs_review():
    # rm over SSH is unrecoverable: "cannot verify same content" must mean "don't".
    plans = plan_fold_safe_deletes(
        [_decision_with_refused(keeper_ticks=None, item_ticks=None)]
    )
    assert len(plans) == 1
    assert plans[0]["status"] == "needs_review"
    assert "duration-unknown" in plans[0]["review_reason"]


def test_plan_marks_duration_mismatch_needs_review():
    # A 96-min keeper and a 45-min delete candidate are NOT the same content.
    plans = plan_fold_safe_deletes(
        [_decision_with_refused(item_ticks=45 * 60 * 10_000_000)]
    )
    assert plans[0]["status"] == "needs_review"
    assert "duration-mismatch" in plans[0]["review_reason"]


def test_plan_allows_small_duration_jitter():
    # Sub-15s / sub-2% differences (container remux rounding) still count as same content.
    plans = plan_fold_safe_deletes(
        [_decision_with_refused(item_ticks=TICKS_96_MIN + 8 * 10_000_000)]
    )
    assert plans[0]["status"] == "ok"


def _incident_shaped_decision(n=19):
    """The 2026-07-09 shape: n DISTINCT episodes mis-named as copies of one episode.

    Nastiest possible variant: every runtime matches the keeper's exactly (episodes of
    one show have near-identical lengths), so the duration gate PASSES — only the
    group-size cap can catch this.
    """
    items = []
    for i in range(2, 2 + n):
        path = f"/media/TV/Show/Season 01/Show S01E01 ({i}).mkv"
        items.append({
            "id": str(i),
            "path": path,
            "runtime_ticks": TICKS_96_MIN,
            "fold_safe_candidate": {
                "keeper": "/media/TV/Show/Season 01/Show S01E01.mkv",
                "delete": path,
                "reason": "co-located",
            },
        })
    return {
        "title": "Show S01E01",
        "keep": {"path": "/media/TV/Show/Season 01/Show S01E01.mkv",
                 "runtime_ticks": TICKS_96_MIN},
        "delete": items,
    }


def test_plan_flags_oversized_group_needs_review():
    plans = plan_fold_safe_deletes([_incident_shaped_decision(19)])
    assert len(plans) == 19
    assert all(p["status"] == "needs_review" for p in plans)
    assert all("group-too-large" in p["review_reason"] for p in plans)


def test_plan_group_cap_does_not_flag_normal_groups():
    # A title with a handful of quality versions stays auto-removable.
    plans = plan_fold_safe_deletes([_incident_shaped_decision(3)])
    assert len(plans) == 3
    assert all(p["status"] == "ok" for p in plans)


def test_incident_regression_nothing_is_removed():
    """End-to-end: the 2026-07-09 incident shape must produce ZERO rm calls."""
    plans = plan_fold_safe_deletes([_incident_shaped_decision(19)])
    calls = []

    def spy_runner(host, script):
        calls.append(script)
        return 0, "OK", ""

    results = execute_fold_safe_deletes(plans, doit=True, runner=spy_runner)
    assert calls == []  # the runner (and therefore rm) must never be reached
    assert len(results) == 19
    assert all(r["status"] == "needs_review" for r in results)


def test_execute_never_removes_needs_review_plan():
    plan = {"id": "1", "group": "g", "keeper": KEEP, "delete": DEL,
            "reason": "co-located", "status": "needs_review",
            "review_reason": "duration-unknown (cannot verify same content)"}

    def exploding_runner(host, script):  # pragma: no cover - must not be called
        raise AssertionError("needs_review plan must never reach SSH")

    results = execute_fold_safe_deletes([plan], doit=True, runner=exploding_runner)
    assert results[0]["status"] == "needs_review"
    assert "duration-unknown" in results[0]["detail"]


# ---- ssh host configuration -------------------------------------------------

def test_no_personal_host_baked_into_the_package():
    """The SSH target must never ship a hardcoded default: this package is published to
    PyPI, and the module runs `rm` on whatever host it is pointed at."""
    import re

    import emby_dedupe.api.fold_safe_delete as mod

    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    # no literal user@host / IP defaults anywhere in the module
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", source), "IP literal in published module"
    assert not re.search(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.[A-Za-z]", source), "user@host literal"
    # and with no env var set, there is no default target at all
    with mock.patch.dict(os.environ, {}, clear=True):
        importlib.reload(mod)
        assert mod.DEFAULT_SSH_HOST == ""
    importlib.reload(mod)


# ---- _build_remote_script ---------------------------------------------------

def test_remote_script_quotes_paths_and_removes_on_doit():
    script = _build_remote_script(KEEP, DEL, doit=True)
    assert "rm -- \"$d\"" in script
    assert "readlink -f" in script          # different-file check present
    assert "rm -r" not in script            # never recursive
    assert "'" in script                    # shlex-quoted literal paths
    assert '"$stem".nfo' in script          # .nfo sidecar swept
    assert '"$stem".*.srt' in script        # language-infixed subtitle sidecar swept
    assert '"$stem".sub' in script          # image-sub sidecar swept


def test_remote_script_dry_run_does_not_rm():
    script = _build_remote_script(KEEP, DEL, doit=False)
    assert "WOULD-RM" in script
    assert "rm -- " not in script


def test_remote_script_handles_special_chars_in_path():
    tricky = "/Movies/Serials/--- UKONCENE ---/A & B (2026)/ep $x.mkv"
    script = _build_remote_script(KEEP, tricky, doit=True)
    # the tricky path must be a single shell-safe token (single-quoted), un-expanded
    import shlex
    assert shlex.quote(tricky) in script


def test_remote_script_sweeps_subtitle_sidecars(tmp_path):
    """Running the built script removes the media file AND its same-stem sidecars,
    including language-infixed subtitles — the 30 monedas orphan-.srt regression."""
    import shutil
    import subprocess

    if not shutil.which("bash"):
        import pytest
        pytest.skip("bash unavailable")

    d = tmp_path / "S01E07.1080i.HDTV.CZ.mkv"
    d.write_text("video")
    (tmp_path / "S01E07.1080i.HDTV.CZ.en.srt").write_text("subs")   # language-infixed
    (tmp_path / "S01E07.1080i.HDTV.CZ.nfo").write_text("meta")
    keeper = tmp_path / "S01E07 - 1080p WEB-DL CZ.mkv"
    keeper.write_text("keeper")
    bystander = tmp_path / "S01E08.1080i.HDTV.CZ.en.srt"          # different episode — must survive
    bystander.write_text("other")

    script = _build_remote_script(str(keeper), str(d), doit=True)
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0 and "OK" in proc.stdout

    assert not d.exists()
    assert not (tmp_path / "S01E07.1080i.HDTV.CZ.en.srt").exists()
    assert not (tmp_path / "S01E07.1080i.HDTV.CZ.nfo").exists()
    assert keeper.exists()          # keeper untouched
    assert bystander.exists()       # unrelated episode's sidecar untouched


# ---- execute_fold_safe_deletes ----------------------------------------------

def test_execute_reports_removed_on_ok():
    calls = []

    def fake_runner(host, script):
        calls.append((host, script))
        return (0, "OK", "")

    results = execute_fold_safe_deletes(
        plan_fold_safe_deletes([_decision_with_refused()]),
        ssh_host="me@box", doit=True, runner=fake_runner,
    )
    assert results[0]["status"] == "removed"
    assert calls[0][0] == "me@box"


def test_execute_dry_run_reports_would_remove():
    def fake_runner(host, script):
        return (0, "WOULD-RM /path", "")

    results = execute_fold_safe_deletes(
        plan_fold_safe_deletes([_decision_with_refused()]),
        doit=False, runner=fake_runner,
    )
    assert results[0]["status"] == "would_remove"


def test_execute_maps_verify_failures_to_skipped():
    for rc, out in [(10, "SKIP delete-not-a-regular-file"), (11, "SKIP keeper-missing"),
                    (12, "SKIP same-file")]:
        results = execute_fold_safe_deletes(
            plan_fold_safe_deletes([_decision_with_refused()]),
            doit=True, runner=lambda h, s, _rc=rc, _o=out: (_rc, _o, ""),
        )
        assert results[0]["status"] == "skipped"
        assert results[0]["detail"] == out


def test_execute_maps_unknown_rc_to_failed():
    results = execute_fold_safe_deletes(
        plan_fold_safe_deletes([_decision_with_refused()]),
        doit=True, runner=lambda h, s: (1, "", "boom"),
    )
    assert results[0]["status"] == "failed"
    assert results[0]["detail"] == "boom"


def test_execute_survives_runner_exception():
    def boom(host, script):
        raise OSError("ssh unreachable")

    results = execute_fold_safe_deletes(
        plan_fold_safe_deletes([_decision_with_refused()]),
        doit=True, runner=boom,
    )
    assert results[0]["status"] == "failed"
    assert "ssh unreachable" in results[0]["detail"]
