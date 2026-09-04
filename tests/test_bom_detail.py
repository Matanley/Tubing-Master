"""Process BOM includes drawing and auxiliary schedule steps."""

from __future__ import annotations

from tubing_master.bom_detail import compute_pass_die_rows
from tubing_master.quotation import pass_charge_lines_from_project


def _project() -> dict:
    return {
        "in_od_mm": 12.0,
        "in_id_mm": 10.0,
        "drawing_method": "Mandrel drawing",
        "pass_schedule": {
            "incoming_process": {
                "kind": "reducer",
                "equipment": "Tube reducer",
                "notes": "tag end",
            },
            "passes": [
                {"kind": "drawing", "r": 0.12, "alpha_deg": 12.0, "mu": 0.06, "equipment": "Drawbench"},
                {
                    "kind": "annealing",
                    "r": 0.0,
                    "temperature": "1050",
                    "time": "45",
                    "protective_gas": "Argon",
                    "equipment": "Batch furnace",
                },
                {"kind": "drawing", "r": 0.10, "alpha_deg": 11.0, "mu": 0.05},
                {"kind": "sandblasting", "r": 0.0, "equipment": "Grit booth", "notes": "light blast"},
            ],
        },
    }


def test_compute_pass_die_rows_includes_auxiliary_and_incoming():
    rows = compute_pass_die_rows(_project())
    kinds = [r["row_kind"] for r in rows]
    assert "incoming" in kinds
    assert kinds.count("die") == 2
    assert kinds.count("tool") == 2  # mandrel for each draw (non-sink)
    assert kinds.count("process") == 2  # annealing + sandblasting
    anneal = next(r for r in rows if r.get("kind") == "annealing")
    assert anneal["equipment"] == "Batch furnace"
    assert "1050" in anneal["notes"]
    assert "Argon" in anneal["notes"]
    blast = next(r for r in rows if r.get("kind") == "sandblasting")
    assert blast["equipment"] == "Grit booth"
    assert "light blast" in blast["notes"]
    # Geometry held across anneal
    dies = [r for r in rows if r["row_kind"] == "die"]
    assert dies[0]["od_after"] == anneal["od_before"]
    assert anneal["od_before"] == anneal["od_after"]


def test_quotation_pass_charges_include_full_schedule():
    lines = pass_charge_lines_from_project(_project())
    kinds = [ln.get("line_kind") for ln in lines]
    assert kinds.count("pass") == 2
    assert kinds.count("process") >= 3  # incoming + annealing + sandblasting
    steps = [ln.get("step") for ln in lines]
    assert "Incoming" in steps
    assert "Annealing" in steps
    assert "Sandblasting" in steps
    anneal = next(ln for ln in lines if ln.get("kind") == "annealing")
    assert "Batch furnace" in anneal["dies"]
    assert "1050" in anneal["description"]


def test_quotation_merge_preserves_process_unit_cost():
    from tubing_master.quotation import merge_pass_charges_preserve_edits

    fresh = pass_charge_lines_from_project(_project())
    old = [dict(x) for x in fresh]
    for ln in old:
        if ln.get("kind") == "annealing":
            ln["unit_cost"] = 42.5
            ln["comments"] = "keep me"
    merge_pass_charges_preserve_edits(fresh, old)
    anneal = next(ln for ln in fresh if ln.get("kind") == "annealing")
    assert anneal["unit_cost"] == 42.5
    assert anneal["comments"] == "keep me"
