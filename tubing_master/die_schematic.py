"""
Schematic side view of a wire/tube drawing die (half-section).

Geometry follows common textbook / supplier sketches (entry bell, reduction cone with semi-die
angle α, cylindrical bearing land, exit relief). Illustrative — not a certified shop drawing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class DieSchematicSpec:
    """Inputs for die profile geometry (inventory row or panel defaults)."""

    alpha_deg: float = 12.0
    bearing_length_mm: float = 1.2
    od_min_mm: float = 0.0
    od_max_mm: float = 10.0
    name: str = ""
    placeholder: Optional[str] = None
    subtitle: Optional[str] = None


@dataclass(frozen=True)
class DieSchematicGeometry:
    """Piecewise die profile in axisymmetric side-view coordinates (z along die axis, r ≥ 0 upper half)."""

    zu: Tuple[float, ...]
    ru: Tuple[float, ...]
    zo: Tuple[float, ...]
    ro: Tuple[float, ...]
    z_bell0: float
    z0: float
    z1: float
    z2: float
    z3: float
    r_hi: float
    r_lo: float
    wall: float
    delta_r: float
    z_min: float
    z_max: float
    r_plot_max: float
    alpha_deg: float
    bearing_length_mm: float
    name: str
    zc: float
    rc_label: float
    dim_depth: float
    y_ext_end: float


_DEFAULT_PANEL_OD_MIN_MM = 10.5
_DEFAULT_PANEL_OD_MAX_MM = 12.5
_DEFAULT_PANEL_ALPHA_DEG = 12.0
_DEFAULT_PANEL_BEAR_MM = 1.2

DEFAULT_PANEL_OD_MIN_MM = _DEFAULT_PANEL_OD_MIN_MM
DEFAULT_PANEL_OD_MAX_MM = _DEFAULT_PANEL_OD_MAX_MM


def empty_die_schematic_spec(*, inventory_empty: bool) -> DieSchematicSpec:
    """Placeholder panel when there is no die row to draw (no example geometry)."""
    if inventory_empty:
        return DieSchematicSpec(
            placeholder="Add dies in the table to preview tooling geometry.",
            subtitle='Click “Add die…” to create a row.',
        )
    return DieSchematicSpec(
        placeholder="Select a die row to show its schematic.",
    )


def compute_die_schematic_geometry(spec: DieSchematicSpec) -> Optional[DieSchematicGeometry]:
    """Return profile geometry, or ``None`` when ``spec.placeholder`` is set."""
    if spec.placeholder:
        return None

    lo = min(float(spec.od_min_mm), float(spec.od_max_mm))
    hi = max(float(spec.od_min_mm), float(spec.od_max_mm))
    r_hi = hi / 2.0
    r_lo = lo / 2.0
    if r_hi < r_lo + 1e-4:
        r_hi = r_lo + 0.15

    alpha_deg = float(spec.alpha_deg)
    alpha = max(alpha_deg, 0.05)
    alpha_rad = math.radians(alpha)
    delta_r = r_hi - r_lo
    L_cone = delta_r / math.tan(alpha_rad)
    L_b = max(float(spec.bearing_length_mm), 0.001)

    L_bell = min(0.45 * max(L_cone, 0.05), 4.0 * delta_r if delta_r > 0 else 2.0)
    bell_delta_r = min(0.12 * r_hi, 0.8 * delta_r if delta_r > 0 else 0.5)

    z_bell0 = -L_bell
    z0 = 0.0
    z1 = L_cone
    z2 = z1 + L_b
    L_rel = max(0.22 * L_b, 0.15)
    relief_a = math.radians(11.0)
    dr_rel = L_rel * math.tan(relief_a)
    z3 = z2 + L_rel

    zu = [z_bell0, z0, z1, z2, z3]
    ru = [r_hi + bell_delta_r, r_hi, r_lo, r_lo, min(r_lo + dr_rel, r_hi)]
    wall = max(0.4, 0.07 * r_hi)
    zo = list(zu)
    ro = [r + wall for r in ru]

    zc = z0 + 0.42 * (z1 - z0)
    t_cone = (zc - z0) / max(z1 - z0, 1e-9)
    r_inner_zc = r_hi - t_cone * delta_r
    inset = max(0.12 * delta_r, 0.11 * r_hi, 0.055)
    extra_drop = 0.06 * max(r_hi, 0.35)
    rc_label = max(r_inner_zc - inset - extra_drop, 0.22 * r_lo)

    dim_depth = 1.28 * (r_hi + wall)
    ext_past = max(0.07 * max(r_hi + wall, 1.0), 0.09 * L_b, 0.06)
    y_ext_end = dim_depth + ext_past

    z_span = max(z3 - z_bell0, 1e-9)
    margin_z = 0.09 * z_span
    bias_left = 0.032 * z_span
    z_min = z_bell0 - margin_z
    z_max = z3 + margin_z + bias_left
    pad_r = 0.28 * (r_hi + wall) + 0.06 * max(r_hi, 1.0)
    r_plot_max = r_hi + wall + pad_r

    return DieSchematicGeometry(
        zu=tuple(zu),
        ru=tuple(ru),
        zo=tuple(zo),
        ro=tuple(ro),
        z_bell0=z_bell0,
        z0=z0,
        z1=z1,
        z2=z2,
        z3=z3,
        r_hi=r_hi,
        r_lo=r_lo,
        wall=wall,
        delta_r=delta_r,
        z_min=z_min,
        z_max=z_max,
        r_plot_max=r_plot_max,
        alpha_deg=alpha_deg,
        bearing_length_mm=float(spec.bearing_length_mm),
        name=(spec.name or "Die").strip() or "Die",
        zc=zc,
        rc_label=rc_label,
        dim_depth=dim_depth,
        y_ext_end=y_ext_end,
    )


def symmetric_outline(zs: List[float], rs_upper: List[float]) -> List[Tuple[float, float]]:
    """Closed CCW outline: upper profile → lower profile (mirrored)."""
    verts = [(zs[i], rs_upper[i]) for i in range(len(zs))]
    verts.extend((zs[i], -rs_upper[i]) for i in range(len(zs) - 1, -1, -1))
    return verts
