"""FEA verification for pass schedules (axisymmetric tube/die model)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from tubing_master.dolfinx_sim import dolfinx_available
from tubing_master.engine import PassInput, simulate_schedule
from tubing_master.fea_tube_die import run_schedule_tube_die_fea
from tubing_master.geometry import TubeGeometry
from tubing_master.materials import MetalMaterial

HYBRID_FEA_TOP_K = 5


@dataclass
class FeaPassProbe:
    pass_index: int
    ok: bool
    max_von_mises_pa: float
    message: str = ""


@dataclass
class FeaScheduleVerification:
    rank_analytical: int
    trial_number: int
    analytical_objective: float
    passes: List[PassInput]
    ok: bool
    pass_probes: List[FeaPassProbe] = field(default_factory=list)
    schedule_max_von_mises_pa: float = 0.0
    fea_score: float = 0.0
    message: str = ""


def _schedule_fea_payload(
    g0: TubeGeometry,
    mat: MetalMaterial,
    passes: List[PassInput],
) -> List[dict]:
    geoms, _, _, _ = simulate_schedule(g0, mat, passes)
    rows: List[dict] = []
    for i, p in enumerate(passes):
        g = geoms[i]
        rows.append(
            {
                "od_in_m": float(g.outer_diameter_m),
                "id_in_m": float(g.inner_diameter_m),
                "area_reduction_fraction": float(p.area_reduction_fraction),
                "semi_die_angle_deg": float(p.semi_die_angle_deg),
            }
        )
    return rows


def verify_pass_schedule_fea(
    g0: TubeGeometry,
    mat: MetalMaterial,
    passes: List[PassInput],
    *,
    timeout_s: float = 1200.0,
) -> FeaScheduleVerification:
    """Axisymmetric tube/die FEA for each pass in the schedule."""
    payload = _schedule_fea_payload(g0, mat, passes)
    youngs_pa = float(mat.e_mpa) * 1e6
    raw = run_schedule_tube_die_fea(
        passes=payload, youngs_pa=youngs_pa, nu=0.30, timeout_s=timeout_s
    )
    probes: List[FeaPassProbe] = []
    if not raw.get("ok") and not raw.get("passes"):
        return FeaScheduleVerification(
            rank_analytical=0,
            trial_number=-1,
            analytical_objective=0.0,
            passes=passes,
            ok=False,
            message=str(raw.get("error", "FEA failed")),
            fea_score=float("inf"),
        )
    for i, row in enumerate(raw.get("passes") or []):
        probes.append(
            FeaPassProbe(
                pass_index=i + 1,
                ok=bool(row.get("ok")),
                max_von_mises_pa=float(row.get("max_von_mises_pa", 0.0)),
                message=str(row.get("message", "")),
            )
        )
    sched_max = float(raw.get("schedule_max_von_mises_pa", 0.0))
    all_ok = all(p.ok for p in probes) and len(probes) == len(passes)
    score = sched_max if all_ok else float("inf")
    return FeaScheduleVerification(
        rank_analytical=0,
        trial_number=-1,
        analytical_objective=0.0,
        passes=passes,
        ok=all_ok,
        pass_probes=probes,
        schedule_max_von_mises_pa=sched_max,
        fea_score=score,
        message="Axisymmetric tube/die FEA per pass.",
    )


def verify_top_schedules_fea(
    g0: TubeGeometry,
    mat: MetalMaterial,
    candidates: Sequence[Tuple[int, int, float, List[PassInput]]],
    *,
    timeout_per_schedule_s: float = 1200.0,
) -> List[FeaScheduleVerification]:
    if not dolfinx_available():
        return [
            FeaScheduleVerification(
                rank_analytical=rank,
                trial_number=trial_no,
                analytical_objective=obj,
                passes=list(passes),
                ok=False,
                message="dolfinx not available.",
                fea_score=float("inf"),
            )
            for rank, trial_no, obj, passes in candidates
        ]

    out: List[FeaScheduleVerification] = []
    for rank, trial_no, obj, passes in candidates:
        row = verify_pass_schedule_fea(
            g0, mat, list(passes), timeout_s=timeout_per_schedule_s
        )
        row.rank_analytical = rank
        row.trial_number = trial_no
        row.analytical_objective = obj
        out.append(row)
    out.sort(key=lambda r: (r.fea_score, r.analytical_objective))
    return out


def pick_fea_best_schedule(
    verified: Sequence[FeaScheduleVerification],
) -> Optional[FeaScheduleVerification]:
    for row in verified:
        if row.ok and math.isfinite(row.fea_score):
            return row
    return None
