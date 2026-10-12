"""Frame check before deletes (2026-10-12).

A Slovak NCIS S19E08 that was really E07 paired with the Czech E08, the lengths agreed
within 2 % and the Czech copy was deleted; and 42 PAL speed-up pairs (25 fps, 4.2 %
shorter) were blocked every run. The frame check compares the pictures of episode pairs
and is the only thing that may confirm a speed-up.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from emby_dedupe.api import deduplication as dd
from emby_dedupe.api.content_check import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    FrameChecker,
    FrameVerdict,
    cache_key,
    cache_path,
    classify,
    coverage_verdict,
    expected_speed,
    frames_from_raw,
    is_speedup_pair,
    point_match,
    stream_base_url,
)

TICKS = 10_000_000


def _ep(item_id, seconds, fps, path="/S/NCIS/S19/x.mkv"):
    return {"id": item_id, "runtime_ticks": int(seconds * TICKS), "frame_rate": fps,
            "is_episode": True, "path": path}


# --- speed-up detection --------------------------------------------------------------------

def test_pal_speedup_pair_is_recognised():
    sk = _ep("sk", 2423, 25.0)  # 25 fps copy runs 4.2 % shorter
    cz = _ep("cz", 2423 * 25 / 23.976, 23.976)
    assert is_speedup_pair(sk, cz) and is_speedup_pair(cz, sk)


def test_extra_recap_is_not_a_speedup():
    # S19E07: 7.6 % longer = speed-up plus an 80 s recap; speed alone does not explain it
    assert not is_speedup_pair(_ep("sk", 2387, 25.0), _ep("cz", 2569, 24.0))


def test_speedup_needs_the_shorter_copy_at_25_fps():
    assert not is_speedup_pair(_ep("a", 2423, 23.976), _ep("b", 2423 * 1.0427, 25.0))
    assert not is_speedup_pair(_ep("a", 2423, 25.0), _ep("b", 2423 * 1.0427, None))


# --- voting and matching ---------------------------------------------------------------------

def test_vote_tolerates_one_cut_scene():
    assert classify([0.9, 0.95, 0.3, 0.88, 0.92]) == "same"


def test_vote_needs_most_points_to_miss_for_different():
    assert classify([0.19, 0.2, 0.25, 0.3, 0.18]) == "different"
    assert classify([0.9, 0.2, 0.25, 0.3, 0.6]) == "unsure"


def _frame(seed):
    return bytes((seed * 37 + i * 11 + (i * i) % 23) % 256 for i in range(FRAME_WIDTH * FRAME_HEIGHT))


def _keyframes(seeds_at):
    """Keyframes from (seed, seconds) pairs."""
    return frames_from_raw(b"".join(_frame(seed) for seed, _ in seeds_at), [at for _, at in seeds_at])


def test_twins_at_one_offset_score_high_and_report_the_offset():
    keeper = _keyframes([(1, 600.0), (2, 603.0), (3, 606.0)])
    window = _keyframes([(9, 590.0), (1, 595.0), (2, 598.1), (3, 601.0)])
    score, offset = point_match(keeper, window, 1.0)
    assert score == 1.0 and abs(offset + 5.0) < 0.2


def test_look_alike_frames_at_scattered_offsets_score_low():
    """Another episode of the same show: each frame finds a look-alike, but at random times."""
    keeper = _keyframes([(1, 600.0), (2, 603.0), (3, 606.0), (4, 609.0)])
    window = _keyframes([(1, 560.0), (2, 640.0), (3, 590.0), (4, 700.0)])
    score, _ = point_match(keeper, window, 1.0)
    assert score == 0.25


def test_no_twin_scores_zero():
    assert point_match(_keyframes([(1, 600.0), (2, 603.0)]), _keyframes([(5, 600.0), (6, 603.0)]), 1.0) == (0.0, None)


def test_flat_frames_are_dropped():
    assert frames_from_raw(bytes([40]) * (FRAME_WIDTH * FRAME_HEIGHT), [0.0]) == []


def test_frames_and_times_that_do_not_line_up_are_rejected():
    """Wrong times would corrupt the offsets, so a truncated read yields nothing."""
    assert frames_from_raw(_frame(1) + _frame(2), [0.0]) == []
    assert frames_from_raw(_frame(1) + b"\x00" * 7, [0.0]) == []


def test_frame_times_are_made_absolute():
    assert [at for at, _ in frames_from_raw(_frame(1), [3.0], start=600.0)] == [603.0]


# --- the checker ---------------------------------------------------------------------------

def test_without_ffmpeg_the_check_is_unavailable():
    checker = FrameChecker("http://emby", "key", ffmpeg_path="")
    assert checker.compare(_ep("a", 2400, 25), _ep("b", 2400, 25)).verdict == "unavailable"


def test_compare_votes_over_points_and_caches():
    checker = FrameChecker("http://emby", "key", ffmpeg_path="ffmpeg")
    points = [(0.9, -3.0), (0.95, -3.5), (0.2, None), (0.9, -4.0), (0.92, -4.5)]
    with patch.object(checker, "_score_point", side_effect=points) as point:
        verdict = checker.compare(_ep("a", 2400, 25), _ep("b", 2500, 24))
        again = checker.compare(_ep("a", 2400, 25), _ep("b", 2500, 24))
    assert verdict.verdict == "same" and again is verdict
    assert point.call_count == 4  # the third matched point wins the vote; the fifth is not read
    assert verdict.offsets == (-3.0, -3.5, -4.0) and verdict.steady


def test_matched_offset_moves_the_next_window_and_a_miss_does_not():
    checker = FrameChecker("http://emby", "key", ffmpeg_path="ffmpeg")
    points = [(0.9, -20.0), (0.1, 40.0), (0.9, -42.0), (0.9, -65.0), (0.9, -85.0)]
    with patch.object(checker, "_score_point", side_effect=points) as point:
        checker.compare(_ep("a", 2400, 25), _ep("b", 2500, 24))
    assert [c.args[4] for c in point.call_args_list] == [None, -20.0, -20.0, -42.0]


def test_a_known_offset_searches_narrow_first_and_falls_back_to_the_full_window():
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    calls = []

    def frames(item, start, seconds, dense=False):
        calls.append((item["id"], round(start), round(seconds), dense))
        return _keyframes([(1, 600.0), (2, 603.0)])

    with patch.object(checker, "_keyframes", side_effect=frames), \
         patch("emby_dedupe.api.content_check.point_match", side_effect=[(0.2, None), (0.9, -5.0)]):
        assert checker._score_point({"id": "k"}, {"id": "o"}, 1000.0, 1.0, -5.0) == (0.9, -5.0)
    assert calls == [("k", 1000, 30, False), ("o", 975, 70, True), ("o", 950, 165, True)]


def test_the_first_point_also_searches_narrow_first():
    """Real copies line up within a few seconds, so even the first point decodes 70 s
    before falling back to the full 165 s window (Fuki: "why that slow?")."""
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    calls = []

    def frames(item, start, seconds, dense=False):
        calls.append((round(start), round(seconds)))
        return _keyframes([(1, 600.0), (2, 603.0)])

    with patch.object(checker, "_keyframes", side_effect=frames), \
         patch("emby_dedupe.api.content_check.point_match", side_effect=[(0.9, -2.0)]):
        assert checker._score_point({"id": "k"}, {"id": "o"}, 1000.0, 1.0, None) == (0.9, -2.0)
    assert calls == [(1000, 30), (980, 70)]


def test_two_foreign_points_stop_early():
    checker = FrameChecker("http://emby", "key", ffmpeg_path="ffmpeg")
    with patch.object(checker, "_score_point", side_effect=[(0.1, None), (0.0, None)]) as point:
        assert checker.compare(_ep("a", 2400, 25), _ep("b", 2400, 25)).verdict == "different"
    assert point.call_count == 2


def test_keyframes_go_through_emby_with_the_token_in_a_header():
    checker = FrameChecker("http://emby:8096", "SECRET", ffmpeg_path="ffmpeg")
    with patch("emby_dedupe.api.content_check.subprocess.run") as run, \
         patch.object(checker, "_media_source_id", return_value="mediasource_123"):
        log = "".join(f"[Parsed_showinfo_2 @ 0x1] n:   {n} pts:  {n} pts_time:{t} duration: 1\n"
                      "[Parsed_showinfo_2 @ 0x1] color_range:tv\n" for n, t in enumerate((0.02, 3.02)))
        run.return_value = MagicMock(stdout=_frame(1) + _frame(2), stderr=log.encode())
        frames = checker._keyframes({"id": "123"}, 600, 30)
    cmd = run.call_args.args[0]
    assert [at for at, _ in frames] == [600.02, 603.02]
    assert "http://emby:8096/Videos/123/stream?static=true&MediaSourceId=mediasource_123" in cmd
    assert all("SECRET" not in part for part in cmd if part.startswith("http"))
    assert "-skip_frame" in cmd and "nokey" in cmd


def test_default_port_is_left_out_of_the_stream_url():
    """2026-10-12 dry run: dedupe passes ``https://host:443``; ffmpeg then sent
    ``Host: host:443``, the reverse proxy answered 5xx and all 124 checks were "unavailable"."""
    assert stream_base_url("https://emby.example.com:443") == "https://emby.example.com"
    assert stream_base_url("http://192.168.1.10:80/") == "http://192.168.1.10"
    assert stream_base_url("http://192.168.1.10:8096") == "http://192.168.1.10:8096"
    checker = FrameChecker("https://emby.example.com:443", "k", ffmpeg_path="ffmpeg")
    with patch("emby_dedupe.api.content_check.subprocess.run") as run, \
         patch.object(checker, "_media_source_id", return_value="ms7"):
        run.return_value = MagicMock(stdout=b"", stderr=b"", returncode=0)
        checker._keyframes({"id": "7"}, 0, 30)
    assert "https://emby.example.com/Videos/7/stream?static=true&MediaSourceId=ms7" in run.call_args.args[0]


def test_an_ffmpeg_error_is_logged_not_swallowed():
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    failed = MagicMock(stdout=b"", stderr=b"Server returned 5XX Server Error reply\n", returncode=8)
    with patch("emby_dedupe.api.content_check.subprocess.run", return_value=failed), \
         patch.object(checker, "_media_source_id", return_value="ms7"), \
         patch("emby_dedupe.api.content_check.logger") as log:
        assert checker._keyframes({"id": "7"}, 0, 30) == []
    assert "5XX" in log.warning.call_args.args[0]


def test_prefetch_warns_when_checks_are_unavailable():
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    with patch.object(checker, "_score_point", return_value=None), \
         patch("emby_dedupe.api.content_check.logger") as log:
        checker.prefetch([(_ep("a", 2400, 25), _ep("b", 2400, 25))])
    assert "1 unavailable" in log.warning.call_args.args[0]


def _items_response(sources):
    return MagicMock(json=MagicMock(return_value={"Items": [{"MediaSources": sources}]}),
                     raise_for_status=MagicMock())


def test_the_stream_names_the_items_own_media_source():
    """2026-10-12: Emby served a version group's default copy for every item id, so a
    720p BluRay and an NCIS SD avi were compared with their 1080p sibling (= themselves)."""
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    sources = [{"Id": "mediasource_2", "ItemId": "2"}, {"Id": "mediasource_1", "ItemId": "1"}]
    with patch("emby_dedupe.api.content_check.httpx.get", return_value=_items_response(sources)) as get:
        assert checker._media_source_id("1") == "mediasource_1"
        assert checker._media_source_id("1") == "mediasource_1"
    assert get.call_count == 1


def test_an_item_without_a_source_of_its_own_is_not_compared():
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    sources = [{"Id": "mediasource_2", "ItemId": "2"}, {"Id": "mediasource_3", "ItemId": "3"}]
    with patch("emby_dedupe.api.content_check.httpx.get", return_value=_items_response(sources)), \
         patch("emby_dedupe.api.content_check.subprocess.run") as run:
        assert checker._keyframes({"id": "1"}, 0, 30) == []
    run.assert_not_called()


def test_on_the_media_host_ffmpeg_reads_the_items_own_file_over_ssh():
    """Fuki 2026-10-12: Emby sits on 25 Gb/s to the NAS, so decode there and ship only
    the 32x18 frames instead of ~0.5 GB of video per pair to the Mac."""
    checker = FrameChecker("https://emby:443", "SECRET", ssh_host="me@media")
    item = {"id": "9", "path": "/Movies/Serials/It's NCIS/S19/NCIS S19E01 - 1080p SK.mkv"}
    with patch("emby_dedupe.api.content_check.subprocess.run") as run, \
         patch("emby_dedupe.api.content_check.httpx.get") as get:
        run.return_value = MagicMock(stdout=b"", stderr=b"", returncode=0)
        checker._keyframes(item, 600, 30)
    cmd = run.call_args.args[0]
    assert cmd[0] == "ssh" and "me@media" in cmd and "BatchMode=yes" in cmd
    remote = cmd[-1]
    assert "'/Movies/Serials/It'\"'\"'s NCIS/S19/NCIS S19E01 - 1080p SK.mkv'" in remote
    assert "SECRET" not in remote and "/Videos/" not in remote
    get.assert_not_called()
    assert checker.available


def test_on_the_media_host_an_item_without_a_path_is_not_compared():
    checker = FrameChecker("http://emby", "k", ssh_host="me@media")
    with patch("emby_dedupe.api.content_check.subprocess.run") as run:
        assert checker._keyframes({"id": "9"}, 0, 30) == []
    run.assert_not_called()


def test_a_failed_source_lookup_is_retried_later():
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    with patch("emby_dedupe.api.content_check.httpx.get", side_effect=httpx.ConnectTimeout("down")):
        assert checker._media_source_id("1") is None
    with patch("emby_dedupe.api.content_check.httpx.get",
               return_value=_items_response([{"Id": "mediasource_1", "ItemId": "1"}])):
        assert checker._media_source_id("1") == "mediasource_1"


def test_prefetch_shows_a_progress_bar_and_caches_every_pair():
    """Fuki 2026-10-12: the frame check ran silently for minutes; it now shows a bar."""
    checker = FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg")
    pairs = [(_ep(f"k{n}", 2400, 25), _ep(f"o{n}", 2400, 25)) for n in range(3)]
    with patch.object(checker, "_compare", return_value=FrameVerdict("same", (0.9,) * 3)), \
         patch("emby_dedupe.api.content_check.tqdm", wraps=__import__("tqdm").tqdm) as bar:
        checker.prefetch(pairs)
    assert bar.call_args.kwargs["total"] == 3 and bar.call_args.kwargs["desc"] == "Frame check"
    with patch.object(checker, "_compare") as again:
        assert all(checker.compare(k, o).verdict == "same" for k, o in pairs)
    again.assert_not_called()


# --- saved verdicts ---------------------------------------------------------------------

def _cached_checker(tmp_path):
    return FrameChecker("http://emby", "k", ffmpeg_path="ffmpeg", cache_file=tmp_path / "fc.json")


def test_a_verdict_is_reused_by_the_next_run(tmp_path):
    """Fuki 2026-10-12: pairs that stay (kept by the guard) were re-checked every run."""
    keeper, other = {**_ep("k", 2400, 25), "path": "/a.mkv"}, {**_ep("o", 2500, 24), "path": "/b.mkv"}
    first = _cached_checker(tmp_path)
    with patch.object(first, "_compare", return_value=FrameVerdict("unsure", (0.4,), ())):
        first.prefetch([(keeper, other)])
    second = _cached_checker(tmp_path)
    with patch.object(second, "_compare") as measured:
        second.prefetch([(keeper, other)])
        assert second.compare(keeper, other) == FrameVerdict("unsure", (0.4,), ())
    measured.assert_not_called()


def test_unavailable_is_never_saved(tmp_path):
    first = _cached_checker(tmp_path)
    with patch.object(first, "_compare", return_value=FrameVerdict("unavailable")):
        first.compare(_ep("k", 2400, 25), _ep("o", 2400, 25))
    second = _cached_checker(tmp_path)
    with patch.object(second, "_compare", return_value=FrameVerdict("same")) as measured:
        second.compare(_ep("k", 2400, 25), _ep("o", 2400, 25))
    measured.assert_called_once()


def test_a_replaced_file_or_a_new_method_version_is_checked_again(tmp_path):
    keeper, other = {**_ep("k", 2400, 25), "path": "/a.mkv"}, {**_ep("o", 2500, 24), "path": "/b.mkv"}
    renamed = {**other, "path": "/b2.mkv"}
    reprobed = {**other, "runtime_ticks": other["runtime_ticks"] + TICKS}
    assert len({cache_key(keeper, other), cache_key(keeper, renamed), cache_key(keeper, reprobed)}) == 3
    first = _cached_checker(tmp_path)
    with patch.object(first, "_compare", return_value=FrameVerdict("same", (0.9,) * 3, (-3.0, -3.1, -3.2))):
        first.compare(keeper, other)
    with patch("emby_dedupe.api.content_check.CHECK_VERSION", 999):
        newer = _cached_checker(tmp_path)
        with patch.object(newer, "_compare", return_value=FrameVerdict("same")) as measured:
            newer.compare(keeper, other)
    measured.assert_called_once()


def test_an_unreadable_cache_starts_fresh(tmp_path):
    (tmp_path / "fc.json").write_text("{not json")
    checker = _cached_checker(tmp_path)
    with patch.object(checker, "_compare", return_value=FrameVerdict("different", (0.0, 0.1))):
        assert checker.compare(_ep("k", 2400, 25), _ep("o", 2400, 25)).verdict == "different"


def test_old_unused_verdicts_are_dropped(tmp_path):
    import json
    stale = {"verdict": "same", "scores": [], "offsets": [], "checked": "2020-01-01T00:00:00+00:00",
             "used": "2020-01-01T00:00:00+00:00"}
    key = cache_key(_ep("x", 2400, 25), _ep("y", 2400, 25))
    (tmp_path / "fc.json").write_text(json.dumps({"pairs": {key: stale}}))
    checker = _cached_checker(tmp_path)
    with patch.object(checker, "_compare", return_value=FrameVerdict("same")):
        checker.compare(_ep("k", 2400, 25), _ep("o", 2400, 25))
    assert key not in json.loads((tmp_path / "fc.json").read_text())["pairs"]


def test_tests_never_touch_the_users_cache(tmp_path):
    assert cache_path() == tmp_path / "frame-check-cache.json"  # conftest points it here


# --- the dedupe guard ------------------------------------------------------------------------

STEADY = (-3.0, -3.5, -4.1, -4.6, -5.3)  # S19E07: a real speed-up lines up at one offset
DRIFTING = (-17.6, -40.0, -62.6, -85.2, -102.1)  # S05E15 avi: same pictures, ~110 s longer


class _Frames:
    def __init__(self, verdict, offsets=STEADY, covered="same"):
        self.verdict = FrameVerdict(verdict, (0.9,) * 5, offsets if verdict == "same" else ())
        self.covered = FrameVerdict(covered, (0.9,) * 7, (), "covered" if covered == "same" else "61s at the start")
        self.calls = 0
        self.cover_calls = 0

    def compare(self, keeper, other):
        self.calls += 1
        return self.verdict

    def covers(self, keeper, other):
        self.cover_calls += 1
        return self.covered


def _film(item_id, seconds, fps, path="/M/A (2001)/a.mkv"):
    return {**_ep(item_id, seconds, fps, path), "is_episode": False}


def test_speedup_is_removed_only_when_frames_match():
    sk, cz = _film("sk", 2423, 25.0), _film("cz", 2423 * 1.0427, 23.976)
    assert dd._different_content_reason(cz, sk, _Frames("same")) is None
    refused = dd._different_content_reason(cz, sk, _Frames("unsure"))
    assert "duration-mismatch" in refused and "PAL speed-up not confirmed" in refused
    assert "duration-mismatch" in dd._different_content_reason(cz, sk, None)


def test_matching_pictures_with_a_drifting_offset_are_not_a_speedup():
    """NCIS S05E15: the SD avi shows the same pictures but runs ~110 s longer at the same
    speed; the runtime ratio only happens to fit 25/24, so the avi is kept."""
    avi, sk = _film("avi", 2423 * 1.0443, 23.976), _film("sk", 2423, 25.0)
    refused = dd._different_content_reason(avi, sk, _Frames("same", DRIFTING))
    assert "duration-mismatch" in refused and "offset drifts" in refused


def test_drift_needs_enough_matched_points():
    assert not FrameVerdict("same", (0.9, 0.9, 0.1, 0.1, 0.1), (-3.0, -3.5)).steady
    assert FrameVerdict("same", (0.9,) * 5, STEADY).steady


def test_a_film_runtime_gap_that_is_not_a_speedup_never_asks_the_frames():
    frames = _Frames("same")
    reason = dd._different_content_reason(_film("cz", 2569, 24.0), _film("sk", 2387, 25.0), frames)
    assert "duration-mismatch" in reason and frames.calls == 0 and frames.cover_calls == 0


def test_episode_runtime_gap_goes_when_the_kept_copy_holds_all_of_it():
    """Fuki 2026-10-12: TVD S06 720p BluRay (EN) vs 1080p CZ+EN, 0.03 % off the PAL ratio."""
    blu, cz = _ep("blu", 2536, 23.976), _ep("cz", 2417, 25.0)
    frames = _Frames("same")
    assert dd._different_content_reason(blu, cz, frames) is None
    assert frames.cover_calls == 1 and frames.calls == 0


def test_episode_with_a_lead_in_the_kept_copy_lacks_stays():
    """TVD S08E16: the copy to delete has a 61 s TV lead-in (Fuki: such cases stay manual)."""
    refused = dd._different_content_reason(_ep("tv", 2606, 23.976), _ep("cz", 2431, 25.0), _Frames("same", covered="unsure"))
    assert "duration-mismatch" in refused and "not proven that the kept copy holds all of it" in refused


def test_episode_runtime_gap_without_frames_stays():
    assert "duration-mismatch" in dd._different_content_reason(_ep("tv", 2606, 23.976), _ep("cz", 2431, 25.0), None)


# --- coverage: does the kept copy hold everything? (real offsets, 2026-10-12) -----------------

TVD_S06E16 = [(45, -0.6), (380, 1.4), (887, 2.5), (1395, 3.6), (1902, 2.9), (2282, 3.2), (2461, 4.2)]


def test_tvd_bluray_is_covered_by_the_pal_web_dl():
    verdict, note = coverage_verdict(TVD_S06E16, 2536, 2417, 2417 / 2536, 23.976 / 25)
    assert verdict == "same" and "covered" in note


def test_kept_copy_longer_than_the_copy_to_delete_covers_it():
    # NCIS S14E15: 480p CZ (25 fps, 38:37) inside the 1080p CZ (23.976, 41:04)
    points = [(45, 0.0), (348, -7.4), (811, -14.1), (1275, -25.2), (1738, -33.9), (2086, -40.2), (2243, -43.9)]
    assert coverage_verdict(points, 2318, 2465, 2465 / 2318, 25 / 23.976)[0] == "same"


def test_a_lead_in_on_the_copy_to_delete_is_not_covered():
    # TVD S08E16 480p: 61 s before the kept copy starts
    points = [(45, -56.3), (391, -47.6), (912, -39.9), (1433, -28.2), (1954, -16.4), (2345, -6.3), (2531, -1.5)]
    verdict, note = coverage_verdict(points, 2606, 2431, 2431 / 2606, 23.976 / 25)
    assert verdict == "unsure" and "at the start" in note


def test_a_missing_scene_is_a_step_off_the_line():
    points = [(45, 0.0), (380, 0.1), (887, 0.2), (1395, 40.3), (1902, 40.4), (2282, 40.5), (2461, 40.6)]
    verdict, note = coverage_verdict(points, 2536, 2577, 1.0, 1.0)
    assert verdict == "unsure" and "missing or extra" in note


def test_extra_minutes_spread_evenly_are_not_a_speed_up():
    """NCIS S05E15: the avi ran ~110 s longer at the SAME speed; spread over the episode the
    offsets made a straight line that looked like 25/23.976, but the frame rates say 1.0."""
    points = [(45, 0.0), (380, -16.6), (887, -40.0), (1395, -62.6), (1902, -85.2), (2282, -102.1), (2461, -110.0)]
    verdict, note = coverage_verdict(points, 2533, 2423, 1.0, 1.0)
    assert verdict == "unsure" and "frame rates" in note


def test_coverage_needs_both_frame_rates():
    assert coverage_verdict(TVD_S06E16, 2536, 2417, 2417 / 2536, None) == ("unsure", "frame rate unknown")
    assert expected_speed(_ep("k", 2417, 25.0), _ep("o", 2536, None)) is None
    assert abs(expected_speed(_ep("k", 2417, 25.0), _ep("o", 2536, 23.976)) - 0.95904) < 1e-4


def test_covers_stops_at_the_first_part_not_found():
    checker = FrameChecker("https://emby", "k", ffmpeg_path="ffmpeg")
    with patch.object(checker, "_score_point", side_effect=[(0.9, 0.1), (0.1, None)]) as score:
        verdict = checker._covers(_ep("k", 2417, 25.0), _ep("o", 2536, 23.976))
    assert verdict.verdict == "different" and score.call_count == 2 and "not found" in verdict.note


def test_covers_samples_the_copy_to_delete_from_its_first_to_its_last_minute():
    checker = FrameChecker("https://emby", "k", ffmpeg_path="ffmpeg")
    offsets = iter(o for _, o in TVD_S06E16)
    with patch.object(checker, "_score_point", side_effect=lambda *a: (1.0, next(offsets))) as score:
        verdict = checker._covers(_ep("k", 2417, 25.0), _ep("o", 2536, 23.976))
    sampled = [call.args[2] for call in score.call_args_list]
    assert sampled[0] == 45 and sampled[-1] == 2536 - 75 and len(sampled) == 7
    assert all(call.args[0]["id"] == "o" for call in score.call_args_list)  # the copy to delete is sampled
    assert verdict.verdict == "same"


def test_cover_and_compare_verdicts_are_saved_apart(tmp_path):
    checker = _cached_checker(tmp_path)
    keeper, other = _ep("k", 2417, 25.0), _ep("o", 2536, 23.976)
    with patch.object(checker, "_compare", return_value=FrameVerdict("same")), \
         patch.object(checker, "_covers", return_value=FrameVerdict("unsure", (), (), "61s at the start")):
        assert checker.compare(keeper, other).verdict == "same"
        assert checker.covers(keeper, other).verdict == "unsure"
    again = _cached_checker(tmp_path)
    with patch.object(again, "_covers") as rerun:
        saved = again.covers(keeper, other)
    assert saved.note == "61s at the start" and not rerun.called
    assert cache_key(keeper, other, "cover") != cache_key(keeper, other)


def test_swapped_episode_with_matching_length_is_kept():
    keeper, other = _ep("sk08", 2461, 25.0), _ep("cz08", 2492, 25.0)  # within 2 %
    assert "frame-check different" in dd._different_content_reason(other, keeper, _Frames("different"))


def test_frames_unavailable_falls_back_to_the_old_checks():
    assert dd._different_content_reason(_ep("b", 2492, 25), _ep("a", 2461, 25), _Frames("unavailable")) is None


def test_movies_skip_the_frame_check():
    frames = _Frames("different")
    movie_a = {**_ep("a", 7000, 24), "is_episode": False, "path": "/M/A (2001)/a.mkv"}
    movie_b = {**_ep("b", 7010, 24), "is_episode": False, "path": "/M/A (2001) 4K/b.mkv"}
    assert dd._different_content_reason(movie_b, movie_a, frames) is None and frames.calls == 0


def test_prefetch_list_holds_only_pairs_the_guard_will_ask_about():
    same_len = {"keep": _ep("k1", 2461, 25), "delete": [_ep("d1", 2470, 25)]}
    speedup = {"keep": _ep("k2", 2423, 25.0), "delete": [_ep("d2", 2423 * 1.0427, 23.976)]}
    recap = {"keep": _ep("k3", 2387, 25.0), "delete": [_ep("d3", 2569, 24.0)]}
    unprobed = {"keep": _ep("k4", 2400, 25), "delete": [{**_ep("d4", 0, 25), "runtime_ticks": None}]}
    pairs, covering = dd._frame_check_pairs([same_len, speedup, recap, unprobed])
    assert [(k["id"], o["id"]) for k, o in pairs] == [("k1", "d1")]
    assert [(k["id"], o["id"]) for k, o in covering] == [("k2", "d2"), ("k3", "d3")]


def test_video_frame_rate_comes_from_the_first_video_stream():
    details = {"MediaStreams": [{"Type": "Audio"}, {"Type": "Video", "RealFrameRate": 25}]}
    assert dd._video_frame_rate(details) == 25.0
    assert dd._video_frame_rate({}) is None


def test_prefetch_runs_covers_for_pairs_whose_lengths_differ(tmp_path):
    checker = _cached_checker(tmp_path)
    same_len, gap = (_ep("k1", 2461, 25), _ep("d1", 2470, 25)), (_ep("k2", 2417, 25.0), _ep("d2", 2536, 23.976))
    with patch.object(checker, "_compare", return_value=FrameVerdict("same")) as compare, \
         patch.object(checker, "_covers", return_value=FrameVerdict("same", (), (), "covered")) as covers:
        checker.prefetch([same_len], [gap])
        assert checker.covers(*gap).verdict == "same" and checker.compare(*same_len).verdict == "same"
    assert compare.call_count == 1 and covers.call_count == 1  # the guard reuses the prefetched verdicts


def test_a_search_window_never_starts_before_the_file():
    """TVD S08E16: the copy to delete has a 61 s lead-in, so its 45 s point lies before the
    kept copy starts; ffmpeg would read from 0 while the frame times said -14 s."""
    checker = FrameChecker("https://emby", "k", ffmpeg_path="ffmpeg")
    frames = [(45.0 + n, [1.0]) for n in range(4)]
    with patch.object(checker, "_keyframes", side_effect=[frames, [(0.0, [1.0])], [(0.0, [1.0])]]) as read:
        checker._score_point(_ep("o", 2606, 23.976), _ep("k", 2431, 25.0), 45.0, 0.93, -56.0)
    assert all(call.args[1] >= 0 for call in read.call_args_list[1:])
