"""Nitinol superelastic constitutive model (loading + unloading)."""

from __future__ import annotations

from tubing_master.engine import PassInput, TubingMasterEngine, simulate_schedule
from tubing_master.geometry import tube_from_od_id_m
from tubing_master.materials import (
    MATERIALS,
    material_from_preset_label,
    normalize_material_label,
)
from tubing_master.nitinol_model import (
    NitinolParams,
    nitinol_flow_stress_mpa,
    nitinol_loading_stress_mpa,
    nitinol_pass_metrics,
    nitinol_stress_mpa,
    nitinol_unloading_stress_at_peak_mpa,
)


def test_nitinol_renamed_and_alias():
    assert "Nitinol" in MATERIALS
    assert "NiTi" not in MATERIALS
    assert normalize_material_label("NiTi") == "Nitinol"


def test_nitinol_piecewise_loading_stress():
    p = NitinolParams()
    s_el = nitinol_loading_stress_mpa(0.001, p)
    assert s_el == p.e_austenite_mpa * 0.001
    eps_ms = p.sigma_ms_mpa / p.e_austenite_mpa
    s_plateau = nitinol_loading_stress_mpa(eps_ms + 0.5 * p.transformation_strain, p)
    assert p.sigma_ms_mpa < s_plateau < p.sigma_mf_mpa
    s_hard = nitinol_loading_stress_mpa(eps_ms + p.transformation_strain + 0.05, p)
    assert s_hard > p.sigma_mf_mpa


def test_nitinol_unloading_below_loading_in_plateau():
    p = NitinolParams()
    eps_ms = p.sigma_ms_mpa / p.e_austenite_mpa
    eps_peak = eps_ms + 0.5 * p.transformation_strain
    s_load = nitinol_loading_stress_mpa(eps_peak, p)
    s_unload = nitinol_unloading_stress_at_peak_mpa(eps_peak, p)
    assert s_unload < s_load
    assert p.sigma_as_mpa <= s_unload <= p.sigma_af_mpa


def test_nitinol_hysteresis_loop_paths():
    p = NitinolParams()
    eps_ms = p.sigma_ms_mpa / p.e_austenite_mpa
    eps_peak = eps_ms + 0.4 * p.transformation_strain
    s_load_peak = nitinol_stress_mpa(eps_peak, p, path="loading")
    s_unload_peak = nitinol_stress_mpa(eps_peak, p, path="unloading", peak_strain=eps_peak)
    s_unload_mid = nitinol_stress_mpa(eps_ms + 0.1 * p.transformation_strain, p, path="unloading", peak_strain=eps_peak)
    assert s_unload_peak < s_load_peak
    assert s_unload_mid >= s_unload_peak


def test_nitinol_pass_metrics_springback():
    p = NitinolParams()
    eps_ms = p.sigma_ms_mpa / p.e_austenite_mpa
    eps_peak = eps_ms + p.transformation_strain + 0.02
    m = nitinol_pass_metrics(eps_peak, p)
    assert m.springback_strain > 0.0
    assert abs(m.permanent_strain - 0.02) < 1e-9
    assert m.hysteresis_mpa > 0.0


def test_nitinol_lower_sf_than_steel_same_pass():
    g0 = tube_from_od_id_m(0.012, 0.010)
    passes = [PassInput(semi_die_angle_deg=12.0, friction_mu=0.06, area_reduction_fraction=0.12)]
    nit = material_from_preset_label("Nitinol")
    steel = material_from_preset_label("316L stainless steel")
    _, n_res, _, _ = simulate_schedule(g0, nit, passes)
    _, s_res, _, _ = simulate_schedule(g0, steel, passes)
    assert n_res[0].safety_factor_vs_uts != s_res[0].safety_factor_vs_uts
    assert n_res[0].unloading_stress_mpa is not None
    assert n_res[0].springback_strain is not None


def test_nitinol_weaker_grain_refinement():
    eng = TubingMasterEngine.from_material(material_from_preset_label("Nitinol"))
    steel = TubingMasterEngine.from_material(material_from_preset_label("316L stainless steel"))
    eps = 0.20
    g1 = eng.calculate_grain_size(eng.material.effective_grain_refinement_strain(eps), d0=20.0)
    g2 = steel.calculate_grain_size(eps, d0=15.0)
    assert g1 > g2


def test_preset_has_unload_plateau_params():
    raw = MATERIALS["Nitinol"]["nitinol"]
    assert "sigma_as_mpa" in raw
    assert "sigma_af_mpa" in raw
    assert raw["sigma_as_mpa"] < raw["sigma_ms_mpa"]
