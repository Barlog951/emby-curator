"""Tests for scripts/dv/dv_common.py — the shared DV Profile-5 criterion.

dv_common is the single source of truth shared by dv-scan and dv-convert, so the
scanner and the converter can never disagree on what 'problematic' means.
"""
import importlib.util
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dv" / "dv_common.py"
_spec = importlib.util.spec_from_file_location("dv_common", _PATH)
dvc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dvc)


def test_is_problematic_only_profile_5():
    assert dvc.is_problematic(5) is True
    for p in (None, 4, 7, 8):  # no-DV / P4 / P7 / P8 all carry a usable base
        assert dvc.is_problematic(p) is False


def test_dv_profile_of_full_probe_dict():
    probe = {"streams": [{"side_data_list": [
        {"side_data_type": "DOVI", "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}]}]}
    assert dvc.dv_profile_of(probe) == (5, 0)


def test_dv_profile_of_single_stream_dict():
    stream = {"side_data_list": [{"dv_profile": 8, "dv_bl_signal_compatibility_id": 1}]}
    assert dvc.dv_profile_of(stream) == (8, 1)


def test_dv_profile_of_no_dv():
    assert dvc.dv_profile_of({"streams": [{"side_data_list": []}]}) == (None, None)
    assert dvc.dv_profile_of({}) == (None, None)


def test_utc_now_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", dvc.utc_now())
