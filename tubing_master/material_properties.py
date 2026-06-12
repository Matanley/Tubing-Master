"""Serialize, merge, and describe editable material model parameters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from tubing_master.materials import (
    MATERIALS,
    MetalMaterial,
    normalize_material_label,
    _nitinol_params_from_dict,
)
from tubing_master.nitinol_model import NitinolParams, nitinol_transformation_onset_mpa


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, val in override.items():
        if key == "nitinol" and isinstance(val, dict):
            nested = dict(out.get("nitinol") or {})
            nested.update(val)
            out["nitinol"] = nested
        else:
            out[key] = val
    return out


def preset_editable_defaults(label: str) -> Dict[str, Any]:
    """Flat editable property dict for a built-in preset (for dialog + JSON)."""
    name = normalize_material_label(label)
    preset = dict(MATERIALS.get(name) or {})
    model = str(preset.get("model", "isotropic_hardening"))
    out: Dict[str, Any] = {
        "model": model,
        "E_mpa": float(preset.get("E_mpa", 210000.0)),
        "yield_mpa": float(preset.get("yield_mpa", 250.0)),
        "density_kg_m3": float(preset.get("density_kg_m3", 7850.0)),
        "limit_pct": float(preset.get("limit_pct", 35.0)),
        "flow_C_mpa": float(preset.get("flow_C_mpa", 1200.0)),
        "hardening_n": float(preset.get("hardening_n", 0.35)),
        "eps0": float(preset.get("eps0", 0.005)),
        "base_uts_mpa": float(preset.get("base_uts_mpa", 600.0)),
        "uts_hardening_coef": float(preset.get("uts_hardening_coef", 800.0)),
        "grain_refinement_m": float(preset.get("grain_refinement_m", 0.45)),
        "min_grain_um": float(preset.get("min_grain_um", 0.5)),
        "initial_grain_um": float(preset.get("initial_grain_um", 15.0)),
        "friction_coeff": float(preset.get("friction_coeff", 0.05)),
    }
    if model == "nitinol_superelastic" and isinstance(preset.get("nitinol"), dict):
        out["nitinol"] = dict(preset["nitinol"])
    return out


def merged_property_dict(label: str, override: Dict[str, Any] | None) -> Dict[str, Any]:
    base = preset_editable_defaults(label)
    if not override:
        return base
    return _deep_merge(base, override)


def metal_material_from_property_dict(
    label: str, override: Dict[str, Any] | None = None
) -> MetalMaterial:
    """Build :class:`MetalMaterial` from preset + optional project overrides."""
    name = normalize_material_label(label)
    props = merged_property_dict(name, override)
    model = str(props.get("model", "isotropic_hardening"))
    nitinol_raw = props.get("nitinol")
    nitinol: NitinolParams | None = None
    if model == "nitinol_superelastic" and isinstance(nitinol_raw, dict):
        nitinol = _nitinol_params_from_dict(nitinol_raw)
    yield_mpa = float(props.get("yield_mpa", 250.0))
    if nitinol is not None:
        yield_mpa = nitinol_transformation_onset_mpa(nitinol)
    e_mpa = float(props.get("E_mpa", 210000.0))
    if nitinol is not None:
        e_mpa = float(nitinol.e_austenite_mpa)
    return MetalMaterial(
        name=str(name or label),
        model_kind=model,  # type: ignore[arg-type]
        e_mpa=e_mpa,
        yield_mpa=yield_mpa,
        flow_C_mpa=float(props.get("flow_C_mpa", 1200.0)),
        hardening_n=float(props.get("hardening_n", 0.35)),
        eps0=float(props.get("eps0", 0.005)),
        base_uts_mpa=float(props.get("base_uts_mpa", 600.0)),
        uts_hardening_coef=float(props.get("uts_hardening_coef", 800.0)),
        grain_refinement_m=float(props.get("grain_refinement_m", 0.45)),
        min_grain_um=float(props.get("min_grain_um", 0.5)),
        initial_grain_um=float(props.get("initial_grain_um", 15.0)),
        friction_coeff=float(props.get("friction_coeff", 0.05)),
        nitinol=nitinol,
    )


def material_for_project(project: Dict[str, Any]) -> MetalMaterial:
    label = str(project.get("material", ""))
    overrides_map = project.get("material_property_overrides") or {}
    override = None
    if isinstance(overrides_map, dict):
        key = normalize_material_label(label)
        raw = overrides_map.get(key) or overrides_map.get(label)
        if isinstance(raw, dict) and raw:
            override = raw
    return metal_material_from_property_dict(label, override)


def property_dict_from_metal_material(mat: MetalMaterial) -> Dict[str, Any]:
    """Current resolved properties (preset + any merged state) as editable dict."""
    props = preset_editable_defaults(mat.name)
    props["E_mpa"] = float(mat.e_mpa)
    props["yield_mpa"] = float(mat.yield_mpa)
    props["flow_C_mpa"] = float(mat.flow_C_mpa)
    props["hardening_n"] = float(mat.hardening_n)
    props["eps0"] = float(mat.eps0)
    props["base_uts_mpa"] = float(mat.base_uts_mpa)
    props["uts_hardening_coef"] = float(mat.uts_hardening_coef)
    props["grain_refinement_m"] = float(mat.grain_refinement_m)
    props["min_grain_um"] = float(mat.min_grain_um)
    props["initial_grain_um"] = float(mat.initial_grain_um)
    props["friction_coeff"] = float(mat.friction_coeff)
    props["model"] = mat.model_kind
    if mat.nitinol is not None:
        p = mat.nitinol
        props["nitinol"] = {
            "e_austenite_mpa": p.e_austenite_mpa,
            "e_martensite_mpa": p.e_martensite_mpa,
            "sigma_ms_mpa": p.sigma_ms_mpa,
            "sigma_mf_mpa": p.sigma_mf_mpa,
            "sigma_as_mpa": p.sigma_as_mpa,
            "sigma_af_mpa": p.sigma_af_mpa,
            "transformation_strain": p.transformation_strain,
            "martensite_eps0": p.martensite_eps0,
            "martensite_C_mpa": p.martensite_C_mpa,
            "martensite_n": p.martensite_n,
            "uts_mpa": p.uts_mpa,
            "uts_strain_coef_mpa": p.uts_strain_coef_mpa,
            "af_temp_c": p.af_temp_c,
            "ms_temp_c": p.ms_temp_c,
        }
    return props


def _diff_from_preset(label: str, props: Dict[str, Any]) -> Dict[str, Any]:
    """Persist only fields that differ from shipped preset."""
    base = preset_editable_defaults(label)
    diff: Dict[str, Any] = {}
    for key, val in props.items():
        if key == "model":
            continue
        if key == "nitinol" and isinstance(val, dict):
            base_n = dict(base.get("nitinol") or {})
            sub: Dict[str, Any] = {}
            for nk, nv in val.items():
                if float(nv) != float(base_n.get(nk, nv)):
                    sub[nk] = float(nv)
            if sub:
                diff["nitinol"] = sub
        elif key != "nitinol":
            try:
                if float(val) != float(base.get(key, val)):
                    diff[key] = float(val)
            except (TypeError, ValueError):
                pass
    return diff


def overrides_differ_from_preset(label: str, props: Dict[str, Any] | None) -> bool:
    if not props:
        return False
    return bool(_diff_from_preset(label, props))


def density_kg_m3_from_properties(label: str, override: Dict[str, Any] | None = None) -> float:
    return float(merged_property_dict(label, override).get("density_kg_m3", 7850.0))


def limit_pct_from_properties(label: str, override: Dict[str, Any] | None = None) -> float:
    return float(merged_property_dict(label, override).get("limit_pct", 35.0))


def internal_model_description(label: str, props: Dict[str, Any] | None = None) -> str:
    """How the analytical engine uses this material (shown at top of property dialog)."""
    merged = merged_property_dict(label, props)
    model = str(merged.get("model", "isotropic_hardening"))
    name = normalize_material_label(label)
    if model == "nitinol_superelastic":
        n = merged.get("nitinol") or {}
        return (
            f"{name} — superelastic Nitinol (illustrative 1D model)\n"
            "Loading (die pull): σ = E_A·ε → plateau σ_ms→σ_mf → martensite hardening.\n"
            "Unloading (springback): reverse plateau σ_af→σ_as; recoverable ε_tr.\n"
            f"SF(UTS) uses UTS ≈ {float(n.get('uts_mpa', 950)):.0f} MPa + strain term.\n"
            "Grain: only martensite plastic strain refines (transformation strain recovers)."
        )
    return (
        f"{name} — isotropic power-law hardening\n"
        f"Flow stress: σ_flow = C·(ε₀ + ε)^n  "
        f"(C={float(merged.get('flow_C_mpa', 1200)):.0f} MPa, n={float(merged.get('hardening_n', 0.35)):.2f}, "
        f"ε₀={float(merged.get('eps0', 0.005)):.4f}).\n"
        f"Pulling stress scales σ_flow by die angle, friction μ={float(merged.get('friction_coeff', 0.05)):.3f}, "
        "and true strain.\n"
        f"UTS ≈ base + k·r^0.7 (base={float(merged.get('base_uts_mpa', 600)):.0f} MPa, "
        f"k={float(merged.get('uts_hardening_coef', 800)):.0f}).\n"
        f"Grain: d_new = max(d/(1+ε^m), d_min) with m={float(merged.get('grain_refinement_m', 0.45)):.2f}."
    )
