"""Tests for DAMASK input generation and analytical fallback (no solver required)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tubing_master.damask_support import (
    analytical_grain_fallback,
    damask_available,
    prepare_damask_workdir,
    write_drawing_load_yaml,
    write_polycrystal_material_yaml,
    write_polycrystal_vti,
)


class DamaskSupportTests(unittest.TestCase):
    def test_analytical_fallback_decreases_grain_with_strain(self) -> None:
        passes = [{"r": 0.15, "alpha_deg": 12.0}, {"r": 0.15, "alpha_deg": 12.0}]
        rows = analytical_grain_fallback(passes)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[1].grain_size_um, rows[0].grain_size_um)
        self.assertEqual(rows[0].source, "analytical_fallback")

    def test_prepare_workdir_writes_inputs(self) -> None:
        passes = [{"r": 0.1, "alpha_deg": 10.0}, {"r": 0.12, "alpha_deg": 11.0}]
        with tempfile.TemporaryDirectory() as td:
            wd = prepare_damask_workdir(Path(td), passes)
            self.assertTrue((wd / "load.yaml").is_file())
            self.assertTrue((wd / "material.yaml").is_file())
            self.assertTrue((wd / "polycrystal.vti").is_file())
            vti = (wd / "polycrystal.vti").read_text(encoding="utf-8")
            self.assertIn("CellData", vti)
            load = (wd / "load.yaml").read_text(encoding="utf-8")
            self.assertIn("loadstep:", load)
            self.assertEqual(load.count("discretization:"), 2)

    def test_vti_material_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g.vti"
            write_polycrystal_vti(p, cells=(7, 7, 0), n_grains=4)
            text = p.read_text(encoding="utf-8")
            self.assertIn("material", text)
            write_polycrystal_material_yaml(Path(td) / "m.yaml", n_grains=4)
            mat = (Path(td) / "m.yaml").read_text(encoding="utf-8")
            self.assertEqual(mat.count("homogenization: SX"), 4)

    def test_load_yaml_strain_steps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "load.yaml"
            write_drawing_load_yaml(path, [0.1, 0.2])
            self.assertTrue(path.is_file())

    def test_damask_available_is_bool(self) -> None:
        self.assertIsInstance(damask_available(), bool)


if __name__ == "__main__":
    unittest.main()
