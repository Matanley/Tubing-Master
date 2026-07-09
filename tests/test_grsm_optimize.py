"""GRSM surrogate optimization helpers (no dolfinx required)."""

from __future__ import annotations

import math

import numpy as np

from tubing_master.engine import PassInput
from tubing_master.geometry import tube_from_od_id_m
from tubing_master.grsm_optimize import (
    GrsmConfig,
    design_to_params,
    grsm_config_from_fea_budget,
    latin_hypercube,
    map_unit_to_bounds,
    optimize_multi_pass_schedule_grsm,
    params_to_design,
)
from tubing_master.materials import MetalMaterial
from tubing_master.optimization import OptimizationConfig, passes_from_optuna_params


def _cfg_3pass() -> OptimizationConfig:
    return OptimizationConfig(
        n_passes=3,
        target_area_reduction_total=0.30,
        min_per_pass_r=0.02,
        max_per_pass_r=0.35,
        min_semi_die_deg=6.0,
        max_semi_die_deg=18.0,
        min_mu=0.02,
        max_mu=0.12,
        min_margin_uts=1.15,
        n_trials=12,
        fixed_semi_die_angle_deg=12.0,
        fixed_friction_mu=0.06,
    )


def test_latin_hypercube_shape_and_coverage():
    rng = np.random.default_rng(0)
    pts = latin_hypercube(8, 3, rng)
    assert pts.shape == (8, 3)
    assert np.all(pts >= 0.0) and np.all(pts <= 1.0)
    # Each dimension should cover distinct strata
    for d in range(3):
        assert len(np.unique(np.round(pts[:, d], 6))) == 8


def test_design_params_roundtrip_fixed_die():
    cfg = _cfg_3pass()
    phi = 1.0 - 0.30
    x = np.array([0.10, 0.12])
    params = design_to_params(x, cfg)
    assert "r_0" in params and "r_1" in params
    assert "alpha_0" not in params
    passes = passes_from_optuna_params(params, cfg, phi=phi)
    assert passes is not None
    assert len(passes) == 3
    x2 = params_to_design(params, cfg)
    assert np.allclose(x, x2)


def test_map_unit_to_bounds():
    lo = np.array([0.0, 10.0])
    hi = np.array([1.0, 20.0])
    u = np.array([0.5, 0.25])
    x = map_unit_to_bounds(u, lo, hi)
    assert abs(x[0] - 0.5) < 1e-12
    assert abs(x[1] - 12.5) < 1e-12


def test_grsm_config_from_fea_budget():
    g = grsm_config_from_fea_budget(12, seed=1)
    assert g.initial_samples >= 4
    assert g.points_per_iteration >= 2
    assert g.n_iterations >= 1
    assert g.seed == 1


def test_grsm_optimize_with_mock_evaluator():
    """Full GRSM loop without dolfinx — mock FEA returns a smooth bowl in r-space."""
    cfg = _cfg_3pass()
    g0 = tube_from_od_id_m(0.012, 0.010)
    mat = MetalMaterial(name="test")
    phi = 1.0 - cfg.target_area_reduction_total

    def mock_eval(_g0, _mat, passes: list[PassInput]) -> float:
        # Prefer mid-range first-pass reduction; penalize extremes.
        r0 = float(passes[0].area_reduction_fraction)
        return float((r0 - 0.10) ** 2 * 1e9 + 2e8)

    gcfg = GrsmConfig(
        initial_samples=6,
        points_per_iteration=3,
        n_iterations=2,
        global_fraction=0.5,
        seed=7,
    )
    result = optimize_multi_pass_schedule_grsm(
        g0, mat, cfg, gcfg, evaluator=mock_eval
    )
    assert result.n_fea_evaluations == 6 + 2 * 3
    assert result.best_passes
    assert len(result.best_passes) == 3
    prod = math.prod(1.0 - p.area_reduction_fraction for p in result.best_passes)
    assert abs(prod - phi) < 1e-4
    assert result.best_fea_score < 1e17
    # Should find something near the mock optimum r0≈0.10
    assert abs(result.best_passes[0].area_reduction_fraction - 0.10) < 0.08
