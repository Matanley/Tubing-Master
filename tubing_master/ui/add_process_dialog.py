"""Dialog to insert a manual process row between Pass Schedule steps."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

PROCESS_KIND_ORDER: tuple[str, ...] = (
    "reducer",
    "drawing",
    "annealing",
    "straightening",
    "sandblasting",
)

PROCESS_KIND_LABELS: Dict[str, str] = {
    "reducer": "Reducer",
    "drawing": "Drawing",
    "annealing": "Annealing",
    "straightening": "Straightening",
    "sandblasting": "Sandblasting",
}

PROCESS_EQUIPMENT_PRESETS: Dict[str, List[str]] = {
    "reducer": [
        "Tube reducer",
        "Rotary swager",
        "Pointing / tagger",
        "Pilger mill (prep)",
    ],
    "drawing": [
        "Drawbench",
        "Hydraulic drawbench",
        "Chain drawbench",
        "Bull block",
    ],
    "annealing": [
        "Continuous annealing furnace",
        "Batch furnace",
        "Vacuum furnace",
        "Bell furnace",
    ],
    "straightening": [
        "Roll straightener",
        "Rotary straightener",
        "Two-roll straightener",
    ],
    "sandblasting": [
        "Sandblast booth",
        "Grit blast cabinet",
        "Shot blast machine",
    ],
}

_AUX_PROCESS_KINDS = frozenset(
    {"reducer", "annealing", "straightening", "sandblasting"}
)


def normalize_process_kind(raw: Any, *, area_reduction: float = 0.0) -> str:
    """Map saved/UI labels to canonical kind keys."""
    s = str(raw or "").strip().lower().replace(" ", "_")
    aliases = {
        "draw": "drawing",
        "drawing_pass": "drawing",
        "ht": "annealing",
        "heat_treatment": "annealing",
        "anneal": "annealing",
        "straightend": "straightening",
        "straightending": "straightening",
        "straighten": "straightening",
        "blast": "sandblasting",
        "sand_blast": "sandblasting",
        "sandblast": "sandblasting",
        "reduce": "reducer",
        "incoming": "reducer",
        "incoming_tube": "reducer",
    }
    s = aliases.get(s, s)
    if s in PROCESS_KIND_LABELS:
        return s
    if float(area_reduction or 0.0) > 0.0:
        return "drawing"
    return "drawing"


def is_auxiliary_process_kind(kind: str) -> bool:
    return normalize_process_kind(kind) in _AUX_PROCESS_KINDS


def process_kind_display_label(kind: str) -> str:
    k = normalize_process_kind(kind)
    return PROCESS_KIND_LABELS.get(k, k.title())


class AddProcessDialog(QDialog):
    """Choose process type, equipment, and type-specific parameters for a new schedule row."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        default_kind: str = "annealing",
        default_temp_c: int = 650,
        default_time_min: int = 45,
        title: str = "Add process",
        initial: Dict[str, Any] | None = None,
        allowed_kinds: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self._result: Optional[Dict[str, Any]] = None
        self._initial = dict(initial or {})
        self._allowed = tuple(allowed_kinds) if allowed_kinds else PROCESS_KIND_ORDER

        root = QVBoxLayout(self)
        intro = QLabel(
            "Choose process type, equipment, and parameters. Drawing changes tube size; "
            "Reducer / Annealing / Straightening / Sandblasting keep geometry."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #334155; font-size: 11px;")
        root.addWidget(intro)

        form = QFormLayout()
        self.kind_combo = QComboBox()
        for key in self._allowed:
            if key in PROCESS_KIND_LABELS:
                self.kind_combo.addItem(PROCESS_KIND_LABELS[key], key)
        start_kind = normalize_process_kind(
            self._initial.get("kind") or default_kind,
            area_reduction=float(self._initial.get("r", 0) or 0),
        )
        idx = self.kind_combo.findData(start_kind)
        if idx < 0:
            idx = self.kind_combo.findData(normalize_process_kind(default_kind))
        self.kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Process type", self.kind_combo)

        self.equipment_combo = QComboBox()
        self.equipment_combo.setEditable(True)
        self.equipment_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.equipment_combo.setToolTip(
            "Type a name or pick from the plant list. New names can be saved to the plant equipment list."
        )
        form.addRow("Equipment", self.equipment_combo)
        eq_hint = QLabel("Type freely; if the name is new you will be asked to save it to the plant list.")
        eq_hint.setWordWrap(True)
        eq_hint.setStyleSheet("color: #64748b; font-size: 10px;")
        form.addRow("", eq_hint)
        root.addLayout(form)

        self._stack = QStackedWidget()
        self._page_drawing = self._build_drawing_page()
        self._page_annealing = self._build_annealing_page(default_temp_c, default_time_min)
        self._page_params = self._build_params_page()
        self._stack.addWidget(self._page_drawing)
        self._stack.addWidget(self._page_annealing)
        self._stack.addWidget(self._page_params)
        root.addWidget(self._stack)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self._apply_initial_values()
        self._on_kind_changed()
        # Re-apply equipment/notes after kind change cleared presets.
        self._apply_initial_values(equipment_only=True)

    def _apply_initial_values(self, *, equipment_only: bool = False) -> None:
        src = self._initial
        if not src:
            return
        eq = str(src.get("equipment", "") or "").strip()
        if eq:
            if self.equipment_combo.findText(eq) < 0:
                self.equipment_combo.setEditText(eq)
            else:
                self.equipment_combo.setCurrentText(eq)
        if equipment_only:
            return
        try:
            self.draw_r.setValue(float(src.get("r", self.draw_r.value()) or self.draw_r.value()))
            self.draw_alpha.setValue(
                float(src.get("alpha_deg", self.draw_alpha.value()) or self.draw_alpha.value())
            )
            self.draw_mu.setValue(float(src.get("mu", self.draw_mu.value()) or self.draw_mu.value()))
        except (TypeError, ValueError):
            pass
        self.draw_notes.setText(str(src.get("notes", "") or ""))
        notes = str(src.get("notes", "") or "")
        self.ann_notes.setText(notes)
        self.aux_notes.setText(notes)
        self.aux_params.setText("")
        if src.get("temperature") not in (None, ""):
            try:
                self.ann_temp.setValue(int(round(float(src["temperature"]))))
            except (TypeError, ValueError):
                pass
        if src.get("time") not in (None, ""):
            try:
                self.ann_time.setValue(int(round(float(src["time"]))))
            except (TypeError, ValueError):
                pass
        gas = str(src.get("protective_gas", "") or "").strip()
        if gas:
            gi = self.ann_gas.findText(gas)
            if gi >= 0:
                self.ann_gas.setCurrentIndex(gi)
            else:
                self.ann_gas.insertItem(1, gas)
                self.ann_gas.setCurrentIndex(1)

    def _build_drawing_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.draw_r = QDoubleSpinBox()
        self.draw_r.setRange(0.001, 0.999)
        self.draw_r.setDecimals(4)
        self.draw_r.setSingleStep(0.01)
        self.draw_r.setValue(0.12)
        form.addRow("Area reduction r", self.draw_r)

        self.draw_alpha = QDoubleSpinBox()
        self.draw_alpha.setRange(1.0, 45.0)
        self.draw_alpha.setDecimals(1)
        self.draw_alpha.setSuffix(" °")
        self.draw_alpha.setValue(12.0)
        form.addRow("Semi-die angle", self.draw_alpha)

        self.draw_mu = QDoubleSpinBox()
        self.draw_mu.setRange(0.0, 0.5)
        self.draw_mu.setDecimals(3)
        self.draw_mu.setSingleStep(0.01)
        self.draw_mu.setValue(0.06)
        form.addRow("Friction μ", self.draw_mu)

        self.draw_notes = QLineEdit()
        self.draw_notes.setPlaceholderText("Optional notes")
        form.addRow("Notes", self.draw_notes)
        return w

    def _build_annealing_page(self, default_temp_c: int, default_time_min: int) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.ann_temp = QSpinBox()
        self.ann_temp.setRange(50, 1300)
        self.ann_temp.setSuffix(" °C")
        self.ann_temp.setValue(int(default_temp_c))
        form.addRow("Temperature", self.ann_temp)

        self.ann_time = QSpinBox()
        self.ann_time.setRange(0, 1440)
        self.ann_time.setSuffix(" min")
        self.ann_time.setValue(int(default_time_min))
        form.addRow("Hold time", self.ann_time)

        self.ann_gas = QComboBox()
        for lab in (
            "—",
            "Air",
            "Nitrogen",
            "Argon",
            "Argon + 6% H₂",
            "Forming gas (H₂/N₂)",
            "Hydrogen",
            "Vacuum / sealed furnace",
        ):
            self.ann_gas.addItem(lab)
        form.addRow("Protective gas", self.ann_gas)

        self.ann_notes = QLineEdit()
        self.ann_notes.setPlaceholderText("Optional notes")
        form.addRow("Notes", self.ann_notes)
        return w

    def _build_params_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.aux_params = QLineEdit()
        self.aux_params.setPlaceholderText(
            "e.g. pressure, media, roll count, line speed — stored in Notes"
        )
        form.addRow("Process parameters", self.aux_params)
        self.aux_notes = QLineEdit()
        self.aux_notes.setPlaceholderText("Optional extra notes")
        form.addRow("Notes", self.aux_notes)
        return w

    def _current_kind(self) -> str:
        data = self.kind_combo.currentData()
        return normalize_process_kind(data or "drawing")

    def _on_kind_changed(self) -> None:
        from tubing_master.plant_equipment import equipment_choices_for_kind

        kind = self._current_kind()
        choices = equipment_choices_for_kind(kind)
        current = self.equipment_combo.currentText().strip()
        self.equipment_combo.blockSignals(True)
        self.equipment_combo.clear()
        for p in choices:
            self.equipment_combo.addItem(p)
        if current:
            if self.equipment_combo.findText(current) < 0:
                self.equipment_combo.setEditText(current)
            else:
                self.equipment_combo.setCurrentText(current)
        elif choices:
            self.equipment_combo.setCurrentIndex(0)
        self.equipment_combo.blockSignals(False)

        if kind == "drawing":
            self._stack.setCurrentWidget(self._page_drawing)
        elif kind == "annealing":
            self._stack.setCurrentWidget(self._page_annealing)
        else:
            hints = {
                "reducer": "Reducer parameters (point length, reduction %, …)",
                "straightening": "Straightening parameters (rolls, speed, …)",
                "sandblasting": "Sandblasting parameters (pressure, media, …)",
            }
            self.aux_params.setPlaceholderText(hints.get(kind, "Process parameters"))
            self._stack.setCurrentWidget(self._page_params)

    def _maybe_save_equipment_to_plant(self, kind: str, equipment: str) -> None:
        from tubing_master.plant_equipment import add_plant_equipment, equipment_known_for_kind

        name = (equipment or "").strip()
        if not name or equipment_known_for_kind(kind, name):
            return
        reply = QMessageBox.question(
            self,
            "Save equipment to plant list?",
            f'“{name}” is not in the plant equipment list for '
            f"{process_kind_display_label(kind)}.\n\n"
            "Plants usually keep a limited equipment set. Save this name to the list "
            "so it appears next time?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            add_plant_equipment(kind, name)

    def _on_accept(self) -> None:
        kind = self._current_kind()
        equipment = self.equipment_combo.currentText().strip()
        self._maybe_save_equipment_to_plant(kind, equipment)
        if kind == "drawing":
            self._result = {
                "kind": kind,
                "r": float(self.draw_r.value()),
                "alpha_deg": float(self.draw_alpha.value()),
                "mu": float(self.draw_mu.value()),
                "equipment": equipment,
                "notes": self.draw_notes.text().strip(),
                "temperature": "",
                "time": "",
                "protective_gas": "",
            }
        elif kind == "annealing":
            gas = self.ann_gas.currentText().strip()
            self._result = {
                "kind": kind,
                "r": 0.0,
                "alpha_deg": 0.0,
                "mu": 0.0,
                "equipment": equipment,
                "notes": self.ann_notes.text().strip(),
                "temperature": str(self.ann_temp.value()),
                "time": str(self.ann_time.value()),
                "protective_gas": "" if gas == "—" else gas,
            }
        else:
            params = self.aux_params.text().strip()
            notes = self.aux_notes.text().strip()
            if params and notes:
                notes_out = f"{params}; {notes}"
            else:
                notes_out = params or notes
            self._result = {
                "kind": kind,
                "r": 0.0,
                "alpha_deg": 0.0,
                "mu": 0.0,
                "equipment": equipment,
                "notes": notes_out,
                "temperature": "",
                "time": "",
                "protective_gas": "",
            }
        self.accept()

    def result_process(self) -> Optional[Dict[str, Any]]:
        return dict(self._result) if self._result else None


def run_add_process_dialog(
    parent: QWidget | None,
    *,
    default_kind: str = "annealing",
    default_temp_c: int = 650,
    default_time_min: int = 45,
    title: str = "Add process",
    initial: Dict[str, Any] | None = None,
    allowed_kinds: tuple[str, ...] | None = None,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    dlg = AddProcessDialog(
        parent,
        default_kind=default_kind,
        default_temp_c=default_temp_c,
        default_time_min=default_time_min,
        title=title,
        initial=initial,
        allowed_kinds=allowed_kinds,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None, False
    return dlg.result_process(), True
