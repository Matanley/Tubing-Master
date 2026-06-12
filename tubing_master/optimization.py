"""Optuna search — single-pass (notebook) and multi-pass schedule optimization."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from tubing_master.engine import (
    PassInput,
    SchedulePassResult,
    TubingMasterEngine,
    simulate_schedule,
)

if TYPE_CHECKING:
    from tubing_master.geometry import TubeGeometry
    from tubing_master.materials import MetalMaterial

# Defaults shared by UI preview and :func:`optimize_multi_pass_schedule` callers.
OPT_SCHEDULE_MIN_PER_PASS_R = 0.02
OPT_SCHEDULE_MAX_PER_PASS_R = 0.35
OPT_SCHEDULE_MAX_PASSES = 64
# Upper bound on per-pass bite when *counting* stations / table rows (tooling is planned in smaller steps than absolute max r).
OPT_SCHEDULE_PLANNING_PER_PASS_R = 0.14


def recommended_pass_count(
    *,
    area_ratio_target_to_inlet: float,
    max_per_pass_r: float = OPT_SCHEDULE_MAX_PER_PASS_R,
    min_per_pass_r: float = OPT_SCHEDULE_MIN_PER_PASS_R,
    min_margin_uts: float = 1.15,
    max_passes_cap: int = OPT_SCHEDULE_MAX_PASSES,
) -> Tuple[int, bool]:
    """
    Estimate how many passes are needed so each step stays near an effective max reduction.

    ``area_ratio_target_to_inlet`` is φ = A_target / A_in (annulus areas). Higher required
    safety margin vs UTS reduces the effective per-pass bite (max r / margin), which increases *n*.
    The bite used for counting is also capped by :data:`OPT_SCHEDULE_PLANNING_PER_PASS_R` so modest
    reductions still allocate multiple die stations instead of a single maximum bite.
    Returns ``(n, hit_cap)`` where *hit_cap* is True if *n* was limited by ``max_passes_cap``.
    """
    phi = float(area_ratio_target_to_inlet)
    if phi >= 1.0 - 1e-15:
        return 1, False
    if phi <= 0.0:
        return max_passes_cap, True
    margin = max(1.0, float(min_margin_uts))
    raw_cap = min(float(max_per_pass_r), float(max_per_pass_r) / margin)
    raw_cap = min(raw_cap, float(OPT_SCHEDULE_PLANNING_PER_PASS_R))
    r_cap = max(float(min_per_pass_r), min(float(max_per_pass_r), raw_cap))
    one_minus = 1.0 - r_cap
    if one_minus <= 1e-15 or one_minus >= 1.0:
        return max_passes_cap, True
    n_unc = int(math.ceil(math.log(phi) / math.log(one_minus)))
    n_unc = max(1, n_unc)
    hit = n_unc > max_passes_cap
    n = min(max_passes_cap, n_unc)
    return n, hit


def optimize_die_and_reduction(
    n_trials: int = 50,
    seed: Optional[int] = None,
) -> Tuple[float, float, Dict[str, Any]]:
    """Return (best_angle_deg, best_reduction_fraction, study_summary)."""
    try:
        import optuna
    except ImportError as e:
        raise RuntimeError("Install optuna to use optimization.") from e

    engine = TubingMasterEngine()

    def objective(trial: "optuna.Trial") -> float:
        angle = trial.suggest_float("die_angle", 3.0, 18.0)
        red = trial.suggest_float("reduction", 0.05, 0.40)
        pull_stress, _, over_stressed, _ = engine.calculate_pulling_stress_and_safety(angle, red)
        if over_stressed:
            return 99999.0
        true_strain = float(np.log(1.0 / (1.0 - red)))
        estimated_grain = engine.calculate_grain_size(true_strain, d0=15.0)
        return float(pull_stress * 0.01 + estimated_grain * 1.0)

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=n_trials)
    best_angle = float(study.best_params["die_angle"])
    best_red = float(study.best_params["reduction"])
    summary: Dict[str, Any] = {"n_trials": n_trials, "best_value": study.best_value}
    return best_angle, best_red, summary


@dataclass
class OptimizationConfig:
    """Bounds and targets for :func:`optimize_multi_pass_schedule`."""

    n_passes: int
    target_area_reduction_total: float
    min_per_pass_r: float
    max_per_pass_r: float
    min_semi_die_deg: float
    max_semi_die_deg: float
    min_mu: float
    max_mu: float
    min_margin_uts: float
    n_trials: int
    # When set, Optuna varies only per-pass area reduction (shop die angle + lubricant fixed).
    fixed_semi_die_angle_deg: Optional[float] = None
    fixed_friction_mu: Optional[float] = None


def passes_from_optuna_params(
    params: Dict[str, Any],
    cfg: OptimizationConfig,
    *,
    phi: float,
) -> Optional[List[PassInput]]:
    """
    Decode one Optuna trial's parameters into a feasible :class:`PassInput` list.

    Returns ``None`` if the last-pass area reduction is outside bounds.
    """
    n_passes = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(cfg.n_passes)))
    if cfg.fixed_semi_die_angle_deg is not None:
        alphas = [float(cfg.fixed_semi_die_angle_deg)] * n_passes
    else:
        alphas = [float(params[f"alpha_{i}"]) for i in range(n_passes)]
    if cfg.fixed_friction_mu is not None:
        mus = [float(cfg.fixed_friction_mu)] * n_passes
    else:
        mus = [float(params[f"mu_{i}"]) for i in range(n_passes)]
    rs: List[float] = []
    prod = 1.0
    for i in range(n_passes - 1):
        rs.append(float(params[f"r_{i}"]))
        prod *= 1.0 - rs[-1]
    if prod <= 1e-12:
        return None
    rn = 1.0 - float(phi) / prod
    if rn < cfg.min_per_pass_r or rn > cfg.max_per_pass_r:
        return None
    rs.append(float(rn))
    return [
        PassInput(
            semi_die_angle_deg=float(alphas[i]),
            friction_mu=float(mus[i]),
            area_reduction_fraction=float(rs[i]),
        )
        for i in range(n_passes)
    ]


def top_k_pass_schedules_from_study(
    study: Any,
    cfg: OptimizationConfig,
    *,
    phi: float,
    k: int = 5,
) -> List[Tuple[int, int, float, List[PassInput]]]:
    """
    Top *k* completed Optuna trials by analytical objective (ascending).

    Returns ``(analytical_rank, trial_number, objective, passes)``.
    """
    try:
        import optuna

        complete = optuna.trial.TrialState.COMPLETE
    except ImportError:
        return []

    trials = [
        t
        for t in study.trials
        if t.state == complete and t.value is not None and float(t.value) < 1e8
    ]
    trials.sort(key=lambda t: float(t.value))
    out: List[Tuple[int, int, float, List[PassInput]]] = []
    for rank, trial in enumerate(trials[: max(1, int(k))], start=1):
        passes = passes_from_optuna_params(dict(trial.params), cfg, phi=phi)
        if passes:
            out.append((rank, int(trial.number), float(trial.value), passes))
    return out


def best_passes_from_study(
    study: Any,
    cfg: OptimizationConfig,
    *,
    phi: float,
) -> Tuple[List[PassInput], int]:
    """
    Decode the best feasible completed Optuna trial into pass rows.

    Returns ``(passes, trial_number)``. Raises :class:`RuntimeError` when no trial decodes.
    """
    try:
        import optuna

        complete = optuna.trial.TrialState.COMPLETE
    except ImportError as e:
        raise RuntimeError("Install optuna to use optimization.") from e

    trials = [
        t
        for t in study.trials
        if t.state == complete and t.value is not None and float(t.value) < 1e8
    ]
    trials.sort(key=lambda t: float(t.value))
    for trial in trials:
        passes = passes_from_optuna_params(dict(trial.params), cfg, phi=phi)
        if passes:
            return passes, int(trial.number)

    n_passes = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(cfg.n_passes)))
    r_tot = 1.0 - float(phi)
    hints: List[str] = []
    if n_passes == 1 and r_tot + 1e-9 < float(cfg.min_per_pass_r):
        hints.append(
            f"Total area reduction ({r_tot:.4f}) is below the minimum per-pass bound "
            f"({cfg.min_per_pass_r:.2f})."
        )
    if float(cfg.min_margin_uts) > 1.2:
        hints.append(
            f"Min safety factor vs UTS ({cfg.min_margin_uts:.2f}) may be too strict for this geometry."
        )
    hint_txt = " ".join(hints) if hints else (
        "Try more trials, relax min safety factor vs UTS, or adjust incoming/target geometry."
    )
    raise RuntimeError(
        "No feasible pass schedule was found. "
        f"{hint_txt}"
    )


def optimize_multi_pass_schedule(
    g0: "TubeGeometry",
    mat: "MetalMaterial",
    cfg: OptimizationConfig,
    *,
    seed: Optional[int] = None,
    trial_callback: Optional[Callable[[Any, Any], None]] = None,
) -> Tuple[List[PassInput], List[SchedulePassResult], Any]:
    """
    Optuna search on per-pass area reductions so cumulative annulus removal matches
    ``cfg.target_area_reduction_total``. Semi-die angle and friction are fixed when
    ``cfg.fixed_semi_die_angle_deg`` / ``cfg.fixed_friction_mu`` are set (typical shop practice).
    Returns ``PassInput`` rows, simulated pass metrics, and the Optuna ``study``.
    """
    try:
        import optuna
    except ImportError as e:
        raise RuntimeError("Install optuna to use optimization.") from e

    in_od_mm = float(g0.outer_diameter_m * 1000.0)
    in_id_mm = float(g0.inner_diameter_m * 1000.0)

    n_passes = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(cfg.n_passes)))
    R_tot = float(np.clip(cfg.target_area_reduction_total, 1e-6, 0.999))
    phi = 1.0 - R_tot
    min_sf_vs_uts = float(max(1.001, cfg.min_margin_uts))

    eng = TubingMasterEngine.from_material(mat)

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
            return 1e9

        rn = 1.0 - phi / prod
        if rn < cfg.min_per_pass_r or rn > cfg.max_per_pass_r:
            return 1e9
        rs.append(rn)

        passes = [{"r": rs[i], "alpha_deg": alphas[i], "mu": mus[i]} for i in range(n_passes)]

        pen = 0.0
        for p in passes:
            _, _, over_stressed, sf_vs_uts = eng.calculate_pulling_stress_and_safety(
                float(p["alpha_deg"]), float(p["r"])
            )
            if over_stressed:
                return 1e9
            if sf_vs_uts < min_sf_vs_uts - 1e-6:
                pen += 5000.0 * (min_sf_vs_uts - sf_vs_uts) ** 2

        try:
            metrics, _, _ = eng.run_pass_schedule_on_annulus(in_od_mm, in_id_mm, passes)
        except Exception:
            return 1e9

        total_pull = sum(m.pulling_stress_mpa for m in metrics)
        final_grain = metrics[-1].grain_um if metrics else 15.0
        base = float(total_pull * 0.01 + final_grain * 0.25)
        return base + pen

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    callbacks = [trial_callback] if trial_callback is not None else None
    study.optimize(objective, n_trials=int(cfg.n_trials), callbacks=callbacks)

    best_passes, _trial_no = best_passes_from_study(study, cfg, phi=phi)
    _, best_results, _, _ = simulate_schedule(g0, mat, best_passes)
    return best_passes, best_results, study


def optimize_multi_pass_schedule_annulus_mm(
    *,
    n_passes: int,
    target_total_area_reduction: float,
    n_trials: int,
    min_reserve_sf_vs_uts: float,
    in_od_mm: float,
    in_id_mm: float,
    seed: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Legacy API: returns dict passes + summary (no geometry objects)."""
    try:
        import optuna
    except ImportError as e:
        raise RuntimeError("Install optuna to use optimization.") from e

    n_passes = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(n_passes)))
    R_tot = float(np.clip(target_total_area_reduction, 1e-6, 0.999))
    phi = 1.0 - R_tot
    min_sf_vs_uts = float(max(1.001, min_reserve_sf_vs_uts))

    eng = TubingMasterEngine()

    fixed_alpha = 12.0
    fixed_mu = 0.06

    def objective(trial: "optuna.Trial") -> float:
        alphas = [fixed_alpha] * n_passes

        rs: List[float] = []
        prod = 1.0
        for i in range(n_passes - 1):
            ri = trial.suggest_float(f"r_{i}", 0.05, 0.42)
            rs.append(ri)
            prod *= 1.0 - ri

        if prod <= 1e-12:
            return 1e9

        rn = 1.0 - phi / prod
        if rn < 0.03 or rn > 0.48:
            return 1e9
        rs.append(rn)

        passes = [{"r": rs[i], "alpha_deg": alphas[i], "mu": fixed_mu} for i in range(n_passes)]

        pen = 0.0
        for p in passes:
            _, _, over_stressed, sf_vs_uts = eng.calculate_pulling_stress_and_safety(
                float(p["alpha_deg"]), float(p["r"])
            )
            if over_stressed:
                return 1e9
            if sf_vs_uts < min_sf_vs_uts - 1e-6:
                pen += 5000.0 * (min_sf_vs_uts - sf_vs_uts) ** 2

        try:
            metrics, _, _ = eng.run_pass_schedule_on_annulus(in_od_mm, in_id_mm, passes)
        except Exception:
            return 1e9

        total_pull = sum(m.pulling_stress_mpa for m in metrics)
        final_grain = metrics[-1].grain_um if metrics else 15.0
        base = float(total_pull * 0.01 + final_grain * 0.25)
        return base + pen

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=n_trials)

    best_p = study.best_params
    alphas = [fixed_alpha] * n_passes
    rs = []
    prod = 1.0
    for i in range(n_passes - 1):
        rs.append(float(best_p[f"r_{i}"]))
        prod *= 1.0 - rs[-1]
    rn = 1.0 - phi / prod
    rs.append(float(rn))

    best_passes = [{"r": rs[i], "alpha_deg": alphas[i], "mu": 0.06} for i in range(n_passes)]

    traj_prod = 1.0
    for p in best_passes:
        traj_prod *= 1.0 - float(p["r"])
    achieved_reduction = 1.0 - traj_prod

    summary: Dict[str, Any] = {
        "n_trials": n_trials,
        "best_value": study.best_value,
        "target_area_reduction": R_tot,
        "achieved_area_reduction_annulus": achieved_reduction,
        "phi_target": phi,
        "min_reserve_sf_vs_uts": min_sf_vs_uts,
    }
    return best_passes, summary
