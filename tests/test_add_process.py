"""Manual process kinds on the Pass Schedule."""

from __future__ import annotations

from tubing_master.ui.add_process_dialog import (
    is_auxiliary_process_kind,
    normalize_process_kind,
    process_kind_display_label,
)
from tubing_master.ui.main_window import _merge_legacy_interleaved_pass_schedule_passes
from tubing_master.engine import drawing_pass_dicts


def test_normalize_process_kind_aliases():
    assert normalize_process_kind("Annealing") == "annealing"
    assert normalize_process_kind("straightending") == "straightening"
    assert normalize_process_kind("Sandblasting") == "sandblasting"
    assert normalize_process_kind("Reducer") == "reducer"
    assert normalize_process_kind("incoming") == "reducer"
    assert normalize_process_kind(None, area_reduction=0.1) == "drawing"
    assert process_kind_display_label("reducer") == "Reducer"
    assert is_auxiliary_process_kind("sandblasting")
    assert is_auxiliary_process_kind("reducer")
    assert not is_auxiliary_process_kind("drawing")


def test_merge_keeps_auxiliary_process_rows():
    passes = [
        {"kind": "drawing", "r": 0.12, "alpha_deg": 12.0, "mu": 0.06},
        {
            "kind": "annealing",
            "r": 0.0,
            "temperature": "1050",
            "time": "45",
            "equipment": "Batch furnace",
        },
        {"kind": "drawing", "r": 0.10, "alpha_deg": 12.0, "mu": 0.06},
        {"kind": "straightening", "r": 0.0, "equipment": "Roll straightener", "notes": "5 rolls"},
    ]
    merged = _merge_legacy_interleaved_pass_schedule_passes(passes)
    assert len(merged) == 4
    assert merged[1]["kind"] == "annealing"
    assert merged[3]["kind"] == "straightening"
    draws = drawing_pass_dicts(merged)
    assert len(draws) == 2
    assert all(float(p["r"]) > 0 for p in draws)


def test_merge_still_folds_legacy_ht_without_kind():
    passes = [
        {"r": 0.12, "alpha_deg": 12.0, "mu": 0.06},
        {"r": 0.0, "temperature": "900", "time": "30"},
    ]
    merged = _merge_legacy_interleaved_pass_schedule_passes(passes)
    assert len(merged) == 1
    assert float(merged[0]["r"]) == 0.12
    assert merged[0]["temperature"] == "900"
