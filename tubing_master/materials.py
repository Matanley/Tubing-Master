"""Material presets (extends notebook expert panel)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from tubing_master.nitinol_model import (
    NitinolParams,
    NitinolPassMetrics,
    nitinol_flow_stress_mpa,
    nitinol_loading_stress_mpa,
    nitinol_pass_metrics,
    nitinol_superelastic_note,
    nitinol_transformation_onset_mpa,
    nitinol_ultimate_strength_mpa,
)

MaterialModelKind = Literal["isotropic_hardening", "nitinol_superelastic"]

MATERIALS: Dict[str, Dict[str, Any]] = {
    "316L stainless steel": {
        "limit_pct": 35.0,
        "color": "cyan",
        "model": "isotropic_hardening",
        "E_mpa": 193000.0,
        "yield_mpa": 205.0,
        "density_kg_m3": 8000.0,
        "flow_C_mpa": 1200.0,
        "hardening_n": 0.35,
        "eps0": 0.005,
        "base_uts_mpa": 600.0,
        "uts_hardening_coef": 800.0,
        "grain_refinement_m": 0.45,
        "min_grain_um": 0.5,
        "friction_coeff": 0.05,
        "note": "Strong work hardening; use good lubrication.",
        "suggested_ht_temp_c": 1050,
        "suggested_ht_time_min": 45,
    },
    "T2 copper": {
        "limit_pct": 50.0,
        "color": "orange",
        "model": "isotropic_hardening",
        "E_mpa": 115000.0,
        "yield_mpa": 70.0,
        "density_kg_m3": 8960.0,
        "flow_C_mpa": 1100.0,
        "hardening_n": 0.42,
        "eps0": 0.005,
        "base_uts_mpa": 220.0,
        "uts_hardening_coef": 400.0,
        "grain_refinement_m": 0.40,
        "min_grain_um": 0.5,
        "friction_coeff": 0.04,
        "note": "High ductility; large reductions possible.",
        "suggested_ht_temp_c": 550,
        "suggested_ht_time_min": 60,
    },
    "Nitinol": {
        "limit_pct": 12.0,
        "color": "magenta",
        "model": "nitinol_superelastic",
        "E_mpa": 69000.0,
        "yield_mpa": 280.0,
        "density_kg_m3": 6450.0,
        "grain_refinement_m": 0.08,
        "min_grain_um": 12.0,
        "initial_grain_um": 20.0,
        "friction_coeff": 0.04,
        "nitinol": {
            "e_austenite_mpa": 69000.0,
            "e_martensite_mpa": 41000.0,
            "sigma_ms_mpa": 280.0,
            "sigma_mf_mpa": 420.0,
            "sigma_as_mpa": 140.0,
            "sigma_af_mpa": 360.0,
            "transformation_strain": 0.055,
            "martensite_eps0": 0.002,
            "martensite_C_mpa": 680.0,
            "martensite_n": 0.28,
            "uts_mpa": 950.0,
            "uts_strain_coef_mpa": 120.0,
            "af_temp_c": 10.0,
            "ms_temp_c": -30.0,
        },
        "note": (
            "Superelastic NiTi shape-memory alloy: stress-induced martensite plateau, "
            "very small per-pass bites, cool lubrication, avoid overheating."
        ),
        "suggested_ht_temp_c": 450,
        "suggested_ht_time_min": 30,
    },
    "Mild steel (illustrative)": {
        "limit_pct": 40.0,
        "color": "steelblue",
        "model": "isotropic_hardening",
        "E_mpa": 210000.0,
        "yield_mpa": 250.0,
        "density_kg_m3": 7850.0,
        "flow_C_mpa": 1200.0,
        "hardening_n": 0.35,
        "eps0": 0.005,
        "base_uts_mpa": 600.0,
        "uts_hardening_coef": 800.0,
        "grain_refinement_m": 0.45,
        "min_grain_um": 0.5,
        "friction_coeff": 0.05,
        "note": "Illustrative mechanical properties for scheduling.",
        "suggested_ht_temp_c": 680,
        "suggested_ht_time_min": 45,
    },
}

# Legacy project / history labels → current preset name.
MATERIAL_ALIASES: Dict[str, str] = {
    "NiTi": "Nitinol",
    "niti": "Nitinol",
}

DRAWING_METHODS: List[str] = [
    "Sink Drawing (Rodless Drawing)",
    "Rod drawing (Mandrel)",
    "Floating plug",
]


def normalize_material_label(label: str) -> str:
    """Map saved aliases (e.g. ``NiTi``) to current preset names."""
    key = str(label or "").strip()
    if not key:
        return key
    if key in MATERIALS:
        return key
    alias = MATERIAL_ALIASES.get(key) or MATERIAL_ALIASES.get(key.lower())
    if alias and alias in MATERIALS:
        return alias
    return key


def material_names() -> List[str]:
    return list(MATERIALS.keys())


def material_density_kg_m3(material_name: str) -> float:
    """Illustrative mass density for stock-weight estimate (kg/m³)."""
    m = MATERIALS.get(normalize_material_label(material_name)) or {}
    return float(m.get("density_kg_m3", 7850.0))


def suggested_interpass_ht_temperature_c(material_name: str) -> int:
    """Nominal inter-pass heat-treat temperature (°C) for Pass Schedule defaults — illustrative."""
    m = MATERIALS.get(normalize_material_label(material_name)) or {}
    return int(m.get("suggested_ht_temp_c", 650))


def suggested_interpass_hold_time_min(material_name: str) -> int:
    """Nominal hold time (minutes) paired with ``suggested_interpass_ht_temperature_c``."""
    m = MATERIALS.get(normalize_material_label(material_name)) or {}
    return int(m.get("suggested_ht_time_min", 45))


def material_limit_pct(material_name: str) -> float:
    m = MATERIALS.get(normalize_material_label(material_name)) or {}
    return float(m.get("limit_pct", 35.0))


@dataclass
class MetalMaterial:
    """Preset fields used by the analytical engine, optimization, and FEA UI."""

    name: str
    model_kind: MaterialModelKind = "isotropic_hardening"
    e_mpa: float = 210000.0
    yield_mpa: float = 250.0
    flow_C_mpa: float = 1200.0
    hardening_n: float = 0.35
    eps0: float = 0.005
    base_uts_mpa: float = 600.0
    uts_hardening_coef: float = 800.0
    grain_refinement_m: float = 0.45
    min_grain_um: float = 0.5
    initial_grain_um: float = 15.0
    friction_coeff: float = 0.05
    nitinol: Optional[NitinolParams] = None

    def effective_grain_refinement_strain(self, true_strain: float) -> float:
        """Strain contributing to grain refinement (SMA transformation strain is recoverable)."""
        eps = max(0.0, float(true_strain))
        if self.is_nitinol() and self.nitinol is not None:
            p = self.nitinol
            eps_ms = p.sigma_ms_mpa / max(p.e_austenite_mpa, 1e-6)
            eps_tr_end = eps_ms + p.transformation_strain
            eps_mart = max(0.0, eps - eps_tr_end)
            return eps_mart * 0.12
        return eps

    def flow_stress_mpa(self, true_strain: float) -> float:
        if self.model_kind == "nitinol_superelastic" and self.nitinol is not None:
            return nitinol_loading_stress_mpa(true_strain, self.nitinol)
        eps = max(0.0, float(true_strain))
        return float(self.flow_C_mpa * (self.eps0 + eps) ** self.hardening_n)

    def nitinol_hysteresis_for_pass(
        self, true_strain: float, *, loading_flow_stress_mpa: float | None = None
    ) -> NitinolPassMetrics | None:
        if not self.is_nitinol() or self.nitinol is None:
            return None
        return nitinol_pass_metrics(
            true_strain,
            self.nitinol,
            loading_flow_stress_mpa=loading_flow_stress_mpa,
        )

    def ultimate_strength_mpa(
        self, true_strain: float, *, reduction_fraction: float = 0.0
    ) -> float:
        if self.model_kind == "nitinol_superelastic" and self.nitinol is not None:
            return nitinol_ultimate_strength_mpa(
                true_strain, self.nitinol, reduction_fraction=reduction_fraction
            )
        r = max(0.0, float(reduction_fraction))
        eps = max(0.0, float(true_strain))
        del eps
        return float(self.base_uts_mpa + self.uts_hardening_coef * (r**0.7))

    def is_nitinol(self) -> bool:
        return self.model_kind == "nitinol_superelastic"

    def fea_reference_stress_mpa(self) -> float:
        """Reference strength for elastic FEA pass/fail hints."""
        if self.is_nitinol() and self.nitinol is not None:
            return nitinol_transformation_onset_mpa(self.nitinol)
        return float(self.yield_mpa)

    def model_description(self) -> str:
        if self.is_nitinol() and self.nitinol is not None:
            return nitinol_superelastic_note(self.nitinol)
        return f"Isotropic power-law hardening (n={self.hardening_n:.2f})."


MATERIAL_PRESET_LABELS: tuple[str, ...] = tuple(MATERIALS.keys())


def _nitinol_params_from_dict(raw: Dict[str, Any]) -> NitinolParams:
    return NitinolParams(
        e_austenite_mpa=float(raw.get("e_austenite_mpa", 69000.0)),
        e_martensite_mpa=float(raw.get("e_martensite_mpa", 41000.0)),
        sigma_ms_mpa=float(raw.get("sigma_ms_mpa", 280.0)),
        sigma_mf_mpa=float(raw.get("sigma_mf_mpa", 420.0)),
        sigma_as_mpa=float(raw.get("sigma_as_mpa", 140.0)),
        sigma_af_mpa=float(raw.get("sigma_af_mpa", 360.0)),
        transformation_strain=float(raw.get("transformation_strain", 0.055)),
        martensite_eps0=float(raw.get("martensite_eps0", 0.002)),
        martensite_C_mpa=float(raw.get("martensite_C_mpa", 680.0)),
        martensite_n=float(raw.get("martensite_n", 0.28)),
        uts_mpa=float(raw.get("uts_mpa", 950.0)),
        uts_strain_coef_mpa=float(raw.get("uts_strain_coef_mpa", 120.0)),
        af_temp_c=float(raw.get("af_temp_c", 10.0)),
        ms_temp_c=float(raw.get("ms_temp_c", -30.0)),
    )


def material_from_preset_label(label: str) -> MetalMaterial:
    name = normalize_material_label(label)
    m = MATERIALS.get(name) or {}
    model = str(m.get("model", "isotropic_hardening"))
    nitinol_raw = m.get("nitinol")
    nitinol = (
        _nitinol_params_from_dict(nitinol_raw)
        if model == "nitinol_superelastic" and isinstance(nitinol_raw, dict)
        else None
    )
    yield_mpa = float(m.get("yield_mpa", 250.0))
    if nitinol is not None:
        yield_mpa = nitinol_transformation_onset_mpa(nitinol)
    return MetalMaterial(
        name=str(name or label),
        model_kind=model,  # type: ignore[arg-type]
        e_mpa=float(m.get("E_mpa", 210000.0)),
        yield_mpa=yield_mpa,
        flow_C_mpa=float(m.get("flow_C_mpa", 1200.0)),
        hardening_n=float(m.get("hardening_n", 0.35)),
        eps0=float(m.get("eps0", 0.005)),
        base_uts_mpa=float(m.get("base_uts_mpa", 600.0)),
        uts_hardening_coef=float(m.get("uts_hardening_coef", 800.0)),
        grain_refinement_m=float(m.get("grain_refinement_m", 0.45)),
        min_grain_um=float(m.get("min_grain_um", 0.5)),
        initial_grain_um=float(m.get("initial_grain_um", 15.0)),
        friction_coeff=float(m.get("friction_coeff", 0.05)),
        nitinol=nitinol,
    )
