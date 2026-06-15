"""Import material properties from tensile-test report PDFs or images (text / OCR)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# (regex, logical field, default unit if omitted)
_TENSILE_PATTERNS: List[Tuple[str, str, str]] = [
    (
        r"(?:young(?:['\u2019]s)?\s*modulus|elastic\s+modulus|modulus\s+of\s+elasticity|\bE\s*modulus\b)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "E_mpa",
        "GPa",
    ),
    (
        r"(?:0\.2\s*%?\s*(?:offset\s*)?(?:yield|proof)|yield\s+strength|proof\s+stress|"
        r"Rp\s*0\.2|Rp0\.2|σ\s*y|Sy|Re\s*0\.2)\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "yield_mpa",
        "MPa",
    ),
    (
        r"(?:ultimate\s+tensile\s+strength|ultimate\s+strength|tensile\s+strength|"
        r"\bUTS\b|Rm|σ\s*u|maximum\s+stress|max\.?\s+load\s+stress)\s*[:=\|]?\s*"
        r"([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "base_uts_mpa",
        "MPa",
    ),
    (
        r"(?:elongation|total\s+elongation|%?\s*elong\.?|A\s*%)\s*[:=\|]?\s*([\d][\d,.\s]*)\s*%?",
        "elongation_pct",
        "%",
    ),
    (
        r"(?:density|ρ)\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(kg/m³|kg/m3|g/cm³|g/cm3)?",
        "density_kg_m3",
        "kg/m3",
    ),
]

# Superelastic Nitinol — loading (upper) and unloading (lower) transformation plateaus.
_NITINOL_PATTERNS: List[Tuple[str, str, str]] = [
    (
        r"(?:σ\s*ms|sigma\s*ms|martensite\s+start|M\s*s\b|upper\s+plateau\s+start|"
        r"loading\s+plateau\s+start|forward\s+transformation\s+start)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "sigma_ms_mpa",
        "MPa",
    ),
    (
        r"(?:σ\s*mf|sigma\s*mf|martensite\s+finish|M\s*f\b|upper\s+plateau\s+end|"
        r"loading\s+plateau\s+end|forward\s+transformation\s+finish|upper\s+plateau\s+stress)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "sigma_mf_mpa",
        "MPa",
    ),
    (
        r"(?:σ\s*as|sigma\s*as|austenite\s+start|A\s*s\b|lower\s+plateau\s+start|"
        r"unloading\s+plateau\s+start|reverse\s+transformation\s+start)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "sigma_as_mpa",
        "MPa",
    ),
    (
        r"(?:σ\s*af|sigma\s*af|austenite\s+finish|A\s*f\b|lower\s+plateau\s+end|"
        r"unloading\s+plateau\s+end|reverse\s+transformation\s+finish|lower\s+plateau\s+stress)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "sigma_af_mpa",
        "MPa",
    ),
    (
        r"(?:transformation\s+strain|superelastic\s+strain|recoverable\s+strain|ε\s*tr|epsilon\s*tr)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*%?",
        "transformation_strain",
        "strain",
    ),
    (
        r"(?:E\s*martensite|martensite\s+modulus|modulus\s+martensite)"
        r"\s*[:=\|]?\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi|N/mm²|N/mm2)?",
        "e_martensite_mpa",
        "GPa",
    ),
]

_CYCLE_HINT_RE = re.compile(
    r"(?:loading\s+and\s+unloading|unloading\s+and\s+loading|full\s+(?:tensile\s+)?cycle|"
    r"hysteresis\s+loop|superelastic\s+(?:cycle|loop)|stress[- ]strain\s+loop|"
    r"upper\s+plateau.*lower\s+plateau|lower\s+plateau.*upper\s+plateau)",
    re.IGNORECASE,
)

_PLATEAU_RANGE_RE = re.compile(
    r"(?:upper|loading|forward)\s+plateau\s*[:=]?\s*"
    r"([\d][\d,.\s]*)\s*(?:–|-|to)\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi)?",
    re.IGNORECASE,
)
_UNLOAD_PLATEAU_RANGE_RE = re.compile(
    r"(?:lower|unloading|reverse)\s+plateau\s*[:=]?\s*"
    r"([\d][\d,.\s]*)\s*(?:–|-|to)\s*([\d][\d,.\s]*)\s*(GPa|MPa|ksi|psi)?",
    re.IGNORECASE,
)


@dataclass
class NitinolCycleAssessment:
    has_loading_plateau: bool
    has_unloading_plateau: bool
    mentions_cycle: bool
    warning: str = ""

    @property
    def has_full_cycle(self) -> bool:
        return self.has_loading_plateau and self.has_unloading_plateau


@dataclass
class TensileComparisonRow:
    """One line in the tensile import review table."""

    report_label: str
    report_value: str
    model_label: str
    fitted_value: str
    current_value: str = ""
    note: str = ""


@dataclass
class TensileImportResult:
    ok: bool
    message: str
    updates: Dict[str, Any] = field(default_factory=dict)
    fields_found: List[str] = field(default_factory=list)
    source_excerpt: str = ""
    warning: str = ""
    source_path: str = ""
    parsed: Dict[str, float] = field(default_factory=dict)
    comparison_rows: List[TensileComparisonRow] = field(default_factory=list)
    nitinol_cycle: Optional[NitinolCycleAssessment] = None


def _parse_number(raw: str) -> float:
    s = raw.strip().replace(",", "").replace(" ", "")
    if not s:
        raise ValueError("empty number")
    return float(s)


def _stress_to_mpa(value: float, unit: str) -> float:
    u = (unit or "MPa").strip().lower().replace("²", "2")
    if u in ("gpa",):
        return value * 1000.0
    if u in ("mpa", "n/mm2", "n/mm²"):
        return value
    if u in ("ksi",):
        return value * 6.894757
    if u in ("psi",):
        return value * 0.006894757
    return value


def _density_to_kg_m3(value: float, unit: str) -> float:
    u = (unit or "kg/m3").strip().lower().replace("³", "3")
    if u in ("g/cm3", "g/cm³"):
        return value * 1000.0
    return value


def _strain_value(raw: float, unit: str) -> float:
    u = (unit or "strain").strip().lower()
    if u in ("%", "pct", "percent"):
        return raw / 100.0
    if raw > 1.0:
        return raw / 100.0
    return raw


def _apply_patterns(
    blob: str,
    patterns: List[Tuple[str, str, str]],
    found: Dict[str, float],
) -> None:
    for pattern, key, default_unit in patterns:
        if key in found:
            continue
        m = re.search(pattern, blob, flags=re.IGNORECASE)
        if not m:
            continue
        val = _parse_number(m.group(1))
        unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else None) or default_unit
        if key == "elongation_pct":
            found[key] = val
        elif key == "density_kg_m3":
            found[key] = _density_to_kg_m3(val, unit)
        elif key == "transformation_strain":
            found[key] = _strain_value(val, unit)
        else:
            found[key] = _stress_to_mpa(val, unit)


def _apply_nitinol_plateau_ranges(blob: str, found: Dict[str, float]) -> None:
    m = _PLATEAU_RANGE_RE.search(blob)
    if m:
        unit = m.group(3) or "MPa"
        lo = _stress_to_mpa(_parse_number(m.group(1)), unit)
        hi = _stress_to_mpa(_parse_number(m.group(2)), unit)
        found.setdefault("sigma_ms_mpa", min(lo, hi))
        found.setdefault("sigma_mf_mpa", max(lo, hi))
    m2 = _UNLOAD_PLATEAU_RANGE_RE.search(blob)
    if m2:
        unit = m2.group(3) or "MPa"
        lo = _stress_to_mpa(_parse_number(m2.group(1)), unit)
        hi = _stress_to_mpa(_parse_number(m2.group(2)), unit)
        found.setdefault("sigma_as_mpa", min(lo, hi))
        found.setdefault("sigma_af_mpa", max(lo, hi))


def parse_tensile_text(text: str) -> Dict[str, float]:
    """Extract tensile metrics from report text (best-effort regex)."""
    if not text or not text.strip():
        return {}
    blob = " ".join(text.split())
    found: Dict[str, float] = {}
    _apply_patterns(blob, _TENSILE_PATTERNS, found)
    _apply_patterns(blob, _NITINOL_PATTERNS, found)
    _apply_nitinol_plateau_ranges(blob, found)
    return found


def assess_nitinol_cycle(text: str, parsed: Dict[str, float]) -> NitinolCycleAssessment:
    """Check whether a report includes both loading and unloading superelastic plateaus."""
    blob = " ".join((text or "").split())
    has_loading = "sigma_ms_mpa" in parsed and "sigma_mf_mpa" in parsed
    has_unloading = "sigma_as_mpa" in parsed and "sigma_af_mpa" in parsed
    mentions_cycle = bool(_CYCLE_HINT_RE.search(blob))
    warning = ""
    has_full = has_loading and has_unloading
    if not has_full:
        parts: List[str] = [
            "Nitinol needs a full superelastic tensile loop (loading and unloading) with both "
            "transformation plateaus:"
        ]
        parts.append("• Upper (loading) plateau: σ_ms and σ_mf (martensite start/finish)")
        parts.append("• Lower (unloading) plateau: σ_as and σ_af (austenite start/finish)")
        missing: List[str] = []
        if not has_loading:
            missing.append("loading / upper plateau (σ_ms, σ_mf)")
        if not has_unloading:
            missing.append("unloading / lower plateau (σ_as, σ_af)")
        parts.append(f"Missing from this report: {', '.join(missing)}.")
        if not mentions_cycle:
            parts.append(
                "The file does not mention a full hysteresis / loading–unloading cycle — "
                "use a superelastic loop test report, not a monotonic tensile certificate."
            )
        parts.append("Only non-plateau fields were applied (e.g. E, UTS). Enter plateaus manually.")
        warning = "\n".join(parts)
    return NitinolCycleAssessment(
        has_loading_plateau=has_loading,
        has_unloading_plateau=has_unloading,
        mentions_cycle=mentions_cycle,
        warning=warning,
    )


def _estimate_flow_c_mpa(yield_mpa: float, *, eps0: float, n: float) -> float:
    proof = 0.002
    denom = (eps0 + proof) ** max(n, 1e-6)
    return float(yield_mpa / max(denom, 1e-12))


def build_property_updates(
    parsed: Dict[str, float],
    *,
    model: str,
    eps0: float = 0.005,
    hardening_n: float = 0.35,
    nitinol_cycle: Optional[NitinolCycleAssessment] = None,
) -> Dict[str, Any]:
    """Map parsed tensile metrics to editable material property keys."""
    if not parsed:
        return {}
    updates: Dict[str, Any] = {}
    e = parsed.get("E_mpa")
    y = parsed.get("yield_mpa")
    uts = parsed.get("base_uts_mpa")
    elong = parsed.get("elongation_pct")
    rho = parsed.get("density_kg_m3")

    if model == "nitinol_superelastic":
        nit: Dict[str, float] = {}
        if e is not None:
            nit["e_austenite_mpa"] = e
            updates["E_mpa"] = e
        if uts is not None:
            nit["uts_mpa"] = uts
        if parsed.get("e_martensite_mpa") is not None:
            nit["e_martensite_mpa"] = float(parsed["e_martensite_mpa"])
        if parsed.get("transformation_strain") is not None:
            nit["transformation_strain"] = float(parsed["transformation_strain"])
        elif elong is not None and 0.5 <= elong <= 25.0 and nitinol_cycle and nitinol_cycle.has_full_cycle:
            nit["transformation_strain"] = min(0.12, max(0.01, elong / 100.0 * 0.35))

        if nitinol_cycle and nitinol_cycle.has_full_cycle:
            for src, dst in (
                ("sigma_ms_mpa", "sigma_ms_mpa"),
                ("sigma_mf_mpa", "sigma_mf_mpa"),
                ("sigma_as_mpa", "sigma_as_mpa"),
                ("sigma_af_mpa", "sigma_af_mpa"),
            ):
                if src in parsed:
                    nit[dst] = float(parsed[src])
        if nit:
            updates["nitinol"] = nit
    else:
        if e is not None:
            updates["E_mpa"] = e
        if y is not None:
            updates["yield_mpa"] = y
            updates["flow_C_mpa"] = _estimate_flow_c_mpa(y, eps0=eps0, n=hardening_n)
        if uts is not None:
            updates["base_uts_mpa"] = uts

    if rho is not None:
        updates["density_kg_m3"] = rho
    return updates


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Install pypdf to read PDF tensile reports: pip install pypdf") from exc
    reader = PdfReader(str(path))
    parts: List[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_image_text(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Image OCR requires Pillow and pytesseract (and the Tesseract app: brew install tesseract)."
        ) from exc
    img = Image.open(path)
    return pytesseract.image_to_string(img)


def extract_text_from_tensile_file(path: Path | str) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    ext = p.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf_text(p)
    if ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"):
        return _extract_image_text(p)
    raise ValueError(f"Unsupported tensile report type: {ext} (use PDF, JPEG, or PNG).")


def _flatten_property_updates(updates: Dict[str, Any]) -> Dict[str, float]:
    flat: Dict[str, float] = {}
    for key, val in updates.items():
        if key == "nitinol" and isinstance(val, dict):
            for nk, nv in val.items():
                flat[f"nitinol.{nk}"] = float(nv)
        elif isinstance(val, (int, float)):
            flat[key] = float(val)
    return flat


def _fmt_mpa(value: float) -> str:
    return f"{value:.0f} MPa"


def _fmt_strain(value: float) -> str:
    return f"{value:.4f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.1f} %"


def _fmt_density(value: float) -> str:
    return f"{value:.0f} kg/m³"


_MODEL_LABELS: Dict[str, str] = {
    "E_mpa": "Young's modulus E",
    "yield_mpa": "Yield strength",
    "flow_C_mpa": "Flow stress C",
    "base_uts_mpa": "UTS base",
    "density_kg_m3": "Density ρ",
    "nitinol.e_austenite_mpa": "E austenite E_A",
    "nitinol.e_martensite_mpa": "E martensite E_M",
    "nitinol.sigma_ms_mpa": "σ_ms (upper plateau start)",
    "nitinol.sigma_mf_mpa": "σ_mf (upper plateau end)",
    "nitinol.sigma_as_mpa": "σ_as (lower plateau start)",
    "nitinol.sigma_af_mpa": "σ_af (lower plateau end)",
    "nitinol.transformation_strain": "Transformation strain ε_tr",
    "nitinol.uts_mpa": "UTS",
}


def _format_model_value(model_key: str, value: float) -> str:
    if model_key.endswith("transformation_strain") or model_key.endswith("strain"):
        return _fmt_strain(value)
    if model_key == "density_kg_m3":
        return _fmt_density(value)
    return _fmt_mpa(value)


def _comparison_row(
    *,
    report_label: str,
    report_value: str,
    model_key: str,
    fitted: Dict[str, float],
    current: Dict[str, float],
    note: str = "",
) -> TensileComparisonRow:
    model_label = _MODEL_LABELS.get(model_key, model_key)
    fitted_value = _format_model_value(model_key, fitted[model_key]) if model_key in fitted else "—"
    current_value = (
        _format_model_value(model_key, current[model_key]) if model_key in current else "—"
    )
    if fitted_value == "—" and not note:
        note = "not applied"
    return TensileComparisonRow(
        report_label=report_label,
        report_value=report_value,
        model_label=model_label,
        fitted_value=fitted_value,
        current_value=current_value,
        note=note,
    )


def build_tensile_comparison_rows(
    parsed: Dict[str, float],
    updates: Dict[str, Any],
    *,
    model: str,
    current: Optional[Dict[str, float]] = None,
    nitinol_cycle: Optional[NitinolCycleAssessment] = None,
) -> List[TensileComparisonRow]:
    """Side-by-side rows: tensile report values vs fitted model properties."""
    fitted = _flatten_property_updates(updates)
    cur = dict(current or {})
    rows: List[TensileComparisonRow] = []

    if model == "nitinol_superelastic":
        if "E_mpa" in parsed or "E_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Young's modulus E",
                    report_value=_fmt_mpa(parsed["E_mpa"]) if "E_mpa" in parsed else "—",
                    model_key="nitinol.e_austenite_mpa",
                    fitted=fitted,
                    current=cur,
                    note="direct" if "nitinol.e_austenite_mpa" in fitted else "",
                )
            )
        for rk, rlabel, mk in (
            ("sigma_ms_mpa", "Upper plateau σ_ms", "nitinol.sigma_ms_mpa"),
            ("sigma_mf_mpa", "Upper plateau σ_mf", "nitinol.sigma_mf_mpa"),
            ("sigma_as_mpa", "Lower plateau σ_as", "nitinol.sigma_as_mpa"),
            ("sigma_af_mpa", "Lower plateau σ_af", "nitinol.sigma_af_mpa"),
        ):
            applied = mk in fitted
            note = "direct" if applied else "not applied — need full loading/unloading cycle"
            rows.append(
                _comparison_row(
                    report_label=rlabel,
                    report_value=_fmt_mpa(parsed[rk]) if rk in parsed else "—",
                    model_key=mk,
                    fitted=fitted,
                    current=cur,
                    note=note if not applied else "direct",
                )
            )
        if "transformation_strain" in parsed or "nitinol.transformation_strain" in fitted:
            note = "direct"
            if (
                "nitinol.transformation_strain" in fitted
                and "transformation_strain" not in parsed
                and "elongation_pct" in parsed
            ):
                note = "estimated from elongation"
            rows.append(
                TensileComparisonRow(
                    report_label="Transformation strain ε_tr",
                    report_value=(
                        _fmt_strain(parsed["transformation_strain"])
                        if "transformation_strain" in parsed
                        else (
                            _fmt_pct(parsed["elongation_pct"])
                            if "elongation_pct" in parsed
                            else "—"
                        )
                    ),
                    model_label=_MODEL_LABELS["nitinol.transformation_strain"],
                    fitted_value=(
                        _format_model_value(
                            "nitinol.transformation_strain", fitted["nitinol.transformation_strain"]
                        )
                        if "nitinol.transformation_strain" in fitted
                        else "—"
                    ),
                    current_value=(
                        _format_model_value(
                            "nitinol.transformation_strain", cur["nitinol.transformation_strain"]
                        )
                        if "nitinol.transformation_strain" in cur
                        else "—"
                    ),
                    note=note if "nitinol.transformation_strain" in fitted else "not applied",
                )
            )
        if "e_martensite_mpa" in parsed or "nitinol.e_martensite_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Martensite modulus E_M",
                    report_value=(
                        _fmt_mpa(parsed["e_martensite_mpa"]) if "e_martensite_mpa" in parsed else "—"
                    ),
                    model_key="nitinol.e_martensite_mpa",
                    fitted=fitted,
                    current=cur,
                    note="direct" if "nitinol.e_martensite_mpa" in fitted else "",
                )
            )
        if "base_uts_mpa" in parsed or "nitinol.uts_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Ultimate tensile strength",
                    report_value=(
                        _fmt_mpa(parsed["base_uts_mpa"]) if "base_uts_mpa" in parsed else "—"
                    ),
                    model_key="nitinol.uts_mpa",
                    fitted=fitted,
                    current=cur,
                    note="direct" if "nitinol.uts_mpa" in fitted else "",
                )
            )
    else:
        if "E_mpa" in parsed or "E_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Young's modulus E",
                    report_value=_fmt_mpa(parsed["E_mpa"]) if "E_mpa" in parsed else "—",
                    model_key="E_mpa",
                    fitted=fitted,
                    current=cur,
                    note="direct" if "E_mpa" in fitted else "",
                )
            )
        if "yield_mpa" in parsed or "yield_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Yield / proof stress",
                    report_value=_fmt_mpa(parsed["yield_mpa"]) if "yield_mpa" in parsed else "—",
                    model_key="yield_mpa",
                    fitted=fitted,
                    current=cur,
                    note="direct" if "yield_mpa" in fitted else "",
                )
            )
        if "yield_mpa" in parsed and "flow_C_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Yield / proof stress",
                    report_value=_fmt_mpa(parsed["yield_mpa"]),
                    model_key="flow_C_mpa",
                    fitted=fitted,
                    current=cur,
                    note="estimated from yield (power-law fit)",
                )
            )
        if "base_uts_mpa" in parsed or "base_uts_mpa" in fitted:
            rows.append(
                _comparison_row(
                    report_label="Ultimate tensile strength",
                    report_value=(
                        _fmt_mpa(parsed["base_uts_mpa"]) if "base_uts_mpa" in parsed else "—"
                    ),
                    model_key="base_uts_mpa",
                    fitted=fitted,
                    current=cur,
                    note="direct" if "base_uts_mpa" in fitted else "",
                )
            )

    if "density_kg_m3" in parsed or "density_kg_m3" in fitted:
        rows.append(
            _comparison_row(
                report_label="Density",
                report_value=(
                    _fmt_density(parsed["density_kg_m3"]) if "density_kg_m3" in parsed else "—"
                ),
                model_key="density_kg_m3",
                fitted=fitted,
                current=cur,
                note="direct" if "density_kg_m3" in fitted else "",
            )
        )

    if "elongation_pct" in parsed and model != "nitinol_superelastic":
        rows.append(
            TensileComparisonRow(
                report_label="Elongation",
                report_value=_fmt_pct(parsed["elongation_pct"]),
                model_label="—",
                fitted_value="—",
                current_value="—",
                note="informational (not mapped to model)",
            )
        )

    if nitinol_cycle and not nitinol_cycle.has_full_cycle and model == "nitinol_superelastic":
        for rk, rlabel in (
            ("yield_mpa", "Yield / proof (monotonic)"),
        ):
            if rk in parsed:
                rows.append(
                    TensileComparisonRow(
                        report_label=rlabel,
                        report_value=_fmt_mpa(parsed[rk]),
                        model_label="—",
                        fitted_value="—",
                        current_value="—",
                        note="not used for Nitinol plateaus",
                    )
                )

    return rows


def _format_import_labels(parsed: Dict[str, float], updates: Dict[str, Any], *, model: str) -> List[str]:
    labels: List[str] = []
    if "E_mpa" in parsed:
        labels.append(f"E (austenite) = {parsed['E_mpa']:.0f} MPa")
    if model == "nitinol_superelastic":
        if "sigma_ms_mpa" in parsed:
            labels.append(f"Upper plateau σ_ms = {parsed['sigma_ms_mpa']:.0f} MPa")
        if "sigma_mf_mpa" in parsed:
            labels.append(f"Upper plateau σ_mf = {parsed['sigma_mf_mpa']:.0f} MPa")
        if "sigma_as_mpa" in parsed:
            labels.append(f"Lower plateau σ_as = {parsed['sigma_as_mpa']:.0f} MPa")
        if "sigma_af_mpa" in parsed:
            labels.append(f"Lower plateau σ_af = {parsed['sigma_af_mpa']:.0f} MPa")
        if "transformation_strain" in parsed:
            labels.append(f"ε_tr = {parsed['transformation_strain']:.4f}")
    else:
        if "yield_mpa" in parsed:
            labels.append(f"Yield = {parsed['yield_mpa']:.0f} MPa")
        if "flow_C_mpa" in updates:
            labels.append(f"Flow C estimated = {updates['flow_C_mpa']:.0f} MPa")
    if "base_uts_mpa" in parsed:
        labels.append(f"UTS = {parsed['base_uts_mpa']:.0f} MPa")
    if "elongation_pct" in parsed:
        labels.append(f"Elongation = {parsed['elongation_pct']:.1f} %")
    if "density_kg_m3" in parsed:
        labels.append(f"Density = {parsed['density_kg_m3']:.0f} kg/m³")
    return labels


def import_tensile_test_file(
    path: Path | str,
    *,
    model: str,
    eps0: float = 0.005,
    hardening_n: float = 0.35,
) -> TensileImportResult:
    """Read a tensile report file and return property updates for the material dialog."""
    try:
        text = extract_text_from_tensile_file(path)
    except Exception as exc:
        return TensileImportResult(ok=False, message=str(exc))

    excerpt = " ".join(text.split())[:400]
    if len(excerpt) < 20:
        return TensileImportResult(
            ok=False,
            message=(
                "Could not read enough text from this file. "
                "Use a text-based PDF or a clear JPEG/PNG scan; for images install Tesseract OCR."
            ),
            source_excerpt=excerpt,
        )

    parsed = parse_tensile_text(text)
    nitinol_cycle: Optional[NitinolCycleAssessment] = None
    warning = ""
    if model == "nitinol_superelastic":
        nitinol_cycle = assess_nitinol_cycle(text, parsed)
        warning = nitinol_cycle.warning

    updates = build_property_updates(
        parsed,
        model=model,
        eps0=eps0,
        hardening_n=hardening_n,
        nitinol_cycle=nitinol_cycle,
    )
    if not updates:
        if model == "nitinol_superelastic":
            return TensileImportResult(
                ok=False,
                message=(
                    "No recognizable Nitinol tensile data found. "
                    "Provide a superelastic loop report with E, upper plateau (σ_ms, σ_mf), "
                    "and lower plateau (σ_as, σ_af)."
                ),
                warning=warning,
                source_excerpt=excerpt,
            )
        return TensileImportResult(
            ok=False,
            message=(
                "No recognizable tensile values (E, yield, UTS) were found. "
                "Check that the report lists Young's modulus, yield / proof stress, and tensile strength."
            ),
            source_excerpt=excerpt,
        )

    labels = _format_import_labels(parsed, updates, model=model)
    comparison = build_tensile_comparison_rows(
        parsed,
        updates,
        model=model,
        nitinol_cycle=nitinol_cycle,
    )
    return TensileImportResult(
        ok=True,
        message="Imported from tensile report:\n" + "\n".join(f"• {x}" for x in labels),
        updates=updates,
        fields_found=list(parsed.keys()),
        source_excerpt=excerpt,
        warning=warning,
        source_path=str(Path(path).resolve()),
        parsed=dict(parsed),
        comparison_rows=comparison,
        nitinol_cycle=nitinol_cycle,
    )
