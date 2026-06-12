"""FEniCSx / dolfinx — optional elastic strip demo."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple

import numpy as np

from tubing_master.app_paths import is_frozen

# Cached result of a subprocess import probe — avoids loading dolfinx/MPI in the Qt process.
_dolfinx_import_ok: Optional[bool] = None


def element_interpolation_points(element) -> np.ndarray:
    """Basix/dolfinx element points — property in current releases, not a method."""
    pts = element.interpolation_points
    if callable(pts):
        return np.asarray(pts())
    return np.asarray(pts)


def dolfinx_available() -> bool:
    """
    Return True if ``import dolfinx`` works in a fresh interpreter.

    The check runs in a subprocess so MPI is never initialized inside the GUI / Qt app.
    MPICH + Qt in one process is fragile on macOS (MPI_Init / shared-memory bootstrap).
    """
    global _dolfinx_import_ok
    if _dolfinx_import_ok is not None:
        return _dolfinx_import_ok
    if is_frozen():
        _dolfinx_import_ok = False
        return False
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import dolfinx"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        _dolfinx_import_ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        _dolfinx_import_ok = False
    return bool(_dolfinx_import_ok)


def run_axisymmetric_probe(die_angle_deg: float, reduction_rate: float) -> Dict[str, Any]:
    raise RuntimeError(
        "dolfinx_sim.run_axisymmetric_probe is not configured in this build. "
        "Use analytical backend or wire dolfinx in this module."
    )


def run_elastic_strip(
    length_m: float,
    height_m: float,
    top_edge_y_displacement_m: float,
    *,
    youngs_pa: float = 210e9,
    poisson: float = 0.3,
    _skip_dolfinx_probe: bool = False,
) -> Dict[str, Any]:
    """
    2D plane-strain strip [0,L]×[0,H]: bottom clamped u=0, top edge u=(0, d).

    Returns coords (N,2), displacement (N,2), and optional error message on failure.
    """
    if not _skip_dolfinx_probe and not dolfinx_available():
        return {"ok": False, "error": "dolfinx is not installed or failed to import."}

    try:
        from mpi4py import MPI

        from dolfinx import default_scalar_type, fem, mesh
        from dolfinx.fem.petsc import LinearProblem
        import ufl
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    comm = MPI.COMM_WORLD
    L = float(length_m)
    H = float(height_m)
    d_top = float(top_edge_y_displacement_m)

    if L <= 0 or H <= 0:
        return {"ok": False, "error": "Length and height must be positive."}

    # Coarser mesh → snappier UI; refine for production runs.
    nx = max(10, min(100, int(L / 0.0012)))
    ny = max(4, min(60, int(H / 0.0012)))

    try:
        domain = mesh.create_rectangle(
            comm,
            [np.array([0.0, 0.0]), np.array([L, H])],
            [nx, ny],
            mesh.CellType.triangle,
        )
        V = fem.functionspace(domain, ("Lagrange", 1, (domain.geometry.dim,)))

        lam = youngs_pa * poisson / ((1.0 + poisson) * (1.0 - 2.0 * poisson))
        mu = youngs_pa / (2.0 * (1.0 + poisson))

        def epsilon(u):
            return ufl.sym(ufl.grad(u))

        def sigma_tensor(u):
            return lam * ufl.tr(epsilon(u)) * ufl.Identity(2) + 2.0 * mu * epsilon(u)

        u_trial = ufl.TrialFunction(V)
        v = ufl.TestFunction(V)
        a_form = ufl.inner(sigma_tensor(u_trial), epsilon(v)) * ufl.dx
        zero = fem.Constant(domain, default_scalar_type((0.0, 0.0)))
        L_form = ufl.inner(zero, v) * ufl.dx

        def bottom(x):
            return np.isclose(x[1], 0.0)

        def top(x):
            return np.isclose(x[1], H)

        dofs_bottom = fem.locate_dofs_geometrical(V, bottom)
        dofs_top = fem.locate_dofs_geometrical(V, top)

        u_bc_bottom = np.zeros(2, dtype=default_scalar_type)
        bc_bottom = fem.dirichletbc(u_bc_bottom, dofs_bottom, V)

        u_bc_top = np.array([0.0, d_top], dtype=default_scalar_type)
        bc_top = fem.dirichletbc(u_bc_top, dofs_top, V)

        problem = LinearProblem(
            a_form,
            L_form,
            bcs=[bc_bottom, bc_top],
            petsc_options_prefix="elastic_strip_",
        )
        uh = problem.solve()

        coords = domain.geometry.x[:, :2].copy()
        u_vals = uh.x.array.reshape(-1, 2).copy()
        u_mag = np.linalg.norm(u_vals, axis=1)

        eps_u = epsilon(uh)
        tr_eps = ufl.tr(eps_u)
        sig = lam * tr_eps * ufl.Identity(2) + 2.0 * mu * eps_u
        dev_sig = sig - (tr_eps / 2.0) * ufl.Identity(2)
        vm_expr = ufl.sqrt(ufl.max_value(1e-30, 1.5 * ufl.inner(dev_sig, dev_sig)))
        V_vm = fem.functionspace(domain, ("DG", 0))
        vm_h = fem.Function(V_vm)
        vm_h.interpolate(fem.Expression(vm_expr, element_interpolation_points(V_vm.element)))
        local_max_vm = float(np.max(vm_h.x.array)) if vm_h.x.array.size else 0.0
        max_von_mises_pa = float(comm.allreduce(local_max_vm, op=MPI.MAX))

        return {
            "ok": True,
            "coords_xy": coords,
            "displacement_xy": u_vals,
            "displacement_mag": u_mag,
            "length_m": L,
            "height_m": H,
            "top_disp_m": d_top,
            "youngs_pa": youngs_pa,
            "poisson": poisson,
            "max_von_mises_pa": max_von_mises_pa,
        }
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def run_elastic_strip_plot_arrays(result: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return x_def, y_def, magnitude for matplotlib scatter."""
    if not result.get("ok"):
        return np.array([]), np.array([]), np.array([])
    c = result["coords_xy"]
    u = result["displacement_xy"]
    mag = result["displacement_mag"]
    xd = c[:, 0] + u[:, 0]
    yd = c[:, 1] + u[:, 1]
    return xd, yd, mag


def run_plane_strain_strip(
    *,
    length_m: float,
    height_m: float,
    e_modulus_pa: float = 200e9,
    nu: float = 0.30,
    top_displacement_m: float,
    _skip_dolfinx_probe: bool = False,
) -> Any:
    """
    UI-facing alias for :func:`run_elastic_strip` with kwargs matching ``main_window``.

    Returns an object with ``ok``, ``message``, and ``max_von_mises_pa`` (placeholder until
    stress recovery is added).
    """
    r = run_elastic_strip(
        float(length_m),
        float(height_m),
        float(top_displacement_m),
        youngs_pa=float(e_modulus_pa),
        poisson=float(nu),
        _skip_dolfinx_probe=_skip_dolfinx_probe,
    )
    if not r.get("ok"):
        return SimpleNamespace(
            ok=False,
            message=str(r.get("error", "unknown error")),
            max_von_mises_pa=0.0,
        )
    max_vm = float(r.get("max_von_mises_pa", 0.0))
    return SimpleNamespace(
        ok=True,
        message="Elastic strip solve finished (plane strain).",
        max_von_mises_pa=max_vm,
    )


def run_plane_strain_strip_subprocess(
    *,
    length_m: float,
    height_m: float,
    e_modulus_pa: float = 200e9,
    nu: float = 0.30,
    top_displacement_m: float,
    timeout_s: float = 600.0,
) -> Any:
    """
    Run :func:`run_plane_strain_strip` in a child process so MPI/PETSc never load in the Qt app.
    """
    payload = {
        "length_m": float(length_m),
        "height_m": float(height_m),
        "e_modulus_pa": float(e_modulus_pa),
        "nu": float(nu),
        "top_displacement_m": float(top_displacement_m),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tubing_master.dolfinx_sim", "--worker-plane-strain"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return SimpleNamespace(ok=False, message="dolfinx worker timed out.", max_von_mises_pa=0.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return SimpleNamespace(ok=False, message=str(exc), max_von_mises_pa=0.0)

    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or (proc.stdout or "").strip() or f"exit {proc.returncode}"
        return SimpleNamespace(ok=False, message=err[:4000], max_von_mises_pa=0.0)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stdout or "")[-2000:]
        return SimpleNamespace(ok=False, message=f"Invalid worker output: {tail!r}", max_von_mises_pa=0.0)

    return SimpleNamespace(
        ok=bool(data.get("ok")),
        message=str(data.get("message", "")),
        max_von_mises_pa=float(data.get("max_von_mises_pa", 0.0)),
    )


def _cli_worker_plane_strain() -> None:
    payload = json.load(sys.stdin)
    r = run_plane_strain_strip(
        length_m=payload["length_m"],
        height_m=payload["height_m"],
        e_modulus_pa=float(payload.get("e_modulus_pa", 200e9)),
        nu=float(payload.get("nu", 0.30)),
        top_displacement_m=payload["top_displacement_m"],
        _skip_dolfinx_probe=True,
    )
    out = {"ok": r.ok, "message": r.message, "max_von_mises_pa": r.max_von_mises_pa}
    json.dump(out, sys.stdout)


def _cli_worker_schedule_fea() -> None:
    """One schedule: plane-strain strip per pass; JSON in/out for hybrid optimization."""
    payload = json.load(sys.stdin)
    youngs_pa = float(payload.get("youngs_pa", 210e9))
    nu = float(payload.get("nu", 0.30))
    rows: list[Dict[str, Any]] = []
    sched_max = 0.0
    all_ok = True
    for pp in payload.get("passes") or []:
        r = run_elastic_strip(
            float(pp["length_m"]),
            float(pp["height_m"]),
            float(pp["top_displacement_m"]),
            youngs_pa=youngs_pa,
            poisson=nu,
            _skip_dolfinx_probe=True,
        )
        if not r.get("ok"):
            all_ok = False
            rows.append(
                {
                    "ok": False,
                    "message": str(r.get("error", "solve failed")),
                    "max_von_mises_pa": 0.0,
                }
            )
            continue
        vm = float(r.get("max_von_mises_pa", 0.0))
        sched_max = max(sched_max, vm)
        rows.append({"ok": True, "message": "ok", "max_von_mises_pa": vm})
    json.dump(
        {
            "ok": all_ok and bool(rows),
            "passes": rows,
            "schedule_max_von_mises_pa": sched_max,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Tubing Master dolfinx helpers (internal worker entry points).")
    p.add_argument(
        "--worker-plane-strain",
        action="store_true",
        help="Read JSON kwargs from stdin; run plane-strain strip; write JSON result to stdout.",
    )
    p.add_argument(
        "--worker-schedule-fea",
        action="store_true",
        help="Read JSON schedule (youngs_pa, passes[]); run strip FEA per pass; write JSON to stdout.",
    )
    args = p.parse_args()
    if args.worker_plane_strain:
        _cli_worker_plane_strain()
    elif args.worker_schedule_fea:
        _cli_worker_schedule_fea()
    else:
        p.print_help()
        sys.exit(2)
