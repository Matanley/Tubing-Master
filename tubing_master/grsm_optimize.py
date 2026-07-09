"""GRSM-style schedule optimization with dolfinx FEA true evaluations.

Mirrors Altair HyperStudy Global Response Surface Method (GRSM) ideas:

1. Latin-hypercube initial designs in the pass-schedule parameter space
2. True evaluation via axisymmetric tube/die FEA (dolfinx)
3. Inverse-distance response surface (cheap surrogate)
4. Each iteration: optimize the surrogate + add global exploration points
5. Re-evaluate new points with FEA and update the surface

Uses far fewer FEA runs than Optuna-on-every-trial while targeting global optima.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from tubing_master.dolfinx_sim import dolfinx_available
from tubing_master.engine import PassInput
from tubing_master.fea_hybrid import verify_pass_schedule_fea
from tubing_master.optimization import OptimizationConfig, passes_from_optuna_params

if TYPE_CHECKING:
    from tubing_master.geometry import TubeGeometry
    from tubing_master.materials import MetalMaterial

FeaEvaluator = Callable[["TubeGeometry", "MetalMaterial", List[PassInput]], float]


@dataclass
class GrsmConfig:
    """Budget and sampling controls for :func:`optimize_multi_pass_schedule_grsm`."""

    initial_samples: int = 8
    points_per_iteration: int = 4
    n_iterations: int = 3
    # Fraction of each iteration's points drawn globally (rest from surrogate opt).
    global_fraction: float = 0.5
    seed: Optional[int] = None


@dataclass
class GrsmResult:
    """Best schedule found plus diagnostics for the FEA log panel."""

    best_passes: List[PassInput]
    best_fea_score: float
    n_fea_evaluations: int
    history: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""


def grsm_config_from_fea_budget(n_trials: int, *, seed: Optional[int] = None) -> GrsmConfig:
    """Map the FEA-tab trial spinbox to a GRSM sample budget."""
    budget = max(6, int(n_trials))
    initial = max(4, min(12, budget // 2))
    remaining = max(2, budget - initial)
    n_iter = max(1, min(4, remaining // 2))
    per_iter = max(2, remaining // n_iter)
    return GrsmConfig(
        initial_samples=initial,
        points_per_iteration=per_iter,
        n_iterations=n_iter,
        global_fraction=0.5,
        seed=seed,
    )


def _bounds(cfg: OptimizationConfig) -> Tuple[np.ndarray, np.ndarray]:
    """Lower/upper bounds for the free design vector (same order as encode/decode)."""
    n = max(1, int(cfg.n_passes))
    lo: List[float] = []
    hi: List[float] = []
    for _ in range(max(0, n - 1)):
        lo.append(float(cfg.min_per_pass_r))
        hi.append(float(cfg.max_per_pass_r))
    if cfg.fixed_semi_die_angle_deg is None:
        for _ in range(n):
            lo.append(float(cfg.min_semi_die_deg))
            hi.append(float(cfg.max_semi_die_deg))
    if cfg.fixed_friction_mu is None:
        for _ in range(n):
            lo.append(float(cfg.min_mu))
            hi.append(float(cfg.max_mu))
    if not lo:
        # Single-pass with fixed α/μ: no free vars — use a dummy unit interval.
        return np.array([0.0]), np.array([1.0])
    return np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)


def design_to_params(x: Sequence[float], cfg: OptimizationConfig) -> Dict[str, Any]:
    """Map free design vector → Optuna-style param dict (last r from φ)."""
    n = max(1, int(cfg.n_passes))
    vals = [float(v) for v in x]
    params: Dict[str, Any] = {}
    idx = 0
    for i in range(max(0, n - 1)):
        params[f"r_{i}"] = vals[idx] if idx < len(vals) else float(cfg.min_per_pass_r)
        idx += 1
    if cfg.fixed_semi_die_angle_deg is None:
        for i in range(n):
            params[f"alpha_{i}"] = vals[idx] if idx < len(vals) else float(cfg.min_semi_die_deg)
            idx += 1
    if cfg.fixed_friction_mu is None:
        for i in range(n):
            params[f"mu_{i}"] = vals[idx] if idx < len(vals) else float(cfg.min_mu)
            idx += 1
    return params


def params_to_design(params: Dict[str, Any], cfg: OptimizationConfig) -> np.ndarray:
    """Inverse of :func:`design_to_params` (free variables only)."""
    n = max(1, int(cfg.n_passes))
    vals: List[float] = []
    for i in range(max(0, n - 1)):
        vals.append(float(params[f"r_{i}"]))
    if cfg.fixed_semi_die_angle_deg is None:
        for i in range(n):
            vals.append(float(params[f"alpha_{i}"]))
    if cfg.fixed_friction_mu is None:
        for i in range(n):
            vals.append(float(params[f"mu_{i}"]))
    if not vals:
        return np.array([0.5], dtype=float)
    return np.asarray(vals, dtype=float)


def latin_hypercube(n_samples: int, n_dim: int, rng: np.random.Generator) -> np.ndarray:
    """Unit-cube Latin hypercube samples, shape ``(n_samples, n_dim)``."""
    n_samples = max(1, int(n_samples))
    n_dim = max(1, int(n_dim))
    cut = np.linspace(0.0, 1.0, n_samples + 1)
    u = rng.uniform(size=(n_samples, n_dim))
    a = cut[:n_samples]
    b = cut[1 : n_samples + 1]
    pts = a[:, None] + (b - a)[:, None] * u
    for d in range(n_dim):
        rng.shuffle(pts[:, d])
    return pts


def map_unit_to_bounds(u: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return lo + u * (hi - lo)


def _idw_predict(
    X: np.ndarray,
    y: np.ndarray,
    xq: np.ndarray,
    *,
    power: float = 2.0,
    eps: float = 1e-12,
) -> float:
    """Inverse-distance weighted prediction at ``xq``."""
    d = np.linalg.norm(X - xq[None, :], axis=1)
    if float(np.min(d)) < eps:
        return float(y[int(np.argmin(d))])
    w = 1.0 / np.maximum(d, eps) ** power
    return float(np.dot(w, y) / np.sum(w))


def _surrogate_minimize(
    X: np.ndarray,
    y: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    rng: np.random.Generator,
    *,
    n_candidates: int = 200,
) -> np.ndarray:
    """Cheap global search on the IDW surface (random + local refine)."""
    best_x = X[int(np.argmin(y))].copy()
    best_y = float(np.min(y))
    u = rng.random((max(20, n_candidates), lo.size))
    cand = map_unit_to_bounds(u, lo, hi)
    for row in cand:
        pred = _idw_predict(X, y, row)
        if pred < best_y:
            best_y = pred
            best_x = row.copy()
    for center in (X[int(np.argmin(y))], best_x):
        span = hi - lo
        for _ in range(40):
            step = rng.normal(0.0, 0.08, size=lo.size) * span
            trial = np.clip(center + step, lo, hi)
            pred = _idw_predict(X, y, trial)
            if pred < best_y:
                best_y = pred
                best_x = trial.copy()
    return best_x


def _default_fea_evaluator(
    g0: "TubeGeometry",
    mat: "MetalMaterial",
    passes: List[PassInput],
) -> float:
    row = verify_pass_schedule_fea(g0, mat, passes)
    if not row.ok:
        return 1e18
    return float(row.fea_score)


def _is_duplicate(x: np.ndarray, X: np.ndarray, *, tol: float = 1e-4) -> bool:
    if X.size == 0:
        return False
    return bool(np.any(np.linalg.norm(X - x[None, :], axis=1) < tol))


def optimize_multi_pass_schedule_grsm(
    g0: "TubeGeometry",
    mat: "MetalMaterial",
    cfg: OptimizationConfig,
    grsm: Optional[GrsmConfig] = None,
    *,
    evaluator: Optional[FeaEvaluator] = None,
) -> GrsmResult:
    """
    GRSM-style search: DOE → FEA → surrogate → iterate.

    ``evaluator`` defaults to dolfinx tube/die FEA. Inject a cheap callable in tests.
    """
    if evaluator is None:
        if not dolfinx_available():
            raise RuntimeError(
                "dolfinx is required for GRSM + FEA. Install via conda-forge, "
                "or pass a custom evaluator= for testing."
            )
        evaluator = _default_fea_evaluator

    gcfg = grsm or grsm_config_from_fea_budget(int(cfg.n_trials), seed=None)
    phi = 1.0 - float(np.clip(cfg.target_area_reduction_total, 1e-6, 0.999))
    lo, hi = _bounds(cfg)
    dim = int(lo.size)
    rng = np.random.default_rng(gcfg.seed)

    def evaluate_x(x: np.ndarray) -> Tuple[Optional[List[PassInput]], float]:
        params = design_to_params(x, cfg)
        passes = passes_from_optuna_params(params, cfg, phi=phi)
        if passes is None:
            return None, 1e18
        score = float(evaluator(g0, mat, passes))
        return passes, score

    n0 = max(1, int(gcfg.initial_samples))
    U0 = latin_hypercube(n0, dim, rng)
    X_list: List[np.ndarray] = []
    y_list: List[float] = []
    passes_list: List[Optional[List[PassInput]]] = []
    history: List[Dict[str, Any]] = []

    for i, u in enumerate(U0):
        x = map_unit_to_bounds(u, lo, hi)
        passes, score = evaluate_x(x)
        X_list.append(x)
        y_list.append(score)
        passes_list.append(passes)
        history.append({"phase": "initial", "index": i, "fea_score": score, "ok": score < 1e17})

    X = np.vstack(X_list)
    y = np.asarray(y_list, dtype=float)

    n_global = max(1, int(round(float(gcfg.global_fraction) * float(gcfg.points_per_iteration))))
    n_local = max(0, int(gcfg.points_per_iteration) - n_global)

    for it in range(max(0, int(gcfg.n_iterations))):
        new_points: List[np.ndarray] = []
        for _ in range(n_local):
            x_s = _surrogate_minimize(X, y, lo, hi, rng)
            if not _is_duplicate(x_s, X) and not any(
                np.linalg.norm(x_s - p) < 1e-4 for p in new_points
            ):
                new_points.append(x_s)
        for _ in range(n_global):
            u = rng.random(dim)
            x_g = map_unit_to_bounds(u, lo, hi)
            if _is_duplicate(x_g, X):
                continue
            if any(np.linalg.norm(x_g - p) < 1e-4 for p in new_points):
                continue
            new_points.append(x_g)

        attempts = 0
        while len(new_points) < int(gcfg.points_per_iteration) and attempts < 40:
            attempts += 1
            u = rng.random(dim)
            x_g = map_unit_to_bounds(u, lo, hi)
            if not _is_duplicate(x_g, X) and not any(
                np.linalg.norm(x_g - p) < 1e-4 for p in new_points
            ):
                new_points.append(x_g)

        for j, x_new in enumerate(new_points[: int(gcfg.points_per_iteration)]):
            passes, score = evaluate_x(x_new)
            X = np.vstack([X, x_new[None, :]])
            y = np.append(y, score)
            passes_list.append(passes)
            history.append(
                {
                    "phase": "iteration",
                    "iteration": it + 1,
                    "index": j,
                    "fea_score": score,
                    "ok": score < 1e17,
                }
            )

    finite = np.isfinite(y) & (y < 1e17)
    if not np.any(finite):
        raise RuntimeError(
            "GRSM completed but no feasible FEA schedule was found. "
            "Try more samples, relax geometry, or check dolfinx."
        )
    best_i = int(np.argmin(np.where(finite, y, np.inf)))
    best_passes = passes_list[best_i]
    if best_passes is None:
        raise RuntimeError("GRSM best design could not be decoded into passes.")

    return GrsmResult(
        best_passes=list(best_passes),
        best_fea_score=float(y[best_i]),
        n_fea_evaluations=int(y.size),
        history=history,
        message=(
            f"GRSM: {int(y.size)} FEA evals, "
            f"best max σ_vm={float(y[best_i]) / 1e6:.1f} MPa"
        ),
    )
