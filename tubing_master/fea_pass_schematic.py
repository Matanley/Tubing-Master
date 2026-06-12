"""Single-pass FEA setup schematic: tube, drawbench die pocket, insert, mandrel/plug."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from tubing_master.die_inventory import DEFAULT_BEARING_LENGTH_MM
from tubing_master.die_schematic import DieSchematicGeometry, DieSchematicSpec, compute_die_schematic_geometry
from tubing_master.fea_tube_die import od_id_after_area_reduction_fixed_bore

ToolingKind = Literal["sink", "mandrel", "plug", "floating_plug"]


@dataclass(frozen=True)
class FeaPassSchematicSpec:
    tooling: ToolingKind = "mandrel"
    od_in_mm: float = 10.0
    id_in_mm: float = 8.0
    area_reduction_fraction: float = 0.12
    semi_die_angle_deg: float = 12.0
    bearing_length_mm: float = DEFAULT_BEARING_LENGTH_MM
    pass_number: int = 1
    pass_total: int = 1
    process_label: str = ""
    placeholder: Optional[str] = None


@dataclass(frozen=True)
class FeaPassSchematicLayout:
    """Axisymmetric side-view layout (z = axial, r = radius, mm)."""

    die: DieSchematicGeometry
    od_in_mm: float
    id_in_mm: float
    od_out_mm: float
    id_out_mm: float
    tooling: ToolingKind
    pass_number: int
    pass_total: int
    process_label: str
    z_plot_min: float
    z_plot_max: float
    r_plot_max: float
    # Incoming tube + swaged lead-in before die bell
    z_stock_start: float
    z_swage_start: float
    z_swage_tip: float
    r_tip_od: float
    r_tip_id: float
    # Drawbench die holder (plate / pocket)
    z_holder_start: float
    z_holder_end: float
    r_holder_outer: float
    r_bore: float
    r_die_seat: float


def tooling_kind_from_drawing_method(label: str) -> ToolingKind:
    t = (label or "").lower()
    if "sink" in t or "rodless" in t:
        return "sink"
    if "floating" in t and "plug" in t:
        return "floating_plug"
    if "mandrel" in t:
        return "mandrel"
    if "plug" in t:
        return "plug"
    return "mandrel"


def _tube_profile_radii_mm(
    z: float,
    *,
    lay: FeaPassSchematicLayout,
    outer: bool,
) -> float:
    """Outer or inner radius of tube wall at axial position z (mm)."""
    r_od_i = lay.od_in_mm / 2.0
    r_id_i = lay.id_in_mm / 2.0
    r_od_o = lay.od_out_mm / 2.0
    r_id_o = lay.id_out_mm / 2.0
    g = lay.die

    if z <= lay.z_swage_start:
        return r_od_i if outer else r_id_i

    if z <= lay.z_swage_tip:
        t = (z - lay.z_swage_start) / max(lay.z_swage_tip - lay.z_swage_start, 1e-9)
        if outer:
            return r_od_i + t * (lay.r_tip_od - r_od_i)
        return r_id_i + t * (lay.r_tip_id - r_id_i)

    if z <= g.z0:
        if outer:
            return lay.r_tip_od + (z - lay.z_swage_tip) / max(g.z0 - lay.z_swage_tip, 1e-9) * (r_od_i - lay.r_tip_od)
        return lay.r_tip_id

    if z <= g.z1:
        t = (z - g.z0) / max(g.z1 - g.z0, 1e-9)
        if outer:
            return r_od_i + t * (r_od_o - r_od_i)
        return r_id_i

    if z <= g.z3:
        if outer:
            return r_od_o
        return r_id_o

    if outer:
        return r_od_o
    return r_id_o


def tube_half_section_polygon(
    lay: FeaPassSchematicLayout,
    *,
    z0: float,
    z1: float,
    n_seg: int = 24,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Upper and lower (r negative) polylines for tube wall between z0 and z1."""
    if z1 <= z0:
        z1 = z0 + 1e-6
    zs = [z0 + (z1 - z0) * i / n_seg for i in range(n_seg + 1)]
    ru = [_tube_profile_radii_mm(z, lay=lay, outer=True) for z in zs]
    rl = [-_tube_profile_radii_mm(z, lay=lay, outer=False) for z in zs]
    upper = list(zip(zs, ru))
    lower = list(zip(zs, rl))
    return upper, lower


def tube_wall_band_polygons(
    lay: FeaPassSchematicLayout,
    *,
    z0: float,
    z1: float,
    n_seg: int = 32,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Closed upper and lower wall bands (outer–inner) for educational side-view fill."""
    if z1 <= z0:
        z1 = z0 + 1e-6
    zs = [z0 + (z1 - z0) * i / n_seg for i in range(n_seg + 1)]
    ro = [_tube_profile_radii_mm(z, lay=lay, outer=True) for z in zs]
    ri = [_tube_profile_radii_mm(z, lay=lay, outer=False) for z in zs]
    upper = list(zip(zs, ro)) + list(zip(reversed(zs), reversed(ri)))
    lower = list(zip(zs, [-r for r in ro])) + list(zip(reversed(zs), [-r for r in reversed(ri)]))
    return upper, lower


def build_fea_pass_schematic_layout(spec: FeaPassSchematicSpec) -> Optional[FeaPassSchematicLayout]:
    if spec.placeholder:
        return None
    od_in = max(0.2, float(spec.od_in_mm))
    id_in = max(0.0, min(od_in - 0.05, float(spec.id_in_mm)))
    od_out, id_out = od_id_after_area_reduction_fixed_bore(
        od_in / 1000.0, id_in / 1000.0, float(spec.area_reduction_fraction)
    )
    od_out *= 1000.0
    id_out *= 1000.0

    die_spec = DieSchematicSpec(
        alpha_deg=float(spec.semi_die_angle_deg),
        bearing_length_mm=float(spec.bearing_length_mm),
        od_min_mm=min(od_out, od_in),
        od_max_mm=max(od_out, od_in),
        name=f"Pass {spec.pass_number}",
    )
    die = compute_die_schematic_geometry(die_spec)
    if die is None:
        return None

    r_od_i = od_in / 2.0
    r_die_outer_max = max(die.ro)
    bore_clear = max(0.08, 0.04 * r_od_i)
    r_bore = r_die_outer_max + bore_clear
    holder_wall = max(0.45 * r_bore, 0.35)
    r_holder_outer = r_bore + holder_wall

    span = die.z3 - die.z_bell0
    z_swage_start = die.z_bell0 - 0.55 * span
    z_stock_start = die.z_bell0 - 0.95 * span
    # Swaged point — slightly under bell ID for insertion
    z_swage_tip = die.z_bell0 + 0.02 * span
    r_tip_od = max(r_od_i * 0.86, die.ru[0] * 0.98)
    r_tip_id = max(id_in / 2.0 * 0.98, r_tip_od * 0.55)

    z_holder_start = die.z_bell0 - 0.12 * span
    z_holder_end = die.z2 + 0.18 * span
    z_plot_min = z_stock_start - 0.08 * span
    z_plot_max = die.z3 + 0.22 * span
    r_plot_max = max(r_holder_outer, r_od_i, r_die_outer_max) * 1.12

    return FeaPassSchematicLayout(
        die=die,
        od_in_mm=od_in,
        id_in_mm=id_in,
        od_out_mm=od_out,
        id_out_mm=id_out,
        tooling=spec.tooling,
        pass_number=int(spec.pass_number),
        pass_total=max(1, int(spec.pass_total)),
        process_label=(spec.process_label or "").strip(),
        z_plot_min=z_plot_min,
        z_plot_max=z_plot_max,
        r_plot_max=r_plot_max,
        z_stock_start=z_stock_start,
        z_swage_start=z_swage_start,
        z_swage_tip=z_swage_tip,
        r_tip_od=r_tip_od,
        r_tip_id=r_tip_id,
        z_holder_start=z_holder_start,
        z_holder_end=z_holder_end,
        r_holder_outer=r_holder_outer,
        r_bore=r_bore,
        r_die_seat=r_die_outer_max,
    )
