"""Dialog to view and edit per-project material model parameters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
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
from tubing_master.tensile_import import (
    TensileImportResult,
    build_tensile_comparison_rows,
    import_tensile_test_file,
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


class TensileFitReviewDialog(QDialog):
    """Side-by-side review of report values vs fitted model properties."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        result: TensileImportResult,
        model: str,
        current_values: Dict[str, float],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tensile test fit review")
        self.setMinimumSize(780, 440)

        root = QVBoxLayout(self)
        src_name = Path(result.source_path).name if result.source_path else "report"
        intro = QLabel(
            f"Compare values from <b>{src_name}</b> with the fitted material model. "
            "Apply only if they are close enough for your simulation."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        unit_parts: list[str] = []
        if result.stress_unit:
            unit_parts.append(f"stress axis: {result.stress_unit}")
        if result.modulus_unit:
            unit_parts.append(f"modulus: {result.modulus_unit}")
        if result.strain_unit:
            unit_parts.append(f"strain axis: {result.strain_unit}")
        if unit_parts:
            unit_lbl = QLabel(
                "Detected diagram units — " + ", ".join(unit_parts) + ". "
                "Values without their own suffix were converted using these axis scales."
            )
            unit_lbl.setWordWrap(True)
            unit_lbl.setStyleSheet("color: #1e40af; font-size: 11px; padding: 4px;")
            root.addWidget(unit_lbl)

        if result.warning:
            warn = QLabel(result.warning)
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "background: #fef3c7; color: #92400e; padding: 8px; border-radius: 4px;"
            )
            root.addWidget(warn)

        rows = build_tensile_comparison_rows(
            result.parsed,
            result.updates,
            model=model,
            current=current_values,
            nitinol_cycle=result.nitinol_cycle,
            value_units=result.value_units,
        )

        table = QTableWidget(len(rows), 6)
        table.setHorizontalHeaderLabels(
            [
                "Report property",
                "From report",
                "Model property",
                "Fitted value",
                "Current value",
                "Note",
            ]
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        align_right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for row_idx, row in enumerate(rows):
            cells = (
                row.report_label,
                row.report_value,
                row.model_label,
                row.fitted_value,
                row.current_value,
                row.note,
            )
            for col_idx, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col_idx in (1, 3, 4):
                    item.setTextAlignment(align_right)
                table.setItem(row_idx, col_idx, item)

        root.addWidget(table, stretch=1)

        if result.source_excerpt:
            excerpt = QLabel(f"<i>Source excerpt:</i> {result.source_excerpt}")
            excerpt.setWordWrap(True)
            excerpt.setStyleSheet("color: #64748b; font-size: 10px;")
            root.addWidget(excerpt)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.setText("Apply fitted values")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


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
        import_btn = QPushButton("Import tensile test...")
        import_btn.setToolTip(
            "Read tensile data from a PDF or JPEG/PNG report.\n"
            "Detects stress (Y-axis) and strain (X-axis) units from diagram labels "
            "(e.g. ksi, MPa, %, strain) and converts values for the model.\n"
            "Nitinol: use a full superelastic loading–unloading loop with upper plateau "
            "(σ_ms, σ_mf) and lower plateau (σ_as, σ_af); monotonic reports trigger a warning.\n"
            "Images need Tesseract OCR (brew install tesseract; pip install pytesseract)."
        )
        import_btn.clicked.connect(self._import_tensile_test)
        reset_btn = QPushButton("Reset to preset defaults")
        reset_btn.clicked.connect(self._reset_to_preset)
        btn_row.addWidget(import_btn)
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

    def _apply_property_updates(self, updates: Dict[str, Any]) -> int:
        """Apply parsed tensile values to spin boxes; returns count of fields updated."""
        n = 0
        for key, spin in self._spinners.items():
            if "." in key:
                head, tail = key.split(".", 1)
                nested = updates.get(head)
                if not isinstance(nested, dict) or tail not in nested:
                    continue
                val = float(nested[tail])
            elif key in updates:
                val = float(updates[key])
            else:
                continue
            lo, hi = float(spin.minimum()), float(spin.maximum())
            spin.blockSignals(True)
            spin.setValue(max(lo, min(hi, val)))
            spin.blockSignals(False)
            n += 1
        return n

    def _import_tensile_test(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import tensile test report",
            str(Path.home()),
            "Tensile reports (*.pdf *.PDF *.jpg *.jpeg *.png *.tif *.tiff *.bmp);;All files (*)",
        )
        if not path:
            return
        props = self._collect_properties()
        result = import_tensile_test_file(
            path,
            model=self._model,
            eps0=float(props.get("eps0", 0.005)),
            hardening_n=float(props.get("hardening_n", 0.35)),
        )
        if not result.ok:
            QMessageBox.warning(self, "Import tensile test", result.message)
            return
        current_values = {key: spin.value() for key, spin in self._spinners.items()}
        review = TensileFitReviewDialog(
            self,
            result=result,
            model=self._model,
            current_values=current_values,
        )
        if review.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_property_updates(result.updates)
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
