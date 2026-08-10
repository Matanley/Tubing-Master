"""Per-project material property overrides."""

from __future__ import annotations

from tubing_master.material_properties import (
    blank_material_template,
    is_builtin_material,
    material_for_project,
    merged_property_dict,
    metal_material_from_property_dict,
    preset_editable_defaults,
)
from tubing_master.engine import TubingMasterEngine


def test_override_changes_flow_stress():
    base = metal_material_from_property_dict("316 Stainless Steel", None)
    custom = metal_material_from_property_dict(
        "316 Stainless Steel", {"flow_C_mpa": 1500.0}
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
        "material": "316 Stainless Steel",
        "material_property_overrides": {
            "316 Stainless Steel": {"friction_coeff": 0.12},
        },
    }
    mat = material_for_project(project)
    eng = TubingMasterEngine.from_material(mat)
    assert eng.friction_coeff == 0.12


def test_partial_override_preserves_preset_fields():
    merged = merged_property_dict("Copper Alloy", {"yield_mpa": 80.0})
    defaults = preset_editable_defaults("Copper Alloy")
    assert merged["yield_mpa"] == 80.0
    assert merged["flow_C_mpa"] == defaults["flow_C_mpa"]


def test_blank_material_template_and_custom_material():
    assert is_builtin_material("316 Stainless Steel")
    assert not is_builtin_material("Inconel 718 custom")
    tmpl = blank_material_template()
    assert tmpl["model"] == "isotropic_hardening"
    assert tmpl["custom"] is True
    props = dict(tmpl)
    props["yield_mpa"] = 900.0
    props["flow_C_mpa"] = 1400.0
    mat = metal_material_from_property_dict("Inconel 718 custom", props)
    assert mat.name == "Inconel 718 custom"
    assert mat.yield_mpa == 900.0
    assert mat.flow_C_mpa == 1400.0


def test_custom_material_for_project():
    project = {
        "material": "My alloy",
        "material_property_overrides": {
            "My alloy": {
                **blank_material_template(),
                "yield_mpa": 420.0,
                "density_kg_m3": 4500.0,
            },
        },
    }
    mat = material_for_project(project)
    assert mat.name == "My alloy"
    assert mat.yield_mpa == 420.0
    merged = merged_property_dict("My alloy", project["material_property_overrides"]["My alloy"])
    assert merged["density_kg_m3"] == 4500.0
