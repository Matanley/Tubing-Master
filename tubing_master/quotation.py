"""Bill of materials and quotation derived from project + pass schedule."""

from __future__ import annotations

from typing import Any, Dict, List

from tubing_master.bom_detail import compute_pass_die_rows
from tubing_master.geometry import annulus_area_mm2
from tubing_master.grain_evolution import summarize_schedule
from tubing_master.materials import material_density_kg_m3

# Fixed bottom rows on the Quotation tab (services / extras); editable qty × unit cost.
PROCESS_CHARGE_SLOTS = ("preprocessing", "postprocessing", "expedite", "extra")
PROCESS_CHARGE_LABELS = {
    "preprocessing": "Preprocessing",
    "postprocessing": "Postprocessing",
    "expedite": "Expedite",
    "extra": "Extra Charge",
}
# Default description text per slot (Pass column still uses PROCESS_CHARGE_LABELS).
PROCESS_CHARGE_DESCRIPTION_DEFAULTS = {
    "preprocessing": "Straightening, Gundrilling, Honing, etc",
    "postprocessing": "Grinding, Bending, Laser Cutting, etc",
    "expedite": "Expedite costs",
    "extra": "Add on cost for other items",
}


def process_charge_description_default(slot: str) -> str:
    return PROCESS_CHARGE_DESCRIPTION_DEFAULTS.get(
        slot, PROCESS_CHARGE_LABELS.get(slot, slot)
    )


def build_pass_bom(project: Dict[str, Any]) -> Dict[str, Any]:
    """Create BOM rows from analytical pass metrics."""
    summary = summarize_schedule(project)
    lines: List[Dict[str, Any]] = []
    for row in summary.get("pass_metrics") or []:
        idx = int(row.get("pass", 0))
        notes = (
            f"σ_pull≈{row['pulling_stress_mpa']:.1f} MPa, "
            f"SF(UTS)≈{row['safety_factor_vs_uts']:.2f}, grain≈{row['grain_um']:.2f} µm"
        )
        if row.get("unloading_stress_mpa") is not None:
            notes += (
                f"; σ_unload≈{float(row['unloading_stress_mpa']):.0f} MPa, "
                f"springback ε≈{float(row.get('springback_strain', 0.0)):.3f}, "
                f"ε_perm≈{float(row.get('permanent_strain', 0.0)):.3f}"
            )
        lines.append(
            {
                "item": f"Pass {idx} drawing",
                "qty": 1.0,
                "unit": "pass",
                "unit_cost": 0.0,
                "notes": notes,
            }
        )
    sch = project.get("pass_schedule") or {}
    ht = sch.get("interpass_ht") or []
    for i, hi in enumerate(ht):
        lines.append(
            {
                "item": f"Interpass heat treatment ({i + 1})",
                "qty": float(hi.get("time_min", 0.0) or 0.0),
                "unit": "min",
                "unit_cost": 0.0,
                "notes": (
                    f"T={hi.get('temp_c', '')} °C, gas={hi.get('gas', '')}, "
                    f"{hi.get('equipment', '')}"
                ),
            }
        )
    return {
        "version": 1,
        "currency": "USD",
        "lines": lines,
        "simulation_summary": summary,
    }


def build_quotation(bom: Dict[str, Any], overhead_pct: float = 12.0) -> Dict[str, Any]:
    """Roll BOM lines into a quote using editable unit_cost on each line."""
    lines_out: List[Dict[str, Any]] = []
    subtotal = 0.0
    for line in bom.get("lines") or []:
        qty = float(line.get("qty", 0.0) or 0.0)
        unit_cost = float(line.get("unit_cost", 0.0) or 0.0)
        ext = qty * unit_cost
        subtotal += ext
        row = dict(line)
        row["extended"] = ext
        lines_out.append(row)
    overhead = subtotal * (overhead_pct / 100.0)
    total = subtotal + overhead
    return {
        "version": 1,
        "currency": bom.get("currency", "USD"),
        "lines": lines_out,
        "subtotal": subtotal,
        "overhead_pct": overhead_pct,
        "overhead": overhead,
        "total": total,
    }


def stock_mass_kg(
    project: Dict[str, Any], stock_length_m: float, density_kg_m3: float
) -> float:
    """Mass of incoming annulus stock (kg) from Tubing Project OD/ID, length, and density."""
    od = float(project.get("in_od_mm", 0.0) or 0.0)
    id_ = float(project.get("in_id_mm", 0.0) or 0.0)
    a_mm2 = annulus_area_mm2(od, id_)
    volume_m3 = a_mm2 * 1e-6 * max(0.0, float(stock_length_m))
    return float(density_kg_m3) * volume_m3


def pass_charge_lines_from_project(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One charge row per drawing pass (die): OD step + semi-die angle from schedule."""
    lines: List[Dict[str, Any]] = []
    for r in compute_pass_die_rows(project):
        if r.get("row_kind") != "die":
            continue
        od_b = float(r["od_before"])
        od_a = float(r["od_after"])
        delta = od_a - od_b
        alpha = float(r["alpha_deg"])
        lines.append(
            {
                "line_kind": "pass",
                "pass": int(r["pass"]),
                "description": f"OD {od_b:.4f} -> {od_a:.4f} mm (Δ {delta:+.4f} mm)",
                "dies": f"α={alpha:.2f}°",
                "qty": 1.0,
                "unit_cost": 0.0,
                "extended": 0.0,
                "comments": "",
            }
        )
    return lines


def _line_extended(ln: Dict[str, Any]) -> float:
    return float(ln.get("qty", 1.0) or 1.0) * float(ln.get("unit_cost", 0.0) or 0.0)


def merge_pass_charges_preserve_edits(
    new_pass_lines: List[Dict[str, Any]], old_lines: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """When the schedule is rebuilt, keep price and comments for passes that still exist."""
    old_by_pass: Dict[int, Dict[str, Any]] = {}
    for x in old_lines:
        if x.get("line_kind") != "pass":
            continue
        p = x.get("pass")
        if p is not None:
            old_by_pass[int(p)] = x
    for row in new_pass_lines:
        p = int(row["pass"])
        if p in old_by_pass:
            o = old_by_pass[p]
            row["unit_cost"] = float(o.get("unit_cost", 0.0) or 0.0)
            row["comments"] = str(o.get("comments", ""))
        row["extended"] = _line_extended(row)
    return new_pass_lines


def extract_surcharge_lines(old_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(x) for x in old_lines if x.get("line_kind") == "surcharge"]


def default_process_charge_line(slot: str) -> Dict[str, Any]:
    return {
        "line_kind": "process_charge",
        "slot": slot,
        "description": process_charge_description_default(slot),
        "dies": "",
        "qty": 1.0,
        "unit_cost": 0.0,
        "extended": 0.0,
        "comments": "",
    }


def build_process_charge_lines_from_old(old_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Four fixed slots at the bottom of the quote; merge user edits from old snapshot."""
    old_by_slot: Dict[str, Dict[str, Any]] = {}
    for x in old_lines:
        if x.get("line_kind") != "process_charge":
            continue
        slot = str(x.get("slot") or "")
        if slot in PROCESS_CHARGE_LABELS:
            old_by_slot[slot] = dict(x)
    out: List[Dict[str, Any]] = []
    for slot in PROCESS_CHARGE_SLOTS:
        row = default_process_charge_line(slot)
        if slot in old_by_slot:
            o = old_by_slot[slot]
            row["description"] = str(o.get("description", row["description"]))
            row["dies"] = str(o.get("dies", ""))
            row["qty"] = float(o.get("qty", 1.0) or 1.0)
            row["unit_cost"] = float(o.get("unit_cost", 0.0) or 0.0)
            row["comments"] = str(o.get("comments", ""))
        row["extended"] = _line_extended(row)
        out.append(row)
    return out


def ensure_process_charge_lines(lines: List[Dict[str, Any]]) -> None:
    """Strip any existing process_charge rows and append the canonical four-slot block."""
    proc = build_process_charge_lines_from_old(lines)
    lines[:] = [x for x in lines if x.get("line_kind") != "process_charge"] + proc


def rebuild_per_pass_lines(
    project: Dict[str, Any], old_quotation: Dict[str, Any]
) -> List[Dict[str, Any]]:
    fresh = pass_charge_lines_from_project(project)
    old = list(old_quotation.get("lines") or [])
    merge_pass_charges_preserve_edits(fresh, old)
    sur = extract_surcharge_lines(old)
    proc = build_process_charge_lines_from_old(old)
    for s in sur:
        s["extended"] = _line_extended(s)
    return fresh + sur + proc


def apply_line_extended(lines: List[Dict[str, Any]]) -> None:
    for ln in lines:
        ln["extended"] = _line_extended(ln)


def finalize_quotation_v2(
    project: Dict[str, Any],
    old_quotation: Dict[str, Any],
    *,
    rebuild_schedule_rows: bool,
) -> Dict[str, Any]:
    """Merge v2 quotation bundle; optionally rebuild pass descriptions from the analytical chain."""
    currency = str(old_quotation.get("currency") or "USD")
    stock_length_m = float(old_quotation.get("stock_length_m", 1.0) or 1.0)
    price_per_kg = float(old_quotation.get("price_per_kg", 0.0) or 0.0)
    mat_name = str(project.get("material", ""))
    default_rho = material_density_kg_m3(mat_name)
    density = float(old_quotation.get("density_kg_m3", default_rho) or default_rho)
    additional_cost = float(old_quotation.get("additional_cost", 0.0) or 0.0)

    if rebuild_schedule_rows:
        lines = rebuild_per_pass_lines(project, old_quotation)
    else:
        lines = [dict(x) for x in (old_quotation.get("lines") or [])]

    ensure_process_charge_lines(lines)
    apply_line_extended(lines)
    mass_kg = stock_mass_kg(project, stock_length_m, density)
    stock_material_cost = mass_kg * price_per_kg
    materials_cost = stock_material_cost + additional_cost
    drawing = sum(float(x.get("extended", 0.0) or 0.0) for x in lines)
    total = materials_cost + drawing

    return {
        "version": 2,
        "currency": currency,
        "stock_length_m": stock_length_m,
        "price_per_kg": price_per_kg,
        "density_kg_m3": density,
        "additional_cost": additional_cost,
        "stock_material_cost": stock_material_cost,
        "lines": lines,
        "materials_cost": materials_cost,
        "drawing_charges": drawing,
        "total": total,
    }
