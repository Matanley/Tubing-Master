"""DAMASK grid-solver coupling for per-pass grain evolution (optional dependency).

When ``DAMASK_grid`` is on PATH (or ``DAMASK_EXAMPLES_GRID`` points at DAMASK's
``examples/grid``), runs a polycrystal CP simulation and derives grain size per
drawing pass from DAMASK slip activity plus the Tubing Master refinement law.

Falls back to the analytical engine when DAMASK is missing or a run fails.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from tubing_master.app_paths import damask_templates_dir
from tubing_master.engine import TubingMasterEngine
from tubing_master.materials import MetalMaterial

_TEMPLATES_DIR = damask_templates_dir()
_damask_grid_ok: Optional[bool] = None
_damask_py_ok: Optional[bool] = None


@dataclass
class DamaskPassGrain:
    pass_index: int
    grain_size_um: float
    equiv_plastic_strain: float
    von_mises_pa: float
    source: str  # "damask" | "analytical_fallback"


@dataclass
class DamaskScheduleGrainResult:
    ok: bool
    passes: List[DamaskPassGrain] = field(default_factory=list)
    message: str = ""
    work_dir: str = ""
    result_h5: str = ""


def damask_grid_executable() -> Optional[str]:
    """Path to ``DAMASK_grid`` if discoverable (PATH, active conda env, or next to ``sys.executable``)."""
    for name in ("DAMASK_grid", "damask_grid"):
        p = shutil.which(name)
        if p:
            return p
    candidates: List[Path] = []
    exe_parent = Path(sys.executable).resolve().parent
    candidates.append(exe_parent / "DAMASK_grid")
    candidates.append(exe_parent / "damask_grid")
    prefix = (os.environ.get("CONDA_PREFIX") or "").strip()
    if prefix:
        conda_bin = Path(prefix).expanduser() / "bin"
        candidates.append(conda_bin / "DAMASK_grid")
        candidates.append(conda_bin / "damask_grid")
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def damask_python_available() -> bool:
    """True if ``import damask`` works in a subprocess (keeps MPI out of Qt)."""
    global _damask_py_ok
    if _damask_py_ok is not None:
        return _damask_py_ok
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import damask"],
            capture_output=True,
            text=True,
            timeout=90,
        )
        _damask_py_ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        _damask_py_ok = False
    return bool(_damask_py_ok)


def damask_can_run() -> bool:
    """True only when ``DAMASK_grid`` can be executed (required for CP grain runs)."""
    return damask_grid_executable() is not None


def damask_available() -> bool:
    """True when CP grain simulations can run (same as :func:`damask_can_run`)."""
    global _damask_grid_ok
    if _damask_grid_ok is not None:
        return _damask_grid_ok
    _damask_grid_ok = damask_can_run()
    return _damask_grid_ok


def damask_status_message() -> str:
    exe = damask_grid_executable()
    ex = _examples_grid_dir()
    if exe:
        return f"DAMASK_grid ready: {exe} — DAMASK Grain backend will run crystal plasticity."
    if ex:
        return (
            f"DAMASK examples found at {ex}, but DAMASK_grid is not on PATH. "
            "DAMASK Grain backend will match Built-In Analytical until you install the solver."
        )
    return (
        "DAMASK_grid not on PATH — DAMASK Grain backend currently matches Built-In Analytical. "
        "Install with: conda install -c conda-forge damask-grid"
    )


def _examples_grid_dir() -> Optional[Path]:
    raw = (os.environ.get("DAMASK_EXAMPLES_GRID") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_dir() and (p / "material.yaml").is_file():
            return p
    return None


def _official_grid_asset_dir() -> Optional[Path]:
    """Directory containing DAMASK's validated ``20grains16x16x16.vti`` + ``material.yaml``."""
    candidates: List[Path] = []
    ex = _examples_grid_dir()
    if ex is not None:
        candidates.append(ex)
    candidates.append(_TEMPLATES_DIR)
    exe = damask_grid_executable()
    if exe:
        root = Path(exe).resolve().parent
        candidates.append((root / "../../info/test/examples/grid").resolve())
        candidates.append((root / "../share/damask/examples/grid").resolve())
    seen: set[str] = set()
    for d in candidates:
        key = str(d.resolve())
        if key in seen:
            continue
        seen.add(key)
        if (d / "20grains16x16x16.vti").is_file() and (d / "material.yaml").is_file():
            return d
    return None


def _template_dir() -> Path:
    ex = _official_grid_asset_dir()
    if ex is not None:
        return ex
    return _TEMPLATES_DIR


def _true_strains_from_passes(passes: Sequence[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    for p in passes:
        r = max(1e-9, min(0.999999, float(p.get("r", 0.0) or 0.0)))
        out.append(float(math.log(1.0 / (1.0 - r))))
    return out


def _quaternion_for_grain(grain_id: int) -> List[float]:
    """Deterministic orientations spread on the orientation sphere."""
    t = (grain_id + 0.5) * 0.618033988749895
    a = 2.0 * math.pi * (t % 1.0)
    b = math.pi * ((t * 1.7) % 1.0)
    w = math.cos(b / 2.0)
    x = math.sin(b / 2.0) * math.cos(a)
    y = math.sin(b / 2.0) * math.sin(a)
    z = 0.0
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    return [w / n, x / n, y / n, z / n]


def write_polycrystal_material_yaml(path: Path, *, n_grains: int) -> None:
    """Write material.yaml with one SX entry per grain ID (0 … n_grains-1)."""
    lines = [
        "homogenization:",
        "  SX:",
        "    N_constituents: 1",
        "    mechanical:",
        "      type: pass",
        "",
        "phase:",
        "  Steel:",
        "    lattice: cF",
        "    mechanical:",
        "      elastic:",
        "        type: Hooke",
        "        C_11: 2.338e11",
        "        C_12: 1.404e11",
        "        C_44: 1.219e11",
        "      plastic:",
        "        type: phenopowerlaw",
        "        output: [xi_sl, gamma_sl]",
        "        N_sl: [12]",
        "        dot_gamma_0_sl: [0.001]",
        "        n_sl: [20]",
        "        a_sl: [2.25]",
        "        xi_0_sl: [95.0e6]",
        "        xi_inf_sl: [222.0e6]",
        "        h_0_sl-sl: [1.0e9]",
        "        h_sl-sl: [1, 1.4, 1, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4]",
        "",
        "material:",
    ]
    for gid in range(n_grains):
        o = _quaternion_for_grain(gid)
        lines.append(f"- homogenization: SX")
        lines.append("  constituents:")
        lines.append("  - phase: Steel")
        lines.append("    v: 1.0")
        lines.append(f"    O: [{o[0]:.8f}, {o[1]:.8f}, {o[2]:.8f}, {o[3]:.8f}]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_polycrystal_vti(path: Path, *, cells: Tuple[int, int, int], n_grains: int) -> None:
    """VTK ImageData with CellData ``material`` (matches DAMASK grid examples)."""
    nx, ny, nz = cells
    if min(nx, ny, nz) < 1:
        nx, ny, nz = 16, 16, 16
    n_cells = nx * ny * nz
    ids = np.zeros(n_cells, dtype=np.int32)
    idx = 0
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                gx = min(n_grains - 1, (i * n_grains) // max(nx, 1))
                gy = min(n_grains - 1, (j * n_grains) // max(ny, 1))
                gz = min(n_grains - 1, (k * n_grains) // max(nz, 1))
                ids[idx] = int((gx + gy + gz) % n_grains)
                idx += 1
    flat = "\n".join(str(int(v)) for v in ids)
    spacing = 1.0 / float(nx)
    xml = f"""<?xml version="1.0"?>
<VTKFile type="ImageData" version="0.1" byte_order="LittleEndian">
  <ImageData WholeExtent="0 {nx} 0 {ny} 0 {nz}" Origin="0 0 0" Spacing="{spacing} {spacing} {spacing}">
    <Piece Extent="0 {nx} 0 {ny} 0 {nz}">
      <PointData>
      </PointData>
      <CellData>
        <DataArray type="Int32" Name="material" format="ascii">
{flat}
        </DataArray>
      </CellData>
    </Piece>
  </ImageData>
</VTKFile>
"""
    path.write_text(xml, encoding="utf-8")


def write_drawing_load_yaml(path: Path, true_strains: Sequence[float]) -> None:
    """Multi load-step axial drawing: F_zz grows with cumulative true strain per pass."""
    lines = [
        "solver:",
        "  mechanical: spectral_basic",
        "",
        "loadstep:",
    ]
    cum = 0.0
    for i, eps in enumerate(true_strains, start=1):
        cum += float(eps)
        fzz = float(math.exp(cum))
        f_lat = float(1.0 / math.sqrt(max(fzz, 1e-12)))
        lines.extend(
            [
                f"  - discretization:",
                f"      t: 1.0",
                f"      N: 1",
                f"    f_out: 1",
                f"    boundary_conditions:",
                f"      mechanical:",
                f"        F:",
                f"          - [{f_lat:.12e}, 0.0, 0.0]",
                f"          - [0.0, {f_lat:.12e}, 0.0]",
                f"          - [0.0, 0.0, {fzz:.12e}]",
                f"        P:",
                f"          - [x, x, x]",
                f"          - [x, x, x]",
                f"          - [x, x, x]",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_damask_workdir(
    work_dir: Path,
    passes: Sequence[Dict[str, Any]],
    *,
    n_grains: int = 20,
    grid_cells: Tuple[int, int, int] = (16, 16, 16),
) -> Path:
    """Write geometry, material, load, and numerics into ``work_dir``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    for num_src in (
        _TEMPLATES_DIR / "numerics.yaml",
        _template_dir() / "numerics.yaml",
    ):
        if num_src.is_file():
            shutil.copy2(num_src, work_dir / "numerics.yaml")
            break

    assets = _official_grid_asset_dir()
    if assets is not None:
        shutil.copy2(assets / "material.yaml", work_dir / "material.yaml")
        shutil.copy2(assets / "20grains16x16x16.vti", work_dir / "polycrystal.vti")
    else:
        write_polycrystal_material_yaml(work_dir / "material.yaml", n_grains=n_grains)
        write_polycrystal_vti(work_dir / "polycrystal.vti", cells=grid_cells, n_grains=n_grains)

    write_drawing_load_yaml(work_dir / "load.yaml", _true_strains_from_passes(passes))
    return work_dir


def _run_damask_grid(work_dir: Path, *, jobname: str = "tubing_master", timeout_s: float = 600.0) -> Tuple[bool, str]:
    exe = damask_grid_executable()
    if not exe:
        return False, "DAMASK_grid executable not found on PATH."
    geom = work_dir / "polycrystal.vti"
    if not geom.is_file():
        geom = work_dir / "20grains16x16x16.vti"
    load = work_dir / "load.yaml"
    mat = work_dir / "material.yaml"
    num = work_dir / "numerics.yaml"
    if not all(p.is_file() for p in (geom, load, mat)):
        return False, f"Missing input files in {work_dir}."
    cmd = [
        exe,
        "--geom",
        geom.name,
        "--load",
        load.name,
        "--material",
        mat.name,
        "--jobname",
        jobname,
        "--workingdirectory",
        str(work_dir),
    ]
    if num.is_file():
        cmd.extend(["--numerics", num.name])
    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", str(min(8, os.cpu_count() or 4)))
    try:
        r = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"DAMASK_grid timed out after {timeout_s:.0f} s."
    except OSError as exc:
        return False, str(exc)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-2000:]
        return False, f"DAMASK_grid failed (code {r.returncode}):\n{tail}"
    return True, ""


def _find_result_h5(work_dir: Path, jobname: str) -> Optional[Path]:
    for pat in (f"{jobname}.hdf5", f"{jobname}.h5"):
        p = work_dir / pat
        if p.is_file():
            return p
    for p in sorted(work_dir.glob("*.hdf5")):
        return p
    for p in sorted(work_dir.glob("*.h5")):
        return p
    return None


def _list_increment_groups(h5_path: Path) -> List[str]:
    try:
        import h5py
    except ImportError:
        return []
    incs: List[str] = []
    with h5py.File(h5_path, "r") as f:
        for key in f.keys():
            if re.match(r"inc\d+", str(key)):
                incs.append(str(key))
    incs.sort(key=lambda s: int(re.search(r"\d+", s).group()))  # type: ignore[union-attr]
    return incs


def _mean_gamma_sl(h5_path: Path, inc_group: str) -> Optional[float]:
    """Volume-averaged cumulative slip activity (proxy for plastic strain)."""
    try:
        import h5py
    except ImportError:
        return None

    def _visit(g, prefix: str = "") -> List[float]:
        vals: List[float] = []
        for name, item in g.items():
            path = f"{prefix}/{name}".strip("/")
            if isinstance(item, h5py.Dataset):
                if "gamma_sl" in name.lower() or path.endswith("gamma_sl"):
                    arr = np.asarray(item[()])
                    vals.append(float(np.mean(np.abs(arr))))
            elif isinstance(item, h5py.Group):
                vals.extend(_visit(item, path))
        return vals

    with h5py.File(h5_path, "r") as f:
        if inc_group not in f:
            return None
        samples = _visit(f[inc_group])
    return float(np.mean(samples)) if samples else None


def _mean_von_mises_pa(h5_path: Path, inc_group: str) -> Optional[float]:
    try:
        import h5py
    except ImportError:
        return None

    def _visit(g) -> List[float]:
        vals: List[float] = []
        for name, item in g.items():
            if isinstance(item, h5py.Dataset):
                lname = name.lower()
                if "cauchy" in lname or lname in ("sigma", "sigma_mises"):
                    arr = np.asarray(item[()])
                    if arr.size == 0:
                        continue
                    if arr.ndim >= 2 and arr.shape[-2:] == (3, 3):
                        s = arr.reshape(-1, 3, 3)
                        vm = []
                        for t in s:
                            vm.append(_von_mises_tensor(t))
                        vals.append(float(np.mean(vm)))
                    else:
                        vals.append(float(np.mean(np.abs(arr))))
            elif isinstance(item, h5py.Group):
                vals.extend(_visit(item))
        return vals

    with h5py.File(h5_path, "r") as f:
        if inc_group not in f:
            return None
        samples = _visit(f[inc_group])
    return float(np.mean(samples)) if samples else None


def _von_mises_tensor(s: np.ndarray) -> float:
    s = np.asarray(s, dtype=float).reshape(3, 3)
    hydro = np.trace(s) / 3.0
    dev = s - hydro * np.eye(3)
    return float(math.sqrt(1.5 * np.sum(dev * dev)))


def _grain_sizes_from_h5(
    h5_path: Path,
    n_passes: int,
    *,
    initial_grain_um: float = 15.0,
    true_strains: Optional[Sequence[float]] = None,
) -> List[DamaskPassGrain]:
    eng = TubingMasterEngine()
    incs = _list_increment_groups(h5_path)
    if not incs:
        raise RuntimeError(f"No increment groups found in {h5_path.name}.")
    # One output increment per pass (load step); use last N increments.
    use = incs[-n_passes:] if len(incs) >= n_passes else incs
    if len(use) < n_passes:
        raise RuntimeError(
            f"Expected at least {n_passes} DAMASK increments, found {len(incs)} in {h5_path.name}."
        )
    grain = float(initial_grain_um)
    out: List[DamaskPassGrain] = []
    ts = list(true_strains or [])
    for i, inc in enumerate(use[-n_passes:], start=1):
        gamma = _mean_gamma_sl(h5_path, inc)
        vm = _mean_von_mises_pa(h5_path, inc)
        eps = float(gamma) if gamma is not None and gamma > 0 else (
            float(ts[i - 1]) if i - 1 < len(ts) else 0.0
        )
        if gamma is not None and gamma > 0:
            grain = eng.calculate_grain_size(eps, d0=grain)
        elif i - 1 < len(ts):
            grain = eng.calculate_grain_size(float(ts[i - 1]), d0=grain)
        out.append(
            DamaskPassGrain(
                pass_index=i,
                grain_size_um=float(grain),
                equiv_plastic_strain=float(eps),
                von_mises_pa=float(vm or 0.0),
                source="damask",
            )
        )
    return out


def run_schedule_grain_simulation(
    passes: Sequence[Dict[str, Any]],
    *,
    initial_grain_um: float = 15.0,
    work_dir: Optional[Path] = None,
    timeout_s: float = 600.0,
    keep_workdir: bool = False,
) -> DamaskScheduleGrainResult:
    """
    Run DAMASK_grid for the pass true strains; return per-pass grain sizes.

    On failure, returns ``ok=False`` with an explanatory message (no exception).
    """
    if not passes:
        return DamaskScheduleGrainResult(ok=False, message="No drawing passes.")
    if not damask_available():
        return DamaskScheduleGrainResult(ok=False, message=damask_status_message())
    if damask_grid_executable() is None:
        return DamaskScheduleGrainResult(
            ok=False,
            message="DAMASK_grid not on PATH (DAMASK_EXAMPLES_GRID alone is not enough to run).",
        )

    owned_tmp = work_dir is None
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="tubing_master_damask_"))
    else:
        work_dir = Path(work_dir)
    try:
        prepare_damask_workdir(work_dir, passes)
        ok, err = _run_damask_grid(work_dir, timeout_s=timeout_s)
        if not ok:
            return DamaskScheduleGrainResult(ok=False, message=err, work_dir=str(work_dir))
        h5 = _find_result_h5(work_dir, "tubing_master")
        if h5 is None:
            return DamaskScheduleGrainResult(
                ok=False,
                message="DAMASK finished but no .hdf5 result was found.",
                work_dir=str(work_dir),
            )
        grains = _grain_sizes_from_h5(
            h5,
            len(passes),
            initial_grain_um=initial_grain_um,
            true_strains=_true_strains_from_passes(passes),
        )
        return DamaskScheduleGrainResult(
            ok=True,
            passes=grains,
            message="DAMASK crystal-plasticity grain evolution.",
            work_dir=str(work_dir),
            result_h5=str(h5),
        )
    except Exception as exc:
        return DamaskScheduleGrainResult(ok=False, message=str(exc), work_dir=str(work_dir))
    finally:
        if owned_tmp and not keep_workdir:
            shutil.rmtree(work_dir, ignore_errors=True)


def analytical_grain_fallback(
    passes: Sequence[Dict[str, Any]],
    *,
    initial_grain_um: float = 15.0,
    material: MetalMaterial | None = None,
) -> List[DamaskPassGrain]:
    """Analytical chain matching :class:`TubingMasterEngine` (when DAMASK is unavailable)."""
    eng = TubingMasterEngine.from_material(material)
    grain = float(initial_grain_um if material is None else eng.initial_grain_um)
    out: List[DamaskPassGrain] = []
    for i, p in enumerate(passes, start=1):
        r = max(1e-9, min(0.999999, float(p.get("r", 0.0) or 0.0)))
        eps = float(math.log(1.0 / (1.0 - r)))
        grain_eps = float(eng.material.effective_grain_refinement_strain(eps))
        grain = eng.calculate_grain_size(grain_eps, d0=grain)
        alpha = float(p.get("alpha_deg", 12.0))
        pull, _, _, _ = eng.calculate_pulling_stress_and_safety(alpha, r)
        out.append(
            DamaskPassGrain(
                pass_index=i,
                grain_size_um=float(grain),
                equiv_plastic_strain=eps,
                von_mises_pa=float(pull) * 1e6,
                source="analytical_fallback",
            )
        )
    return out


def run_schedule_grain_with_fallback(
    passes: Sequence[Dict[str, Any]],
    *,
    prefer_damask: bool = True,
    initial_grain_um: float = 15.0,
) -> Tuple[List[DamaskPassGrain], str]:
    """Try DAMASK when requested and available; otherwise analytical fallback."""
    if prefer_damask and damask_grid_executable():
        res = run_schedule_grain_simulation(passes, initial_grain_um=initial_grain_um)
        if res.ok and res.passes:
            return res.passes, res.message
        tail = res.message.strip()
        fb = analytical_grain_fallback(passes, initial_grain_um=initial_grain_um)
        msg = "Analytical grain estimates (DAMASK unavailable or run failed)."
        if tail:
            msg += f" {tail}"
        return fb, msg
    fb = analytical_grain_fallback(passes, initial_grain_um=initial_grain_um)
    return fb, "Analytical grain estimates."


def _worker_main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Tubing Master DAMASK grain worker")
    p.add_argument("--pass-json", required=True, help="JSON list of pass dicts with r, alpha_deg")
    p.add_argument("--initial-grain-um", type=float, default=15.0)
    p.add_argument("--work-dir", default="")
    p.add_argument("--keep-workdir", action="store_true")
    args = p.parse_args(argv)
    passes = json.loads(args.pass_json)
    wd = Path(args.work_dir) if args.work_dir else None
    res = run_schedule_grain_simulation(
        passes,
        initial_grain_um=float(args.initial_grain_um),
        work_dir=wd,
        keep_workdir=bool(args.keep_workdir),
    )
    payload = {
        "ok": res.ok,
        "message": res.message,
        "work_dir": res.work_dir,
        "result_h5": res.result_h5,
        "passes": [
            {
                "pass_index": g.pass_index,
                "grain_size_um": g.grain_size_um,
                "equiv_plastic_strain": g.equiv_plastic_strain,
                "von_mises_pa": g.von_mises_pa,
                "source": g.source,
            }
            for g in res.passes
        ],
    }
    print(json.dumps(payload))
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(_worker_main())
