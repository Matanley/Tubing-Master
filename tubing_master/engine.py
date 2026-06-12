"""Analytical tube drawing engine (from Jupyter Tubing Master prototypes)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import numpy as np

from tubing_master.geometry import TubeGeometry, annulus_area_mm2, tube_from_od_id_m
from tubing_master.materials import MetalMaterial, material_from_preset_label


@dataclass
class PassResult:
    pass_index: int
    die_angle_deg: float
    reduction_fraction: float
    pulling_stress_mpa: float
    safety_factor_vs_uts: float  # UTS / pulling_stress; >1 is safe
    true_strain: float
    grain_um: float
    unloading_stress_mpa: float | None = None
    springback_strain: float | None = None
    permanent_strain: float | None = None
    residual_stress_mpa: float | None = None
    hysteresis_mpa: float | None = None


@dataclass
class PassInput:
    """One drawing pass for the Qt schedule table / optimization."""

    semi_die_angle_deg: float
    friction_mu: float
    area_reduction_fraction: float


@dataclass
class SchedulePassResult:
    """Per-pass outputs shown in the Tubing Master summary (plane-stress proxy)."""

    von_mises_equiv_pa: float
    safety_factor_vs_uts: float
    grain_size_um: float
    broken_risk_score: float
    grain_source: str = "analytical"  # "damask" | "analytical" | "analytical_fallback"
    unloading_stress_mpa: float | None = None
    springback_strain: float | None = None
    permanent_strain: float | None = None
    residual_stress_mpa: float | None = None
    hysteresis_mpa: float | None = None


class TubingMasterEngine:
    """Multi-pass analytical metrics (notebook Cells 1–2)."""

    def __init__(self, material: MetalMaterial | None = None) -> None:
        self.material = material or MetalMaterial(name="default")
        self.C = float(self.material.flow_C_mpa)
        self.n = float(self.material.hardening_n)
        self.eps0 = float(self.material.eps0)
        self.m = float(self.material.grain_refinement_m)
        self.min_grain_size = float(self.material.min_grain_um)
        self.base_uts = float(self.material.base_uts_mpa)
        self.friction_coeff = float(self.material.friction_coeff)
        self.initial_grain_um = float(self.material.initial_grain_um)

    @classmethod
    def from_material(cls, material: MetalMaterial | None) -> "TubingMasterEngine":
        return cls(material=material)

    def calculate_pulling_stress_and_safety(
        self,
        die_angle_deg: float,
        reduction_fraction: float,
        *,
        friction_coeff: float | None = None,
    ) -> Tuple[float, float, bool, float]:
        """Return pulling_stress_mpa, grain_size_estimate, over_stressed, safety_factor_vs_uts."""
        alpha_rad = np.radians(die_angle_deg)
        r = max(1e-9, min(0.999999, float(reduction_fraction)))
        true_strain = float(np.log(1.0 / (1.0 - r)))
        flow_stress = float(self.material.flow_stress_mpa(true_strain))
        mu = float(self.friction_coeff if friction_coeff is None else friction_coeff)
        tan_a = max(1e-6, float(np.tan(alpha_rad)))
        pulling_stress = float(
            flow_stress
            * (
                true_strain
                + (4.0 / (3.0 * np.sqrt(3.0))) * alpha_rad
                + mu / tan_a * true_strain
            )
        )
        grain_size_estimate = float(
            self.calculate_grain_size(true_strain, d0=self.initial_grain_um)
        )
        current_uts = float(
            self.material.ultimate_strength_mpa(true_strain, reduction_fraction=r)
        )
        safety_factor_vs_uts = float(current_uts / max(pulling_stress, 1e-6))
        over_stressed = safety_factor_vs_uts < 1.0
        return pulling_stress, grain_size_estimate, over_stressed, safety_factor_vs_uts

    def simulate_pass_strain(
        self,
        x: float,
        r_coord: float,
        die_angle_deg: float,
        reduction_fraction: float,
        tube_length_mm: float,
    ) -> float:
        """Spatial strain field for visualization (multi-pass notebook)."""
        alpha_rad = np.radians(die_angle_deg)
        base_strain = float(np.log(1.0 / (1.0 - max(1e-9, min(0.999999, reduction_fraction)))))
        shear_factor = (r_coord / 5.0) * np.sin(alpha_rad)
        zone_start = tube_length_mm * 0.4
        zone_end = tube_length_mm * 0.7
        if x < zone_start:
            deformation_history = 0.05
        elif x > zone_end:
            deformation_history = 1.0
        else:
            deformation_history = (x - zone_start) / max(zone_end - zone_start, 1e-9)
        return float(base_strain * deformation_history + shear_factor * (deformation_history**2))

    def calculate_grain_size(self, eps_p: float, d0: float = 15.0) -> float:
        if eps_p < 1e-3:
            return float(d0)
        return float(max(d0 / (1.0 + eps_p**self.m), self.min_grain_size))

    def run_pass_schedule_on_annulus(
        self,
        in_od_mm: float,
        in_id_mm: float,
        passes: List[Dict[str, Any]],
    ) -> Tuple[List[PassResult], float, float]:
        """Apply sequential area reductions; outputs cumulative grain from strain accumulation."""
        a = annulus_area_mm2(in_od_mm, in_id_mm)
        od, id_ = float(in_od_mm), float(in_id_mm)
        grain = float(self.initial_grain_um)
        tube_length = 50.0
        results: List[PassResult] = []

        for i, p in enumerate(passes):
            r_frac = float(p.get("r", 0.0))
            alpha = float(p.get("alpha_deg", 12.0))
            r_frac = max(1e-9, min(0.999999, r_frac))
            new_a = a * (1.0 - r_frac)
            scale = math.sqrt(max(new_a, 1e-12) / max(a, 1e-12))
            od *= scale
            id_ *= scale
            a = new_a
            true_strain = float(np.log(1.0 / (1.0 - r_frac)))
            grain_eps = float(self.material.effective_grain_refinement_strain(true_strain))
            grain = self.calculate_grain_size(grain_eps, d0=grain)
            pull, _, _, sf_vs_uts = self.calculate_pulling_stress_and_safety(alpha, r_frac)
            flow = float(self.material.flow_stress_mpa(true_strain))
            hyst = self.material.nitinol_hysteresis_for_pass(
                true_strain, loading_flow_stress_mpa=flow
            )
            tube_length = tube_length / (1.0 - r_frac)
            results.append(
                PassResult(
                    pass_index=i + 1,
                    die_angle_deg=alpha,
                    reduction_fraction=r_frac,
                    pulling_stress_mpa=float(pull),
                    safety_factor_vs_uts=float(sf_vs_uts),
                    true_strain=true_strain,
                    grain_um=float(grain),
                    unloading_stress_mpa=(hyst.unloading_stress_mpa if hyst else None),
                    springback_strain=(hyst.springback_strain if hyst else None),
                    permanent_strain=(hyst.permanent_strain if hyst else None),
                    residual_stress_mpa=(hyst.residual_stress_mpa if hyst else None),
                    hysteresis_mpa=(hyst.hysteresis_mpa if hyst else None),
                )
            )

        return results, od, id_


def drawing_pass_dicts(passes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows with positive area reduction *r* (drawing passes). Excludes HT / planning rows with *r* ≤ 0."""
    out: List[Dict[str, Any]] = []
    for p in passes:
        try:
            r = float(p.get("r", 0.0) or 0.0)
        except (TypeError, ValueError):
            r = 0.0
        if r > 0:
            out.append(p)
    return out


def annulus_od_id_trajectory(
    in_od_mm: float,
    in_id_mm: float,
    passes: List[Dict[str, Any]],
) -> List[Tuple[float, float]]:
    """After each pass, annulus OD/ID. Index 0 = incoming."""
    traj: List[Tuple[float, float]] = [(float(in_od_mm), float(in_id_mm))]
    a = annulus_area_mm2(in_od_mm, in_id_mm)
    od, id_ = float(in_od_mm), float(in_id_mm)
    for p in passes:
        r_frac = max(1e-9, min(0.999999, float(p.get("r", 0.0))))
        new_a = a * (1.0 - r_frac)
        scale = math.sqrt(max(new_a, 1e-12) / max(a, 1e-12))
        od *= scale
        id_ *= scale
        a = new_a
        traj.append((od, id_))
    return traj


def equal_r_per_pass_for_target_od(
    n_passes: int, od_in_m: float, od_tgt_m: float
) -> Optional[float]:
    """
    Uniform annulus area-reduction fraction per pass so outer diameter scales from ``od_in_m``
    to ``od_tgt_m`` in ``n_pass`` steps (constant ID/OD ratio).
    """
    if n_passes <= 0 or od_in_m <= 0:
        return None
    if od_tgt_m >= od_in_m:
        return None
    ratio_area = (od_tgt_m / od_in_m) ** 2
    r = 1.0 - ratio_area ** (1.0 / float(n_passes))
    return float(max(1e-9, min(0.999999, r)))


def predict_id_for_scaled_od(od_in_m: float, id_in_m: float, od_tgt_m: float) -> float:
    """Inner diameter when OD shrinks from ``od_in_m`` to ``od_tgt_m`` with fixed ID/OD ratio."""
    if od_in_m <= 0:
        return 0.0
    k = id_in_m / od_in_m
    return float(od_tgt_m * k)


def predict_od_for_scaled_id(od_in_m: float, id_in_m: float, id_tgt_m: float) -> float:
    """Outer diameter when ID scales from ``id_in_m`` to ``id_tgt_m`` with fixed ID/OD ratio."""
    if id_in_m <= 0:
        return 0.0
    return float(id_tgt_m * (od_in_m / id_in_m))


def _quantize_area_reduction_for_fit_schedule_tab(r: float, decimals: int = 6) -> float:
    """
    Values written to the pass table use fixed decimal formatting; the analytical chain must use the
    same quantized prefix reductions when solving for the last pass, otherwise simulated geometry
    diverges (especially with many passes).
    """
    x = float(f"{float(r):.{decimals}f}")
    return float(max(1e-9, min(0.999999, x)))


def equal_r_per_pass_for_target_id(
    n_passes: int, od_in_m: float, id_in_m: float, id_tgt_m: float
) -> Optional[float]:
    """
    Uniform annulus area-reduction per pass so inner diameter scales from ``id_in_m`` to
    ``id_tgt_m`` in ``n_passes`` steps (constant ID/OD ratio). Equivalent to targeting
    OD ``od_in_m * (id_tgt_m / id_in_m)``.
    """
    if n_passes <= 0 or od_in_m <= 0 or id_in_m <= 0:
        return None
    if id_tgt_m >= id_in_m:
        return None
    od_equiv_m = od_in_m * (id_tgt_m / id_in_m)
    return equal_r_per_pass_for_target_od(n_passes, od_in_m, od_equiv_m)


def final_pass_r_after_uniform_prefix_for_target_id(
    id_in_m: float,
    id_tgt_m: float,
    n_passes: int,
    uniform_r: float,
) -> float:
    """
    After ``n_passes - 1`` draws each using ``uniform_r``, return area-reduction ``r`` for the last pass
    so final inner diameter equals ``id_tgt_m`` exactly (same proportional OD/ID scaling as the engine).

    Prefix reductions are quantized like the schedule table (default 6 decimals) before predicting the
    pre-last-pass bore; otherwise the corrective last pass is solved from an inconsistent chain.
    """
    ru = max(1e-9, min(0.999999, float(uniform_r)))
    ru_tab = _quantize_area_reduction_for_fit_schedule_tab(ru)
    if n_passes <= 1:
        if id_in_m <= 0:
            return ru_tab
        ratio = (id_tgt_m / id_in_m) ** 2
        return float(max(1e-9, min(0.999999, 1.0 - ratio)))
    id_prev_m = float(id_in_m * ((1.0 - ru_tab) ** (0.5 * float(n_passes - 1))))
    if id_prev_m <= 1e-18:
        return ru_tab
    if id_tgt_m >= id_prev_m:
        return ru_tab
    ratio_sq = float(id_tgt_m / id_prev_m) ** 2
    r_last = 1.0 - ratio_sq
    return float(max(1e-9, min(0.999999, r_last)))


def final_pass_r_after_uniform_prefix_for_target_od(
    od_in_m: float,
    od_tgt_m: float,
    n_passes: int,
    uniform_r: float,
) -> float:
    """
    After ``n_passes - 1`` draws each using ``uniform_r``, return area-reduction ``r`` for the last pass
    so final outer diameter equals ``od_tgt_m`` exactly (same proportional OD/ID scaling as the engine).

    Used when bore ID is not the controlled output (e.g. sink / rodless drawing).
    Prefix reductions are quantized like the schedule table before predicting pre-last-pass OD.
    """
    ru = max(1e-9, min(0.999999, float(uniform_r)))
    ru_tab = _quantize_area_reduction_for_fit_schedule_tab(ru)
    if n_passes <= 1:
        if od_in_m <= 0:
            return ru_tab
        ratio = (od_tgt_m / od_in_m) ** 2
        return float(max(1e-9, min(0.999999, 1.0 - ratio)))
    od_prev_m = float(od_in_m * ((1.0 - ru_tab) ** (0.5 * float(n_passes - 1))))
    if od_prev_m <= 1e-18:
        return ru_tab
    if od_tgt_m >= od_prev_m - 1e-18:
        return ru_tab
    ratio_sq = float(od_tgt_m / od_prev_m) ** 2
    r_last = 1.0 - ratio_sq
    return float(max(1e-9, min(0.999999, r_last)))


def simulate_schedule(
    g0: TubeGeometry,
    mat: Any,
    passes: List[PassInput],
    *,
    grain_backend: Literal["analytical", "damask"] = "analytical",
) -> Tuple[List[TubeGeometry], List[SchedulePassResult], float, str]:
    """
    Run the analytical engine on ``passes``; return geometry chain (mm axes), pass results, and
    cumulative sum of true strain (dimensionless).

    When ``grain_backend`` is ``"damask"``, per-pass ``grain_size_um`` (and stress when available)
    are taken from a DAMASK grid run when ``DAMASK_grid`` is on PATH; otherwise grain falls back
    to the analytical refinement law.
    """
    mat_obj = mat if isinstance(mat, MetalMaterial) else material_from_preset_label(str(mat or ""))
    od_mm = g0.outer_diameter_m * 1000.0
    id_mm = g0.inner_diameter_m * 1000.0
    dict_passes = [
        {
            "r": float(p.area_reduction_fraction),
            "alpha_deg": float(p.semi_die_angle_deg),
            "mu": float(p.friction_mu),
        }
        for p in passes
    ]
    eng = TubingMasterEngine.from_material(mat_obj)
    metrics, _, _ = eng.run_pass_schedule_on_annulus(od_mm, id_mm, dict_passes)
    traj = annulus_od_id_trajectory(od_mm, id_mm, dict_passes)
    geoms = [
        tube_from_od_id_m(traj[i][0] / 1000.0, traj[i][1] / 1000.0) for i in range(len(traj))
    ]
    results: List[SchedulePassResult] = []
    for m in metrics:
        pull_pa = float(m.pulling_stress_mpa) * 1e6
        sf_vs_uts = float(m.safety_factor_vs_uts)
        results.append(
            SchedulePassResult(
                von_mises_equiv_pa=pull_pa,
                safety_factor_vs_uts=sf_vs_uts,
                grain_size_um=float(m.grain_um),
                broken_risk_score=max(0.0, 1.0 - sf_vs_uts),
                grain_source="analytical",
                unloading_stress_mpa=m.unloading_stress_mpa,
                springback_strain=m.springback_strain,
                permanent_strain=m.permanent_strain,
                residual_stress_mpa=m.residual_stress_mpa,
                hysteresis_mpa=m.hysteresis_mpa,
            )
        )
    cum_strain = sum(float(x.true_strain) for x in metrics)
    if mat_obj.is_nitinol():
        grain_note = f"Grain: weak refinement (SMA). {mat_obj.model_description()}"
    else:
        grain_note = "Grain: analytical refinement law."
    if grain_backend == "damask" and results:
        from tubing_master.damask_support import run_schedule_grain_with_fallback

        grain_rows, grain_note = run_schedule_grain_with_fallback(
            dict_passes,
            prefer_damask=True,
            initial_grain_um=float(mat_obj.initial_grain_um),
        )
        merged: List[SchedulePassResult] = []
        for i, base in enumerate(results):
            g = grain_rows[i] if i < len(grain_rows) else None
            vm = float(base.von_mises_equiv_pa)
            if g is not None and float(g.von_mises_pa) > 0.0:
                vm = float(g.von_mises_pa)
            gu = float(g.grain_size_um) if g is not None else float(base.grain_size_um)
            src = str(g.source) if g is not None else "analytical_fallback"
            merged.append(
                SchedulePassResult(
                    von_mises_equiv_pa=vm,
                    safety_factor_vs_uts=float(base.safety_factor_vs_uts),
                    grain_size_um=gu,
                    broken_risk_score=float(base.broken_risk_score),
                    grain_source=src,
                    unloading_stress_mpa=base.unloading_stress_mpa,
                    springback_strain=base.springback_strain,
                    permanent_strain=base.permanent_strain,
                    residual_stress_mpa=base.residual_stress_mpa,
                    hysteresis_mpa=base.hysteresis_mpa,
                )
            )
        results = merged
    return geoms, results, cum_strain, grain_note


def equal_area_reduction_per_pass_fraction(
    n_passes: int,
    in_od_mm: float,
    in_id_mm: float,
    target_od_mm: float,
    target_id_mm: float,
) -> float:
    """Uniform per-pass area-reduction fraction r so that (1-r)^n ≈ A_target / A_incoming."""
    if n_passes <= 0:
        return 0.15
    a0 = annulus_area_mm2(in_od_mm, in_id_mm)
    a1 = annulus_area_mm2(target_od_mm, target_id_mm)
    if a0 <= 0:
        return 0.15
    ratio = max(1e-15, min(1.0, a1 / a0))
    r = 1.0 - (ratio ** (1.0 / float(n_passes)))
    return float(max(1e-6, min(0.999999, r)))
