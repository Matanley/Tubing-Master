"""Optuna schedule search using axisymmetric tube/die FEA objective (FEA tab only)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Tuple

import numpy as np

from tubing_master.engine import PassInput, simulate_schedule
from tubing_master.fea_hybrid import HYBRID_FEA_TOP_K, verify_top_schedules_fea
from tubing_master.dolfinx_sim import dolfinx_available
from tubing_master.optimization import (
    OPT_SCHEDULE_MAX_PASSES,
    OptimizationConfig,
    passes_from_optuna_params,
    top_k_pass_schedules_from_study,
)

if TYPE_CHECKING:
    from tubing_master.geometry import TubeGeometry
    from tubing_master.materials import MetalMaterial


def _fea_schedule_objective_value(
    g0: "TubeGeometry",
    mat: "MetalMaterial",
    passes: List[PassInput],
) -> float:
    """Lower is better: max von Mises (Pa) over passes from tube/die FEA."""
    from tubing_master.fea_hybrid import verify_pass_schedule_fea

    row = verify_pass_schedule_fea(g0, mat, passes)
    if not row.ok:
        return 1e18
    return float(row.fea_score)


def optimize_multi_pass_schedule_fea(
    g0: "TubeGeometry",
    mat: "MetalMaterial",
    cfg: OptimizationConfig,
    *,
    seed: Optional[int] = None,
) -> Tuple[List[PassInput], Any]:
    """
    Optuna on per-pass area reductions; each trial scored by full tube/die FEA chain.
    Slow — intended for the FEA tab with modest trial counts.
    """
    if not dolfinx_available():
        raise RuntimeError("dolfinx is required for FEA optimization. Install via conda-forge.")

    try:
        import optuna
    except ImportError as e:
        raise RuntimeError("Install optuna to use FEA optimization.") from e

    n_passes = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(cfg.n_passes)))
    R_tot = float(np.clip(cfg.target_area_reduction_total, 1e-6, 0.999))
    phi = 1.0 - R_tot

    def objective(trial: "optuna.Trial") -> float:
        if cfg.fixed_semi_die_angle_deg is not None:
            alphas = [float(cfg.fixed_semi_die_angle_deg)] * n_passes
        else:
            alphas = [
                trial.suggest_float(f"alpha_{i}", cfg.min_semi_die_deg, cfg.max_semi_die_deg)
                for i in range(n_passes)
            ]
        if cfg.fixed_friction_mu is not None:
            mus = [float(cfg.fixed_friction_mu)] * n_passes
        else:
            mus = [trial.suggest_float(f"mu_{i}", cfg.min_mu, cfg.max_mu) for i in range(n_passes)]

        rs: List[float] = []
        prod = 1.0
        for i in range(n_passes - 1):
            ri = trial.suggest_float(f"r_{i}", cfg.min_per_pass_r, cfg.max_per_pass_r)
            rs.append(ri)
            prod *= 1.0 - ri
        if prod <= 1e-12:
            return 1e18
        rn = 1.0 - phi / prod
        if rn < cfg.min_per_pass_r or rn > cfg.max_per_pass_r:
            return 1e18
        rs.append(rn)

        passes = [
            PassInput(
                semi_die_angle_deg=float(alphas[i]),
                friction_mu=float(mus[i]),
                area_reduction_fraction=float(rs[i]),
            )
            for i in range(n_passes)
        ]
        return _fea_schedule_objective_value(g0, mat, passes)

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=int(cfg.n_trials))

    best_passes = passes_from_optuna_params(dict(study.best_params), cfg, phi=phi)
    if not best_passes:
        raise RuntimeError("FEA Optuna best trial could not be decoded.")
    return best_passes, study


def fea_verify_top_analytical_schedules(
    g0: "TubeGeometry",
    mat: "MetalMaterial",
    cfg: OptimizationConfig,
    study: Any,
    *,
    phi: float,
    k: int = HYBRID_FEA_TOP_K,
):
    """After analytical Optuna on the FEA tab: FEA-check top *k* trials."""
    candidates = top_k_pass_schedules_from_study(study, cfg, phi=phi, k=k)
    return verify_top_schedules_fea(g0, mat, candidates)
