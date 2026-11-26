"""Tests for fold-safe deletion — the SSH file-only removal of guard-refused duplicates."""
import importlib
import os
import pathlib
import shutil
from unittest import mock

import pytest

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


def test_plan_group_cap_counts_members_held_by_another_rule():
    # Six copies to delete, one held by the guard for its better picture (no fold-safe
    # marker): five plans remain, and the cap must still see a group of six.
    decision = _incident_shaped_decision(6)
    del decision["delete"][0]["fold_safe_candidate"]
    plans = plan_fold_safe_deletes([decision])
    assert len(plans) == 5
    assert all(p["status"] == "needs_review" for p in plans)
    assert all("6 copies to delete" in p["review_reason"] for p in plans)


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
    assert proc.returncode == 0
    assert "OK" in proc.stdout

    assert not d.exists()
    assert not (tmp_path / "S01E07.1080i.HDTV.CZ.en.srt").exists()
    assert not (tmp_path / "S01E07.1080i.HDTV.CZ.nfo").exists()
    assert keeper.exists()          # keeper untouched
    assert bystander.exists()       # unrelated episode's sidecar untouched


_SHELLS = [sh for sh in ("sh", "bash", "dash") if shutil.which(sh)]


def _run_sweep(shell, folder, keeper, delete):
    import subprocess

    script = _build_remote_script(str(folder / keeper), str(folder / delete), doit=True)
    proc = subprocess.run([shell, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
    return sorted(p.name for p in folder.iterdir())


def _touch(folder, *names):
    for name in names:
        (folder / name).write_text(name)


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_the_sidecars_of_a_keeper_whose_name_extends_the_deleted_one(tmp_path, shell):
    _touch(tmp_path, "X - 576p.mkv", "X - 576p.nfo", "X - 576p.cs.srt",
           "X - 576p CZ.mkv", "X - 576p CZ.nfo", "X - 576p CZ.cs.srt")
    left = _run_sweep(shell, tmp_path, "X - 576p CZ.mkv", "X - 576p.mkv")
    assert left == ["X - 576p CZ.cs.srt", "X - 576p CZ.mkv", "X - 576p CZ.nfo"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_a_dot_named_keepers_subtitles(tmp_path, shell):
    # "X.*.srt" would also match "X.CZ.cs.srt", which belongs to "X.CZ.mkv".
    _touch(tmp_path, "X.mkv", "X.en.srt", "X.CZ.mkv", "X.CZ.cs.srt", "X.CZ.nfo")
    left = _run_sweep(shell, tmp_path, "X.CZ.mkv", "X.mkv")
    assert left == ["X.CZ.cs.srt", "X.CZ.mkv", "X.CZ.nfo"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_sidecars_shared_with_a_same_stem_keeper(tmp_path, shell):
    _touch(tmp_path, "X.mkv", "X.MP4", "X.nfo", "X.cs.srt")
    left = _run_sweep(shell, tmp_path, "X.MP4", "X.mkv")
    assert left == ["X.MP4", "X.cs.srt", "X.nfo"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_sidecars_of_a_same_stem_keeper_in_another_container(tmp_path, shell):
    _touch(tmp_path, "X.avi", "X.mkv", "X.nfo", "X.sk.srt")
    left = _run_sweep(shell, tmp_path, "X.mkv", "X.avi")
    assert left == ["X.mkv", "X.nfo", "X.sk.srt"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_failed_rm_is_not_reported_as_removed(tmp_path, shell):
    import os
    import subprocess

    if os.geteuid() == 0:
        pytest.skip("root ignores folder permissions")
    _touch(tmp_path, "X.mkv", "X.nfo", "X CZ.mkv")
    tmp_path.chmod(0o555)
    try:
        script = _build_remote_script(str(tmp_path / "X CZ.mkv"), str(tmp_path / "X.mkv"), doit=True)
        proc = subprocess.run([shell, "-c", script], capture_output=True, text=True)
    finally:
        tmp_path.chmod(0o755)
    assert proc.returncode == 13
    assert "OK" not in proc.stdout
    assert (tmp_path / "X.mkv").exists()
    assert (tmp_path / "X.nfo").exists()
    results = execute_fold_safe_deletes(
        [{"keeper": "k", "delete": "d", "status": "ok"}], "h", doit=True,
        runner=lambda host, script: (proc.returncode, proc.stdout.strip(), proc.stderr.strip()),
    )
    assert results[0]["status"] == "failed"


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_a_subtitle_emby_lists_under_the_kept_copy(tmp_path, shell):
    # Emby lists "X.CZ.cs.srt" under "X.mkv" too; deleting "X.CZ.mkv" must leave it.
    _touch(tmp_path, "X.mkv", "X.CZ.mkv", "X.CZ.cs.srt", "X.CZ.nfo")
    left = _run_sweep(shell, tmp_path, "X.mkv", "X.CZ.mkv")
    assert left == ["X.CZ.cs.srt", "X.CZ.nfo", "X.mkv"]


def _case_sensitive(folder) -> bool:
    probe = folder / "case-probe"
    probe.write_text("")
    try:
        return not (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_a_subtitle_listed_under_a_kept_copy_in_another_case(tmp_path, shell):
    # A server that ignores case lists "T (2001).CZ.cs.srt" under the kept "t (2001).mkv".
    _touch(tmp_path, "t (2001).mkv", "T (2001).CZ.mkv", "T (2001).CZ.cs.srt", "T (2001).CZ.nfo")
    left = _run_sweep(shell, tmp_path, "t (2001).mkv", "T (2001).CZ.mkv")
    assert left == ["T (2001).CZ.cs.srt", "T (2001).CZ.nfo", "t (2001).mkv"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_keeps_sidecars_of_a_kept_copy_whose_name_differs_only_in_case(tmp_path, shell):
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system")
    _touch(tmp_path, "T (2001).mkv", "t (2001).mkv", "t (2001).nfo", "t (2001).cs.srt")
    left = _run_sweep(shell, tmp_path, "T (2001).mkv", "t (2001).mkv")
    assert left == ["T (2001).mkv", "t (2001).cs.srt", "t (2001).nfo"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_case_match_covers_non_ascii_letters(tmp_path, shell):
    _touch(tmp_path, "Čert (2001).mkv", "čert (2001).CZ.mkv", "čert (2001).CZ.cs.srt")
    left = _run_sweep(shell, tmp_path, "Čert (2001).mkv", "čert (2001).CZ.mkv")
    assert left == ["Čert (2001).mkv", "čert (2001).CZ.cs.srt"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_still_removes_a_non_ascii_deleted_files_own_sidecars(tmp_path, shell):
    _touch(tmp_path, "Čert (2001).mkv", "Čert (2001).nfo", "Čert (2001).sk.srt", "Čert (2001) - 1080p.mkv")
    left = _run_sweep(shell, tmp_path, "Čert (2001) - 1080p.mkv", "Čert (2001).mkv")
    assert left == ["Čert (2001) - 1080p.mkv"]


@pytest.mark.parametrize("shell", _SHELLS)
def test_sweep_still_removes_the_deleted_files_own_sidecars(tmp_path, shell):
    _touch(tmp_path, "Show S01E01.mkv", "Show S01E01.nfo", "Show S01E01.sk.srt", "Show S01E01.idx",
           "Show S01E01 - 1080p.mkv", "Show S01E02.cs.srt")
    left = _run_sweep(shell, tmp_path, "Show S01E01 - 1080p.mkv", "Show S01E01.mkv")
    assert left == ["Show S01E01 - 1080p.mkv", "Show S01E02.cs.srt"]


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


def test_ssh_runner_reuses_the_shared_connection():
    """A batch of file removals logs in to the media host once, not once per file."""
    from emby_dedupe.api import fold_safe_delete as fsd

    with mock.patch.object(fsd.subprocess, "run") as run:
        run.return_value = mock.MagicMock(returncode=0, stdout="OK\n", stderr="")
        assert fsd._ssh_runner("me@media", "echo OK") == (0, "OK", "")
    cmd = run.call_args.args[0]
    assert "ControlMaster=auto" in cmd
    assert "BatchMode=yes" in cmd
    assert cmd[-2:] == ["me@media", "echo OK"]
