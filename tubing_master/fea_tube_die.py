"""
Axisymmetric tube / semi-die FEA (dolfinx).

2D meridional (r, z) model with hoop stress (axisymmetric). Die semi-angle sets the
reduction-zone length; exit radial displacement enforces annulus area reduction with
fixed bore (plug/mandrel analogy: u_r = 0 on inner radius).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from tubing_master.dolfinx_sim import dolfinx_available, element_interpolation_points

_AXISYM_R_MIN_M = 1.0e-5


@dataclass
class TubeDiePassResult:
    ok: bool
    max_von_mises_pa: float
    od_in_m: float
    id_in_m: float
    od_out_m: float
    id_out_m: float
    semi_die_angle_deg: float
    area_reduction_fraction: float
    reduction_zone_length_m: float
    message: str = ""


def od_id_after_area_reduction_fixed_bore(
    od_m: float, id_m: float, area_reduction_fraction: float
) -> tuple[float, float]:
    """Annulus area reduction with inner diameter fixed (mandrel / plug drawing)."""
    ri = max(_AXISYM_R_MIN_M, 0.5 * float(id_m))
    ro = max(ri + 1e-6, 0.5 * float(od_m))
    a0 = math.pi * (ro * ro - ri * ri)
    r = max(1e-9, min(0.999999, float(area_reduction_fraction)))
    a1 = a0 * (1.0 - r)
    ro_new = math.sqrt(ri * ri + a1 / math.pi)
    return 2.0 * ro_new, 2.0 * ri


def tube_die_pass_geometry(
    od_in_m: float,
    id_in_m: float,
    area_reduction_fraction: float,
    semi_die_angle_deg: float,
) -> Dict[str, float]:
    """Mesh domain sizes and target radial draw for one pass."""
    ri = max(_AXISYM_R_MIN_M, 0.5 * float(id_in_m))
    ro = max(ri + 1e-6, 0.5 * float(od_in_m))
    od_out, id_out = od_id_after_area_reduction_fixed_bore(od_in_m, id_in_m, area_reduction_fraction)
    ro_f = 0.5 * od_out
    delta_ro = max(0.0, ro - ro_f)
    alpha = math.radians(max(3.0, min(45.0, float(semi_die_angle_deg))))
    wall = ro - ri
    lr = float(np.clip(delta_ro / max(math.sin(alpha), 0.05), wall * 0.5, wall * 8.0))
    lb = max(wall * 0.5, 0.003)
    lt = max(wall * 0.3, 0.002)
    l_total = lr + lb + lt
    return {
        "r_inner_m": ri,
        "r_outer_m": ro,
        "r_outer_target_m": ro_f,
        "delta_r_outer_m": delta_ro,
        "reduction_zone_m": lr,
        "bearing_zone_m": lb,
        "length_m": l_total,
        "od_out_m": od_out,
        "id_out_m": id_out,
    }


def run_axisymmetric_tube_die_pass(
    *,
    od_in_m: float,
    id_in_m: float,
    area_reduction_fraction: float,
    semi_die_angle_deg: float,
    youngs_pa: float = 210e9,
    poisson: float = 0.30,
    _skip_dolfinx_probe: bool = False,
) -> Dict[str, Any]:
    """
    One drawing pass: axisymmetric elastic tube wall in semi-die reduction + bearing length.
    """
    if not _skip_dolfinx_probe and not dolfinx_available():
        return {"ok": False, "error": "dolfinx is not installed or failed to import."}

    geo = tube_die_pass_geometry(od_in_m, id_in_m, area_reduction_fraction, semi_die_angle_deg)
    ri = geo["r_inner_m"]
    ro = geo["r_outer_m"]
    ro_f = geo["r_outer_target_m"]
    delta_ro = geo["delta_r_outer_m"]
    lr = geo["reduction_zone_m"]
    L = geo["length_m"]

    try:
        from mpi4py import MPI

        from dolfinx import default_scalar_type, fem, mesh
        from dolfinx.fem.petsc import LinearProblem
        import ufl
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    comm = MPI.COMM_WORLD
    nx = max(12, min(80, int((ro - ri) / max(ri * 0.08, 2e-4))))
    nz = max(16, min(120, int(L / max(ri * 0.08, 2e-4))))

    try:
        domain = mesh.create_rectangle(
            comm,
            [np.array([ri, 0.0]), np.array([ro, L])],
            [nx, nz],
            mesh.CellType.triangle,
        )
        V = fem.functionspace(domain, ("Lagrange", 1, (domain.geometry.dim,)))
        x = ufl.SpatialCoordinate(domain)
        r_coord = x[0]

        lam = youngs_pa * poisson / ((1.0 + poisson) * (1.0 - 2.0 * poisson))
        mu_s = youngs_pa / (2.0 * (1.0 + poisson))
        two_pi_r = 2.0 * math.pi * r_coord

        def axisym_strain(u_vec):
            g = ufl.grad(u_vec)
            eps_rr = g[0, 0]
            eps_zz = g[1, 1]
            eps_tt = u_vec[0] / r_coord
            eps_rz = 0.5 * (g[0, 1] + g[1, 0])
            return eps_rr, eps_zz, eps_tt, eps_rz

        def axisym_stress_from_strain(eps_rr, eps_zz, eps_tt, eps_rz):
            tr = eps_rr + eps_zz + eps_tt
            s_rr = lam * tr + 2.0 * mu_s * eps_rr
            s_zz = lam * tr + 2.0 * mu_s * eps_zz
            s_tt = lam * tr + 2.0 * mu_s * eps_tt
            s_rz = 2.0 * mu_s * eps_rz
            return s_rr, s_zz, s_tt, s_rz

        def stress_work(s, e):
            return s[0] * e[0] + s[1] * e[1] + s[2] * e[2] + 2.0 * s[3] * e[3]

        u = ufl.TrialFunction(V)
        v = ufl.TestFunction(V)
        eu = axisym_strain(u)
        su = axisym_stress_from_strain(*eu)
        ev = axisym_strain(v)
        a_form = stress_work(su, ev) * two_pi_r * ufl.dx
        zero = fem.Constant(domain, default_scalar_type((0.0, 0.0)))
        L_form = ufl.inner(zero, v) * two_pi_r * ufl.dx

        def on_inner(boundary_x):
            return np.isclose(boundary_x[0], ri)

        def on_entry(boundary_x):
            return np.isclose(boundary_x[1], 0.0)

        def on_exit_outer(boundary_x):
            return np.isclose(boundary_x[1], L) & np.isclose(boundary_x[0], ro)

        def on_reduction_outer(boundary_x):
            z = boundary_x[1]
            return np.isclose(boundary_x[0], ro) & (z > 1e-9) & (z < lr - 1e-9)

        # Entry: fix axial; inner mandrel
        bc_entry = fem.dirichletbc(
            np.zeros(2, dtype=default_scalar_type),
            fem.locate_dofs_geometrical(V, on_entry),
            V,
        )
        bc_inner = fem.dirichletbc(
            np.array([0.0, 0.0], dtype=default_scalar_type),
            fem.locate_dofs_geometrical(V, on_inner),
            V,
        )

        # Exit: prescribed radial draw on OD (fixed bore)
        u_exit = np.array([-delta_ro, 0.0], dtype=default_scalar_type)
        bc_exit = fem.dirichletbc(u_exit, fem.locate_dofs_geometrical(V, on_exit_outer), V)

        # Reduction zone OD: average radial draw over the die contact length
        u_red = np.array([-0.5 * delta_ro, 0.0], dtype=default_scalar_type)
        bcs = [bc_entry, bc_inner, bc_exit]
        dofs_red = fem.locate_dofs_geometrical(V, on_reduction_outer)
        if len(dofs_red) > 0:
            bcs.append(fem.dirichletbc(u_red, dofs_red, V))

        problem = LinearProblem(
            a_form,
            L_form,
            bcs=bcs,
            petsc_options_prefix="tube_die_axisym_",
        )
        uh = problem.solve()

        eu_s = axisym_strain(uh)
        su_s = axisym_stress_from_strain(*eu_s)
        s_mean = (su_s[0] + su_s[1] + su_s[2]) / 3.0
        vm_expr = ufl.sqrt(
            ufl.max_value(
                1e-30,
                0.5
                * (
                    (su_s[0] - s_mean) ** 2
                    + (su_s[1] - s_mean) ** 2
                    + (su_s[2] - s_mean) ** 2
                    + 6.0 * su_s[3] ** 2
                ),
            )
        )
        V_vm = fem.functionspace(domain, ("DG", 0))
        vm_h = fem.Function(V_vm)
        vm_h.interpolate(fem.Expression(vm_expr, element_interpolation_points(V_vm.element)))
        local_max = float(np.max(vm_h.x.array)) if vm_h.x.array.size else 0.0
        max_vm = float(comm.allreduce(local_max, op=MPI.MAX))

        return {
            "ok": True,
            "max_von_mises_pa": max_vm,
            "geometry": geo,
            "semi_die_angle_deg": float(semi_die_angle_deg),
            "area_reduction_fraction": float(area_reduction_fraction),
            "od_in_m": float(od_in_m),
            "id_in_m": float(id_in_m),
            "od_out_m": geo["od_out_m"],
            "id_out_m": geo["id_out_m"],
        }
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def run_tube_die_pass_subprocess(
    *,
    od_in_m: float,
    id_in_m: float,
    area_reduction_fraction: float,
    semi_die_angle_deg: float,
    youngs_pa: float,
    nu: float = 0.30,
    timeout_s: float = 600.0,
) -> TubeDiePassResult:
    payload = {
        "od_in_m": float(od_in_m),
        "id_in_m": float(id_in_m),
        "area_reduction_fraction": float(area_reduction_fraction),
        "semi_die_angle_deg": float(semi_die_angle_deg),
        "youngs_pa": float(youngs_pa),
        "nu": float(nu),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tubing_master.fea_tube_die", "--worker-pass"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return TubeDiePassResult(
            ok=False,
            max_von_mises_pa=0.0,
            od_in_m=od_in_m,
            id_in_m=id_in_m,
            od_out_m=od_in_m,
            id_out_m=id_in_m,
            semi_die_angle_deg=semi_die_angle_deg,
            area_reduction_fraction=area_reduction_fraction,
            reduction_zone_length_m=0.0,
            message="tube/die FEA worker timed out.",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TubeDiePassResult(
            ok=False,
            max_von_mises_pa=0.0,
            od_in_m=od_in_m,
            id_in_m=id_in_m,
            od_out_m=od_in_m,
            id_out_m=id_in_m,
            semi_die_angle_deg=semi_die_angle_deg,
            area_reduction_fraction=area_reduction_fraction,
            reduction_zone_length_m=0.0,
            message=str(exc),
        )

    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or (proc.stdout or "").strip() or f"exit {proc.returncode}"
        return TubeDiePassResult(
            ok=False,
            max_von_mises_pa=0.0,
            od_in_m=od_in_m,
            id_in_m=id_in_m,
            od_out_m=od_in_m,
            id_out_m=id_in_m,
            semi_die_angle_deg=semi_die_angle_deg,
            area_reduction_fraction=area_reduction_fraction,
            reduction_zone_length_m=0.0,
            message=err[:4000],
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return TubeDiePassResult(
            ok=False,
            max_von_mises_pa=0.0,
            od_in_m=od_in_m,
            id_in_m=id_in_m,
            od_out_m=od_in_m,
            id_out_m=id_in_m,
            semi_die_angle_deg=semi_die_angle_deg,
            area_reduction_fraction=area_reduction_fraction,
            reduction_zone_length_m=0.0,
            message="Invalid worker JSON.",
        )

    geo = data.get("geometry") or {}
    return TubeDiePassResult(
        ok=bool(data.get("ok")),
        max_von_mises_pa=float(data.get("max_von_mises_pa", 0.0)),
        od_in_m=float(data.get("od_in_m", od_in_m)),
        id_in_m=float(data.get("id_in_m", id_in_m)),
        od_out_m=float(data.get("od_out_m", od_in_m)),
        id_out_m=float(data.get("id_out_m", id_in_m)),
        semi_die_angle_deg=float(semi_die_angle_deg),
        area_reduction_fraction=float(area_reduction_fraction),
        reduction_zone_length_m=float(geo.get("reduction_zone_m", 0.0)),
        message=str(data.get("error", "ok" if data.get("ok") else "failed")),
    )


def run_schedule_tube_die_fea(
    *,
    passes: Sequence[Dict[str, float]],
    youngs_pa: float,
    nu: float = 0.30,
    timeout_s: float = 1200.0,
) -> Dict[str, Any]:
    """
    ``passes``: each dict has od_in_m, id_in_m, area_reduction_fraction, semi_die_angle_deg.
    Chains OD/ID through the schedule.
    """
    payload = {"youngs_pa": float(youngs_pa), "nu": float(nu), "passes": list(passes)}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tubing_master.fea_tube_die", "--worker-schedule"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "schedule FEA timed out", "passes": []}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "passes": []}
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        return {"ok": False, "error": err[:4000], "passes": []}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid JSON", "passes": []}


def _cli_worker_pass() -> None:
    p = json.load(sys.stdin)
    r = run_axisymmetric_tube_die_pass(
        od_in_m=p["od_in_m"],
        id_in_m=p["id_in_m"],
        area_reduction_fraction=p["area_reduction_fraction"],
        semi_die_angle_deg=p["semi_die_angle_deg"],
        youngs_pa=float(p.get("youngs_pa", 210e9)),
        poisson=float(p.get("nu", 0.30)),
        _skip_dolfinx_probe=True,
    )
    json.dump(r, sys.stdout)


def _cli_worker_schedule() -> None:
    payload = json.load(sys.stdin)
    youngs = float(payload.get("youngs_pa", 210e9))
    nu = float(payload.get("nu", 0.30))
    rows: List[Dict[str, Any]] = []
    sched_max = 0.0
    od = float(payload["passes"][0]["od_in_m"]) if payload.get("passes") else 0.01
    id_ = float(payload["passes"][0]["id_in_m"]) if payload.get("passes") else 0.008
    all_ok = True
    for pp in payload.get("passes") or []:
        od_in = float(pp.get("od_in_m", od))
        id_in = float(pp.get("id_in_m", id_))
        r = run_axisymmetric_tube_die_pass(
            od_in_m=od_in,
            id_in_m=id_in,
            area_reduction_fraction=float(pp["area_reduction_fraction"]),
            semi_die_angle_deg=float(pp["semi_die_angle_deg"]),
            youngs_pa=youngs,
            poisson=nu,
            _skip_dolfinx_probe=True,
        )
        if not r.get("ok"):
            all_ok = False
            rows.append({"ok": False, "message": r.get("error", "fail"), "max_von_mises_pa": 0.0})
            continue
        vm = float(r.get("max_von_mises_pa", 0.0))
        sched_max = max(sched_max, vm)
        od = float(r.get("od_out_m", od_in))
        id_ = float(r.get("id_out_m", id_in))
        rows.append({"ok": True, "message": "ok", "max_von_mises_pa": vm})
    json.dump({"ok": all_ok and bool(rows), "passes": rows, "schedule_max_von_mises_pa": sched_max}, sys.stdout)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Tube/die axisymmetric FEA workers")
    ap.add_argument("--worker-pass", action="store_true")
    ap.add_argument("--worker-schedule", action="store_true")
    args = ap.parse_args()
    if args.worker_pass:
        _cli_worker_pass()
    elif args.worker_schedule:
        _cli_worker_schedule()
    else:
        ap.print_help()
        sys.exit(2)
