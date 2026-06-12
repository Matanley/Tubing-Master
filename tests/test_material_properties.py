"""Per-project material property overrides."""

from __future__ import annotations

from tubing_master.material_properties import (
    material_for_project,
    merged_property_dict,
    metal_material_from_property_dict,
    preset_editable_defaults,
)
from tubing_master.engine import TubingMasterEngine


def test_override_changes_flow_stress():
    base = metal_material_from_property_dict("316L stainless steel", None)
    custom = metal_material_from_property_dict(
        "316L stainless steel", {"flow_C_mpa": 1500.0}
    )
    assert custom.flow_C_mpa == 1500.0
    assert base.flow_stress_mpa(0.1) != custom.flow_stress_mpa(0.1)


def test_nitinol_override_sigma_ms():
    mat = metal_material_from_property_dict("Nitinol", {"nitinol": {"sigma_ms_mpa": 320.0}})
    assert mat.nitinol is not None
    assert mat.nitinol.sigma_ms_mpa == 320.0


def test_material_for_project_bundle():
    project = {
        "material": "Nitinol",
        "material_property_overrides": {
            "Nitinol": {"nitinol": {"sigma_af_mpa": 330.0}},
        },
    }
    mat = material_for_project(project)
    assert mat.nitinol is not None
    assert mat.nitinol.sigma_af_mpa == 330.0


def test_engine_uses_override():
    project = {
        "material": "316L stainless steel",
        "material_property_overrides": {
            "316L stainless steel": {"friction_coeff": 0.12},
        },
    }
    mat = material_for_project(project)
    eng = TubingMasterEngine.from_material(mat)
    assert eng.friction_coeff == 0.12


def test_partial_override_preserves_preset_fields():
    merged = merged_property_dict("T2 copper", {"yield_mpa": 80.0})
    defaults = preset_editable_defaults("T2 copper")
    assert merged["yield_mpa"] == 80.0
    assert merged["flow_C_mpa"] == defaults["flow_C_mpa"]
