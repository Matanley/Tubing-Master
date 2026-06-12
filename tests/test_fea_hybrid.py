"""FEA tube/die helpers (no dolfinx required for geometry tests)."""

from __future__ import annotations

import math

from tubing_master.engine import PassInput
from tubing_master.fea_hybrid import HYBRID_FEA_TOP_K
from tubing_master.fea_tube_die import (
    od_id_after_area_reduction_fixed_bore,
    tube_die_pass_geometry,
)
from tubing_master.geometry import tube_from_od_id_m
from tubing_master.optimization import OptimizationConfig, passes_from_optuna_params


def test_od_id_after_area_reduction_fixed_bore():
    od_out, id_out = od_id_after_area_reduction_fixed_bore(0.010, 0.008, 0.12)
    assert id_out == 0.008
    assert od_out < 0.010


def test_tube_die_pass_geometry_sizes():
    geo = tube_die_pass_geometry(0.010, 0.008, 0.12, 12.0)
    assert geo["r_inner_m"] > 0
    assert geo["r_outer_m"] > geo["r_inner_m"]
    assert geo["length_m"] >= geo["reduction_zone_m"]
    assert geo["delta_r_outer_m"] > 0


def test_passes_from_optuna_params_fixed_die():
    cfg = OptimizationConfig(
        n_passes=3,
        target_area_reduction_total=0.35,
        min_per_pass_r=0.02,
        max_per_pass_r=0.35,
        min_semi_die_deg=6.0,
        max_semi_die_deg=18.0,
        min_mu=0.02,
        max_mu=0.12,
        min_margin_uts=1.15,
        n_trials=10,
        fixed_semi_die_angle_deg=12.0,
        fixed_friction_mu=0.06,
    )
    phi = 1.0 - 0.35
    params = {"r_0": 0.10, "r_1": 0.12}
    passes = passes_from_optuna_params(params, cfg, phi=phi)
    assert passes is not None
    assert len(passes) == 3
    prod = math.prod(1.0 - p.area_reduction_fraction for p in passes)
    assert abs(prod - phi) < 1e-4


def test_hybrid_top_k_constant():
    assert HYBRID_FEA_TOP_K == 5
