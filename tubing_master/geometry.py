"""Annulus geometry helpers for tube drawing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


def annulus_area_mm2(od_mm: float, id_mm: float) -> float:
    ro, ri = od_mm / 2.0, id_mm / 2.0
    return math.pi * max(ro * ro - ri * ri, 0.0)


def area_reduction_fraction(a0_mm2: float, a1_mm2: float) -> float:
    if a0_mm2 <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - a1_mm2 / a0_mm2))


def percent_reduction(a0_mm2: float, a1_mm2: float) -> float:
    return 100.0 * area_reduction_fraction(a0_mm2, a1_mm2)


def wall_thickness_sink_estimate(t0_mm: float, d0_mm: float, d1_mm: float) -> float:
    """Thin-wall sink approximation from notebook (constant volume heuristic)."""
    if d1_mm <= 0:
        return t0_mm
    return t0_mm * math.sqrt(d0_mm / d1_mm)


def tube_length_after_reduction(length_mm: float, reduction_frac: float) -> float:
    """Uniform stretching along axis for area reduction (illustrative)."""
    if reduction_frac >= 1.0:
        return length_mm
    return length_mm / (1.0 - reduction_frac)


@dataclass(frozen=True)
class TubeGeometry:
    """Annulus cross-section in meters (axisymmetric)."""

    outer_diameter_m: float
    inner_diameter_m: float

    @property
    def wall_thickness_m(self) -> float:
        return (self.outer_diameter_m - self.inner_diameter_m) / 2.0


def tube_from_od_id_m(outer_diameter_m: float, inner_diameter_m: float) -> TubeGeometry:
    if outer_diameter_m <= 0 or inner_diameter_m <= 0:
        raise ValueError("OD and ID must be positive.")
    if outer_diameter_m <= inner_diameter_m:
        raise ValueError("Outer diameter must exceed inner diameter.")
    return TubeGeometry(float(outer_diameter_m), float(inner_diameter_m))


def implied_area_reduction_fraction(
    od0_m: float,
    id0_m: float,
    od1_m: float,
    id1_m: float,
) -> float:
    """Fractional annulus area removed from state 0 → 1 (negative if area grows)."""
    a0 = annulus_area_mm2(od0_m * 1000.0, id0_m * 1000.0)
    a1 = annulus_area_mm2(od1_m * 1000.0, id1_m * 1000.0)
    return area_reduction_fraction(a0, a1)
