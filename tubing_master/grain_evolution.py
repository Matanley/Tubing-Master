"""Grain trajectory summaries used by BOM / plots."""

from __future__ import annotations

from typing import Any, Dict, List

from tubing_master.damask_support import analytical_grain_fallback, run_schedule_grain_with_fallback
from tubing_master.engine import TubingMasterEngine, drawing_pass_dicts
from tubing_master.material_properties import material_for_project

def summarize_schedule(project: Dict[str, Any]) -> Dict[str, Any]:
    """Return od/id trajectory + pass metrics (analytical stress; DAMASK grain when configured)."""
    mat = material_for_project(project)
    eng = TubingMasterEngine.from_material(mat)
    sch = project.get("pass_schedule") or {}
    passes: List[Dict[str, Any]] = drawing_pass_dicts(list(sch.get("passes") or []))
    in_od = float(project.get("in_od_mm", 10.0))
    in_id = float(project.get("in_id_mm", 8.0))
    backend = str(sch.get("simulation_backend") or "analytical").lower()
    pass_rows, out_od, out_id = eng.run_pass_schedule_on_annulus(in_od, in_id, passes)

    grain_backend = "damask" if backend == "damask" else "analytical"
    grain_note = ""
    if grain_backend == "damask" and passes:
        grain_rows, grain_note = run_schedule_grain_with_fallback(
            passes, prefer_damask=True, initial_grain_um=mat.initial_grain_um
        )
    else:
        grain_rows = (
            analytical_grain_fallback(passes, material=mat) if passes else []
        )

    metrics: List[Dict[str, Any]] = []
    for pr in pass_rows:
        g = next((x for x in grain_rows if x.pass_index == pr.pass_index), None)
        grain_um = float(g.grain_size_um) if g is not None else float(pr.grain_um)
        row: Dict[str, Any] = {
                "pass": pr.pass_index,
                "pulling_stress_mpa": pr.pulling_stress_mpa,
                "safety_factor_vs_uts": pr.safety_factor_vs_uts,
                "true_strain": pr.true_strain,
                "grain_um": grain_um,
                "grain_source": (g.source if g is not None else "analytical"),
            }
        if pr.unloading_stress_mpa is not None:
            row["unloading_stress_mpa"] = pr.unloading_stress_mpa
            row["springback_strain"] = pr.springback_strain
            row["permanent_strain"] = pr.permanent_strain
            row["residual_stress_mpa"] = pr.residual_stress_mpa
            row["hysteresis_mpa"] = pr.hysteresis_mpa
        metrics.append(row)

    return {
        "version": 1,
        "simulation_backend": backend,
        "grain_backend": grain_backend,
        "grain_note": grain_note,
        "in_od_mm": in_od,
        "in_id_mm": in_id,
        "out_od_mm": out_od,
        "out_id_mm": out_id,
        "pass_metrics": metrics,
    }
