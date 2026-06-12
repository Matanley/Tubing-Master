"""Dialog to view and edit per-project material model parameters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tubing_master.material_properties import (
    _diff_from_preset,
    internal_model_description,
    merged_property_dict,
    overrides_differ_from_preset,
    preset_editable_defaults,
)


def _spin(
    *,
    minimum: float,
    maximum: float,
    decimals: int,
    value: float,
    suffix: str = "",
) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setDecimals(decimals)
    s.setValue(float(value))
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


FieldSpec = Tuple[str, str, float, float, int, str, str]


_COMMON_FIELDS: List[FieldSpec] = [
    ("E_mpa", "Young's modulus E", 1000.0, 500000.0, 0, " MPa", "Elastic & scheduling"),
    ("density_kg_m3", "Density ρ", 1000.0, 25000.0, 1, " kg/m³", "Elastic & scheduling"),
    ("limit_pct", "Max per-pass bite hint", 1.0, 60.0, 1, " %", "Elastic & scheduling"),
    ("friction_coeff", "Die friction μ (analytical)", 0.0, 0.5, 3, "", "Elastic & scheduling"),
    ("initial_grain_um", "Initial grain size d₀", 0.1, 200.0, 2, " µm", "Grain refinement"),
    ("grain_refinement_m", "Refinement exponent m", 0.01, 1.5, 3, "", "Grain refinement"),
    ("min_grain_um", "Minimum grain d_min", 0.1, 50.0, 2, " µm", "Grain refinement"),
]

_ISOTROPIC_FIELDS: List[FieldSpec] = [
    ("yield_mpa", "Yield (FEA reference)", 10.0, 2000.0, 0, " MPa", "Isotropic hardening"),
    ("flow_C_mpa", "Flow stress C", 100.0, 5000.0, 0, " MPa", "Isotropic hardening"),
    ("hardening_n", "Hardening exponent n", 0.01, 1.0, 3, "", "Isotropic hardening"),
    ("eps0", "Offset strain ε₀", 0.0, 0.1, 4, "", "Isotropic hardening"),
    ("base_uts_mpa", "UTS base", 50.0, 3000.0, 0, " MPa", "Strength / SF(UTS)"),
    ("uts_hardening_coef", "UTS vs r coefficient k", 0.0, 3000.0, 0, " MPa", "Strength / SF(UTS)"),
]

_NITINOL_FIELDS: List[FieldSpec] = [
    ("nitinol.e_austenite_mpa", "E austenite E_A", 10000.0, 120000.0, 0, " MPa", "Loading path"),
    ("nitinol.sigma_ms_mpa", "σ_ms (martensite start)", 50.0, 800.0, 0, " MPa", "Loading path"),
    ("nitinol.sigma_mf_mpa", "σ_mf (martensite finish)", 50.0, 900.0, 0, " MPa", "Loading path"),
    ("nitinol.transformation_strain", "Transformation strain ε_tr", 0.01, 0.12, 4, "", "Loading path"),
    ("nitinol.e_martensite_mpa", "E martensite E_M", 10000.0, 80000.0, 0, " MPa", "Unloading path"),
    ("nitinol.sigma_af_mpa", "σ_af (austenite finish)", 50.0, 800.0, 0, " MPa", "Unloading path"),
    ("nitinol.sigma_as_mpa", "σ_as (austenite start)", 20.0, 600.0, 0, " MPa", "Unloading path"),
    ("nitinol.martensite_C_mpa", "Martensite C", 100.0, 3000.0, 0, " MPa", "Martensite / UTS"),
    ("nitinol.martensite_n", "Martensite n", 0.05, 0.8, 3, "", "Martensite / UTS"),
    ("nitinol.martensite_eps0", "Martensite ε₀", 0.0, 0.05, 4, "", "Martensite / UTS"),
    ("nitinol.uts_mpa", "UTS", 200.0, 2000.0, 0, " MPa", "Martensite / UTS"),
    ("nitinol.uts_strain_coef_mpa", "UTS strain coef.", 0.0, 500.0, 0, " MPa", "Martensite / UTS"),
    ("nitinol.af_temp_c", "Af temperature", -100.0, 100.0, 0, " °C", "Transformation temps"),
    ("nitinol.ms_temp_c", "Ms temperature", -150.0, 50.0, 0, " °C", "Transformation temps"),
]


def _get_nested(props: Dict[str, Any], key: str) -> float:
    if "." not in key:
        return float(props.get(key, 0.0))
    head, tail = key.split(".", 1)
    nested = props.get(head) or {}
    return float(nested.get(tail, 0.0))


def _set_nested(props: Dict[str, Any], key: str, value: float) -> None:
    if "." not in key:
        props[key] = float(value)
        return
    head, tail = key.split(".", 1)
    nested = dict(props.get(head) or {})
    nested[tail] = float(value)
    props[head] = nested


def _fields_for_model(model: str) -> List[FieldSpec]:
    fields = list(_COMMON_FIELDS)
    if model == "nitinol_superelastic":
        fields.extend(_NITINOL_FIELDS)
    else:
        fields.extend(_ISOTROPIC_FIELDS)
    return fields


class MaterialPropertiesDialog(QDialog):
    """Edit analytical material parameters for the current preset."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        material_label: str,
        current_override: Dict[str, Any] | None,
    ) -> None:
        super().__init__(parent)
        self._material_label = material_label
        self._preset = preset_editable_defaults(material_label)
        self._model = str(self._preset.get("model", "isotropic_hardening"))
        self._spinners: Dict[str, QDoubleSpinBox] = {}

        self.setWindowTitle(f"Material properties — {material_label}")
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)

        root = QVBoxLayout(self)

        self._desc = QLabel(internal_model_description(material_label, merged_property_dict(material_label, current_override)))
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet("color: #334155; font-size: 11px; padding: 4px;")
        root.addWidget(self._desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)

        model_lbl = QLabel(f"Internal model: {self._model}")
        model_lbl.setStyleSheet("font-weight: 600;")
        inner_layout.addWidget(model_lbl)

        groups: Dict[str, QFormLayout] = {}
        for key, label, lo, hi, dec, suffix, group in _fields_for_model(self._model):
            if group not in groups:
                box = QGroupBox(group)
                groups[group] = QFormLayout(box)
                inner_layout.addWidget(box)
            spin = _spin(
                minimum=lo,
                maximum=hi,
                decimals=dec,
                value=_get_nested(merged_property_dict(material_label, current_override), key),
                suffix=suffix,
            )
            spin.valueChanged.connect(self._refresh_description)
            groups[group].addRow(label, spin)
            self._spinners[key] = spin

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to preset defaults")
        reset_btn.clicked.connect(self._reset_to_preset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._saved_override: Optional[Dict[str, Any]] = None
        self._cleared = False

    def _collect_properties(self) -> Dict[str, Any]:
        props = dict(self._preset)
        if self._model == "nitinol_superelastic":
            props["nitinol"] = dict(self._preset.get("nitinol") or {})
        for key, spin in self._spinners.items():
            _set_nested(props, key, spin.value())
        return props

    def _refresh_description(self) -> None:
        self._desc.setText(
            internal_model_description(self._material_label, self._collect_properties())
        )

    def _reset_to_preset(self) -> None:
        defaults = preset_editable_defaults(self._material_label)
        for key, spin in self._spinners.items():
            spin.blockSignals(True)
            spin.setValue(_get_nested(defaults, key))
            spin.blockSignals(False)
        self._refresh_description()

    def _on_save(self) -> None:
        props = self._collect_properties()
        if overrides_differ_from_preset(self._material_label, props):
            self._saved_override = _diff_from_preset(self._material_label, props)
            self._cleared = False
        else:
            self._saved_override = None
            self._cleared = True
        self.accept()

    def result_override(self) -> Tuple[Optional[Dict[str, Any]], bool]:
        return self._saved_override, self._cleared


def run_material_properties_dialog(
    parent: QWidget | None,
    *,
    material_label: str,
    current_override: Dict[str, Any] | None,
) -> Tuple[Optional[Dict[str, Any]], bool, bool]:
    """Returns ``(override, cleared, accepted)``."""
    dlg = MaterialPropertiesDialog(
        parent, material_label=material_label, current_override=current_override
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return current_override, False, False
    override, cleared = dlg.result_override()
    return override, cleared, True
