"""Safety factor vs UTS uses one convention everywhere (UTS / pulling_stress)."""

from __future__ import annotations

from tubing_master.engine import TubingMasterEngine, simulate_schedule
from tubing_master.geometry import tube_from_od_id_m
from tubing_master.grain_evolution import summarize_schedule
from tubing_master.engine import PassInput


def test_safety_factor_vs_uts_is_uts_over_pull():
    eng = TubingMasterEngine()
    pull, _, over_stressed, sf = eng.calculate_pulling_stress_and_safety(12.0, 0.10)
    uts = eng.base_uts + 800.0 * (0.10**0.7)
    assert abs(sf - uts / pull) < 1e-9
    assert over_stressed == (sf < 1.0)


def test_pass_result_matches_schedule_result():
    g0 = tube_from_od_id_m(0.012, 0.010)
    passes = [PassInput(semi_die_angle_deg=12.0, friction_mu=0.06, area_reduction_fraction=0.10)]
    _, results, _, _ = simulate_schedule(g0, None, passes)
    eng = TubingMasterEngine()
    metrics, _, _ = eng.run_pass_schedule_on_annulus(12.0, 10.0, [{"r": 0.10, "alpha_deg": 12.0}])
    assert len(results) == 1
    assert abs(results[0].safety_factor_vs_uts - metrics[0].safety_factor_vs_uts) < 1e-9
    assert results[0].broken_risk_score == max(0.0, 1.0 - results[0].safety_factor_vs_uts)


def test_summarize_schedule_exports_sf_vs_uts():
    project = {
        "in_od_mm": 12.0,
        "in_id_mm": 10.0,
        "pass_schedule": {"passes": [{"r": 0.10, "alpha_deg": 12.0, "mu": 0.06}]},
    }
    summary = summarize_schedule(project)
    row = summary["pass_metrics"][0]
    assert "safety_factor_vs_uts" in row
    assert row["safety_factor_vs_uts"] > 1.0
