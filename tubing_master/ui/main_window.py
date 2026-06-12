"""Qt main window for Tubing Master."""

from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import numpy as np

from tubing_master.damask_support import damask_can_run, damask_status_message
from tubing_master.dolfinx_sim import dolfinx_available
from tubing_master.excel_export import (
    write_pass_bom_xlsx,
    write_process_document_xlsx,
    write_quotation_xlsx,
)
from tubing_master.engine import (
    PassInput,
    equal_r_per_pass_for_target_id,
    equal_r_per_pass_for_target_od,
    final_pass_r_after_uniform_prefix_for_target_id,
    final_pass_r_after_uniform_prefix_for_target_od,
    predict_id_for_scaled_od,
    predict_od_for_scaled_id,
    simulate_schedule,
)
from tubing_master.geometry import (
    TubeGeometry,
    implied_area_reduction_fraction,
    tube_from_od_id_m,
)
from tubing_master.material_properties import (
    density_kg_m3_from_properties,
    metal_material_from_property_dict,
)
from tubing_master.materials import (
    MATERIAL_PRESET_LABELS,
    MetalMaterial,
    material_density_kg_m3,
    normalize_material_label,
    suggested_interpass_hold_time_min,
    suggested_interpass_ht_temperature_c,
)
from tubing_master.ui.material_properties_dialog import run_material_properties_dialog
from tubing_master.quotation import (
    PROCESS_CHARGE_LABELS,
    process_charge_description_default,
    apply_line_extended,
    finalize_quotation_v2,
    stock_mass_kg,
)
from tubing_master.fea_hybrid import (
    HYBRID_FEA_TOP_K,
    pick_fea_best_schedule,
    verify_pass_schedule_fea,
    verify_top_schedules_fea,
)
from tubing_master.fea_optimize import (
    fea_verify_top_analytical_schedules,
    optimize_multi_pass_schedule_fea,
)
from tubing_master.fea_tube_die import run_tube_die_pass_subprocess
from tubing_master.optimization import (
    OPT_SCHEDULE_MAX_PER_PASS_R,
    OPT_SCHEDULE_MAX_PASSES,
    OPT_SCHEDULE_MIN_PER_PASS_R,
    OptimizationConfig,
    optimize_multi_pass_schedule,
    recommended_pass_count,
)
from tubing_master.bom_detail import compute_pass_die_rows, merge_detail_into_pass_bom_payload
from tubing_master.die_inventory import (
    DEFAULT_ANGLE_TOLERANCE_DEG,
    DEFAULT_BEARING_LENGTH_MM,
    draw_pass_match_details,
    inventory_alpha_updates_before_last_pass,
    normalize_die_records,
    snap_alpha_deg_from_inventory,
)
from tubing_master.die_schematic import (
    DEFAULT_PANEL_OD_MAX_MM,
    DEFAULT_PANEL_OD_MIN_MM,
    DieSchematicSpec,
    empty_die_schematic_spec,
)
from tubing_master.app_paths import default_export_dir, suggested_projects_dir
from tubing_master.project_history import (
    TubingProjectRecord,
    default_history_path,
    find_closest,
    find_exact,
    load_project_bundle,
    load_projects,
    split_bundle,
    make_bundle_from_parts,
    make_record_from_ui,
    now_iso_utc,
    save_project_bundle,
    upsert_project,
    workdir_projects_dir,
)
from tubing_master.ui.cross_section_strip import (
    CrossSectionPassSegment,
    CrossSectionStripModel,
    CrossSectionStripWidget,
)
from tubing_master.ui.die_schematic_widget import DieSchematicWidget
from tubing_master.ui.fea_pass_schematic_widget import FeaPassSchematicWidget
from tubing_master.fea_pass_schematic import FeaPassSchematicSpec, tooling_kind_from_drawing_method

# Minimum table rows before Tubing Project / Optuna-derived sizing runs.
_MIN_PASS_TABLE_ROWS = 4

# Pass Schedule table: row 0 = Incoming (Output OD/ID = tubing incoming; Mandrel/Plug unused); cols 0–1 outputs, 2–5 mechanics + Mandrel/Plug; 6–10 = HT / notes (saved in JSON).
_PASS_SCHEDULE_COL_COUNT = 11
_COL_OUTPUT_OD = 0
_COL_OUTPUT_ID = 1
_COL_AREA_REDUCTION = 2
_COL_SEMI_DIE_ANGLE = 3
_COL_LUBRICANT = 4
_COL_MANDREL_PLUG = 5
_COL_PS_TEMPERATURE = 6
_COL_PS_TIME = 7
_COL_PS_PROTECTIVE_GAS = 8
_COL_PS_EQUIPMENT = 9
_COL_PS_NOTES = 10
# Process BOM table: Lubricant column must show full preset labels (no ellipsis).
_BOM_COL_LUBRICANT = 7
# Die-inventory match shading applies only to this column (semi-die α), not the whole row.
_PASS_SCHEDULE_DIE_MATCH_COL = _COL_SEMI_DIE_ANGLE
# Protective atmosphere presets for Pass Schedule inter-pass heat treatment (column 8).
_PROTECTIVE_GAS_OPTIONS: tuple[str, ...] = (
    "—",
    "Air",
    "Nitrogen",
    "Argon",
    "Argon + 6% H₂",
    "Forming gas (H₂/N₂)",
    "Hydrogen",
    "Vacuum / sealed furnace",
)
# Display names map to friction coefficient μ for the analytical engine.
_LUBRICANT_PRESETS_MM: list[tuple[str, float]] = [
    ("Dry / boundary (μ≈0.02)", 0.02),
    ("Low-viscosity oil (μ≈0.04)", 0.04),
    ("Drawing oil — typical (μ≈0.06)", 0.06),
    ("Heavy-duty compound (μ≈0.08)", 0.08),
    ("Soap / stiff compound (μ≈0.10)", 0.10),
    ("High-pressure lubricant (μ≈0.12)", 0.12),
]

# Pass Schedule tab index (used for cross-section strip height sync).
_TAB_INDEX_PASS_SCHEDULE = 1
_TAB_INDEX_OPTIMIZATION = 2
_TAB_INDEX_FEA = 3
# Die inventory tab index (shop dies vs schedule).
_TAB_INDEX_DIE_INVENTORY = 5
# Quotation tab index (auto-refresh quote lines when selected).
_TAB_INDEX_QUOTATION = 6
# Nearest history rows shown in Judgment + copied to ./suggested_projects/ for Fetch History.
_CLOSEST_HISTORY_REFERENCE_COUNT = 3

# Pass Schedule row shading (die availability vs inventory).
_DIE_INV_COL_COUNT = 10
_DIE_INV_COL_NAME = 0
_DIE_INV_COL_ALPHA = 1
_DIE_INV_COL_OD_MIN = 2
_DIE_INV_COL_OD_MAX = 3
_DIE_INV_COL_BEAR = 4
_DIE_INV_COL_MATERIAL = 5
_DIE_INV_COL_SUPPLIER = 6
_DIE_INV_COL_STOCK = 7
_DIE_INV_COL_QUANTITY = 8
_DIE_INV_COL_NOTES = 9

_DIE_ALERT_AVAILABLE_BG = "#d4edda"
_DIE_ALERT_UNAVAILABLE_BG = "#fff3cd"
_DIE_ALERT_NONE_BG = "#f8d7da"
_DIE_ALERT_EMPTY_INV_BG = "#e9ecef"
_DIE_ALERT_NO_GEOM_BG = "#dee2e6"


def _pass_table_row_count_for_draws(n_draw: int) -> int:
    """Each drawing pass occupies one table row (no separate inter-pass rows)."""
    return max(1, min(OPT_SCHEDULE_MAX_PASSES, int(n_draw)))


def _pass_table_total_rows_for_draw_count(n_draw: int) -> int:
    """Incoming summary row + one row per draw pass."""
    return 1 + _pass_table_row_count_for_draws(n_draw)


# First Pass Schedule row shows Tubing Project incoming OD/ID in Output OD/ID columns.
_PASS_SCHEDULE_INCOMING_ROW = 0


def _merge_legacy_interleaved_pass_schedule_passes(passes: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """
    Older projects stored alternating draw / inter-pass rows. Merge each inter-pass row's HT fields
    into the preceding draw-pass row when r>0 / r<=0 pattern matches, then drop inter-pass rows.
    """
    if not passes:
        return []
    out: list[Dict[str, Any]] = []
    i = 0
    n = len(passes)
    while i < n:
        raw = passes[i]
        p = dict(raw) if isinstance(raw, dict) else {}
        try:
            r = float(p.get("r", 0) or 0)
        except (TypeError, ValueError):
            r = 0.0
        if r > 0:
            if i + 1 < n and isinstance(passes[i + 1], dict):
                q = passes[i + 1]
                try:
                    r2 = float(q.get("r", 0) or 0)
                except (TypeError, ValueError):
                    r2 = 0.0
                if r2 <= 0:
                    for key in ("temperature", "time", "protective_gas", "equipment", "notes"):
                        pt = str(p.get(key, "") or "").strip()
                        qt = str(q.get(key, "") or "").strip()
                        if not pt and qt:
                            p[key] = q[key]
                    out.append(p)
                    i += 2
                    continue
            out.append(p)
            i += 1
        else:
            i += 1
    return out if out else []


def _style_layout_horizontal_margin_px(style: QStyle | None) -> int:
    """Horizontal layout inset from QStyle for toolbar alignment across tabs.

    PySide6/Qt 6 may omit ``PM_LayoutHorizontalMargin``; try ``PM_LayoutLeftMargin``, else 11 px.
    """
    if style is None:
        return 11
    for name in ("PM_LayoutHorizontalMargin", "PM_LayoutLeftMargin"):
        metric = getattr(QStyle.PixelMetric, name, None)
        if metric is None:
            continue
        try:
            v = int(style.pixelMetric(metric))
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return 11


_CENTER_TABLE_ALIGN = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter


class _CenteredTableItemDelegate(QStyledItemDelegate):
    """Paint and edit table cells with horizontal and vertical center alignment."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.displayAlignment = _CENTER_TABLE_ALIGN

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setAlignment(_CENTER_TABLE_ALIGN)
        return editor


class TubingMaster(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Tubing Master")
        # Default size; first show fits to the active screen work area (see _fit_window_to_available_screen).
        self.resize(980, 800)
        self.setMinimumSize(600, 440)
        self._did_fit_to_screen = False
        self._quotation_snapshot: Dict[str, Any] = {}
        self._embed_quotation_in_project: bool = False
        self._incoming_id_programmatic = False
        self._target_id_programmatic = False
        self._incoming_id_tracks_od = True
        self._target_id_tracks_od = True
        self._optuna_pass_count_override: int | None = None
        self._tubing_project_baseline_passes: list[PassInput] = []
        self._lubricant_combo_programmatic = False
        self._material_property_overrides: Dict[str, Dict[str, Any]] = {}
        self._area_reduction_programmatic = False
        self._opt_preview_area_reduction_programmatic = False
        self._semi_die_angle_programmatic = False
        self._mandrel_plug_programmatic = False
        self._pass_schedule_ht_programmatic = False
        # (tabs height px, Tubing cross-section strip height); used to mirror height on Pass Schedule.
        self._strip_plot_calib: tuple[int, int] | None = None
        # Prevent nested _refresh_schedule_visuals (Qt processEvents / mpl draw can re-enter).
        self._in_refresh_schedule_visuals: bool = False
        self._pending_refresh_schedule_visuals: bool = False
        # Match default QLayout horizontal inset (Pass Schedule’s button row sits under left_pv with style margins).
        _app0 = QApplication.instance()
        _style0 = _app0.style() if _app0 is not None else None
        self._tab_page_h_inset = _style_layout_horizontal_margin_px(_style0)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        tabs = QTabWidget()
        self.tabs = tabs
        layout.addWidget(tabs, 1)

        # --- Basic tab ---
        basic = QWidget()
        basic_layout = QVBoxLayout(basic)
        basic_layout.setContentsMargins(0, 0, 0, 0)
        basic_layout.setSpacing(6)

        top_section = QWidget()
        top_v = QVBoxLayout(top_section)
        top_v.setContentsMargins(0, 0, 0, 0)
        top_v.setSpacing(6)

        in_box = QGroupBox("Incoming Tube (Before Drawing)")
        in_form = QFormLayout(in_box)
        self.in_od_mm = QDoubleSpinBox()
        self.in_od_mm.setRange(0.1, 500.0)
        self.in_od_mm.setValue(12.0)
        self.in_od_mm.setDecimals(3)
        self.in_id_mm = QDoubleSpinBox()
        self.in_id_mm.setRange(0.01, 499.0)
        self.in_id_mm.setDecimals(3)
        self._configure_tubing_project_diameter_spin(self.in_od_mm, single_step=0.01)
        self._configure_tubing_project_diameter_spin(self.in_id_mm, single_step=0.01)
        in_form.addRow("Outer Diameter OD (mm)", self.in_od_mm)
        in_form.addRow("Inner Diameter ID (mm)", self.in_id_mm)

        out_box = QGroupBox("Target Output Tube (After Drawing)")
        out_form = QFormLayout(out_box)
        self.out_od_mm = QDoubleSpinBox()
        self.out_od_mm.setRange(0.1, 500.0)
        self.out_od_mm.setValue(10.5)
        self.out_od_mm.setDecimals(3)
        self.out_id_mm = QDoubleSpinBox()
        self.out_id_mm.setRange(0.01, 499.0)
        self.out_id_mm.setDecimals(3)
        self._configure_tubing_project_diameter_spin(self.out_od_mm, single_step=0.01)
        self._configure_tubing_project_diameter_spin(self.out_id_mm, single_step=0.01)
        out_form.addRow("Outer Diameter OD (mm)", self.out_od_mm)
        out_form.addRow("Inner Diameter ID (mm)", self.out_id_mm)

        self.in_id_mm.setValue(self.in_od_mm.value())
        self.out_id_mm.setValue(self.out_od_mm.value())

        method_box = QGroupBox("Drawing Method")
        method_form = QFormLayout(method_box)
        self.drawing_method_combo = QComboBox()
        self.drawing_method_combo.addItems(
            [
                "Sink drawing (rodless drawing)",
                "Floating plug drawing",
                "Long mandrel drawing",
                "Fixed plug drawing",
                "Short mandrel drawing",
                "Tethered plug drawing",
            ]
        )
        self.drawing_method_combo.setToolTip(
            "Tooling / process for documentation and Fit Area Reductions: for Sink / rodless drawing, Fit targets "
            "target output OD (bore ID follows ratio). All other methods Fit target output ID (OD follows ratio)."
        )
        method_form.addRow("Process", self.drawing_method_combo)

        material_box = QGroupBox("Material")
        material_form = QFormLayout(material_box)
        self.material_combo = QComboBox()
        self.material_combo.addItems(list(MATERIAL_PRESET_LABELS))
        self.material_combo.setToolTip(
            "Material constitutive model for analytical stress, safety factor, and grain estimates."
        )
        material_form.addRow("Material", self.material_combo)
        self.material_properties_btn = QPushButton("Edit Material Property...")
        self.material_properties_btn.setToolTip(
            "View how this material is modeled internally and edit key parameters for this project."
        )
        self.material_properties_btn.clicked.connect(self._open_material_properties_dialog)
        material_form.addRow("", self.material_properties_btn)
        self.material_combo.installEventFilter(self)

        for box in (in_box, out_box, method_box, material_box):
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        geom_grid = QGridLayout()
        geom_grid.setSpacing(6)
        geom_grid.setContentsMargins(0, 0, 0, 0)
        geom_grid.addWidget(in_box, 0, 0)
        geom_grid.addWidget(out_box, 1, 0)
        geom_grid.addWidget(method_box, 0, 1)
        geom_grid.addWidget(material_box, 1, 1)
        geom_grid.setColumnStretch(0, 1)
        geom_grid.setColumnStretch(1, 1)
        geom_grid.setRowStretch(0, 1)
        geom_grid.setRowStretch(1, 1)
        top_v.addLayout(geom_grid)

        self.geometry_hints = QLabel("")
        self.geometry_hints.setWordWrap(True)
        self.geometry_hints.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        top_v.addWidget(self.geometry_hints)

        # Untitled frame: heading + judgment live inside the same bordered box (no separate group title bar)
        project_id_box = QGroupBox()
        pid_outer = QHBoxLayout(project_id_box)
        pid_outer.setSpacing(10)
        pid_left = QVBoxLayout()
        pid_left.setSpacing(4)
        self.project_history_heading = QLabel("Project History & Reference")
        self.project_history_heading.setFont(self.geometry_hints.font())
        self.project_history_heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.project_status_label = QLabel("")
        self.project_status_label.setWordWrap(True)
        self.project_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.project_closest_label = QLabel("")
        self.project_closest_label.setWordWrap(True)
        self.project_closest_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.project_closest_label.setMaximumHeight(88)
        self.save_as_new_btn = QPushButton("Save As New…")
        self.save_as_new_btn.setToolTip(
            "Export the current Tubing Project setup, pass schedule, die BOM, and quotation snapshot to a JSON bundle."
        )
        self.save_as_new_btn.clicked.connect(self._save_project_as_new_bundle)
        self.save_project_to_history_btn = QPushButton("Save To History")
        self.save_project_to_history_btn.setToolTip(
            "Stores incoming/target OD/ID, material, and drawing method locally for future comparison. "
            f"File: {default_history_path()}"
        )
        self.save_project_to_history_btn.clicked.connect(self._save_current_project_to_history)
        self.fetch_history_btn = QPushButton("Fetch History")
        self.fetch_history_btn.setToolTip(
            "Pick a Tubing Master project JSON from ./suggested_projects/ (the three nearest saved projects are "
            "copied there automatically from Judgment), then export snapshots to Excel."
        )
        self.fetch_history_btn.clicked.connect(self._fetch_history_from_suggested_projects)
        self.open_projects_btn = QPushButton("Open Projects…")
        self.open_projects_btn.setToolTip(
            "Load geometry, material, process, pass schedule, backend, and quotation from ./Projects history "
            "or from a Tubing Master JSON bundle (Save As New)."
        )
        self.open_projects_btn.clicked.connect(self._open_projects_dialog)
        self._matched_history_record: TubingProjectRecord | None = None

        pid_left.addWidget(self.project_history_heading)
        judgment_row = QHBoxLayout()
        judgment_row.setSpacing(10)
        judgment_row.addWidget(self.project_status_label, stretch=1)
        judgment_row.addWidget(
            self.fetch_history_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        judgment_row.addSpacing(20)
        pid_left.addLayout(judgment_row)
        pid_left.addWidget(self.project_closest_label)

        pid_outer.addLayout(pid_left, stretch=1)
        top_v.addWidget(project_id_box)

        # OD→ID sync must run when OD keyboard entry completes (before schedule commit).
        self.in_od_mm.editingFinished.connect(self._apply_incoming_od_id_tracking_after_od_value)
        self.out_od_mm.editingFinished.connect(self._apply_target_od_id_tracking_after_od_value)
        for sp in (self.in_od_mm, self.in_id_mm, self.out_od_mm, self.out_id_mm):
            sp.valueChanged.connect(self._update_geometry_hints)
            sp.valueChanged.connect(self._update_project_history_panel)
            # Typed OD/ID often commit only on blur; valueChanged alone misses Pass Schedule + Tubing diagram updates.
            sp.editingFinished.connect(self._on_tubing_diameter_edit_finished)
        self.in_od_mm.valueChanged.connect(self._on_incoming_od_for_id_tracking)
        self.in_id_mm.valueChanged.connect(self._on_incoming_id_for_tracking)
        self.out_od_mm.valueChanged.connect(self._on_target_od_for_id_tracking)
        self.out_id_mm.valueChanged.connect(self._on_target_id_for_tracking)
        self._style_id_spin_tracks_od(self.in_id_mm, True)
        self._style_id_spin_tracks_od(self.out_id_mm, True)
        self.material_combo.currentIndexChanged.connect(self._on_material_or_drawing_changed)
        self.drawing_method_combo.currentIndexChanged.connect(self._on_material_or_drawing_changed)
        self._update_material_combo_tooltip()
        self._update_geometry_hints()
        self._update_project_history_panel()

        basic_layout.addWidget(top_section, 0)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(
            [
                "Built-In Analytical",
                "Manual Schedule - Master Mode",
                "FEniCSx Strip (Optional)",
                "DAMASK Grain (Crystal Plasticity)",
            ]
        )
        self.backend_combo.setToolTip(
            "Built-in: pass schedule + analytical model; summary compares result to target output OD/ID.\n"
            "Master Mode: same physics, but only your pass table matters — target output is not used in the summary.\n"
            "FEniCSx: use the FEA tab for tube/die FEA or FEA optimization; other tabs use the analytical engine.\n"
            "DAMASK: per-pass grain size from DAMASK_grid polycrystal CP when installed (analytical fallback otherwise)."
        )
        self.backend_combo.setMinimumWidth(140)
        self.backend_combo.setMaximumWidth(260)
        self.backend_combo.currentIndexChanged.connect(lambda _ix: self._refresh_damask_status())
        backend_box = QGroupBox("Simulation Backend")
        backend_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        br_layout = QHBoxLayout(backend_box)
        br_layout.setContentsMargins(8, 6, 8, 6)
        br_layout.setSpacing(8)
        run_btn = QPushButton("Run Schedule / Refresh Plots")
        run_btn.clicked.connect(self._run_analytical)
        run_btn.setMinimumHeight(28)
        br_layout.addWidget(self.backend_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        br_layout.addWidget(run_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.damask_status_label = QLabel()
        self.damask_status_label.setWordWrap(True)
        self.damask_status_label.setStyleSheet("color: #555; font-size: 11px;")
        self._refresh_damask_status()
        basic_layout.addWidget(self.damask_status_label)
        backend_action_row = QWidget()
        bar_layout = QHBoxLayout(backend_action_row)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(10)
        bar_layout.addWidget(backend_box, alignment=Qt.AlignmentFlag.AlignLeft)
        bar_layout.addWidget(self.save_as_new_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        bar_layout.addWidget(self.save_project_to_history_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        bar_layout.addStretch(1)
        bar_layout.addWidget(
            self.open_projects_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        bar_layout.addSpacing(20)
        basic_layout.addWidget(backend_action_row)

        self.backend_combo.currentIndexChanged.connect(self._update_geometry_hints)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(80)
        self.summary.setMinimumHeight(56)
        basic_layout.addWidget(self.summary)

        self.tubing_cross_section_strip = CrossSectionStripWidget()
        basic_layout.addWidget(self.tubing_cross_section_strip, 1)

        tabs.addTab(basic, "Tubing Project")

        # --- Passes tab: pass table + cross-section strip (baseline comparison via Tubing Project tab) ---
        passes_tab = QWidget()
        pass_outer = QVBoxLayout(passes_tab)
        pass_outer.setContentsMargins(0, 0, 0, 0)
        pass_outer.setSpacing(6)
        left_pass = QWidget()
        left_pass.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        left_pv = QVBoxLayout(left_pass)
        left_pv.setSpacing(6)
        self.pass_schedule_title = QLabel("Pass And Heat Treatment Schedule")
        _title_font = self.pass_schedule_title.font()
        _title_font.setBold(True)
        self.pass_schedule_title.setFont(_title_font)
        self.pass_schedule_title.setWordWrap(True)
        left_pv.addWidget(self.pass_schedule_title)

        preferred_row = QWidget()
        preferred_layout = QHBoxLayout(preferred_row)
        preferred_layout.setContentsMargins(0, 0, 0, 0)
        preferred_layout.setSpacing(10)
        preferred_layout.addWidget(QLabel("Prefered Number Of Passes"))
        self.preferred_pass_count_spin = QSpinBox()
        self.preferred_pass_count_spin.setRange(1, OPT_SCHEDULE_MAX_PASSES)
        self.preferred_pass_count_spin.setValue(max(1, _MIN_PASS_TABLE_ROWS))
        self.preferred_pass_count_spin.setToolTip(
            "Manual Schedule — Master Mode only: target draw-pass row count for “Manual Reschedule — Master Mode” "
            "and for Fit area reductions when that backend is selected. Built-In Analytical uses a geometry- and "
            "SF-derived pass count instead (see Pass Schedule hint below). Each drawing pass is one row "
            "(Temperature…Notes on that row)."
        )
        preferred_layout.addWidget(self.preferred_pass_count_spin)
        self.manual_reschedule_btn = QPushButton("Manual Reschedule - Master Mode")
        self.manual_reschedule_btn.setToolTip(
            "Resize the pass table to the preferred draw-pass count (one row per pass). "
            "Heat treatment fields (Temperature…Notes) follow each pass on the same row."
        )
        self.manual_reschedule_btn.clicked.connect(self._manual_reschedule_pass_table)
        self.pass_schedule_open_btn = QPushButton("Open Project…")
        self.pass_schedule_open_btn.setToolTip(self.open_projects_btn.toolTip())
        self.pass_schedule_open_btn.clicked.connect(self._open_projects_dialog)
        preferred_layout.addWidget(self.manual_reschedule_btn)
        preferred_layout.addStretch(1)
        preferred_layout.addWidget(
            self.pass_schedule_open_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        left_pv.addWidget(preferred_row)

        self.pass_schedule_count_label = QLabel("")
        self.pass_schedule_count_label.setWordWrap(True)
        left_pv.addWidget(self.pass_schedule_count_label)

        actions_row = QWidget()
        actions_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self.fit_target_schedule_btn = QPushButton("Fit Area Reductions → Target Output")
        self.fit_target_schedule_btn.setToolTip(
            "If Prefered Number Of Passes does not match the table yet, the table is resized first (same as Manual Reschedule). "
            "Then sets nominal area reductions so Run Schedule matches Target output: inner diameter for normal draws; "
            "outer diameter for Sink / rodless drawing (ID then follows fixed ratio). "
            "Semi-die angle and friction are unchanged on existing rows (new rows get defaults)."
        )
        self.fit_target_schedule_btn.clicked.connect(self._fit_pass_schedule_to_target_output)
        self.undo_fit_btn = QPushButton("Undo Fit")
        self.undo_fit_btn.setToolTip(
            "Restores area reduction, semi-die angle, and friction to what they were immediately before the last Fit."
        )
        self.undo_fit_btn.setEnabled(False)
        self.undo_fit_btn.clicked.connect(self._undo_fit_pass_schedule)
        self.pass_schedule_save_history_btn = QPushButton("Save To History")
        self.pass_schedule_save_history_btn.setToolTip(self.save_project_to_history_btn.toolTip())
        self.pass_schedule_save_history_btn.clicked.connect(self._save_current_project_to_history_from_pass_schedule)
        self.pass_schedule_save_as_btn = QPushButton("Save As New…")
        self.pass_schedule_save_as_btn.setToolTip(self.save_as_new_btn.toolTip())
        self.pass_schedule_save_as_btn.clicked.connect(self._save_project_as_new_bundle)
        self.export_pass_schedule_btn = QPushButton("Export Pass And Heat-treatment Schedule…")
        self.export_pass_schedule_btn.setToolTip(
            "Export project overview and Pass BOM to a two-sheet Excel workbook "
            "(same layout as Fetch History export)."
        )
        self.export_pass_schedule_btn.clicked.connect(self._export_pass_schedule_process_xlsx)
        self.sync_modifications_btn = QPushButton("Sync Modifications to Tubing Project")
        self.sync_modifications_btn.setToolTip(
            "Copies the current pass schedule table into the Tubing Project baseline used by the cross-section strip "
            "on that tab. Until you Sync, Tubing Project keeps the last generated suggestion (resize / Fit / Optuna / load); "
            "this tab’s strip follows live edits."
        )
        self.sync_modifications_btn.clicked.connect(self._sync_modifications_to_tubing_project_baseline)
        for _w in (
            self.fit_target_schedule_btn,
            self.undo_fit_btn,
            self.sync_modifications_btn,
            self.pass_schedule_save_history_btn,
            self.pass_schedule_save_as_btn,
        ):
            actions_layout.addWidget(_w)
        actions_layout.addStretch(1)
        actions_layout.addWidget(
            self.export_pass_schedule_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        left_pv.addWidget(actions_row)

        self._fit_undo_snapshot: list[tuple[str, str, str]] | None = None

        self.table = QTableWidget(0, _PASS_SCHEDULE_COL_COUNT)
        self.table.setHorizontalHeaderLabels(
            [
                "Output OD (mm)",
                "Output ID (mm)",
                "Area Reduction",
                "Semi-Die Angle (°)",
                "Lubricants",
                "Mandrel/Plug",
                "Temperature",
                "Time",
                "Protective Gas",
                "Equipment",
                "Notes",
            ]
        )
        self.table.verticalHeader().setDefaultSectionSize(22)
        _th = self.table.horizontalHeader()
        _th.setStretchLastSection(True)
        for _c in range(self.table.columnCount()):
            _th.setSectionResizeMode(_c, QHeaderView.ResizeMode.Stretch)
        _th.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setMinimumWidth(280)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.blockSignals(True)
        try:
            self._rebuild_pass_table_for_draw_count(_MIN_PASS_TABLE_ROWS)
        finally:
            self.table.blockSignals(False)
        self._ht_suggestion_anchor_material = self.material_combo.currentText()
        # Scroll the pass table so extra vertical space goes to the cross-section strip (matches Tubing Project layout).
        self.pass_schedule_table_scroll = QScrollArea()
        self.pass_schedule_table_scroll.setWidgetResizable(True)
        self.pass_schedule_table_scroll.setMinimumHeight(200)
        self.pass_schedule_table_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.pass_schedule_table_scroll.setWidget(self.table)
        left_pv.addWidget(self.pass_schedule_table_scroll, 1)

        pass_outer.addWidget(left_pass, 0)

        self.pass_schedule_cross_section_strip = CrossSectionStripWidget()
        pass_outer.addWidget(self.pass_schedule_cross_section_strip, 1)

        self.table.cellChanged.connect(self._on_pass_schedule_table_cell_changed)
        for sp in (self.in_od_mm, self.in_id_mm, self.out_od_mm, self.out_id_mm):
            sp.valueChanged.connect(self._refresh_schedule_visuals)

        tabs.addTab(passes_tab, "Pass Schedule")

        # --- Optimization tab ---
        opt = QWidget()
        ov = QVBoxLayout(opt)
        ov.setContentsMargins(0, 0, 0, 0)
        og = QGroupBox("Optimization Targets")
        og_layout = QVBoxLayout(og)
        og_layout.setContentsMargins(8, 8, 8, 8)
        og_layout.setSpacing(8)
        derived_heading = QLabel("Derived from Tubing Project")
        derived_heading.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.opt_derived_label = QLabel()
        self.opt_derived_label.setWordWrap(True)
        self.opt_derived_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.opt_derived_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        og_layout.addWidget(derived_heading)
        og_layout.addWidget(self.opt_derived_label)
        expected_row = QWidget()
        erh = QHBoxLayout(expected_row)
        erh.setContentsMargins(0, 0, 0, 0)
        erh.setSpacing(10)
        self.opt_expected_passes_btn = QPushButton("Expected passes…")
        self.opt_expected_passes_btn.setToolTip(
            "Set how many drawing passes Optuna should optimize. For that fixed count, Optuna searches "
            "per-pass area reduction only — semi-die angle and lubricant stay as on the Pass schedule. "
            "Leave automatic to use the geometry-derived count from Tubing Project."
        )
        self.opt_expected_passes_btn.clicked.connect(self._open_expected_passes_dialog)
        self.opt_expected_passes_label = QLabel("")
        self.opt_expected_passes_label.setWordWrap(True)
        self.opt_expected_passes_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        erh.addWidget(self.opt_expected_passes_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        erh.addWidget(self.opt_expected_passes_label, 1)
        og_layout.addWidget(expected_row)
        trials_sf_row = QWidget()
        trials_sf_layout = QHBoxLayout(trials_sf_row)
        trials_sf_layout.setContentsMargins(0, 0, 0, 0)
        trials_sf_layout.setSpacing(10)
        trials_lbl = QLabel("Optimization trials")
        trials_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opt_trials = QSpinBox()
        self.opt_trials.setRange(5, 500)
        self.opt_trials.setValue(40)
        self.opt_trials.setMinimumWidth(72)
        uts_lbl = QLabel("Min safety factor vs UTS")
        uts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opt_min_sf = QDoubleSpinBox()
        self.opt_min_sf.setRange(1.01, 5.0)
        self.opt_min_sf.setDecimals(2)
        self.opt_min_sf.setValue(1.15)
        self.opt_min_sf.setMinimumWidth(72)
        trials_sf_layout.addWidget(trials_lbl)
        trials_sf_layout.addWidget(self.opt_trials)
        trials_sf_layout.addSpacing(16)
        trials_sf_layout.addWidget(uts_lbl)
        trials_sf_layout.addWidget(self.opt_min_sf)
        trials_sf_layout.addSpacing(16)
        self.opt_run_btn = QPushButton("Run optimization")
        self.opt_run_btn.setMinimumHeight(28)
        self.opt_run_btn.clicked.connect(self._run_optuna)
        trials_sf_layout.addWidget(self.opt_run_btn)
        trials_sf_layout.addStretch(1)
        og_layout.addWidget(trials_sf_row)
        opt_outer = QWidget()
        opt_outer_layout = QVBoxLayout(opt_outer)
        opt_outer_layout.setContentsMargins(0, 0, 0, 0)
        opt_outer_layout.setSpacing(6)
        opt_outer_layout.addWidget(og, 0)

        preview_box = QGroupBox("Optimized pass schedule (preview)")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)
        preview_hdr = QWidget()
        preview_hdr_layout = QHBoxLayout(preview_hdr)
        preview_hdr_layout.setContentsMargins(0, 0, 0, 0)
        preview_hdr_layout.setSpacing(10)
        self.opt_preview_hint = QLabel(
            "After Run optimization, review and edit area reductions below. "
            "Semi-die angle and lubricant stay fixed from the Pass schedule."
        )
        self.opt_preview_hint.setWordWrap(True)
        self.opt_preview_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        preview_hdr_layout.addWidget(self.opt_preview_hint, 1)
        self.opt_apply_schedule_btn = QPushButton("Apply to Pass Schedule…")
        self.opt_apply_schedule_btn.setToolTip(
            "Copy this optimized schedule into the Pass schedule tab and sync Tubing Project, "
            "Process BOM, and Quotation when you open those tabs."
        )
        self.opt_apply_schedule_btn.setEnabled(False)
        self.opt_apply_schedule_btn.clicked.connect(self._apply_optimized_preview_to_pass_schedule)
        preview_hdr_layout.addWidget(self.opt_apply_schedule_btn)
        preview_layout.addWidget(preview_hdr)

        self.opt_preview_table = QTableWidget(0, _PASS_SCHEDULE_COL_COUNT)
        self.opt_preview_table.setHorizontalHeaderLabels(
            [
                "Output OD (mm)",
                "Output ID (mm)",
                "Area Reduction",
                "Semi-Die Angle (°)",
                "Lubricants",
                "Mandrel/Plug",
                "Temperature",
                "Time",
                "Protective Gas",
                "Equipment",
                "Notes",
            ]
        )
        self.opt_preview_table.verticalHeader().setDefaultSectionSize(22)
        _opt_th = self.opt_preview_table.horizontalHeader()
        _opt_th.setStretchLastSection(True)
        for _c in range(self.opt_preview_table.columnCount()):
            _opt_th.setSectionResizeMode(_c, QHeaderView.ResizeMode.Stretch)
        _opt_th.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.opt_preview_table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.opt_preview_table.setMinimumWidth(280)
        self.opt_preview_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.opt_preview_table.cellChanged.connect(self._on_opt_preview_table_cell_changed)
        self.opt_preview_table_scroll = QScrollArea()
        self.opt_preview_table_scroll.setWidgetResizable(True)
        self.opt_preview_table_scroll.setMinimumHeight(200)
        self.opt_preview_table_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.opt_preview_table_scroll.setWidget(self.opt_preview_table)
        preview_layout.addWidget(self.opt_preview_table_scroll, 1)
        opt_outer_layout.addWidget(preview_box, 3)

        self.opt_log = QTextEdit()
        self.opt_log.setReadOnly(True)
        self.opt_log.setMaximumHeight(72)
        self.opt_log.setMinimumHeight(48)
        opt_outer_layout.addWidget(self.opt_log, 0)

        self.opt_preview_cross_section_strip = CrossSectionStripWidget()
        opt_outer_layout.addWidget(self.opt_preview_cross_section_strip, 1)
        ov.addWidget(opt_outer, 1)

        self.opt_min_sf.valueChanged.connect(lambda *_: self._update_geometry_hints())
        self.opt_min_sf.valueChanged.connect(lambda *_: self._update_optuna_derived_label())
        for _w in (self.in_od_mm, self.in_id_mm, self.out_od_mm, self.out_id_mm):
            _w.valueChanged.connect(lambda *_: self._update_optuna_derived_label())
        self._update_optuna_derived_label()
        self._update_optuna_expected_passes_label()
        tabs.addTab(opt, "Optimization")

        # --- FEA tab (axisymmetric tube / die + optional FEA optimization) ---
        fea_tab = QWidget()
        fv = QVBoxLayout(fea_tab)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(0)
        fea_outer = QWidget()
        fea_outer_layout = QVBoxLayout(fea_outer)
        fea_outer_layout.setContentsMargins(0, 0, 0, 0)
        fea_outer_layout.setSpacing(6)
        self.fenics_status = QLabel()
        self._refresh_fenics_status()
        fea_outer_layout.addWidget(self.fenics_status, 0)

        fea_intro = QLabel(
            "Axisymmetric tube / semi-die elastic FEA (dolfinx). Optimization on other tabs stays analytical; "
            "use this tab only when you want FEA-based checks or FEA optimization."
        )
        fea_intro.setWordWrap(True)
        fea_outer_layout.addWidget(fea_intro, 0)

        pass_grp = QGroupBox("Single pass — tube / die model")
        pass_grp_layout = QHBoxLayout(pass_grp)
        pass_grp_layout.setContentsMargins(8, 8, 8, 8)
        pass_grp_layout.setSpacing(10)
        self.fea_pass_schematic = FeaPassSchematicWidget()
        pass_grp_layout.addWidget(self.fea_pass_schematic, 2)

        pass_right = QWidget()
        pass_form = QFormLayout(pass_right)
        pass_form.setContentsMargins(0, 0, 0, 0)
        self.fea_od_in = QDoubleSpinBox()
        self.fea_od_in.setRange(0.1, 500.0)
        self.fea_od_in.setDecimals(4)
        self.fea_od_in.setSuffix(" mm")
        self.fea_id_in = QDoubleSpinBox()
        self.fea_id_in.setRange(0.0, 499.0)
        self.fea_id_in.setDecimals(4)
        self.fea_id_in.setSuffix(" mm")
        self.fea_area_r = QDoubleSpinBox()
        self.fea_area_r.setRange(0.0, 0.95)
        self.fea_area_r.setDecimals(4)
        self.fea_area_r.setSingleStep(0.01)
        self.fea_alpha = QDoubleSpinBox()
        self.fea_alpha.setRange(3.0, 30.0)
        self.fea_alpha.setDecimals(2)
        self.fea_alpha.setSuffix(" °")
        self.fea_alpha.setValue(12.0)
        self.fea_load_opt_btn = QPushButton("Load Pass from Optimization")
        self.fea_load_opt_btn.setToolTip(
            "Fill OD, ID, r, and α from the Optimization preview for the selected pass number. "
            "Run optimization on the Optimization tab first if the preview is empty."
        )
        self.fea_load_opt_btn.clicked.connect(self._fea_load_from_optimization)
        self.fea_pass_select = QSpinBox()
        self.fea_pass_select.setRange(1, 1)
        self.fea_pass_select.setMinimumWidth(52)
        self.fea_pass_select.setToolTip(
            "Pass number from the Optimization preview (1, 2, …). Used with Load Pass from Optimization."
        )
        pass_form.addRow("Incoming OD", self.fea_od_in)
        pass_form.addRow("Incoming ID", self.fea_id_in)
        pass_form.addRow("Area reduction r", self.fea_area_r)
        self.fea_manual_btn = QPushButton("Manual FEA")
        self.fea_manual_btn.setToolTip(
            "Run tube/die FEA using the OD, ID, r, and α values entered above (not from Optimization load)."
        )
        self.fea_manual_btn.clicked.connect(self._run_fea_single_pass)
        fea_alpha_row = QWidget()
        fea_alpha_row_layout = QHBoxLayout(fea_alpha_row)
        fea_alpha_row_layout.setContentsMargins(0, 0, 0, 0)
        fea_alpha_row_layout.setSpacing(8)
        fea_alpha_row_layout.addWidget(self.fea_alpha, 0)
        fea_alpha_row_layout.addWidget(self.fea_manual_btn)
        fea_alpha_row_layout.addStretch(1)
        pass_form.addRow("Semi-die angle α", fea_alpha_row)
        fea_load_row = QWidget()
        fea_load_row_layout = QHBoxLayout(fea_load_row)
        fea_load_row_layout.setContentsMargins(0, 0, 0, 0)
        fea_load_row_layout.setSpacing(8)
        fea_load_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        fea_load_row_layout.addWidget(self.fea_load_opt_btn)
        fea_load_row_layout.addWidget(self.fea_pass_select)
        fea_load_row_layout.addStretch(1)
        pass_form.addRow("", fea_load_row)
        fea_action_row = QWidget()
        fea_action_layout = QHBoxLayout(fea_action_row)
        fea_action_layout.setContentsMargins(0, 0, 0, 0)
        fea_action_layout.setSpacing(8)
        fea_action_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.fea_run_pass_btn = QPushButton("Single Pass FEA")
        self.fea_run_pass_btn.setToolTip(
            "Load the selected Optimization pass into the fields above, then run tube/die FEA on that pass."
        )
        self.fea_run_pass_btn.clicked.connect(self._run_fea_single_pass_from_optimization)
        self.fea_analyze_schedule_btn = QPushButton("All Passes FEA")
        self.fea_analyze_schedule_btn.setToolTip(
            "Run tube/die FEA on every drawing pass in the current Pass schedule table."
        )
        self.fea_analyze_schedule_btn.clicked.connect(self._run_fea_analyze_pass_schedule)
        fea_action_layout.addWidget(self.fea_run_pass_btn)
        fea_action_layout.addWidget(self.fea_analyze_schedule_btn)
        fea_action_layout.addStretch(1)
        pass_form.addRow("", fea_action_row)
        pass_grp_layout.addWidget(pass_right, 1)
        for _spin in (self.fea_od_in, self.fea_id_in, self.fea_area_r, self.fea_alpha):
            _spin.valueChanged.connect(lambda *_: self._refresh_fea_pass_schematic())
        self.drawing_method_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_fea_pass_schematic()
        )
        fea_outer_layout.addWidget(pass_grp, 0)

        fea_opt_grp = QGroupBox("FEA schedule optimization (optional)")
        fea_opt_layout = QVBoxLayout(fea_opt_grp)
        fea_opt_row = QHBoxLayout()
        fea_opt_row.addWidget(QLabel("Trials"))
        self.fea_opt_trials = QSpinBox()
        self.fea_opt_trials.setRange(3, 80)
        self.fea_opt_trials.setValue(12)
        self.fea_opt_trials.setToolTip("Each trial runs full tube/die FEA — keep low.")
        fea_opt_row.addWidget(self.fea_opt_trials)
        fea_opt_row.addSpacing(12)
        self.fea_opt_pure_btn = QPushButton("Run FEA optimization")
        self.fea_opt_pure_btn.setToolTip(
            "Optuna minimizes max von Mises from tube/die FEA (slow; requires dolfinx)."
        )
        self.fea_opt_pure_btn.clicked.connect(self._run_fea_optimization_pure)
        fea_opt_row.addWidget(self.fea_opt_pure_btn)
        fea_opt_row.addSpacing(12)
        self.fea_opt_hybrid_btn = QPushButton("Analytical search + FEA top 5")
        self.fea_opt_hybrid_btn.setToolTip(
            "Fast analytical Optuna, then re-rank the best 5 schedules with tube/die FEA."
        )
        self.fea_opt_hybrid_btn.clicked.connect(self._run_fea_optimization_hybrid)
        fea_opt_row.addWidget(self.fea_opt_hybrid_btn)
        fea_opt_row.addStretch(1)
        fea_opt_layout.addLayout(fea_opt_row)
        self.fea_apply_to_schedule_btn = QPushButton("Apply FEA-best schedule to Pass schedule…")
        self.fea_apply_to_schedule_btn.setEnabled(False)
        self.fea_apply_to_schedule_btn.clicked.connect(self._apply_fea_best_to_pass_schedule)
        fea_opt_layout.addWidget(self.fea_apply_to_schedule_btn)
        fea_outer_layout.addWidget(fea_opt_grp, 0)

        self.fea_out = QTextEdit()
        self.fea_out.setReadOnly(True)
        self.fea_out.setMaximumHeight(72)
        self.fea_out.setMinimumHeight(48)
        fea_outer_layout.addWidget(self.fea_out, 0)

        self.fea_cross_section_strip = CrossSectionStripWidget()
        fea_outer_layout.addWidget(self.fea_cross_section_strip, 1)
        fv.addWidget(fea_outer, 1)

        self._fea_last_best_passes: list[PassInput] = []
        self._fea_display_passes: list[PassInput] = []  # drives FEA tab side-view strip
        self.fea_pass_select.valueChanged.connect(self._on_fea_pass_select_changed)
        tabs.addTab(fea_tab, "FEA")

        # --- Pass bill of materials (dies) ---
        bom_tab = QWidget()
        bom_layout = QVBoxLayout(bom_tab)
        bom_layout.setContentsMargins(0, 0, 0, 0)
        bom_layout.setSpacing(4)
        _bom_intro = QLabel(
            "Per-pass die BOM (pass annulus OD/ID in/out, die size, α, lubricant, Mandrel/Plug from Pass schedule) — from Tubing Project & Pass schedule; verify against drawings."
        )
        _bom_intro.setWordWrap(False)
        _bom_intro.setToolTip(
            "Per-pass bill of materials focused on drawing dies: semi-die angle, lubricant (as on Pass schedule), "
            "annulus OD/ID before and after each pass, and mandrel/plug copied from the Mandrel/Plug column on the "
            "Pass schedule tab (same tooling OD you set there). Derived from the Tubing Project tab (incoming tube) "
            "and the Pass schedule table. Use for tooling lists — verify against shop standards and die drawings."
        )
        bom_layout.addWidget(_bom_intro)
        self.bom_hint = QLabel("")
        self.bom_hint.setWordWrap(True)
        bom_layout.addWidget(self.bom_hint)
        bom_btn_row = QWidget()
        bom_btn_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bom_btn_layout = QHBoxLayout(bom_btn_row)
        # Pass Schedule action row is inside left_pv (style default L/R margins); BOM tab uses margin 0 at root.
        bom_btn_layout.setContentsMargins(self._tab_page_h_inset, 0, self._tab_page_h_inset, 0)
        bom_btn_layout.setSpacing(8)
        self.bom_refresh_btn = QPushButton("Refresh BOM from schedule")
        self.bom_refresh_btn.clicked.connect(self._refresh_die_bom_manual)
        bom_btn_layout.addWidget(self.bom_refresh_btn)
        bom_btn_layout.addStretch(1)
        self.bom_export_btn = QPushButton("Export BOM to Excel…")
        self.bom_export_btn.clicked.connect(self._export_bom_excel)
        bom_btn_layout.addWidget(
            self.bom_export_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        bom_layout.addWidget(bom_btn_row)
        self.bom_table = QTableWidget(0, 10)
        self.bom_table.setHorizontalHeaderLabels(
            [
                "Pass",
                "OD before (mm)",
                "ID before (mm)",
                "OD after (mm)",
                "ID after (mm)",
                "Die Size",
                "Semi-die α (°)",
                "Lubricant",
                "Mandrel/Plug",
                "Notes",
            ]
        )
        self.bom_table.horizontalHeader().setStretchLastSection(True)
        self.bom_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bom_table.verticalHeader().setVisible(False)
        self.bom_table.setAlternatingRowColors(True)
        self.bom_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bom_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.bom_table.horizontalHeader().setSectionResizeMode(
            _BOM_COL_LUBRICANT, QHeaderView.ResizeMode.ResizeToContents
        )
        bom_layout.addWidget(self.bom_table, 1)
        tabs.addTab(bom_tab, "Process BOM")

        # --- Die inventory (schedule matching + Pass row alerts) ---
        die_inv_tab = QWidget()
        div = QVBoxLayout(die_inv_tab)
        div.setContentsMargins(0, 0, 0, 0)
        div.setSpacing(6)
        die_inv_intro = QLabel(
            "List shop dies by name, semi-die angle α (°), entry annulus OD band (mm), and bearing length for the schematic. "
            "Select a row to update the side-view diagram (α, bearing length, OD band). "
            "On Pass schedule, drawing passes are colored by inventory match: green / amber / red / gray."
        )
        die_inv_intro.setWordWrap(True)
        div.addWidget(die_inv_intro)
        self.die_inv_splitter = QSplitter(Qt.Orientation.Horizontal)
        die_left_w = QWidget()
        die_left = QVBoxLayout(die_left_w)
        die_left_w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        die_left.setContentsMargins(0, 0, 0, 0)
        die_left.setSpacing(4)
        die_sketch_title = QLabel(
            "Drawing die — side view (schematic half-section, textbook-style layout: bell → cone α → bearing → relief)"
        )
        die_sketch_title.setWordWrap(True)
        die_sketch_title.setStyleSheet("font-weight: bold;")
        die_left.addWidget(die_sketch_title)
        die_sketch_hint = QLabel(
            "Reference style follows common wire-drawing die diagrams (reduction angle / semi-die α, bearing land). "
            "Illustration is not a certified tool drawing."
        )
        die_sketch_hint.setWordWrap(True)
        die_sketch_hint.setStyleSheet("color: #555; font-size: 11px;")
        die_left.addWidget(die_sketch_hint)
        self.die_inv_diagram_banner = QLabel("")
        self.die_inv_diagram_banner.setWordWrap(True)
        self.die_inv_diagram_banner.setStyleSheet(
            "color: #5c6c7c; font-size: 11px; padding: 2px 0 6px 0;"
        )
        die_left.addWidget(self.die_inv_diagram_banner)
        self.die_inv_entry_od = QLabel("")
        self.die_inv_entry_od.setWordWrap(True)
        self.die_inv_entry_od.setStyleSheet("color: #495057; font-size: 11px; padding: 0 0 4px 0;")
        die_left.addWidget(self.die_inv_entry_od)
        self.die_inv_schematic = DieSchematicWidget()
        self.die_inv_schematic.set_spec(empty_die_schematic_spec(inventory_empty=True))
        die_canvas_row = QWidget()
        die_canvas_row_layout = QHBoxLayout(die_canvas_row)
        die_canvas_row_layout.setContentsMargins(0, 0, 0, 0)
        die_canvas_row_layout.setSpacing(6)
        die_canvas_row_layout.addWidget(self.die_inv_schematic, 1)
        self.die_inv_stock_badge = QLabel("")
        self.die_inv_stock_badge.setMinimumWidth(0)
        self.die_inv_stock_badge.setMaximumWidth(160)
        self.die_inv_stock_badge.setWordWrap(True)
        self.die_inv_stock_badge.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        die_canvas_row_layout.addWidget(self.die_inv_stock_badge, 0)
        die_left.addWidget(die_canvas_row, 1)
        die_right_w = QWidget()
        die_right = QVBoxLayout(die_right_w)
        die_right_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        die_right.setContentsMargins(0, 0, 0, 0)
        die_right.setSpacing(6)
        tol_row = QWidget()
        tol_layout = QHBoxLayout(tol_row)
        tol_layout.setContentsMargins(0, 0, 0, 0)
        tol_match_lbl = QLabel("Angle match vs inventory α (±°):")
        tol_match_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.die_inv_angle_tol = QDoubleSpinBox()
        self.die_inv_angle_tol.setRange(0.01, 5.0)
        self.die_inv_angle_tol.setDecimals(3)
        self.die_inv_angle_tol.setValue(DEFAULT_ANGLE_TOLERANCE_DEG)
        self.die_inv_angle_tol.setToolTip(
            "Schedule semi-die angle must be within this tolerance of an inventory die's α after OD-band filtering."
        )
        self.die_inv_angle_tol.valueChanged.connect(self._on_die_inventory_tolerance_changed)
        tol_layout.addWidget(tol_match_lbl)
        tol_layout.addWidget(self.die_inv_angle_tol)
        tol_layout.addStretch(1)
        die_right.addWidget(tol_row)
        die_btn_row = QWidget()
        die_btn_layout = QHBoxLayout(die_btn_row)
        die_btn_layout.setContentsMargins(0, 0, 0, 0)
        self.die_inv_add_btn = QPushButton("Add die…")
        self.die_inv_add_btn.clicked.connect(self._die_inventory_add_row)
        self.die_inv_remove_btn = QPushButton("Remove selected row")
        self.die_inv_remove_btn.clicked.connect(self._die_inventory_remove_selected_row)
        self.die_inv_snap_btn = QPushButton("Snap semi-die α to inventory (OD band)")
        self.die_inv_snap_btn.setToolTip(
            "For each drawing pass, set the schedule semi-die angle to the inventory α of the die "
            "whose OD range contains that pass's entry OD and whose α is closest to the current value."
        )
        self.die_inv_snap_btn.clicked.connect(self._die_inventory_snap_semi_die_angles)
        die_btn_layout.addWidget(self.die_inv_add_btn)
        die_btn_layout.addWidget(self.die_inv_remove_btn)
        die_btn_layout.addWidget(self.die_inv_snap_btn)
        die_btn_layout.addStretch(1)
        die_right.addWidget(die_btn_row)
        self.die_inv_table = QTableWidget(0, _DIE_INV_COL_COUNT)
        self.die_inv_table.setHorizontalHeaderLabels(
            [
                "Name",
                "α (°)",
                "OD min (mm)",
                "OD max (mm)",
                "Bearing Length (mm)",
                "Material",
                "Supplier",
                "In stock",
                "Quantity",
                "Notes",
            ]
        )
        self.die_inv_table.setMinimumWidth(440)
        _dh = self.die_inv_table.horizontalHeader()
        _dh.setStretchLastSection(True)
        for _c in range(_DIE_INV_COL_NOTES):
            _dh.setSectionResizeMode(_c, QHeaderView.ResizeMode.ResizeToContents)
        _dh.setSectionResizeMode(_DIE_INV_COL_NOTES, QHeaderView.ResizeMode.Stretch)
        self.die_inv_table.verticalHeader().setVisible(True)
        self.die_inv_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.die_inv_table.setAlternatingRowColors(True)
        self.die_inv_table.itemChanged.connect(self._on_die_inventory_table_item_changed)
        self.die_inv_table.itemSelectionChanged.connect(self._on_die_inventory_selection_changed)
        die_right.addWidget(self.die_inv_table, 1)
        self.die_inv_splitter.setChildrenCollapsible(False)
        self.die_inv_splitter.addWidget(die_left_w)
        self.die_inv_splitter.addWidget(die_right_w)
        self.die_inv_splitter.setStretchFactor(0, 1)
        self.die_inv_splitter.setStretchFactor(1, 2)
        self.die_inv_splitter.setSizes([360, 560])
        div.addWidget(self.die_inv_splitter, 1)
        self._update_die_inv_stock_badge()
        tabs.addTab(die_inv_tab, "Die inventory")

        # --- Quotation (pricing from schedule + stock) ---
        quote_tab = QWidget()
        quote_layout = QVBoxLayout(quote_tab)
        quote_layout.setContentsMargins(0, 0, 0, 0)
        quote_layout.setSpacing(6)
        quote_intro = QLabel(
            "Combine incoming stock mass (annulus × length × density × price/kg) with per-pass drawing charges. "
            "Unit costs and comments merge across schedule refreshes when pass indices match."
        )
        quote_intro.setWordWrap(True)
        quote_layout.addWidget(quote_intro)
        econ_box = QGroupBox("Stock & pricing")
        econ_grid = QGridLayout(econ_box)
        econ_grid.setHorizontalSpacing(12)
        econ_grid.setVerticalSpacing(8)
        self.quote_currency = QLineEdit("USD")
        self.quote_currency.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.quote_stock_len = QDoubleSpinBox()
        self.quote_stock_len.setRange(0.001, 1e6)
        self.quote_stock_len.setDecimals(4)
        self.quote_stock_len.setValue(1.0)
        self.quote_stock_len.setSuffix(" m")
        self.quote_stock_len.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.quote_price_kg = QDoubleSpinBox()
        self.quote_price_kg.setRange(0.0, 1e9)
        self.quote_price_kg.setDecimals(4)
        self.quote_price_kg.setPrefix("")
        self.quote_price_kg.setValue(0.0)
        self.quote_price_kg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.quote_density = QDoubleSpinBox()
        self.quote_density.setRange(100.0, 25000.0)
        self.quote_density.setDecimals(1)
        self.quote_density.setSuffix(" kg/m³")
        self.quote_density.setValue(material_density_kg_m3(self.material_combo.currentText()))
        self.quote_density.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lbl_align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        cur_lbl = QLabel("Currency")
        cur_lbl.setAlignment(lbl_align)
        stock_lbl = QLabel("Stock length")
        stock_lbl.setAlignment(lbl_align)
        price_lbl = QLabel("Price / kg (material)")
        price_lbl.setAlignment(lbl_align)
        density_lbl = QLabel("Density")
        density_lbl.setAlignment(lbl_align)
        stock_price_panel_lbl = QLabel("Stock price")
        stock_price_panel_lbl.setAlignment(lbl_align)
        stock_price_panel_lbl.setToolTip(
            "Mass × price/kg from incoming OD/ID, stock length, density, and price per kg (before additional cost)."
        )
        self.quote_stock_price_display = QLabel("—")
        self.quote_stock_price_display.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.quote_stock_price_display.setMaximumWidth(160)
        self.quote_stock_price_display.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.quote_stock_price_display.setToolTip(stock_price_panel_lbl.toolTip())
        additional_cost_lbl = QLabel("Additional Cost")
        additional_cost_lbl.setAlignment(lbl_align)
        additional_cost_lbl.setToolTip(
            "Surcharge added to stock price for the materials line total (e.g. handling, coating)."
        )
        self.quote_additional_cost = QDoubleSpinBox()
        self.quote_additional_cost.setRange(0.0, 1e12)
        self.quote_additional_cost.setDecimals(2)
        self.quote_additional_cost.setPrefix("")
        self.quote_additional_cost.setValue(0.0)
        self.quote_additional_cost.setToolTip(additional_cost_lbl.toolTip())
        self.quote_additional_cost.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Three label + field pairs per row (six logical columns).
        econ_grid.addWidget(cur_lbl, 0, 0)
        econ_grid.addWidget(self.quote_currency, 0, 1)
        econ_grid.addWidget(stock_lbl, 0, 2)
        econ_grid.addWidget(self.quote_stock_len, 0, 3)
        econ_grid.addWidget(price_lbl, 0, 4)
        econ_grid.addWidget(self.quote_price_kg, 0, 5)
        econ_grid.addWidget(density_lbl, 1, 0)
        econ_grid.addWidget(self.quote_density, 1, 1)
        econ_grid.addWidget(stock_price_panel_lbl, 1, 2)
        econ_grid.addWidget(self.quote_stock_price_display, 1, 3)
        econ_grid.addWidget(additional_cost_lbl, 1, 4)
        econ_grid.addWidget(self.quote_additional_cost, 1, 5)
        for _col in (1, 3, 5):
            econ_grid.setColumnStretch(_col, 1)
        quote_layout.addWidget(econ_box)
        quote_btn_row = QWidget()
        quote_btn_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        quote_btn_layout = QHBoxLayout(quote_btn_row)
        # Same horizontal inset as BOM / Pass Schedule export toolbars (see bom_btn_layout above).
        quote_btn_layout.setContentsMargins(self._tab_page_h_inset, 0, self._tab_page_h_inset, 0)
        quote_btn_layout.setSpacing(8)
        self.quote_refresh_btn = QPushButton("Recalculate from schedule")
        self.quote_refresh_btn.setToolTip(
            "After you change passes or geometry, click here to rebuild pricing lines from Tubing Project + Pass schedule "
            "(preserves unit costs where pass numbers match). Opening this tab refreshes automatically."
        )
        self.quote_refresh_btn.clicked.connect(self._recalculate_quotation_from_schedule)
        self.quote_export_btn = QPushButton("Export quotation to Excel…")
        self.quote_export_btn.clicked.connect(self._export_quotation_excel)
        self.quote_save_to_project_btn = QPushButton("Save Quotation To Project")
        self.quote_save_to_project_btn.setToolTip(
            "Store the current quotation snapshot inside the Tubing Project dict so it travels with "
            "Save To History and Save As New… bundle exports."
        )
        self.quote_save_to_project_btn.clicked.connect(self._save_quotation_to_project)
        self.quote_save_as_new_btn = QPushButton("Save As New…")
        self.quote_save_as_new_btn.setToolTip(
            "Write a Tubing Master project bundle JSON (geometry, schedule, BOM, quotation) — same as Tubing Project tab."
        )
        self.quote_save_as_new_btn.clicked.connect(self._save_project_as_new_bundle)
        quote_btn_layout.addWidget(self.quote_refresh_btn)
        quote_btn_layout.addWidget(self.quote_save_to_project_btn)
        quote_btn_layout.addWidget(self.quote_save_as_new_btn)
        quote_btn_layout.addStretch(1)
        quote_btn_layout.addWidget(
            self.quote_export_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        quote_layout.addWidget(quote_btn_row)
        self.quote_totals_label = QLabel(
            "Opening this tab loads lines from your current pass schedule; use “Recalculate from schedule” after edits."
        )
        self.quote_totals_label.setWordWrap(True)
        quote_layout.addWidget(self.quote_totals_label)
        self.quote_table = QTableWidget(0, 6)
        self.quote_table.setHorizontalHeaderLabels(
            ["Pass", "Description", "Die", "Qty", "Unit cost", "Comments"]
        )
        self.quote_table.setWordWrap(True)
        self.quote_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        _qh_q = self.quote_table.horizontalHeader()
        _qh_q.setStretchLastSection(False)
        _qh_q.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        _qh_q.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        _qh_q.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        _qh_q.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        _qh_q.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        _qh_q.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.quote_table.horizontalHeader().setDefaultAlignment(_CENTER_TABLE_ALIGN)
        self.quote_table.setItemDelegate(_CenteredTableItemDelegate(self.quote_table))
        self.quote_table.verticalHeader().setVisible(False)
        self.quote_table.setAlternatingRowColors(True)
        self.quote_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.quote_table.itemChanged.connect(self._on_quote_table_item_changed)
        quote_layout.addWidget(self.quote_table, 1)
        self.quote_stock_len.valueChanged.connect(self._on_quote_economic_inputs_changed)
        self.quote_price_kg.valueChanged.connect(self._on_quote_economic_inputs_changed)
        self.quote_density.valueChanged.connect(self._on_quote_economic_inputs_changed)
        self.quote_additional_cost.valueChanged.connect(self._on_quote_economic_inputs_changed)
        self.quote_currency.textChanged.connect(self._update_quote_stock_price_display)
        tabs.addTab(quote_tab, "Quotation")
        self.tabs.currentChanged.connect(self._on_main_tab_changed)

        self.tubing_cross_section_strip.installEventFilter(self)
        self._update_geometry_hints()
        QTimer.singleShot(0, self._startup_pass_table_and_visuals)
        QTimer.singleShot(0, self._refresh_die_inventory_schematic)
        QTimer.singleShot(0, self._update_quote_stock_price_display)
        QTimer.singleShot(0, self._sync_material_property_btn_width)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            combo = getattr(self, "material_combo", None)
            if combo is not None and watched is combo:
                self._sync_material_property_btn_width()
            strip = getattr(self, "tubing_cross_section_strip", None)
            if strip is not None and watched is strip:
                h = int(strip.height())
                if h >= 50:
                    self._record_cross_section_strip_calibration(h)
        return super().eventFilter(watched, event)

    def _record_cross_section_strip_calibration(self, tubing_strip_px: int) -> None:
        """Match Pass Schedule side-view canvas pixel height to Tubing Project (source of truth)."""
        h = int(tubing_strip_px)
        if h < 50:
            return
        self._strip_plot_calib = (max(1, self.tabs.height()), h)
        self.pass_schedule_cross_section_strip.setFixedHeight(h)
        if getattr(self, "opt_preview_cross_section_strip", None) is not None:
            self.opt_preview_cross_section_strip.setFixedHeight(h)
        if getattr(self, "fea_cross_section_strip", None) is not None:
            self.fea_cross_section_strip.setFixedHeight(h)
        self._relayout_visible_figures()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scale_pass_schedule_strip_with_tabs_height()

    def _scale_pass_schedule_strip_with_tabs_height(self) -> None:
        """While Pass Schedule, Optimization, or FEA is visible, keep strip height in sync when the tab resizes."""
        idx = self.tabs.currentIndex()
        if idx == _TAB_INDEX_PASS_SCHEDULE:
            strip = self.pass_schedule_cross_section_strip
        elif idx == _TAB_INDEX_OPTIMIZATION:
            strip = getattr(self, "opt_preview_cross_section_strip", None)
        elif idx == _TAB_INDEX_FEA:
            strip = getattr(self, "fea_cross_section_strip", None)
        else:
            return
        if strip is None:
            return
        cal = self._strip_plot_calib
        if not cal:
            return
        th0, strip0 = cal
        th = max(1, self.tabs.height())
        if th0 <= 0:
            return
        mh = strip.minimumSize().height()
        new_h = max(mh, int(round(strip0 * (th / th0))))
        strip.setFixedHeight(new_h)
        self._strip_plot_calib = (th, new_h)
        self._relayout_visible_figures()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._did_fit_to_screen:
            self._did_fit_to_screen = True
            QTimer.singleShot(0, self._fit_window_to_available_screen)

    def _fit_window_to_available_screen(self) -> None:
        """Size and center the window to fit the current screen’s available work area (user-resizable afterward)."""
        screen = self.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        ag = screen.availableGeometry()
        margin = 24
        max_w = max(self.minimumWidth(), ag.width() - margin)
        max_h = max(self.minimumHeight(), ag.height() - margin)
        # Prefer ~92% of the work area so the UI scales with laptop/desktop screens without overflowing.
        target_w = min(max_w, max(self.minimumWidth(), int(ag.width() * 0.92)))
        target_h = min(max_h, max(self.minimumHeight(), int(ag.height() * 0.92)))
        w = min(target_w, max_w)
        h = min(target_h, max_h)
        self.resize(w, h)
        fg = self.frameGeometry()
        fg.moveCenter(ag.center())
        self.move(fg.topLeft())
        self._relayout_visible_figures()

    def _relayout_visible_figures(self) -> None:
        """Refresh native cross-section strips and die schematic layout."""
        for w in (
            getattr(self, "tubing_cross_section_strip", None),
            getattr(self, "pass_schedule_cross_section_strip", None),
            getattr(self, "opt_preview_cross_section_strip", None),
            getattr(self, "fea_cross_section_strip", None),
            getattr(self, "fea_pass_schematic", None),
            getattr(self, "die_inv_schematic", None),
        ):
            if w is not None:
                w.update()

    @staticmethod
    def _centered_table_item(text: str, *, read_only: bool = False) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setTextAlignment(_CENTER_TABLE_ALIGN)
        if read_only:
            it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        return it

    @staticmethod
    def _centered_table_cell_widget(widget: QWidget) -> QWidget:
        """Host a cell widget centered in the table cell (horizontal and vertical)."""
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(2, 0, 2, 0)
        outer.setSpacing(0)
        outer.addStretch(1)
        inner_row = QWidget()
        row = QHBoxLayout(inner_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        row.addWidget(widget, 0, Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        outer.addWidget(inner_row, 0)
        outer.addStretch(1)
        return host

    @staticmethod
    def _apply_process_charge_description_placeholder_style(
        item: QTableWidgetItem, slot: str, text: str
    ) -> None:
        """Gray when showing default slot label or empty (editable hint); normal color when customized."""
        default = process_charge_description_default(slot)
        t = (text or "").strip()
        d = (default or "").strip()
        pal = QGuiApplication.palette()
        if t == "" or t == d:
            c = pal.color(QPalette.ColorRole.PlaceholderText)
            if not c.isValid() or c == pal.color(QPalette.ColorRole.Text):
                c = QColor(128, 128, 128)
            item.setForeground(QBrush(c))
        else:
            item.setForeground(QBrush(pal.color(QPalette.ColorRole.Text)))

    @staticmethod
    def _schedule_process_item(text: str) -> QTableWidgetItem:
        """Editable text for Equipment / Notes (centered)."""
        it = QTableWidgetItem(text)
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return it

    @staticmethod
    def _parse_schedule_saved_int(raw: Any, default: int) -> int:
        """Parse temperature or time from saved project strings or numbers."""
        if raw is None:
            return default
        if isinstance(raw, bool):
            return default
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(round(raw))
        s = str(raw).strip()
        if not s:
            return default
        low = s.lower()
        for junk in ("°c", "°", "min", "minutes", "mins"):
            low = low.replace(junk, "")
        low = low.strip()
        try:
            return int(round(float(low)))
        except ValueError:
            return default

    def _clear_pass_schedule_ht_widgets(self, row: int) -> None:
        self.table.removeCellWidget(row, _COL_PS_TEMPERATURE)
        self.table.removeCellWidget(row, _COL_PS_TIME)
        self.table.removeCellWidget(row, _COL_PS_PROTECTIVE_GAS)

    def _style_pass_schedule_spinbox(self, sb: QSpinBox) -> None:
        sb.setMinimumHeight(22)
        le = sb.lineEdit()
        if le is not None:
            le.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _set_protective_gas_combo_from_saved(self, cb: QComboBox, gas_raw: str) -> None:
        g = (gas_raw or "").strip()
        if not g:
            idx = cb.findText("—")
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            return
        hit = cb.findText(g, Qt.MatchFlag.MatchExactly)
        if hit >= 0:
            cb.setCurrentIndex(hit)
            return
        cb.insertItem(1, g)
        cb.setCurrentIndex(1)

    def _pass_schedule_row_is_draw_pass(self, row: int) -> bool:
        """True when area reduction (col 2) is positive — heat-treatment controls live on that pass row."""
        return self._pass_row_area_reduction_fraction(row) > 0.0

    def _set_pass_schedule_process_cells(self, row: int, p: Dict[str, Any] | None = None) -> None:
        """Draw-pass rows (area reduction > 0): temp/time spinboxes + gas combo on the same row; equipment & notes."""
        src = p or {}
        self._clear_pass_schedule_ht_widgets(row)

        if not self._pass_schedule_row_is_draw_pass(row):
            for col in (_COL_PS_TEMPERATURE, _COL_PS_TIME, _COL_PS_PROTECTIVE_GAS):
                self.table.setItem(row, col, self._schedule_process_item(""))
            self.table.setItem(
                row,
                _COL_PS_EQUIPMENT,
                self._schedule_process_item(str(src.get("equipment", "") or "")),
            )
            self.table.setItem(row, _COL_PS_NOTES, self._schedule_process_item(str(src.get("notes", "") or "")))
            return

        mat = self.material_combo.currentText()
        def_temp = suggested_interpass_ht_temperature_c(mat)
        def_time = suggested_interpass_hold_time_min(mat)
        temp_val = self._parse_schedule_saved_int(src.get("temperature"), def_temp)
        time_val = self._parse_schedule_saved_int(src.get("time"), def_time)
        gas_raw = str(src.get("protective_gas", "") or "").strip()

        sb_t = QSpinBox()
        sb_t.setRange(50, 1300)
        sb_t.setSingleStep(5)
        sb_t.setSuffix(" °C")
        sb_t.setKeyboardTracking(False)
        sb_t.setValue(temp_val)
        sb_t.setToolTip(
            f"Illustrative inter-pass heat-treat temperature (°C); default {def_temp} °C for {mat}. "
            "Adjust with the spin buttons — verify against your qualified procedures."
        )
        self._style_pass_schedule_spinbox(sb_t)

        sb_tm = QSpinBox()
        sb_tm.setRange(0, 1440)
        sb_tm.setSingleStep(5)
        sb_tm.setSuffix(" min")
        sb_tm.setKeyboardTracking(False)
        sb_tm.setValue(time_val)
        sb_tm.setToolTip(
            f"Illustrative hold time; default {def_time} min for {mat}. Adjust with the spin buttons."
        )
        self._style_pass_schedule_spinbox(sb_tm)

        cb = QComboBox()
        cb.setMinimumHeight(22)
        for lab in _PROTECTIVE_GAS_OPTIONS:
            cb.addItem(lab)
        self._set_protective_gas_combo_from_saved(cb, gas_raw)
        cb.setToolTip("Protective atmosphere for heat treatment on this pass (preset list).")

        sb_t.valueChanged.connect(lambda _v, r=row: self._on_ht_temperature_value_changed(r))
        sb_tm.valueChanged.connect(lambda _v, r=row: self._on_ht_time_value_changed(r))
        cb.activated.connect(lambda _i, r=row: self._on_ht_gas_combo_activated(r))

        self.table.setCellWidget(row, _COL_PS_TEMPERATURE, sb_t)
        self.table.setCellWidget(row, _COL_PS_TIME, sb_tm)
        self.table.setCellWidget(row, _COL_PS_PROTECTIVE_GAS, cb)

        self.table.setItem(
            row,
            _COL_PS_EQUIPMENT,
            self._schedule_process_item(str(src.get("equipment", "") or "")),
        )
        self.table.setItem(row, _COL_PS_NOTES, self._schedule_process_item(str(src.get("notes", "") or "")))

    def _clear_area_reduction_cell(self, row: int) -> None:
        self.table.removeCellWidget(row, _COL_AREA_REDUCTION)

    def _pass_row_area_reduction_fraction(self, row: int) -> float:
        """Area reduction r in column 2 (spinbox or legacy table item)."""
        w = self.table.cellWidget(row, _COL_AREA_REDUCTION)
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        it = self.table.item(row, _COL_AREA_REDUCTION)
        if it is not None:
            try:
                return float(it.text().strip())
            except ValueError:
                pass
        return 0.0

    def _style_pass_schedule_double_spinbox(self, sb: QDoubleSpinBox) -> None:
        sb.setMinimumHeight(22)
        le = sb.lineEdit()
        if le is not None:
            le.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _set_pass_row_area_reduction_spin(self, row: int, r: float) -> None:
        """Area reduction column as QDoubleSpinBox with up/down steppers."""
        self._clear_area_reduction_cell(row)
        sb = QDoubleSpinBox()
        sb.setRange(0.0, 0.9999)
        sb.setDecimals(4)
        sb.setSingleStep(0.001)
        sb.setKeyboardTracking(False)
        ru = float(max(0.0, min(0.9999, r)))
        sb.setValue(ru)
        sb.setToolTip(
            "Per-pass annulus area reduction fraction r (0–1). Use spin buttons or type a value. "
            f"Typical optimization bounds: {OPT_SCHEDULE_MIN_PER_PASS_R:.2f}–{OPT_SCHEDULE_MAX_PER_PASS_R:.2f}."
        )
        self._style_pass_schedule_double_spinbox(sb)
        sb.valueChanged.connect(lambda _v, rrow=row: self._on_pass_schedule_area_reduction_changed(rrow))
        self.table.setCellWidget(row, _COL_AREA_REDUCTION, sb)

    def _set_pass_row_area_reduction_spin_programmatic(self, row: int, r: float) -> None:
        self._area_reduction_programmatic = True
        try:
            self._set_pass_row_area_reduction_spin(row, r)
        finally:
            self._area_reduction_programmatic = False

    def _on_pass_schedule_area_reduction_changed(self, row: int) -> None:
        if self._area_reduction_programmatic:
            return
        self.table.blockSignals(True)
        try:
            self._set_pass_schedule_process_cells(row, self._pass_schedule_row_to_dict(row))
        finally:
            self.table.blockSignals(False)
        self._restore_pass_table_vertical_labels_from_cells()
        self._refresh_schedule_visuals()

    def _commit_pass_schedule_area_reduction_edits(self) -> None:
        """Commit typed text in area-reduction spinboxes before geometry reads (same idea as OD/ID)."""
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, _COL_AREA_REDUCTION)
            if isinstance(w, QDoubleSpinBox):
                w.interpretText()

    def _clear_semi_die_angle_cell(self, row: int) -> None:
        self.table.removeCellWidget(row, _COL_SEMI_DIE_ANGLE)

    def _pass_row_semi_die_angle_deg(self, row: int) -> float:
        """Semi-die angle α (°) in column 3 (spinbox or legacy table item)."""
        w = self.table.cellWidget(row, _COL_SEMI_DIE_ANGLE)
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        it = self.table.item(row, _COL_SEMI_DIE_ANGLE)
        if it is not None:
            try:
                return float(it.text().strip())
            except ValueError:
                pass
        return 12.0

    def _set_pass_row_semi_die_angle_spin(self, row: int, alpha_deg: float) -> None:
        """Semi-die angle column as QDoubleSpinBox with up/down steppers."""
        self._clear_semi_die_angle_cell(row)
        sb = QDoubleSpinBox()
        sb.setRange(0.5, 45.0)
        sb.setDecimals(2)
        sb.setSingleStep(0.5)
        sb.setSuffix(" °")
        sb.setKeyboardTracking(False)
        av = float(max(0.5, min(45.0, alpha_deg)))
        sb.setValue(av)
        sb.setToolTip(
            "Semi-die angle α (degrees). Use spin buttons or type a value — typical scheduling range ~6–18° "
            "(see Optimization tab bounds). Like lubricant: pass 1 updates later draw passes; a later pass that "
            "differs from the previous may ask for confirmation."
        )
        self._style_pass_schedule_double_spinbox(sb)
        sb.valueChanged.connect(lambda _v, rrow=row: self._on_semi_die_angle_value_changed(rrow))
        self.table.setCellWidget(row, _COL_SEMI_DIE_ANGLE, sb)

    def _set_pass_row_semi_die_angle_spin_programmatic(self, row: int, alpha_deg: float) -> None:
        self._semi_die_angle_programmatic = True
        try:
            self._set_pass_row_semi_die_angle_spin(row, alpha_deg)
        finally:
            self._semi_die_angle_programmatic = False

    def _set_semi_die_angle_spin_value_programmatic(self, row: int, alpha_deg: float) -> None:
        """Update semi-die spinbox value without treating it as a user edit (revert after confirmation)."""
        self._semi_die_angle_programmatic = True
        try:
            w = self.table.cellWidget(row, _COL_SEMI_DIE_ANGLE)
            if isinstance(w, QDoubleSpinBox):
                w.blockSignals(True)
                w.setValue(float(max(0.5, min(45.0, alpha_deg))))
                w.blockSignals(False)
        finally:
            self._semi_die_angle_programmatic = False

    def _propagate_semi_die_angle_to_draw_passes_from(
        self, alpha_deg: float, draw_rows: list[int], *, start_index: int
    ) -> None:
        """Mirror semi-die angle α to subsequent draw rows (same pattern as lubricant / heat treatment)."""
        self._semi_die_angle_programmatic = True
        try:
            av = float(max(0.5, min(45.0, alpha_deg)))
            for j in range(start_index, len(draw_rows)):
                rw = draw_rows[j]
                w = self.table.cellWidget(rw, _COL_SEMI_DIE_ANGLE)
                if isinstance(w, QDoubleSpinBox):
                    w.blockSignals(True)
                    w.setValue(av)
                    w.blockSignals(False)
        finally:
            self._semi_die_angle_programmatic = False

    def _on_semi_die_angle_value_changed(self, row: int) -> None:
        """First draw pass: mirror semi-die angle to later passes. Later passes: confirm if different from previous."""
        if self._semi_die_angle_programmatic:
            return
        draw_rows = self._draw_row_indices()
        if row not in draw_rows:
            self._refresh_schedule_visuals()
            return
        pos = draw_rows.index(row)
        new_a = self._pass_row_semi_die_angle_deg(row)

        if pos == 0:
            self._propagate_semi_die_angle_to_draw_passes_from(new_a, draw_rows, start_index=1)
            self._refresh_schedule_visuals()
            return

        prev_a = self._pass_row_semi_die_angle_deg(draw_rows[pos - 1])
        if abs(new_a - prev_a) < 1e-6:
            self._refresh_schedule_visuals()
            return

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Icon.Question)
        mb.setWindowTitle("Semi-die angle change")
        mb.setText(
            f"Pass {pos + 1} uses a different semi-die angle than the previous draw pass.\n\n"
            f"Previous pass: {prev_a:.2f}°\n"
            f"This pass: {new_a:.2f}°"
        )
        mb.setInformativeText(
            "Yes — this change is intentional: keep it and apply this angle to all later passes too.\n"
            "No — revert this row to match the previous pass."
        )
        yes_btn = mb.addButton("Yes, intentional", QMessageBox.ButtonRole.AcceptRole)
        mb.addButton("No, match previous pass", QMessageBox.ButtonRole.RejectRole)
        mb.exec()
        clicked = mb.clickedButton()
        if clicked is None or clicked != yes_btn:
            self._set_semi_die_angle_spin_value_programmatic(row, prev_a)
        else:
            self._propagate_semi_die_angle_to_draw_passes_from(new_a, draw_rows, start_index=pos + 1)
        self._refresh_schedule_visuals()

    def _commit_pass_schedule_semi_die_angle_edits(self) -> None:
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, _COL_SEMI_DIE_ANGLE)
            if isinstance(w, QDoubleSpinBox):
                w.interpretText()

    def _clear_mandrel_plug_cell(self, row: int) -> None:
        self.table.removeCellWidget(row, _COL_MANDREL_PLUG)

    def _set_pass_row_mandrel_plug_spin(self, row: int, id_mm: float) -> None:
        """Mandrel/plug OD (mm); defaults from simulated output ID — editable with spin buttons."""
        self._clear_mandrel_plug_cell(row)
        sb = QDoubleSpinBox()
        sb.setRange(0.0001, 2000.0)
        sb.setDecimals(4)
        sb.setSingleStep(0.01)
        sb.setSuffix(" mm")
        sb.setKeyboardTracking(False)
        sb.setValue(float(max(0.0001, min(2000.0, id_mm))))
        sb.setToolTip(
            "Nominal mandrel or plug OD (mm), usually the tube inner diameter after this pass. "
            "Use the spin buttons or type a value; the value is refreshed from Output ID when the schedule is recalculated."
        )
        self._style_pass_schedule_double_spinbox(sb)
        sb.valueChanged.connect(lambda _v, rrow=row: self._on_mandrel_plug_value_changed(rrow))
        self.table.setCellWidget(row, _COL_MANDREL_PLUG, sb)

    def _set_pass_row_mandrel_plug_spin_programmatic(self, row: int, id_mm: float) -> None:
        self._mandrel_plug_programmatic = True
        try:
            self._set_pass_row_mandrel_plug_spin(row, id_mm)
        finally:
            self._mandrel_plug_programmatic = False

    def _sync_mandrel_plug_cell_from_output_id_mm(self, row: int, id_mm: float) -> None:
        """Set mandrel spinbox value to match computed output ID without recreating the widget when possible."""
        self._mandrel_plug_programmatic = True
        try:
            w = self.table.cellWidget(row, _COL_MANDREL_PLUG)
            vm = float(max(0.0001, min(2000.0, id_mm)))
            if isinstance(w, QDoubleSpinBox):
                w.blockSignals(True)
                w.setValue(vm)
                w.blockSignals(False)
            else:
                self._set_pass_row_mandrel_plug_spin(row, vm)
        finally:
            self._mandrel_plug_programmatic = False

    def _set_mandrel_plug_placeholder(self, row: int) -> None:
        self._clear_mandrel_plug_cell(row)
        self.table.setItem(row, _COL_MANDREL_PLUG, self._centered_table_item("—", read_only=True))

    def _pass_row_mandrel_plug_mm_display(self, row: int) -> str:
        """Mandrel/plug value from Pass schedule column (spinbox or placeholder) — drives Process BOM."""
        if self._is_sink_drawing_method():
            return "—"
        w = self.table.cellWidget(row, _COL_MANDREL_PLUG)
        if isinstance(w, QDoubleSpinBox):
            return f"{float(w.value()):.4f}"
        it = self.table.item(row, _COL_MANDREL_PLUG)
        if it is not None:
            txt = (it.text() or "").strip()
            if txt and txt != "—":
                return txt
        return "—"

    def _on_mandrel_plug_value_changed(self, _row: int) -> None:
        """Keep Process BOM Mandrel/Plug column in sync when the Pass schedule spinbox is edited."""
        if self._mandrel_plug_programmatic:
            return
        self._commit_pass_schedule_mandrel_plug_edits()
        self._refresh_die_bom(silent=True)

    def _is_pass_schedule_incoming_row(self, row: int) -> bool:
        """First row shows Tubing Project incoming OD/ID in Output columns (not a drawing pass)."""
        return row == _PASS_SCHEDULE_INCOMING_ROW and self.table.rowCount() > row

    def _setup_pass_schedule_incoming_row(self) -> None:
        """Placeholders for mechanics columns; Output OD/ID from Tubing Project incoming tube."""
        row = _PASS_SCHEDULE_INCOMING_ROW
        self._clear_area_reduction_cell(row)
        self._clear_semi_die_angle_cell(row)
        self._clear_lubricant_cell(row)
        self._clear_mandrel_plug_cell(row)
        for col in (_COL_AREA_REDUCTION, _COL_SEMI_DIE_ANGLE, _COL_LUBRICANT):
            self.table.setItem(row, col, self._centered_table_item("—", read_only=True))
        self._clear_pass_schedule_ht_widgets(row)
        self.table.setItem(row, _COL_PS_EQUIPMENT, self._schedule_process_item(""))
        self.table.setItem(row, _COL_PS_NOTES, self._schedule_process_item(""))
        self.table.setItem(row, _COL_PS_TEMPERATURE, self._schedule_process_item(""))
        self.table.setItem(row, _COL_PS_TIME, self._schedule_process_item(""))
        self.table.setItem(row, _COL_PS_PROTECTIVE_GAS, self._schedule_process_item(""))
        self._sync_pass_schedule_incoming_row_outputs()

    def _sync_pass_schedule_incoming_row_outputs(self) -> None:
        """Output OD/ID on incoming row mirror Tubing Project incoming diameters; Mandrel/Plug stays empty (—)."""
        self._commit_tubing_project_diameter_edits()
        row = _PASS_SCHEDULE_INCOMING_ROW
        if self.table.rowCount() <= row:
            return
        odi = float(self.in_od_mm.value())
        idi = float(self.in_id_mm.value())
        self.table.setItem(row, _COL_OUTPUT_OD, self._centered_table_item(f"{odi:.4f}", read_only=True))
        self.table.setItem(row, _COL_OUTPUT_ID, self._centered_table_item(f"{idi:.4f}", read_only=True))
        self._set_mandrel_plug_placeholder(row)

    def _commit_pass_schedule_mandrel_plug_edits(self) -> None:
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, _COL_MANDREL_PLUG)
            if isinstance(w, QDoubleSpinBox):
                w.interpretText()

    def _clear_lubricant_cell(self, row: int) -> None:
        self.table.removeCellWidget(row, _COL_LUBRICANT)

    def _pass_row_friction_mu(self, row: int) -> float:
        """Friction μ from lubricant combo (column 4), or legacy numeric cell text."""
        w = self.table.cellWidget(row, _COL_LUBRICANT)
        if isinstance(w, QComboBox):
            d = w.currentData()
            if d is not None:
                return float(d)
        it = self.table.item(row, _COL_LUBRICANT)
        if it is not None:
            try:
                return float(it.text().strip())
            except ValueError:
                pass
        return 0.06

    def _set_pass_row_lubricant_combo(self, row: int, mu: float) -> None:
        """Replace column 4 with a preset dropdown; μ must match engine PassInput.friction_mu."""
        self._clear_lubricant_cell(row)
        cb = QComboBox()
        cb.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for label, m in _LUBRICANT_PRESETS_MM:
            cb.addItem(label, float(m))
        mu_f = float(mu)
        sel = -1
        for i in range(cb.count()):
            d = cb.itemData(i)
            if d is None:
                continue
            if abs(float(d) - mu_f) < 1e-5:
                sel = i
                break
        if sel < 0:
            cb.insertItem(0, f"Custom μ = {mu_f:.4f}", mu_f)
            sel = 0
        cb.setEditable(True)
        line_edit = cb.lineEdit()
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line_edit.setReadOnly(True)
        cb.blockSignals(True)
        try:
            cb.setCurrentIndex(sel)
        finally:
            cb.blockSignals(False)
        cb.activated.connect(lambda _i, r=row: self._on_lubricant_combo_activated(r))
        self.table.setCellWidget(row, _COL_LUBRICANT, cb)

    def _set_pass_row_lubricant_combo_programmatic(self, row: int, mu: float) -> None:
        """Update lubricant combo without treating it as a user edit (load / propagate / undo)."""
        self._lubricant_combo_programmatic = True
        try:
            self._set_pass_row_lubricant_combo(row, mu)
        finally:
            self._lubricant_combo_programmatic = False

    @staticmethod
    def _lubricant_display_label_for_mu(mu: float) -> str:
        for label, m in _LUBRICANT_PRESETS_MM:
            if abs(float(m) - float(mu)) < 1e-5:
                return label
        return f"μ={float(mu):.4f}"

    def _propagate_lubricant_mu_to_draw_passes_from(
        self, mu: float, draw_rows: list[int], *, start_index: int
    ) -> None:
        for j in range(start_index, len(draw_rows)):
            self._set_pass_row_lubricant_combo_programmatic(draw_rows[j], mu)

    def _on_lubricant_combo_activated(self, row: int) -> None:
        """First pass: mirror lubricant to later passes. Later passes: confirm if μ differs from previous draw."""
        if self._lubricant_combo_programmatic:
            return
        draw_rows = self._draw_row_indices()
        if row not in draw_rows:
            self._refresh_schedule_visuals()
            return
        pos = draw_rows.index(row)
        cb = self.table.cellWidget(row, _COL_LUBRICANT)
        if not isinstance(cb, QComboBox):
            self._refresh_schedule_visuals()
            return
        d = cb.currentData()
        if d is None:
            self._refresh_schedule_visuals()
            return
        new_mu = float(d)

        if pos == 0:
            self._propagate_lubricant_mu_to_draw_passes_from(new_mu, draw_rows, start_index=1)
            self._refresh_schedule_visuals()
            return

        prev_r = draw_rows[pos - 1]
        prev_mu = self._pass_row_friction_mu(prev_r)
        if abs(new_mu - prev_mu) < 1e-8:
            self._refresh_schedule_visuals()
            return

        prev_lab = self._lubricant_display_label_for_mu(prev_mu)
        new_lab = self._lubricant_display_label_for_mu(new_mu)
        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Icon.Question)
        mb.setWindowTitle("Lubricant change")
        mb.setText(
            f"Pass {pos + 1} uses a different lubricant than the previous draw pass.\n\n"
            f"Previous pass: {prev_lab} (μ={prev_mu:.4f})\n"
            f"Your selection: {new_lab} (μ={new_mu:.4f})"
        )
        mb.setInformativeText(
            "Yes — this change is intentional: keep it and apply this lubricant to all later passes too.\n"
            "No — revert this row to match the previous pass."
        )
        yes_btn = mb.addButton("Yes, intentional", QMessageBox.ButtonRole.AcceptRole)
        mb.addButton("No, match previous pass", QMessageBox.ButtonRole.RejectRole)
        mb.exec()
        clicked = mb.clickedButton()
        if clicked is None or clicked != yes_btn:
            self._set_pass_row_lubricant_combo_programmatic(row, prev_mu)
        else:
            self._propagate_lubricant_mu_to_draw_passes_from(new_mu, draw_rows, start_index=pos + 1)
        self._refresh_schedule_visuals()

    # --- Heat treatment (Temperature / Time / Gas): same propagation + confirmation pattern as lubricant ---

    def _pass_row_ht_temperature_int(self, row: int) -> int:
        w = self.table.cellWidget(row, _COL_PS_TEMPERATURE)
        if isinstance(w, QSpinBox):
            return int(w.value())
        return 0

    def _pass_row_ht_time_int(self, row: int) -> int:
        w = self.table.cellWidget(row, _COL_PS_TIME)
        if isinstance(w, QSpinBox):
            return int(w.value())
        return 0

    def _pass_row_ht_gas_selection_text(self, row: int) -> str:
        """Normalized protective gas (empty string means unset / —)."""
        w = self.table.cellWidget(row, _COL_PS_PROTECTIVE_GAS)
        if isinstance(w, QComboBox):
            t = w.currentText().strip()
            return "" if t == "—" else t
        return ""

    @staticmethod
    def _ht_gas_dialog_label(gas_raw: str) -> str:
        g = (gas_raw or "").strip()
        return "(none)" if not g else g

    def _set_ht_temperature_programmatic(self, row: int, temp_c: int) -> None:
        self._pass_schedule_ht_programmatic = True
        try:
            w = self.table.cellWidget(row, _COL_PS_TEMPERATURE)
            if isinstance(w, QSpinBox):
                w.blockSignals(True)
                w.setValue(int(temp_c))
                w.blockSignals(False)
        finally:
            self._pass_schedule_ht_programmatic = False

    def _propagate_ht_temperature_to_draw_passes_from(
        self, temp_c: int, draw_rows: list[int], *, start_index: int
    ) -> None:
        self._pass_schedule_ht_programmatic = True
        try:
            for j in range(start_index, len(draw_rows)):
                rw = draw_rows[j]
                w = self.table.cellWidget(rw, _COL_PS_TEMPERATURE)
                if isinstance(w, QSpinBox):
                    w.blockSignals(True)
                    w.setValue(int(temp_c))
                    w.blockSignals(False)
        finally:
            self._pass_schedule_ht_programmatic = False

    def _set_ht_time_programmatic(self, row: int, time_min: int) -> None:
        self._pass_schedule_ht_programmatic = True
        try:
            w = self.table.cellWidget(row, _COL_PS_TIME)
            if isinstance(w, QSpinBox):
                w.blockSignals(True)
                w.setValue(int(time_min))
                w.blockSignals(False)
        finally:
            self._pass_schedule_ht_programmatic = False

    def _propagate_ht_time_to_draw_passes_from(
        self, time_min: int, draw_rows: list[int], *, start_index: int
    ) -> None:
        self._pass_schedule_ht_programmatic = True
        try:
            for j in range(start_index, len(draw_rows)):
                rw = draw_rows[j]
                w = self.table.cellWidget(rw, _COL_PS_TIME)
                if isinstance(w, QSpinBox):
                    w.blockSignals(True)
                    w.setValue(int(time_min))
                    w.blockSignals(False)
        finally:
            self._pass_schedule_ht_programmatic = False

    def _set_ht_gas_programmatic(self, row: int, gas_raw: str) -> None:
        self._pass_schedule_ht_programmatic = True
        try:
            cb = self.table.cellWidget(row, _COL_PS_PROTECTIVE_GAS)
            if isinstance(cb, QComboBox):
                cb.blockSignals(True)
                self._set_protective_gas_combo_from_saved(cb, gas_raw)
                cb.blockSignals(False)
        finally:
            self._pass_schedule_ht_programmatic = False

    def _propagate_ht_gas_to_draw_passes_from(
        self, gas_raw: str, draw_rows: list[int], *, start_index: int
    ) -> None:
        self._pass_schedule_ht_programmatic = True
        try:
            for j in range(start_index, len(draw_rows)):
                rw = draw_rows[j]
                cb = self.table.cellWidget(rw, _COL_PS_PROTECTIVE_GAS)
                if isinstance(cb, QComboBox):
                    cb.blockSignals(True)
                    self._set_protective_gas_combo_from_saved(cb, gas_raw)
                    cb.blockSignals(False)
        finally:
            self._pass_schedule_ht_programmatic = False

    def _on_ht_temperature_value_changed(self, row: int) -> None:
        """First draw pass: mirror temperature to later passes. Later passes: confirm if different from previous."""
        if self._pass_schedule_ht_programmatic:
            return
        draw_rows = self._draw_row_indices()
        if row not in draw_rows:
            self._refresh_schedule_visuals()
            return
        pos = draw_rows.index(row)
        new_t = self._pass_row_ht_temperature_int(row)

        if pos == 0:
            self._propagate_ht_temperature_to_draw_passes_from(new_t, draw_rows, start_index=1)
            self._refresh_schedule_visuals()
            return

        prev_t = self._pass_row_ht_temperature_int(draw_rows[pos - 1])
        if new_t == prev_t:
            self._refresh_schedule_visuals()
            return

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Icon.Question)
        mb.setWindowTitle("Temperature change")
        mb.setText(
            f"Pass {pos + 1} uses a different temperature than the previous pass.\n\n"
            f"Previous pass: {prev_t} °C\n"
            f"This pass: {new_t} °C"
        )
        mb.setInformativeText(
            "Yes — this change is intentional: keep it and apply this temperature to all later passes too.\n"
            "No — revert this row to match the previous pass."
        )
        yes_btn = mb.addButton("Yes, intentional", QMessageBox.ButtonRole.AcceptRole)
        mb.addButton("No, match previous pass", QMessageBox.ButtonRole.RejectRole)
        mb.exec()
        clicked = mb.clickedButton()
        if clicked is None or clicked != yes_btn:
            self._set_ht_temperature_programmatic(row, prev_t)
        else:
            self._propagate_ht_temperature_to_draw_passes_from(new_t, draw_rows, start_index=pos + 1)
        self._refresh_schedule_visuals()

    def _on_ht_time_value_changed(self, row: int) -> None:
        if self._pass_schedule_ht_programmatic:
            return
        draw_rows = self._draw_row_indices()
        if row not in draw_rows:
            self._refresh_schedule_visuals()
            return
        pos = draw_rows.index(row)
        new_tm = self._pass_row_ht_time_int(row)

        if pos == 0:
            self._propagate_ht_time_to_draw_passes_from(new_tm, draw_rows, start_index=1)
            self._refresh_schedule_visuals()
            return

        prev_tm = self._pass_row_ht_time_int(draw_rows[pos - 1])
        if new_tm == prev_tm:
            self._refresh_schedule_visuals()
            return

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Icon.Question)
        mb.setWindowTitle("Hold time change")
        mb.setText(
            f"Pass {pos + 1} uses a different hold time than the previous pass.\n\n"
            f"Previous pass: {prev_tm} min\n"
            f"This pass: {new_tm} min"
        )
        mb.setInformativeText(
            "Yes — this change is intentional: keep it and apply this time to all later passes too.\n"
            "No — revert this row to match the previous pass."
        )
        yes_btn = mb.addButton("Yes, intentional", QMessageBox.ButtonRole.AcceptRole)
        mb.addButton("No, match previous pass", QMessageBox.ButtonRole.RejectRole)
        mb.exec()
        clicked = mb.clickedButton()
        if clicked is None or clicked != yes_btn:
            self._set_ht_time_programmatic(row, prev_tm)
        else:
            self._propagate_ht_time_to_draw_passes_from(new_tm, draw_rows, start_index=pos + 1)
        self._refresh_schedule_visuals()

    def _on_ht_gas_combo_activated(self, row: int) -> None:
        if self._pass_schedule_ht_programmatic:
            return
        draw_rows = self._draw_row_indices()
        if row not in draw_rows:
            self._refresh_schedule_visuals()
            return
        pos = draw_rows.index(row)
        new_g = self._pass_row_ht_gas_selection_text(row)

        if pos == 0:
            self._propagate_ht_gas_to_draw_passes_from(new_g, draw_rows, start_index=1)
            self._refresh_schedule_visuals()
            return

        prev_g = self._pass_row_ht_gas_selection_text(draw_rows[pos - 1])
        if new_g == prev_g:
            self._refresh_schedule_visuals()
            return

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Icon.Question)
        mb.setWindowTitle("Protective gas change")
        mb.setText(
            f"Pass {pos + 1} uses a different protective gas than the previous pass.\n\n"
            f"Previous pass: {self._ht_gas_dialog_label(prev_g)}\n"
            f"This pass: {self._ht_gas_dialog_label(new_g)}"
        )
        mb.setInformativeText(
            "Yes — this change is intentional: keep it and apply this atmosphere to all later passes too.\n"
            "No — revert this row to match the previous pass."
        )
        yes_btn = mb.addButton("Yes, intentional", QMessageBox.ButtonRole.AcceptRole)
        mb.addButton("No, match previous pass", QMessageBox.ButtonRole.RejectRole)
        mb.exec()
        clicked = mb.clickedButton()
        if clicked is None or clicked != yes_btn:
            self._set_ht_gas_programmatic(row, prev_g)
        else:
            self._propagate_ht_gas_to_draw_passes_from(new_g, draw_rows, start_index=pos + 1)
        self._refresh_schedule_visuals()

    def _pass_schedule_row_to_dict(self, row: int) -> Dict[str, Any]:
        def cell(c: int) -> str:
            it = self.table.item(row, c)
            return it.text().strip() if it else ""

        r = self._pass_row_area_reduction_fraction(row)
        a = self._pass_row_semi_die_angle_deg(row)
        mu = self._pass_row_friction_mu(row)

        if r <= 0.0:
            temp_s = ""
            time_s = ""
            gas_s = ""
        else:
            wt = self.table.cellWidget(row, _COL_PS_TEMPERATURE)
            if isinstance(wt, QSpinBox):
                temp_s = str(wt.value())
            else:
                temp_s = cell(_COL_PS_TEMPERATURE)

            wm = self.table.cellWidget(row, _COL_PS_TIME)
            if isinstance(wm, QSpinBox):
                time_s = str(wm.value())
            else:
                time_s = cell(_COL_PS_TIME)

            wg = self.table.cellWidget(row, _COL_PS_PROTECTIVE_GAS)
            if isinstance(wg, QComboBox):
                gt = wg.currentText().strip()
                gas_s = "" if gt == "—" else gt
            else:
                gas_s = cell(_COL_PS_PROTECTIVE_GAS)

        return {
            "r": r,
            "alpha_deg": a,
            "mu": mu,
            "temperature": temp_s,
            "time": time_s,
            "protective_gas": gas_s,
            "equipment": cell(_COL_PS_EQUIPMENT),
            "notes": cell(_COL_PS_NOTES),
        }

    def _on_pass_schedule_table_cell_changed(self, row: int, column: int) -> None:
        """Semi-die angle / equipment / notes etc.; area reduction uses its own spinbox signals."""
        self._refresh_schedule_visuals()

    def _pass_schedule_table_as_dicts(self) -> list[Dict[str, Any]]:
        return [
            self._pass_schedule_row_to_dict(row)
            for row in range(self.table.rowCount())
            if not self._is_pass_schedule_incoming_row(row)
        ]

    def _rebuild_pass_table_for_draw_count(self, n_draw: int) -> None:
        """Incoming row + one row per drawing pass; Temperature…Notes sit on each pass row."""
        n_draw = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(n_draw)))
        total = _pass_table_total_rows_for_draw_count(n_draw)
        self.table.setRowCount(total)
        labels = ["Incoming"] + [f"Pass {i + 1}" for i in range(n_draw)]
        self.table.setVerticalHeaderLabels(labels)
        self._setup_pass_schedule_incoming_row()
        for r in range(1, total):
            self._set_pass_row_area_reduction_spin_programmatic(r, 0.12)
            self._set_pass_row_semi_die_angle_spin_programmatic(r, 12.0)
            self._set_pass_row_lubricant_combo_programmatic(r, 0.06)
            self.table.setItem(r, _COL_OUTPUT_OD, self._centered_table_item("—", read_only=True))
            self.table.setItem(r, _COL_OUTPUT_ID, self._centered_table_item("—", read_only=True))
            self._set_mandrel_plug_placeholder(r)
            self._set_pass_schedule_process_cells(r, {})
        self._ht_suggestion_anchor_material = self.material_combo.currentText()
        self._prioritize_inventory_dies_for_non_final_passes()

    def _apply_pass_inputs_list_to_table(self, draws: list[PassInput]) -> None:
        """Replace table with incoming row + one row per draw pass (defaults on heat-treatment columns)."""
        n_draw = len(draws)
        if n_draw < 1:
            return
        total = _pass_table_total_rows_for_draw_count(n_draw)
        self.table.setRowCount(total)
        labels = ["Incoming"] + [f"Pass {i + 1}" for i in range(n_draw)]
        self.table.setVerticalHeaderLabels(labels)
        self._setup_pass_schedule_incoming_row()
        for i, p in enumerate(draws):
            r = 1 + i
            self._set_pass_row_area_reduction_spin_programmatic(r, p.area_reduction_fraction)
            self._set_pass_row_semi_die_angle_spin_programmatic(r, p.semi_die_angle_deg)
            self._set_pass_row_lubricant_combo_programmatic(r, p.friction_mu)
            self.table.setItem(r, _COL_OUTPUT_OD, self._centered_table_item("—", read_only=True))
            self.table.setItem(r, _COL_OUTPUT_ID, self._centered_table_item("—", read_only=True))
            self._set_mandrel_plug_placeholder(r)
            self._set_pass_schedule_process_cells(r, {})
        self._ht_suggestion_anchor_material = self.material_combo.currentText()

    def _is_pass_schedule_incoming_row_on(self, table: QTableWidget, row: int) -> bool:
        return row == _PASS_SCHEDULE_INCOMING_ROW and table.rowCount() > row

    def _table_pass_row_area_reduction_fraction(self, table: QTableWidget, row: int) -> float:
        w = table.cellWidget(row, _COL_AREA_REDUCTION)
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        it = table.item(row, _COL_AREA_REDUCTION)
        if it is not None:
            try:
                return float(it.text().strip())
            except ValueError:
                pass
        return 0.0

    def _table_pass_row_semi_die_angle_deg(self, table: QTableWidget, row: int) -> float:
        w = table.cellWidget(row, _COL_SEMI_DIE_ANGLE)
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        it = table.item(row, _COL_SEMI_DIE_ANGLE)
        if it is not None:
            role = it.data(Qt.ItemDataRole.UserRole)
            if role is not None:
                return float(role)
            try:
                return float(it.text().strip())
            except ValueError:
                pass
        return 12.0

    def _table_pass_row_friction_mu(self, table: QTableWidget, row: int) -> float:
        w = table.cellWidget(row, _COL_LUBRICANT)
        if isinstance(w, QComboBox):
            d = w.currentData()
            if d is not None:
                return float(d)
        it = table.item(row, _COL_LUBRICANT)
        if it is not None:
            role = it.data(Qt.ItemDataRole.UserRole)
            if role is not None:
                return float(role)
            try:
                return float(it.text().strip())
            except ValueError:
                pass
        return 0.06

    def _read_passes_from_table(self, table: QTableWidget) -> list[PassInput]:
        out: list[PassInput] = []
        for row in range(table.rowCount()):
            if self._is_pass_schedule_incoming_row_on(table, row):
                continue
            r = self._table_pass_row_area_reduction_fraction(table, row)
            if r <= 0:
                continue
            out.append(
                PassInput(
                    semi_die_angle_deg=self._table_pass_row_semi_die_angle_deg(table, row),
                    friction_mu=self._table_pass_row_friction_mu(table, row),
                    area_reduction_fraction=r,
                )
            )
        return out

    def _pass_schedule_row_to_dict_on(self, table: QTableWidget, row: int) -> Dict[str, Any]:
        def cell(c: int) -> str:
            it = table.item(row, c)
            return it.text().strip() if it else ""

        r = self._table_pass_row_area_reduction_fraction(table, row)
        a = self._table_pass_row_semi_die_angle_deg(table, row)
        mu = self._table_pass_row_friction_mu(table, row)
        if r <= 0.0:
            temp_s = ""
            time_s = ""
            gas_s = ""
        else:
            wt = table.cellWidget(row, _COL_PS_TEMPERATURE)
            if isinstance(wt, QSpinBox):
                temp_s = str(wt.value())
            else:
                temp_s = cell(_COL_PS_TEMPERATURE)

            wm = table.cellWidget(row, _COL_PS_TIME)
            if isinstance(wm, QSpinBox):
                time_s = str(wm.value())
            else:
                time_s = cell(_COL_PS_TIME)

            wg = table.cellWidget(row, _COL_PS_PROTECTIVE_GAS)
            if isinstance(wg, QComboBox):
                gt = wg.currentText().strip()
                gas_s = "" if gt == "—" else gt
            else:
                gas_s = cell(_COL_PS_PROTECTIVE_GAS)

        return {
            "r": r,
            "alpha_deg": a,
            "mu": mu,
            "temperature": temp_s,
            "time": time_s,
            "protective_gas": gas_s,
            "equipment": cell(_COL_PS_EQUIPMENT),
            "notes": cell(_COL_PS_NOTES),
        }

    def _clear_area_reduction_cell_on(self, table: QTableWidget, row: int) -> None:
        table.removeCellWidget(row, _COL_AREA_REDUCTION)

    def _set_pass_row_area_reduction_spin_on(
        self, table: QTableWidget, row: int, r: float, *, on_user_change
    ) -> None:
        self._clear_area_reduction_cell_on(table, row)
        sb = QDoubleSpinBox()
        sb.setRange(0.0, 0.9999)
        sb.setDecimals(4)
        sb.setSingleStep(0.001)
        sb.setKeyboardTracking(False)
        sb.setValue(float(max(0.0, min(0.9999, r))))
        sb.setToolTip(
            "Per-pass annulus area reduction fraction r (0–1). Adjust before applying to Pass schedule."
        )
        self._style_pass_schedule_double_spinbox(sb)
        sb.valueChanged.connect(lambda _v, rrow=row: on_user_change(rrow))
        table.setCellWidget(row, _COL_AREA_REDUCTION, sb)

    def _set_opt_preview_row_die_lube_readonly(self, table: QTableWidget, row: int, p: PassInput) -> None:
        a_it = self._centered_table_item(f"{p.semi_die_angle_deg:.2f}", read_only=True)
        a_it.setData(Qt.ItemDataRole.UserRole, float(p.semi_die_angle_deg))
        table.setItem(row, _COL_SEMI_DIE_ANGLE, a_it)
        lub = self._lubricant_display_label_for_mu(p.friction_mu)
        l_it = self._centered_table_item(lub, read_only=True)
        l_it.setData(Qt.ItemDataRole.UserRole, float(p.friction_mu))
        table.setItem(row, _COL_LUBRICANT, l_it)

    def _clear_pass_schedule_ht_widgets_on(self, table: QTableWidget, row: int) -> None:
        table.removeCellWidget(row, _COL_PS_TEMPERATURE)
        table.removeCellWidget(row, _COL_PS_TIME)
        table.removeCellWidget(row, _COL_PS_PROTECTIVE_GAS)

    def _set_pass_schedule_process_cells_on(
        self,
        table: QTableWidget,
        row: int,
        p: Dict[str, Any] | None = None,
        *,
        on_ht_changed=None,
    ) -> None:
        src = p or {}
        self._clear_pass_schedule_ht_widgets_on(table, row)
        is_draw = self._table_pass_row_area_reduction_fraction(table, row) > 0.0
        if not is_draw:
            for col in (_COL_PS_TEMPERATURE, _COL_PS_TIME, _COL_PS_PROTECTIVE_GAS):
                table.setItem(row, col, self._schedule_process_item(""))
            table.setItem(
                row,
                _COL_PS_EQUIPMENT,
                self._schedule_process_item(str(src.get("equipment", "") or "")),
            )
            table.setItem(row, _COL_PS_NOTES, self._schedule_process_item(str(src.get("notes", "") or "")))
            return

        mat = self.material_combo.currentText()
        def_temp = suggested_interpass_ht_temperature_c(mat)
        def_time = suggested_interpass_hold_time_min(mat)
        temp_val = self._parse_schedule_saved_int(src.get("temperature"), def_temp)
        time_val = self._parse_schedule_saved_int(src.get("time"), def_time)
        gas_raw = str(src.get("protective_gas", "") or "").strip()

        sb_t = QSpinBox()
        sb_t.setRange(50, 1300)
        sb_t.setSingleStep(5)
        sb_t.setSuffix(" °C")
        sb_t.setKeyboardTracking(False)
        sb_t.setValue(temp_val)
        self._style_pass_schedule_spinbox(sb_t)

        sb_tm = QSpinBox()
        sb_tm.setRange(0, 1440)
        sb_tm.setSingleStep(5)
        sb_tm.setSuffix(" min")
        sb_tm.setKeyboardTracking(False)
        sb_tm.setValue(time_val)
        self._style_pass_schedule_spinbox(sb_tm)

        cb = QComboBox()
        cb.setMinimumHeight(22)
        for lab in _PROTECTIVE_GAS_OPTIONS:
            cb.addItem(lab)
        self._set_protective_gas_combo_from_saved(cb, gas_raw)

        if on_ht_changed is not None:
            sb_t.valueChanged.connect(lambda _v, r=row: on_ht_changed())
            sb_tm.valueChanged.connect(lambda _v, r=row: on_ht_changed())
            cb.activated.connect(lambda _i, r=row: on_ht_changed())

        table.setCellWidget(row, _COL_PS_TEMPERATURE, sb_t)
        table.setCellWidget(row, _COL_PS_TIME, sb_tm)
        table.setCellWidget(row, _COL_PS_PROTECTIVE_GAS, cb)
        table.setItem(
            row,
            _COL_PS_EQUIPMENT,
            self._schedule_process_item(str(src.get("equipment", "") or "")),
        )
        table.setItem(row, _COL_PS_NOTES, self._schedule_process_item(str(src.get("notes", "") or "")))

    def _setup_pass_schedule_incoming_row_on(self, table: QTableWidget) -> None:
        row = _PASS_SCHEDULE_INCOMING_ROW
        self._clear_area_reduction_cell_on(table, row)
        table.removeCellWidget(row, _COL_SEMI_DIE_ANGLE)
        table.removeCellWidget(row, _COL_LUBRICANT)
        table.removeCellWidget(row, _COL_MANDREL_PLUG)
        for col in (_COL_AREA_REDUCTION, _COL_SEMI_DIE_ANGLE, _COL_LUBRICANT):
            table.setItem(row, col, self._centered_table_item("—", read_only=True))
        self._clear_pass_schedule_ht_widgets_on(table, row)
        table.setItem(row, _COL_PS_EQUIPMENT, self._schedule_process_item(""))
        table.setItem(row, _COL_PS_NOTES, self._schedule_process_item(""))
        for col in (_COL_PS_TEMPERATURE, _COL_PS_TIME, _COL_PS_PROTECTIVE_GAS):
            table.setItem(row, col, self._schedule_process_item(""))
        self._sync_pass_schedule_incoming_row_outputs_on(table)

    def _sync_pass_schedule_incoming_row_outputs_on(self, table: QTableWidget) -> None:
        self._commit_tubing_project_diameter_edits()
        row = _PASS_SCHEDULE_INCOMING_ROW
        if table.rowCount() <= row:
            return
        odi = float(self.in_od_mm.value())
        idi = float(self.in_id_mm.value())
        table.setItem(row, _COL_OUTPUT_OD, self._centered_table_item(f"{odi:.4f}", read_only=True))
        table.setItem(row, _COL_OUTPUT_ID, self._centered_table_item(f"{idi:.4f}", read_only=True))
        table.setItem(row, _COL_MANDREL_PLUG, self._centered_table_item("—", read_only=True))

    def _sync_pass_table_outputs_on(self, table: QTableWidget) -> None:
        def _set_out(row: int, od_txt: str, id_txt: str) -> None:
            table.setItem(row, _COL_OUTPUT_OD, self._centered_table_item(od_txt, read_only=True))
            table.setItem(row, _COL_OUTPUT_ID, self._centered_table_item(id_txt, read_only=True))
            it = id_txt.strip()
            if od_txt.strip() == "—" or it == "—":
                table.setItem(row, _COL_MANDREL_PLUG, self._centered_table_item("—", read_only=True))
            elif self._is_sink_drawing_method():
                table.setItem(row, _COL_MANDREL_PLUG, self._centered_table_item("—", read_only=True))
            else:
                try:
                    id_mm = float(it)
                    table.setItem(row, _COL_MANDREL_PLUG, self._centered_table_item(f"{id_mm:.4f}", read_only=True))
                except ValueError:
                    table.setItem(row, _COL_MANDREL_PLUG, self._centered_table_item("—", read_only=True))

        passes = self._read_passes_from_table(table)
        table.blockSignals(True)
        try:
            self._sync_pass_schedule_incoming_row_outputs_on(table)
            valid_rows: list[int] = []
            for row in range(table.rowCount()):
                if self._is_pass_schedule_incoming_row_on(table, row):
                    continue
                if self._table_pass_row_area_reduction_fraction(table, row) > 0:
                    valid_rows.append(row)
            ods_mm, ids_mm, _n_passes, err = self._schedule_geometry_mm_from_passes(passes)
            for row in range(table.rowCount()):
                if self._is_pass_schedule_incoming_row_on(table, row):
                    continue
                if err:
                    _set_out(row, "—", "—")
                    continue
                if row not in valid_rows:
                    _set_out(row, "—", "—")
                    continue
                idx = valid_rows.index(row)
                if idx + 1 >= len(ods_mm):
                    _set_out(row, "—", "—")
                    continue
                _set_out(row, f"{ods_mm[idx + 1]:.4f}", f"{ids_mm[idx + 1]:.4f}")
        finally:
            table.blockSignals(False)
        table.viewport().update()

    def _commit_opt_preview_area_reduction_edits(self) -> None:
        table = self.opt_preview_table
        for row in range(table.rowCount()):
            w = table.cellWidget(row, _COL_AREA_REDUCTION)
            if isinstance(w, QDoubleSpinBox):
                w.interpretText()

    def _on_opt_preview_area_reduction_changed(self, _row: int) -> None:
        if getattr(self, "_opt_preview_area_reduction_programmatic", False):
            return
        self._refresh_optimization_preview_visuals()

    def _on_opt_preview_table_cell_changed(self, _row: int, _column: int) -> None:
        self._refresh_optimization_preview_visuals()

    def _populate_optimization_preview(self, draws: list[PassInput]) -> None:
        table = self.opt_preview_table
        n_draw = len(draws)
        if n_draw < 1:
            self.opt_apply_schedule_btn.setEnabled(False)
            return
        main_rows = [row for row, _ in self._iter_schedule_pass_rows_and_inputs()]
        total = _pass_table_total_rows_for_draw_count(n_draw)
        self._opt_preview_area_reduction_programmatic = True
        table.blockSignals(True)
        try:
            table.setRowCount(total)
            labels = ["Incoming"] + [f"Pass {i + 1}" for i in range(n_draw)]
            table.setVerticalHeaderLabels(labels)
            self._setup_pass_schedule_incoming_row_on(table)
            ht_refresh = self._refresh_optimization_preview_visuals
            for i, p in enumerate(draws):
                r = 1 + i
                self._set_pass_row_area_reduction_spin_on(
                    table, r, p.area_reduction_fraction, on_user_change=self._on_opt_preview_area_reduction_changed
                )
                self._set_opt_preview_row_die_lube_readonly(table, r, p)
                table.setItem(r, _COL_OUTPUT_OD, self._centered_table_item("—", read_only=True))
                table.setItem(r, _COL_OUTPUT_ID, self._centered_table_item("—", read_only=True))
                ht_src: Dict[str, Any] = {}
                if i < len(main_rows):
                    ht_src = self._pass_schedule_row_to_dict(main_rows[i])
                elif main_rows:
                    ht_src = self._pass_schedule_row_to_dict(main_rows[-1])
                self._set_pass_schedule_process_cells_on(table, r, ht_src, on_ht_changed=ht_refresh)
        finally:
            table.blockSignals(False)
            self._opt_preview_area_reduction_programmatic = False
        self.opt_apply_schedule_btn.setEnabled(True)
        self._sync_opt_preview_table_geometry()
        try:
            self._fea_sync_pass_select_from_preview()
            self._fea_set_display_passes(draws)
        except Exception:
            pass
        self._ensure_optimization_preview_strip_height()
        self._refresh_optimization_preview_visuals()
        self._finish_optimization_preview_ui(n_draw)

    def _sync_opt_preview_table_geometry(self) -> None:
        """Keep the preview table tall enough to show rows inside its scroll area."""
        table = self.opt_preview_table
        if table.rowCount() <= 0:
            return
        table.resizeRowsToContents()
        row_h = max(22, table.verticalHeader().length())
        table.setMinimumHeight(row_h + table.horizontalHeader().height() + 6)
        table.updateGeometry()
        if getattr(self, "opt_preview_table_scroll", None) is not None:
            self.opt_preview_table_scroll.updateGeometry()

    def _ensure_optimization_preview_strip_height(self) -> None:
        strip = getattr(self, "opt_preview_cross_section_strip", None)
        if strip is None:
            return
        if strip.height() >= 50:
            return
        ref = getattr(self, "pass_schedule_cross_section_strip", None)
        h = ref.height() if ref is not None and ref.height() >= 50 else 160
        strip.setFixedHeight(h)

    def _finish_optimization_preview_ui(self, n_draw: int) -> None:
        self.opt_preview_hint.setText(
            f"Optimized {n_draw} drawing pass(es). Edit area reductions below, then click "
            "“Apply to Pass Schedule…” to update the main schedule."
        )
        if getattr(self, "opt_preview_table_scroll", None) is not None:
            self.opt_preview_table_scroll.ensureWidgetVisible(self.opt_preview_table, 0, 0)
        QApplication.processEvents()
        self._relayout_visible_figures()

    def _fea_sync_pass_select_from_preview(self) -> None:
        """Limit FEA pass selector to drawing passes in the Optimization preview table."""
        if not getattr(self, "fea_pass_select", None):
            return
        passes = self._read_passes_from_table(self.opt_preview_table)
        n = max(1, len(passes))
        cur = int(self.fea_pass_select.value())
        self.fea_pass_select.setRange(1, n)
        self.fea_pass_select.setValue(max(1, min(cur, n)))

    def _refresh_optimization_preview_visuals(self) -> None:
        if not getattr(self, "opt_preview_table", None):
            return
        self._commit_opt_preview_area_reduction_edits()
        self._sync_pass_table_outputs_on(self.opt_preview_table)
        passes = self._read_passes_from_table(self.opt_preview_table)
        self.opt_preview_cross_section_strip.set_model(self._build_cross_section_strip_model(passes))
        self.opt_preview_cross_section_strip.update()

    def _apply_optimized_preview_to_pass_schedule(self) -> None:
        passes = self._read_passes_from_table(self.opt_preview_table)
        if not passes:
            QMessageBox.warning(
                self,
                "Optimization",
                "No optimized drawing passes to apply — run optimization first.",
            )
            return
        ans = QMessageBox.question(
            self,
            "Apply optimized schedule",
            "Copy the optimized pass schedule (including your area-reduction edits) into the "
            "Pass schedule tab and refresh BOM and quotation data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.table.blockSignals(True)
        try:
            self._apply_pass_inputs_list_to_table(passes)
            for i in range(len(passes)):
                r = 1 + i
                if r < self.table.rowCount():
                    self._set_pass_schedule_process_cells(
                        r, self._pass_schedule_row_to_dict_on(self.opt_preview_table, r)
                    )
        finally:
            self.table.blockSignals(False)
        self._capture_tubing_project_baseline_from_table()
        self._refresh_schedule_visuals()
        QMessageBox.information(
            self,
            "Pass schedule",
            "Optimized schedule applied. Open Process BOM or Quotation to see updated values.",
        )

    def _restore_pass_table_vertical_labels_from_cells(self) -> None:
        """Row 0 is Incoming; below that label Pass n when area reduction (col 2) > 0."""
        labels: list[str] = []
        pass_n = 0
        for row in range(self.table.rowCount()):
            if self._is_pass_schedule_incoming_row(row):
                labels.append("Incoming")
                continue
            rv = self._pass_row_area_reduction_fraction(row)
            if rv > 0:
                pass_n += 1
                labels.append(f"Pass {pass_n}")
            else:
                labels.append("—")
        if labels:
            self.table.setVerticalHeaderLabels(labels)

    def _draw_row_indices(self) -> list[int]:
        """Row indices for drawing passes (positive area reduction in col 2); stops before legacy HT rows."""
        out: list[int] = []
        for row in range(self.table.rowCount()):
            h = self.table.verticalHeaderItem(row)
            lab = (h.text() if h else "").strip()
            if lab.upper().startswith("HT"):
                break
            rv = self._pass_row_area_reduction_fraction(row)
            if rv > 0:
                out.append(row)
        return out

    def _refresh_fenics_status(self) -> None:
        if dolfinx_available():
            self.fenics_status.setText(
                "dolfinx: available — axisymmetric tube/die FEA and FEA optimization can run."
            )
        else:
            self.fenics_status.setText(
                "dolfinx: not importable — install conda-forge dolfinx (requirements-fenics.txt) for this tab."
            )

    def _refresh_damask_status(self) -> None:
        if not getattr(self, "damask_status_label", None):
            return
        msg = damask_status_message()
        self.damask_status_label.setText(msg)
        if self._simulation_backend_kind() == "damask" and not damask_can_run():
            self.damask_status_label.setStyleSheet(
                "color: #9a3412; font-size: 11px; font-weight: 600;"
            )
        else:
            self.damask_status_label.setStyleSheet("color: #555; font-size: 11px;")

    def _grain_backend_for_simulation(self) -> Literal["analytical", "damask"]:
        return "damask" if self._simulation_backend_kind() == "damask" else "analytical"

    def _update_geometry_hints(self) -> None:
        if getattr(self, "_incoming_id_tracks_od", False) or getattr(self, "_target_id_tracks_od", False):
            lines: list[str] = []
            if self._incoming_id_tracks_od:
                lines.append(
                    "Incoming: inner diameter matches OD (gray) — enter an ID smaller than OD for a valid tube wall."
                )
            if self._target_id_tracks_od:
                lines.append(
                    "Target output: inner diameter matches OD (gray) — enter an ID smaller than OD for a valid tube wall."
                )
            self.geometry_hints.setText("\n".join(lines))
            self._update_schedule_pass_count_display()
            return
        odi = self.in_od_mm.value()
        idi = self.in_id_mm.value()
        odo = self.out_od_mm.value()
        ido = self.out_id_mm.value()
        if odi <= idi:
            self.geometry_hints.setText("Incoming: OD must be greater than ID.")
            self._update_schedule_pass_count_display()
            return
        if odo <= ido:
            self.geometry_hints.setText("Target output: OD must be greater than ID.")
            self._update_schedule_pass_count_display()
            return
        wi = 0.5 * (odi - idi)
        wo = 0.5 * (odo - ido)
        try:
            r = implied_area_reduction_fraction(odi / 1000.0, idi / 1000.0, odo / 1000.0, ido / 1000.0)
        except ValueError:
            self.geometry_hints.setText("Check diameters: wall thickness must be positive.")
            self._update_schedule_pass_count_display()
            return
        if r >= 0.0:
            area_txt = f"Implied annulus area reduction (incoming → target): {100.0 * r:.2f}%"
        else:
            area_txt = (
                f"Target annulus area is {-100.0 * r:.2f}% larger than incoming "
                f"(output OD/ID are not a smaller draw than input — check values)"
            )
        lines = [
            f"Incoming wall: {wi:.4f} mm · Target wall: {wo:.4f} mm · {area_txt}",
        ]
        if getattr(self, "opt_min_sf", None) is not None:
            bsch = self._pass_schedule_draw_pass_count()
            if bsch is not None:
                _rt, _phi, n_draw_s, hit_cap_s = bsch
                n_draw_s = max(1, int(n_draw_s))
                cap_s = (
                    f" (draw passes capped at {OPT_SCHEDULE_MAX_PASSES})"
                    if hit_cap_s
                    else ""
                )
                lines.append(
                    f"Suggested pass table: {n_draw_s} row(s) (one per pass)"
                    f"{cap_s} — uses Min SF {self.opt_min_sf.value():.2f} on Optimization tab."
                )
        self.geometry_hints.setText("\n".join(lines))
        self._update_schedule_pass_count_display()

    def _update_schedule_pass_count_display(self) -> None:
        """Pass Schedule tab line + kept in sync with Tubing Project hints."""
        if not getattr(self, "pass_schedule_count_label", None) or not getattr(self, "opt_min_sf", None):
            return
        b = self._pass_schedule_draw_pass_count()
        if b is None:
            self.pass_schedule_count_label.setText(
                "Suggested table: — Set incoming/target OD/ID with positive annulus reduction (see hints above on Tubing Project)."
            )
            return
        _r_tot, _phi, n_draw, hit_cap = b
        n_draw = max(1, int(n_draw))
        cap_note = f" (draw passes capped at {OPT_SCHEDULE_MAX_PASSES})" if hit_cap else ""
        if self._simulation_backend_kind() == "manual":
            tail = (
                f"Min SF {self.opt_min_sf.value():.2f} (Optimization). Master Mode: set Prefered Number Of Passes "
                "and click Manual Reschedule — Master Mode."
            )
        else:
            tail = (
                f"Min SF {self.opt_min_sf.value():.2f} (Optimization). Built-In Analytical uses this count when "
                "resizing the table after OD/ID edits — Prefered Number Of Passes applies only in Master Mode."
            )
        self.pass_schedule_count_label.setText(
            f"Suggested table: {n_draw} row(s) (one per pass){cap_note}. "
            f"{tail}"
        )

    def _current_project_record(self) -> TubingProjectRecord:
        return make_record_from_ui(
            self.in_od_mm.value(),
            self.in_id_mm.value(),
            self.out_od_mm.value(),
            self.out_id_mm.value(),
            self.material_combo.currentText(),
            self.drawing_method_combo.currentText(),
        )

    def _clear_judgment_suggested_project_files(self) -> None:
        """Remove auto-copied judgment JSONs; leaves other files in ``suggested_projects`` untouched."""
        root = self._suggested_projects_dir()
        if not root.is_dir():
            return
        for p in root.glob("judgment_closest_*.json"):
            try:
                p.unlink()
            except OSError:
                pass

    def _sync_closest_history_refs_to_suggested_projects(
        self, cur: TubingProjectRecord, projects: List[TubingProjectRecord]
    ) -> None:
        """Copy the nearest history rows (by OD/ID) into ./suggested_projects/ as bundle JSON."""
        root = self._suggested_projects_dir()
        root.mkdir(parents=True, exist_ok=True)
        for p in root.glob("judgment_closest_*.json"):
            try:
                p.unlink()
            except OSError:
                pass

        closest = find_closest(cur, projects, k=_CLOSEST_HISTORY_REFERENCE_COUNT)
        for i, (_dist, rec) in enumerate(closest, start=1):
            title_part = self._sanitize_filename_part(rec.title or rec.material)[:72]
            fname = f"judgment_closest_{i:02d}_{title_part}_{rec.id[:8]}.json"
            if len(fname) > 200:
                fname = f"judgment_closest_{i:02d}_{rec.id}.json"
            out_path = root / fname
            bundle = make_bundle_from_parts(
                project=dict(rec.project),
                pass_schedule=rec.pass_schedule,
                pass_bom=rec.pass_bom,
                quotation=rec.quotation,
                record_meta=rec,
            )
            try:
                save_project_bundle(out_path, bundle)
            except OSError:
                pass

    def _update_project_history_panel(self) -> None:
        self._matched_history_record = None

        path = default_history_path()
        projects = load_projects(path)
        cur = self._current_project_record()
        if not projects:
            self._clear_judgment_suggested_project_files()
            self.project_status_label.setText(
                "Judgment: no history file yet — treat this as a new project until you save a baseline."
            )
            self.project_closest_label.setText("")
            return

        hit = find_exact(cur, projects)
        if hit:
            self._matched_history_record = hit
            when = hit.saved_at[:10] if hit.saved_at else "?"
            self.project_status_label.setText(
                "Judgment: historical — incoming/target OD/ID, material, and process match a saved project "
                f"(saved {when}). Same setup as before."
            )
            self.project_closest_label.setText(
                "No closest-size list needed — this matches history exactly. "
                "The three nearest saved projects (by OD/ID) are copied to ./suggested_projects/ for Fetch History."
            )
        else:
            self.project_status_label.setText(
                "Judgment: new project — no saved entry matches these OD/IDs with the same material and process."
            )
            closest = find_closest(cur, projects, k=_CLOSEST_HISTORY_REFERENCE_COUNT)
            if not closest:
                self.project_closest_label.setText("")
            else:
                lines: list[str] = ["Closest saved references (by OD/ID only):"]
                for i, (dist, r) in enumerate(closest, start=1):
                    when = r.saved_at[:10] if r.saved_at else "?"
                    lines.append(
                        f"  {i}) In {r.in_od_mm:.3f}/{r.in_id_mm:.3f} → Out {r.out_od_mm:.3f}/{r.out_id_mm:.3f} mm "
                        f"· Δ≈{dist:.3f} mm · {r.material} · {when}"
                    )
                self.project_closest_label.setText("\n".join(lines))

        self._sync_closest_history_refs_to_suggested_projects(cur, projects)

    @staticmethod
    def _sanitize_filename_part(s: str) -> str:
        s = (s or "").strip()
        s = re.sub(r'[\\/:*?"<>|]+', "_", s)
        s = re.sub(r"\s+", "_", s)
        s = re.sub(r"_+", "_", s)
        return s.strip("_") or "unknown"

    def _suggested_export_filename(
        self,
        extension: str,
        *,
        record: TubingProjectRecord | None = None,
    ) -> str:
        """
        ``Tubing_Master_{Date}_{Material}_{Drawing Method}_From_{IOD}x_{IID}_To_{OOD}x_{OID}.ext``
        """
        ext = extension.lstrip(".")
        d = date.today().isoformat()
        if record is not None:
            mat = self._sanitize_filename_part(record.material)
            method = self._sanitize_filename_part(record.drawing_method)
            iod = f"{record.in_od_mm:.3f}"
            iid = f"{record.in_id_mm:.3f}"
            ood = f"{record.out_od_mm:.3f}"
            oid = f"{record.out_id_mm:.3f}"
        else:
            mat = self._sanitize_filename_part(self.material_combo.currentText())
            method = self._sanitize_filename_part(self.drawing_method_combo.currentText())
            iod = f"{self.in_od_mm.value():.3f}"
            iid = f"{self.in_id_mm.value():.3f}"
            ood = f"{self.out_od_mm.value():.3f}"
            oid = f"{self.out_id_mm.value():.3f}"
        stem = f"Tubing_Master_{d}_{mat}_{method}_From_{iod}x_{iid}_To_{ood}x_{oid}"
        if len(stem) > 180:
            stem = stem[:180].rstrip("_")
        return f"{stem}.{ext}"

    def _export_dialog_initial_path(
        self,
        extension: str,
        *,
        record: TubingProjectRecord | None = None,
    ) -> str:
        return str(default_export_dir() / self._suggested_export_filename(extension, record=record))

    def _save_project_as_new_bundle(self) -> None:
        """Write ``tubing_master_project_bundle`` JSON: project, pass schedule, BOM, quotation."""
        err_geo = self._geometry_error_message(require_target=False)
        if err_geo:
            QMessageBox.warning(self, "Save As New", f"Fix geometry first:\n{err_geo}")
            return

        proj = self._project_dict()
        proj.setdefault(
            "note",
            f"{proj.get('material', 'Project')} — {len((proj.get('pass_schedule') or {}).get('passes') or [])} pass(es)",
        )
        pass_sched = proj.get("pass_schedule") or {}

        try:
            die_rows = compute_pass_die_rows(proj)
            pass_bom = merge_detail_into_pass_bom_payload(proj, die_rows)
        except Exception as exc:
            QMessageBox.warning(self, "Save As New", f"Could not build pass BOM:\n{exc}")
            return

        suggested = self._export_dialog_initial_path("json")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save project bundle",
            suggested,
            "JSON (*.json);;All files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")

        quot_raw = getattr(self, "_quotation_snapshot", None)
        quotation: Dict[str, Any] | None = dict(quot_raw) if quot_raw else None

        bundle = make_bundle_from_parts(
            project=proj,
            pass_schedule=pass_sched,
            pass_bom=pass_bom,
            quotation=quotation,
        )
        try:
            save_project_bundle(path, bundle)
        except OSError as exc:
            QMessageBox.warning(self, "Save As New", f"Could not write file:\n{exc}")
            return
        QMessageBox.information(self, "Save As New", f"Saved project bundle:\n{path}")

    def _enriched_history_record(
        self,
        *,
        reuse_id: str | None = None,
        reuse_created: str | None = None,
    ) -> TubingProjectRecord:
        proj = self._project_dict()
        mat = self.material_combo.currentText()
        now = now_iso_utc()
        rid = reuse_id or str(uuid.uuid4())
        created = reuse_created or now
        iod, iid = float(self.in_od_mm.value()), float(self.in_id_mm.value())
        ood, oid = float(self.out_od_mm.value()), float(self.out_id_mm.value())
        title = f"{mat} {iod:.3f}/{iid:.3f} → {ood:.3f}/{oid:.3f}"
        pass_sched = proj.get("pass_schedule")
        quot_raw = getattr(self, "_quotation_snapshot", None)
        quotation: Optional[Dict[str, Any]] = dict(quot_raw) if quot_raw else None
        pass_bom: Dict[str, Any] | None
        try:
            rows = compute_pass_die_rows(proj)
            pass_bom = merge_detail_into_pass_bom_payload(proj, rows)
        except Exception:
            pass_bom = None
        return TubingProjectRecord(
            id=rid,
            title=title,
            created=created,
            updated=now,
            project=proj,
            pass_schedule=pass_sched,
            pass_bom=pass_bom,
            quotation=quotation,
        )

    def _save_current_project_to_history_from_pass_schedule(self) -> None:
        """Like Save To History on Tubing Project; if user picks *Save as new* on a duplicate baseline, offer Save-file dialog (Save As New…)."""
        self._save_current_project_to_history(offer_bundle_dialog_on_save_as_new=True)

    def _save_current_project_to_history(
        self, *, offer_bundle_dialog_on_save_as_new: bool = False
    ) -> None:
        err = self._geometry_error_message(require_target=False)
        if err:
            QMessageBox.warning(self, "Project history", err)
            return
        path = default_history_path()
        probe = make_record_from_ui(
            self.in_od_mm.value(),
            self.in_id_mm.value(),
            self.out_od_mm.value(),
            self.out_id_mm.value(),
            self.material_combo.currentText(),
            self.drawing_method_combo.currentText(),
        )
        existing = load_projects(path)
        hit = find_exact(probe, existing)
        bundle_saved: Path | None = None
        bundle_export_cancelled = False
        if hit:
            saved_hint = hit.saved_at[:19].replace("T", " ") if hit.saved_at else "(unknown date)"
            mb = QMessageBox(self)
            mb.setIcon(QMessageBox.Icon.Question)
            mb.setWindowTitle("Project history")
            mb.setText(
                "This Tubing Project baseline matches an existing history entry "
                f"(saved {saved_hint}).\n\n"
                "Overwrite that entry with your current pass schedule, BOM, and quotation snapshots, "
                "or keep both rows and save as a new history entry?"
            )
            ow_btn = mb.addButton("Overwrite", QMessageBox.ButtonRole.AcceptRole)
            new_btn = mb.addButton("Save as new", QMessageBox.ButtonRole.ActionRole)
            cancel_btn = mb.addButton(QMessageBox.StandardButton.Cancel)
            mb.exec()
            clicked = mb.clickedButton()
            if clicked is None or clicked == cancel_btn:
                return
            if clicked == ow_btn:
                rec = self._enriched_history_record(reuse_id=hit.id, reuse_created=hit.created)
            elif clicked == new_btn:
                rec = self._enriched_history_record()
            else:
                return
            upsert_project(rec)
            if offer_bundle_dialog_on_save_as_new and clicked == new_btn:
                bundle_saved, bundle_export_cancelled = self._prompt_save_project_bundle_dialog_for_record(rec)
        else:
            upsert_project(self._enriched_history_record())

        self._update_project_history_panel()
        # Pass Schedule → duplicate baseline → Save as new → cancel bundle dialog: no success toast (history still saved).
        if offer_bundle_dialog_on_save_as_new and bundle_export_cancelled:
            return
        suggested_name = self._suggested_export_filename("json")
        msg = (
            f"Saved project baseline.\n\n{path}\n\nSuggested filename for bundle exports:\n{suggested_name}"
        )
        if bundle_saved is not None:
            msg += f"\n\nProject bundle also saved to:\n{bundle_saved}"
        QMessageBox.information(
            self,
            "Project history",
            msg,
        )

    def _prompt_save_project_bundle_dialog_for_record(
        self, rec: TubingProjectRecord
    ) -> tuple[Path | None, bool]:
        """Same file dialog and JSON bundle format as *Save As New…*, using an existing history record.

        Returns ``(path_or_none, cancelled)``. ``cancelled`` is True only when the user dismissed the dialog
        without choosing a path; write failures set ``cancelled`` False (a warning was already shown).
        """
        suggested = self._export_dialog_initial_path("json", record=rec)
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save project bundle",
            suggested,
            "JSON (*.json);;All files (*)",
        )
        if not path_str:
            return None, True
        out = Path(path_str)
        if out.suffix.lower() != ".json":
            out = out.with_suffix(".json")
        bundle = make_bundle_from_parts(
            project=dict(rec.project or {}),
            pass_schedule=rec.pass_schedule,
            pass_bom=rec.pass_bom,
            quotation=rec.quotation,
            record_meta=rec,
        )
        try:
            save_project_bundle(out, bundle)
        except OSError as exc:
            QMessageBox.warning(self, "Save To History", f"Could not write bundle:\n{exc}")
            return None, False
        return out, False

    @staticmethod
    def _suggested_projects_dir() -> Path:
        """Folder for optional Tubing Master JSON bundles (Fetch History picks from here)."""
        return suggested_projects_dir()

    def _fetch_history_from_suggested_projects(self) -> None:
        suggested_root = self._suggested_projects_dir()
        suggested_root.mkdir(parents=True, exist_ok=True)
        json_files = sorted(suggested_root.glob("*.json"))
        if not json_files:
            QMessageBox.information(
                self,
                "Fetch History",
                "There are no JSON files in:\n"
                f"{suggested_root.resolve()}\n\n"
                "Copy or save Tubing Master project bundle (*.json) files into this folder, then try again.",
            )
            return
        src, _ = QFileDialog.getOpenFileName(
            self,
            "Select suggested project JSON",
            str(suggested_root),
            "JSON (*.json);;All files (*)",
        )
        if not src:
            return
        src_path = Path(src).resolve()
        root = suggested_root.resolve()
        try:
            src_path.relative_to(root)
        except ValueError:
            QMessageBox.warning(
                self,
                "Fetch History",
                "Choose a JSON file inside the suggested projects folder:\n"
                f"{root}",
            )
            return
        try:
            raw = load_project_bundle(Path(src))
            _proj, rec = split_bundle(raw)
        except Exception as exc:
            QMessageBox.warning(self, "Fetch History", f"Could not read project file:\n{exc}")
            return
        suggested = self._export_dialog_initial_path("xlsx", record=rec)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export process document to Excel",
            suggested,
            "Excel workbook (*.xlsx);;All files (*)",
        )
        if not dest:
            return
        path = Path(dest)
        try:
            write_process_document_xlsx(path, rec, src_path)
        except ImportError:
            QMessageBox.warning(
                self,
                "Fetch History",
                "openpyxl is required for Excel export. Install with: pip install openpyxl",
            )
            return
        except OSError as exc:
            QMessageBox.warning(self, "Fetch History", f"Could not write file:\n{exc}")
            return
        QMessageBox.information(self, "Fetch History", f"Exported Excel workbook:\n{path}")

    @staticmethod
    def _set_combo_to_text(combo: QComboBox, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        idx = combo.findText(t, Qt.MatchFlag.MatchExactly)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return
        combo.insertItem(0, t)
        combo.setCurrentIndex(0)

    def _set_backend_from_schedule_dict(self, sch: Dict[str, Any]) -> None:
        kind = str(sch.get("simulation_backend") or "analytical").lower()
        if kind == "manual":
            self.backend_combo.setCurrentIndex(1)
        elif kind == "fenicsx":
            self.backend_combo.setCurrentIndex(2)
        elif kind == "damask":
            self.backend_combo.setCurrentIndex(3)
        else:
            self.backend_combo.setCurrentIndex(0)
        self._refresh_damask_status()

    def _apply_loaded_project(self, proj: Dict[str, Any], rec: TubingProjectRecord) -> None:
        """Apply bundle/history payload to spin boxes, combos, pass table, quotation, then refresh visuals."""
        self._fit_undo_snapshot = None
        sch: Dict[str, Any]
        if rec.pass_schedule is not None and isinstance(rec.pass_schedule, dict):
            sch = dict(rec.pass_schedule)
        else:
            sch = dict(proj.get("pass_schedule") or {})
        passes_raw = list(sch.get("passes") or [])
        passes = _merge_legacy_interleaved_pass_schedule_passes(passes_raw)
        if not passes:
            passes = passes_raw

        for w in (
            self.material_combo,
            self.drawing_method_combo,
            self.backend_combo,
            self.in_od_mm,
            self.in_id_mm,
            self.out_od_mm,
            self.out_id_mm,
        ):
            w.blockSignals(True)
        self.table.blockSignals(True)
        try:
            self.in_od_mm.setValue(float(proj.get("in_od_mm", self.in_od_mm.value())))
            self.in_id_mm.setValue(float(proj.get("in_id_mm", self.in_id_mm.value())))
            self.out_od_mm.setValue(float(proj.get("out_od_mm", self.out_od_mm.value())))
            self.out_id_mm.setValue(float(proj.get("out_id_mm", self.out_id_mm.value())))
            self._set_combo_to_text(
                self.material_combo, normalize_material_label(str(proj.get("material", "")))
            )
            raw_overrides = proj.get("material_property_overrides")
            self._material_property_overrides = (
                dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
            )
            self._set_combo_to_text(self.drawing_method_combo, str(proj.get("drawing_method", "")))
            if sch:
                self._set_backend_from_schedule_dict(sch)

            if passes:
                total = _pass_table_total_rows_for_draw_count(len(passes))
                self.table.setRowCount(total)
                labels = ["Incoming"] + [f"Pass {i + 1}" for i in range(len(passes))]
                self.table.setVerticalHeaderLabels(labels)
                self._setup_pass_schedule_incoming_row()
                for row, p in enumerate(passes):
                    if not isinstance(p, dict):
                        p = {}
                    r_idx = 1 + row
                    r = float(p.get("r", 0.12))
                    a = float(p.get("alpha_deg", 12.0))
                    mu = float(p.get("mu", 0.06))
                    self._set_pass_row_area_reduction_spin_programmatic(r_idx, r)
                    self._set_pass_row_semi_die_angle_spin_programmatic(r_idx, a)
                    self._set_pass_row_lubricant_combo_programmatic(r_idx, mu)
                    self.table.setItem(r_idx, _COL_OUTPUT_OD, self._centered_table_item("—", read_only=True))
                    self.table.setItem(r_idx, _COL_OUTPUT_ID, self._centered_table_item("—", read_only=True))
                    self._set_mandrel_plug_placeholder(r_idx)
                    self._set_pass_schedule_process_cells(r_idx, p)
                self._restore_pass_table_vertical_labels_from_cells()
            else:
                if not self._resize_pass_table_from_geometry_suggestion(silent=True):
                    self.table.blockSignals(True)
                    try:
                        self._rebuild_pass_table_for_draw_count(_MIN_PASS_TABLE_ROWS)
                    finally:
                        self.table.blockSignals(False)

            q_src = rec.quotation if rec.quotation is not None else proj.get("quotation")
            if isinstance(q_src, dict) and q_src:
                self._embed_quotation_in_project = True
                self._quotation_snapshot = dict(q_src)
                q = self._quotation_snapshot
                self.quote_currency.setText(str(q.get("currency", "USD")))
                self.quote_stock_len.setValue(float(q.get("stock_length_m", 1.0) or 1.0))
                self.quote_price_kg.setValue(float(q.get("price_per_kg", 0.0) or 0.0))
                rho = float(q.get("density_kg_m3", 0.0) or 0.0)
                if rho <= 0:
                    rho = density_kg_m3_from_properties(
                        self.material_combo.currentText(), self._current_material_override()
                    )
                self.quote_density.setValue(rho)
                self.quote_additional_cost.setValue(float(q.get("additional_cost", 0.0) or 0.0))
                self._update_quote_stock_price_display()
                if q.get("lines"):
                    try:
                        self._quotation_snapshot = finalize_quotation_v2(
                            proj, self._quotation_snapshot, rebuild_schedule_rows=False
                        )
                        q = self._quotation_snapshot
                    except Exception:
                        pass
                    self._fill_quotation_table(q)
                self._update_quotation_totals_label()
            else:
                self._embed_quotation_in_project = False
                self._quotation_snapshot = {}
                self._sync_quote_density_from_material()
            self._load_die_inventory_from_project(proj)
            self._sync_id_tracking_state_from_loaded_geometry()
        finally:
            for w in (
                self.material_combo,
                self.drawing_method_combo,
                self.backend_combo,
                self.in_od_mm,
                self.in_id_mm,
                self.out_od_mm,
                self.out_id_mm,
            ):
                w.blockSignals(False)
            self.table.blockSignals(False)

        self._ht_suggestion_anchor_material = self.material_combo.currentText()
        self._update_geometry_hints()
        self._update_optuna_derived_label()
        self._capture_tubing_project_baseline_from_table()
        self._sync_preferred_pass_spin_from_table(self._draw_pass_row_count())
        self._refresh_schedule_visuals()
        self._refresh_die_bom(silent=True)
        self._update_project_history_panel()
        if self._read_passes():
            self._run_analytical()
        else:
            self.summary.setPlainText(
                "Project loaded. Add passes with positive area reduction on Pass Schedule, then Run Schedule."
            )

    def _browse_project_json_file(self, close_dialog: QDialog | None = None) -> None:
        root = workdir_projects_dir()
        root.mkdir(parents=True, exist_ok=True)
        parent = close_dialog if close_dialog is not None else self
        path_str, _ = QFileDialog.getOpenFileName(
            parent,
            "Open Tubing Master project JSON",
            str(root),
            "JSON (*.json);;All files (*)",
        )
        if not path_str:
            return
        try:
            raw = load_project_bundle(Path(path_str))
            proj, rec = split_bundle(raw)
            self._apply_loaded_project(proj, rec)
            if close_dialog is not None:
                close_dialog.accept()
            QMessageBox.information(self, "Open project", f"Loaded:\n{path_str}")
        except Exception as exc:
            QMessageBox.warning(self, "Open project", str(exc))

    def _open_projects_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Open project")
        dlg.resize(540, 380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Load from local history (see ./Projects/project_history.json):"))
        lw = QListWidget()
        records = load_projects(default_history_path())
        for rec in records:
            when = rec.saved_at[:19].replace("T", " ") if rec.saved_at else "?"
            lw.addItem(f"{rec.title}  ·  {when}")
        lw.setMinimumHeight(200)
        lay.addWidget(lw, 1)
        lay.addWidget(
            QLabel(
                "Or use Browse to open any Tubing Master bundle JSON (Save As New, snapshots under "
                "./Projects/snapshots/, judgment copies in ./suggested_projects/, etc.)."
            )
        )
        browse = QPushButton("Browse JSON file…")
        browse.clicked.connect(lambda: self._browse_project_json_file(dlg))
        lay.addWidget(browse)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        lay.addWidget(bb)

        def load_selected() -> None:
            row = lw.currentRow()
            if row < 0 or row >= len(records):
                QMessageBox.information(
                    dlg,
                    "Open project",
                    "Select a row in the history list, or use Browse JSON file.",
                )
                return
            sel = records[row]
            proj = dict(sel.project)
            if sel.pass_schedule is not None:
                proj["pass_schedule"] = sel.pass_schedule
            self._apply_loaded_project(proj, sel)
            dlg.accept()
            QMessageBox.information(self, "Open project", f"Loaded from history:\n{sel.title}")

        bb.accepted.connect(load_selected)
        bb.rejected.connect(dlg.reject)
        lw.itemDoubleClicked.connect(lambda _item: load_selected())
        dlg.exec()

    def _current_material_override(self) -> Dict[str, Any] | None:
        key = normalize_material_label(self.material_combo.currentText())
        raw = self._material_property_overrides.get(key)
        return dict(raw) if isinstance(raw, dict) and raw else None

    def _selected_material(self) -> MetalMaterial:
        label = self.material_combo.currentText()
        return metal_material_from_property_dict(label, self._current_material_override())

    def _open_material_properties_dialog(self) -> None:
        label = self.material_combo.currentText()
        key = normalize_material_label(label)
        override, cleared, accepted = run_material_properties_dialog(
            self,
            material_label=label,
            current_override=self._current_material_override(),
        )
        if not accepted:
            return
        if cleared:
            self._material_property_overrides.pop(key, None)
        elif override:
            self._material_property_overrides[key] = dict(override)
        self._update_material_combo_tooltip()
        self._sync_quote_density_from_material()
        self._refresh_schedule_visuals()
        self._update_geometry_hints()

    def _retarget_pass_schedule_ht_suggestions_from_material_change(self) -> None:
        """When material changes, update temp/time spins that still match the previous material's defaults."""
        old_mat = getattr(self, "_ht_suggestion_anchor_material", None)
        new_mat = self.material_combo.currentText()
        if old_mat is None:
            self._ht_suggestion_anchor_material = new_mat
            return
        old_t = suggested_interpass_ht_temperature_c(old_mat)
        new_t = suggested_interpass_ht_temperature_c(new_mat)
        old_tm = suggested_interpass_hold_time_min(old_mat)
        new_tm = suggested_interpass_hold_time_min(new_mat)
        for row in range(self.table.rowCount()):
            if not self._pass_schedule_row_is_draw_pass(row):
                continue
            wt = self.table.cellWidget(row, _COL_PS_TEMPERATURE)
            if isinstance(wt, QSpinBox) and wt.value() == old_t:
                wt.blockSignals(True)
                wt.setValue(new_t)
                wt.blockSignals(False)
            wm = self.table.cellWidget(row, _COL_PS_TIME)
            if isinstance(wm, QSpinBox) and wm.value() == old_tm:
                wm.blockSignals(True)
                wm.setValue(new_tm)
                wm.blockSignals(False)
        self._ht_suggestion_anchor_material = new_mat

    def _update_material_combo_tooltip(self) -> None:
        mat = self._selected_material()
        tip = (
            "Material constitutive model for analytical stress, safety factor, and grain."
        )
        if mat.is_nitinol():
            tip += (
                " Nitinol uses an illustrative superelastic SMA model "
                "(austenite → loading plateau → martensite hardening; "
                "unload plateau on springback)."
            )
        self.material_combo.setToolTip(tip)

    def _sync_material_property_btn_width(self) -> None:
        """Match Edit Material Property button width to the material combo (combo unchanged)."""
        if not getattr(self, "material_combo", None) or not getattr(
            self, "material_properties_btn", None
        ):
            return
        w = int(self.material_combo.width())
        if w > 0:
            self.material_properties_btn.setFixedWidth(w)

    def _on_material_or_drawing_changed(self) -> None:
        self._commit_tubing_project_diameter_edits()
        self._update_material_combo_tooltip()
        self._sync_quote_density_from_material()
        self._retarget_pass_schedule_ht_suggestions_from_material_change()
        self._reconcile_target_bore_for_id_driven_fit()
        self._update_geometry_hints()
        self._update_optuna_derived_label()
        self._refresh_schedule_visuals()
        self._update_project_history_panel()

    def _simulation_backend_kind(self) -> Literal["analytical", "manual", "fenicsx", "damask"]:
        t = self.backend_combo.currentText().lower()
        if "manual" in t:
            return "manual"
        if "damask" in t:
            return "damask"
        if "fenics" in t:
            return "fenicsx"
        return "analytical"

    def _target_draw_pass_count_for_fit_and_table(self) -> int | None:
        """
        Draw-pass count used when resizing for Fit: Master Mode uses Prefered Number Of Passes;
        Built-In Analytical and FEniCSx use geometry- and SF-derived pass count (same as schedule hints).
        """
        if self._simulation_backend_kind() == "manual":
            return max(1, min(OPT_SCHEDULE_MAX_PASSES, int(self.preferred_pass_count_spin.value())))
        b = self._pass_schedule_draw_pass_count()
        if b is None:
            return None
        return max(1, min(OPT_SCHEDULE_MAX_PASSES, int(b[2])))

    _OD_ID_TRACK_EPS_MM = 1e-6

    @staticmethod
    def _tubing_od_spin_user_is_editing_text(sb: QDoubleSpinBox) -> bool:
        """True while the user is typing in the OD box (avoid OD→ID sync stomping partial input)."""
        le = sb.lineEdit()
        return bool(le is not None and le.hasFocus() and le.isModified())

    @staticmethod
    def _configure_tubing_project_diameter_spin(sb: QDoubleSpinBox, *, single_step: float) -> None:
        """Show ▲/▼ steppers (native UI); keyboard tracking enables typing."""
        sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        sb.setSingleStep(single_step)
        try:
            sb.setStepType(QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
        except Exception:
            pass
        sb.setAccelerated(True)
        sb.setKeyboardTracking(True)
        le = sb.lineEdit()
        if le is not None:
            le.setReadOnly(False)

    def _apply_incoming_od_id_tracking_after_od_value(self) -> None:
        eps = self._OD_ID_TRACK_EPS_MM
        odi = float(self.in_od_mm.value())
        idi = float(self.in_id_mm.value())
        if self._incoming_id_tracks_od:
            if abs(idi - odi) > eps:
                self._set_incoming_id_value_programmatic(odi)
            return
        if idi >= odi - eps:
            self._set_incoming_id_tracks_od(True)

    def _apply_target_od_id_tracking_after_od_value(self) -> None:
        eps = self._OD_ID_TRACK_EPS_MM
        odo = float(self.out_od_mm.value())
        ido = float(self.out_id_mm.value())
        if self._target_id_tracks_od:
            if abs(ido - odo) > eps:
                self._set_target_id_value_programmatic(odo)
            return
        if ido >= odo - eps:
            self._set_target_id_tracks_od(True)

    @staticmethod
    def _style_id_spin_tracks_od(sb: QDoubleSpinBox, tracks: bool) -> None:
        """Gray ID text while it tracks OD (placeholder); normal color when user sets ID < OD."""
        le = sb.lineEdit()
        if le is None:
            return
        if tracks:
            pal = QGuiApplication.palette()
            c = pal.color(QPalette.ColorRole.PlaceholderText)
            if not c.isValid() or c == pal.color(QPalette.ColorRole.Text):
                c = QColor(128, 128, 128)
            le.setStyleSheet(f"color: {c.name()};")
        else:
            le.setStyleSheet("")

    def _set_incoming_id_value_programmatic(self, v: float) -> None:
        self._incoming_id_programmatic = True
        try:
            self.in_id_mm.setValue(float(v))
        finally:
            self._incoming_id_programmatic = False

    def _set_target_id_value_programmatic(self, v: float) -> None:
        self._target_id_programmatic = True
        try:
            self.out_id_mm.setValue(float(v))
        finally:
            self._target_id_programmatic = False

    def _set_incoming_id_tracks_od(self, tracks: bool) -> None:
        self._incoming_id_tracks_od = tracks
        if tracks:
            self._set_incoming_id_value_programmatic(float(self.in_od_mm.value()))
        self._style_id_spin_tracks_od(self.in_id_mm, tracks)

    def _set_target_id_tracks_od(self, tracks: bool) -> None:
        self._target_id_tracks_od = tracks
        if tracks:
            self._set_target_id_value_programmatic(float(self.out_od_mm.value()))
        self._style_id_spin_tracks_od(self.out_id_mm, tracks)

    def _on_incoming_od_for_id_tracking(self, _v: float) -> None:
        if self._tubing_od_spin_user_is_editing_text(self.in_od_mm):
            return
        self._apply_incoming_od_id_tracking_after_od_value()

    def _on_incoming_id_for_tracking(self, _v: float) -> None:
        if self._incoming_id_programmatic:
            return
        eps = self._OD_ID_TRACK_EPS_MM
        odi = float(self.in_od_mm.value())
        idi = float(self.in_id_mm.value())
        if idi >= odi - eps:
            self._set_incoming_id_tracks_od(True)
        else:
            self._set_incoming_id_tracks_od(False)

    def _on_target_od_for_id_tracking(self, _v: float) -> None:
        if self._tubing_od_spin_user_is_editing_text(self.out_od_mm):
            return
        self._apply_target_od_id_tracking_after_od_value()

    def _on_target_id_for_tracking(self, _v: float) -> None:
        if self._target_id_programmatic:
            return
        eps = self._OD_ID_TRACK_EPS_MM
        odo = float(self.out_od_mm.value())
        ido = float(self.out_id_mm.value())
        if ido >= odo - eps:
            self._set_target_id_tracks_od(True)
        else:
            self._set_target_id_tracks_od(False)

    def _sync_id_tracking_state_from_loaded_geometry(self) -> None:
        """After loading a bundle or history row, infer gray placeholder vs user ID from OD/ID values."""
        eps = self._OD_ID_TRACK_EPS_MM
        odi = float(self.in_od_mm.value())
        idi = float(self.in_id_mm.value())
        odo = float(self.out_od_mm.value())
        ido = float(self.out_id_mm.value())
        inc_track = idi >= odi - eps
        tgt_track = ido >= odo - eps
        self._incoming_id_tracks_od = inc_track
        self._target_id_tracks_od = tgt_track
        if inc_track:
            self._set_incoming_id_value_programmatic(odi)
        self._style_id_spin_tracks_od(self.in_id_mm, inc_track)
        if tgt_track:
            self._set_target_id_value_programmatic(odo)
        self._style_id_spin_tracks_od(self.out_id_mm, tgt_track)

    def _geometry_error_message(self, require_target: bool = True) -> str | None:
        if getattr(self, "_incoming_id_tracks_od", False):
            return (
                "Incoming inner diameter: enter a value strictly below OD "
                "(gray placeholder matched OD until edited)."
            )
        if getattr(self, "_target_id_tracks_od", False) and require_target:
            return (
                "Target inner diameter: enter a value strictly below OD "
                "(gray placeholder matched OD until edited)."
            )
        odi, idi = self.in_od_mm.value(), self.in_id_mm.value()
        odo, ido = self.out_od_mm.value(), self.out_id_mm.value()
        if odi <= idi:
            return "Incoming tube: outer diameter must be greater than inner diameter."
        if require_target:
            if odo <= ido:
                return "Target output tube: outer diameter must be greater than inner diameter."
            try:
                tube_from_od_id_m(odo / 1000.0, ido / 1000.0)
            except ValueError as e:
                return str(e)
        try:
            tube_from_od_id_m(odi / 1000.0, idi / 1000.0)
        except ValueError as e:
            return str(e)
        return None

    def _warn_od_must_exceed_id_if_needed(self) -> None:
        """Show a modal warning when incoming or target OD ≤ ID (matches :meth:`_geometry_error_message`)."""
        if getattr(self, "_incoming_id_tracks_od", False) or getattr(self, "_target_id_tracks_od", False):
            return
        odi, idi = float(self.in_od_mm.value()), float(self.in_id_mm.value())
        odo, ido = float(self.out_od_mm.value()), float(self.out_id_mm.value())
        parts: list[str] = []
        if odi <= idi:
            parts.append("Incoming tube: outer diameter must be greater than inner diameter.")
        if odo <= ido:
            parts.append("Target output tube: outer diameter must be greater than inner diameter.")
        if parts:
            QMessageBox.warning(self, "Geometry", "\n\n".join(parts))

    def _geom(self) -> TubeGeometry:
        """Start geometry from incoming OD / ID (mm)."""
        err = self._geometry_error_message()
        if err:
            raise ValueError(err)
        return tube_from_od_id_m(self.in_od_mm.value() / 1000.0, self.in_id_mm.value() / 1000.0)

    def _target_geometry(self) -> TubeGeometry:
        err = self._geometry_error_message()
        if err:
            raise ValueError(err)
        return tube_from_od_id_m(self.out_od_mm.value() / 1000.0, self.out_id_mm.value() / 1000.0)

    def _pass_schedule_draw_pass_count(self) -> tuple[float, float, int, bool] | None:
        """
        (total annulus area reduction r_tot, area ratio φ, suggested draw passes, hit_cap) when geometry
        implies a positive reduction; otherwise None.
        """
        err = self._geometry_error_message()
        if err:
            return None
        try:
            g0 = self._geom()
            g1 = self._target_geometry()
        except ValueError:
            return None
        r_tot = implied_area_reduction_fraction(
            g0.outer_diameter_m,
            g0.inner_diameter_m,
            g1.outer_diameter_m,
            g1.inner_diameter_m,
        )
        phi = 1.0 - r_tot
        if r_tot <= 1e-12 or r_tot < 0.0:
            return None
        min_sf = float(self.opt_min_sf.value())
        n_pass, hit_cap = recommended_pass_count(
            area_ratio_target_to_inlet=phi,
            max_per_pass_r=OPT_SCHEDULE_MAX_PER_PASS_R,
            min_per_pass_r=OPT_SCHEDULE_MIN_PER_PASS_R,
            min_margin_uts=min_sf,
            max_passes_cap=OPT_SCHEDULE_MAX_PASSES,
        )
        return (r_tot, phi, n_pass, hit_cap)

    def _draw_pass_row_count(self) -> int:
        """Count of drawing pass rows (positive area reduction); excludes Incoming; skips legacy ``HT …`` rows."""
        return len(self._draw_row_indices())

    def _resize_pass_table_from_geometry_suggestion(self, *, silent: bool = False) -> bool:
        """Set one table row per draw pass; fill defaults. Returns False if geometry unavailable."""
        b = self._pass_schedule_draw_pass_count()
        if b is None:
            if not silent:
                QMessageBox.information(
                    self,
                    "Suggested rows",
                    "Set valid incoming and target OD/ID on Tubing Project with positive annulus reduction. "
                    "Min safety factor is read from the Optimization tab.",
                )
            self._capture_tubing_project_baseline_from_table()
            self._refresh_schedule_visuals()
            return False
        _r_tot, _phi, n_draw, hit_cap = b
        n_draw = max(1, int(n_draw))
        self.table.blockSignals(True)
        try:
            self._rebuild_pass_table_for_draw_count(n_draw)
        finally:
            self.table.blockSignals(False)
        self._fit_undo_snapshot = None
        self.undo_fit_btn.setEnabled(False)
        if not silent and hit_cap:
            QMessageBox.information(
                self,
                "Suggested rows",
                f"Suggested draw passes hit the cap ({OPT_SCHEDULE_MAX_PASSES}). "
                "Each row is one pass — adjust Temperature…Notes per row as needed.",
            )
        if self._simulation_backend_kind() == "manual":
            self._sync_preferred_pass_spin_from_table(n_draw)
        self._capture_tubing_project_baseline_from_table()
        self._refresh_schedule_visuals()
        return True

    def _sync_preferred_pass_spin_from_table(self, n_draw: int) -> None:
        spin = getattr(self, "preferred_pass_count_spin", None)
        if spin is None:
            return
        v = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(n_draw)))
        spin.blockSignals(True)
        try:
            spin.setValue(v)
        finally:
            spin.blockSignals(False)

    def _resize_pass_table_to_draw_count(self, n_draw: int) -> None:
        """Set ``n_draw`` rows (one per pass); fill defaults. Clears Fit undo."""
        n_draw = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(n_draw)))
        self.table.blockSignals(True)
        try:
            self._rebuild_pass_table_for_draw_count(n_draw)
        finally:
            self.table.blockSignals(False)
        self._fit_undo_snapshot = None
        self.undo_fit_btn.setEnabled(False)

    def _manual_reschedule_pass_table(self) -> None:
        """Resize to Prefered Number Of Passes (one row per pass); refresh defaults."""
        self._resize_pass_table_to_draw_count(int(self.preferred_pass_count_spin.value()))
        self._capture_tubing_project_baseline_from_table()
        self._refresh_schedule_visuals()

    def _export_pass_schedule_process_xlsx(self) -> None:
        """Multi-sheet Excel export from current UI (pass schedule, BOM, quotation)."""
        rec = self._enriched_history_record()
        suggested = self._export_dialog_initial_path("xlsx", record=rec)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export pass and heat-treatment schedule",
            suggested,
            "Excel workbook (*.xlsx);;All files (*)",
        )
        if not dest:
            return
        path = Path(dest)
        path = path.with_suffix(".xlsx") if path.suffix.lower() != ".xlsx" else path
        ref = default_history_path()
        try:
            write_process_document_xlsx(path, rec, ref)
        except ImportError:
            QMessageBox.warning(
                self,
                "Export schedule",
                "openpyxl is required for Excel export. Install with: pip install openpyxl",
            )
            return
        except OSError as exc:
            QMessageBox.warning(self, "Export schedule", f"Could not write file:\n{exc}")
            return
        QMessageBox.information(self, "Export schedule", f"Exported workbook:\n{path}")

    def _startup_pass_table_and_visuals(self) -> None:
        self._resize_pass_table_from_geometry_suggestion(silent=True)

    def _iter_schedule_pass_rows_and_inputs(self):
        """Yield (schedule table row index, PassInput) for each draw row with positive area reduction."""
        for row in range(self.table.rowCount()):
            if self._is_pass_schedule_incoming_row(row):
                continue
            r = self._pass_row_area_reduction_fraction(row)
            a = self._pass_row_semi_die_angle_deg(row)
            mu = self._pass_row_friction_mu(row)
            if r <= 0:
                continue
            yield row, PassInput(semi_die_angle_deg=a, friction_mu=mu, area_reduction_fraction=r)

    def _read_passes(self) -> list[PassInput]:
        return [p for _, p in self._iter_schedule_pass_rows_and_inputs()]

    def _pass_display_number_for_schedule_row(self, row: int, fallback_one_based: int) -> str:
        """Pass index as shown on the schedule (vertical header *Pass n*), for BOM row alignment."""
        h = self.table.verticalHeaderItem(row)
        if h is not None:
            m = re.match(r"^Pass\s+(\d+)", h.text().strip(), re.IGNORECASE)
            if m:
                return m.group(1)
        return str(fallback_one_based)

    def _lubricant_display_for_schedule_row(self, row: int) -> str:
        """Text shown on Pass schedule for lubricant (combo label or legacy cell), for BOM display."""
        w = self.table.cellWidget(row, _COL_LUBRICANT)
        if isinstance(w, QComboBox):
            t = w.currentText().strip()
            if t:
                return t
        it = self.table.item(row, _COL_LUBRICANT)
        if it is not None:
            txt = it.text().strip()
            if txt:
                return txt
        return self._lubricant_display_label_for_mu(self._pass_row_friction_mu(row))

    def _schedule_geometry_mm_from_passes(
        self, passes: list[PassInput]
    ) -> tuple[list[float], list[float], int, str | None]:
        """
        Full OD/ID chains in mm (index 0 = incoming), and pass count.
        If ``n_passes == 0``, chains have length 1 (incoming only). ``err`` set if geometry invalid.
        """
        err = self._geometry_error_message(require_target=False)
        if err:
            return [], [], 0, err
        try:
            g0 = tube_from_od_id_m(self.in_od_mm.value() / 1000.0, self.in_id_mm.value() / 1000.0)
        except ValueError as exc:
            return [], [], 0, str(exc)
        if not passes:
            return (
                [g0.outer_diameter_m * 1000.0],
                [g0.inner_diameter_m * 1000.0],
                0,
                None,
            )
        geoms, _, _, _ = simulate_schedule(
            g0,
            self._selected_material(),
            passes,
            grain_backend=self._grain_backend_for_simulation(),
        )
        ods_mm = [g.outer_diameter_m * 1000.0 for g in geoms]
        ids_mm = [g.inner_diameter_m * 1000.0 for g in geoms]
        return ods_mm, ids_mm, len(passes), None

    def _schedule_geometry_mm(self) -> tuple[list[float], list[float], int, str | None]:
        return self._schedule_geometry_mm_from_passes(self._read_passes())

    def _is_sink_drawing_method(self) -> bool:
        """Rodless / sink drawing — no internal mandrel or plug tooling."""
        t = (self.drawing_method_combo.currentText() or "").lower()
        return "sink" in t or "rodless" in t

    def _fit_area_reduction_targets_outer_diameter(self) -> bool:
        """
        True when Fit should match Target OD (sink / rodless without plug or mandrel tooling).

        Plug and mandrel processes use Target ID as the bore driver; sink-only labels use OD.
        """
        t = (self.drawing_method_combo.currentText() or "").lower()
        if "plug" in t or "mandrel" in t:
            return False
        return self._is_sink_drawing_method()

    def _reconcile_target_bore_for_id_driven_fit(self) -> None:
        """
        Plug/mandrel Fit needs target bore < incoming bore. After sink (OD-driven) work, Target ID
        is often left informational and can sit above incoming ID — Fit then cannot solve. Align Target
        ID with Target OD using the same fixed OD/ID ratio as the analytical chain when needed.
        """
        if self._fit_area_reduction_targets_outer_diameter():
            return
        err = self._geometry_error_message(require_target=True)
        if err:
            return
        od_i = float(self.in_od_mm.value())
        id_i = float(self.in_id_mm.value())
        od_o = float(self.out_od_mm.value())
        id_o = float(self.out_id_mm.value())
        if od_i <= id_i or od_o <= id_o:
            return
        if id_o < id_i - 1e-9:
            return
        id_pred = predict_id_for_scaled_od(od_i / 1000.0, id_i / 1000.0, od_o / 1000.0) * 1000.0
        eps = max(1e-4, id_i * 1e-6)
        new_id = min(id_pred, id_i - eps)
        lo, hi = float(self.out_id_mm.minimum()), float(self.out_id_mm.maximum())
        new_id = max(lo, min(hi, new_id))
        if new_id >= id_i - 1e-9:
            return
        self.out_id_mm.blockSignals(True)
        try:
            self.out_id_mm.setValue(new_id)
        finally:
            self.out_id_mm.blockSignals(False)

    def _fit_pass_schedule_to_target_output(self) -> None:
        """
        Set area reductions from Target output: inner diameter for normal draws; outer diameter for
        sink / rodless drawing. Semi-die angle and friction cells are unchanged on existing rows.
        """
        self._commit_tubing_project_diameter_edits()
        self._reconcile_target_bore_for_id_driven_fit()
        err = self._geometry_error_message(require_target=True)
        if err:
            QMessageBox.warning(self, "Fit schedule", err)
            return

        n_pref = self._target_draw_pass_count_for_fit_and_table()
        if n_pref is None:
            QMessageBox.warning(
                self,
                "Fit schedule",
                "Could not derive pass count: set valid incoming and target OD/ID on Tubing Project with "
                "positive annulus reduction (see hints). With Built-In Analytical, pass count comes from "
                "geometry and Min SF on the Optimization tab — not from Prefered Number Of Passes.",
            )
            return
        n_cur = self._draw_pass_row_count()
        need_total = _pass_table_total_rows_for_draw_count(n_pref)
        resized_for_fit = n_cur != n_pref or self.table.rowCount() != need_total
        if resized_for_fit:
            self._resize_pass_table_to_draw_count(n_pref)

        n_passes = self._draw_pass_row_count()
        if n_passes < 1:
            QMessageBox.warning(self, "Fit schedule", "The pass table has no rows.")
            return

        od_in_m = self.in_od_mm.value() / 1000.0
        id_in_m = self.in_id_mm.value() / 1000.0
        od_tgt_mm = self.out_od_mm.value()
        id_tgt_mm = self.out_id_mm.value()
        od_tgt_m = od_tgt_mm / 1000.0
        id_tgt_m = id_tgt_mm / 1000.0

        fit_od = self._fit_area_reduction_targets_outer_diameter()

        if fit_od:
            r_uniform = equal_r_per_pass_for_target_od(n_passes, od_in_m, od_tgt_m)
            if r_uniform is None:
                QMessageBox.warning(
                    self,
                    "Fit schedule",
                    "Cannot match target OD with uniform reductions per pass: "
                    "target outer diameter must be smaller than incoming OD.",
                )
                return
            r_last = final_pass_r_after_uniform_prefix_for_target_od(
                od_in_m, od_tgt_m, n_passes, r_uniform
            )
            id_pred_mm = predict_id_for_scaled_od(od_in_m, id_in_m, od_tgt_m) * 1000.0
            if n_passes <= 1:
                lines = [
                    f"Sink / rodless: single pass r = {r_last:.6f}. Semi-die angle and friction are unchanged.",
                    f"Simulated final OD will match target ({od_tgt_mm:.4f} mm) after Run Schedule.",
                ]
            else:
                lines = [
                    "Sink / rodless: passes 1…n−1 share nominal r; the last pass trims OD to target exactly. "
                    "Semi-die angle and friction are unchanged.",
                    f"Nominal r (passes 1…{n_passes - 1}): {r_uniform:.6f} · Last pass r: {r_last:.6f}",
                    f"Simulated final OD will match target ({od_tgt_mm:.4f} mm) after Run Schedule.",
                ]
            if resized_for_fit:
                if self._simulation_backend_kind() == "manual":
                    resize_note = (
                        f"The pass table was resized to Prefered Number Of Passes ({n_pref}; one row per pass)."
                    )
                else:
                    resize_note = (
                        f"The pass table was resized to the geometry-derived pass count ({n_pref}; one row per pass; "
                        "Min SF on Optimization tab)."
                    )
                lines.insert(1, resize_note)
            if abs(id_pred_mm - id_tgt_mm) > 0.05:
                lines.append(
                    "Fixed ID/OD ratio: implied ID at that OD is "
                    f"{id_pred_mm:.4f} mm (target ID {id_tgt_mm:.4f} mm is informational for sink / rodless)."
                )
        else:
            r_uniform = equal_r_per_pass_for_target_id(n_passes, od_in_m, id_in_m, id_tgt_m)
            if r_uniform is None:
                QMessageBox.warning(
                    self,
                    "Fit schedule",
                    "Cannot match target ID with uniform reductions per pass: "
                    "target inner diameter must be smaller than incoming ID.\n\n"
                    "If you switched from sink (OD-driven) drawing, set Target ID below incoming ID, "
                    "or rely on automatic adjustment when changing process to plug/mandrel.",
                )
                return

            r_last = final_pass_r_after_uniform_prefix_for_target_id(
                id_in_m, id_tgt_m, n_passes, r_uniform
            )

            od_pred_mm = predict_od_for_scaled_id(od_in_m, id_in_m, id_tgt_m) * 1000.0
            if n_passes <= 1:
                lines = [
                    f"Single pass: area reduction r = {r_last:.6f}. Semi-die angle and friction are unchanged.",
                    f"Simulated final ID will match target ({id_tgt_mm:.4f} mm) after Run Schedule.",
                ]
            else:
                lines = [
                    "Passes 1…n−1 share one nominal reduction; the last pass is adjusted so Run Schedule "
                    "hits target ID exactly (avoids rounding drift). Semi-die angle and friction are unchanged.",
                    f"Nominal r (passes 1…{n_passes - 1}): {r_uniform:.6f} · Last pass r: {r_last:.6f}",
                    f"Simulated final ID will match target ({id_tgt_mm:.4f} mm) after Run Schedule.",
                ]
            if resized_for_fit:
                if self._simulation_backend_kind() == "manual":
                    resize_note = (
                        f"The pass table was resized to Prefered Number Of Passes ({n_pref}; one row per pass)."
                    )
                else:
                    resize_note = (
                        f"The pass table was resized to the geometry-derived pass count ({n_pref}; one row per pass; "
                        "Min SF on Optimization tab)."
                    )
                lines.insert(1, resize_note)
            if abs(od_pred_mm - od_tgt_mm) > 0.05:
                lines.append(
                    "This model keeps ID/OD ratio fixed: predicted OD at that ID is "
                    f"{od_pred_mm:.4f} mm (target OD {od_tgt_mm:.4f} mm). "
                    "A different wall schedule would need an extended model."
                )

        draw_rows = self._draw_row_indices()
        if len(draw_rows) < n_passes:
            QMessageBox.warning(
                self,
                "Fit schedule",
                "Pass table layout does not match the expected number of drawing passes. "
                "Try Manual Reschedule — Master Mode or reload the project.",
            )
            return

        QMessageBox.information(self, "Fit schedule", "\n\n".join(lines))

        self._fit_undo_snapshot = self._snapshot_pass_table_inputs()

        self.table.blockSignals(True)
        try:
            for i in range(max(0, n_passes - 1)):
                self._set_pass_row_area_reduction_spin_programmatic(draw_rows[i], float(r_uniform))
            self._set_pass_row_area_reduction_spin_programmatic(draw_rows[n_passes - 1], float(r_last))
        finally:
            self.table.blockSignals(False)
        self._prioritize_inventory_dies_for_non_final_passes()
        self.undo_fit_btn.setEnabled(True)
        self._capture_tubing_project_baseline_from_table()
        self._refresh_schedule_visuals()

    def _snapshot_pass_table_inputs(self) -> list[tuple[str, str, str]]:
        """Area reduction, semi-die angle, friction μ per drawing pass row (for Undo Fit; excludes Incoming)."""
        out: list[tuple[str, str, str]] = []

        def _snap_cell(r: int, col: int) -> str:
            if col == _COL_AREA_REDUCTION:
                return f"{self._pass_row_area_reduction_fraction(r):.6f}"
            if col == _COL_SEMI_DIE_ANGLE:
                return f"{self._pass_row_semi_die_angle_deg(r):.6f}"
            if col == _COL_LUBRICANT:
                hh = self.table.verticalHeaderItem(r)
                ll = (hh.text() if hh else "").strip().lower()
                if ll.startswith("inter"):
                    return ""
                return f"{self._pass_row_friction_mu(r):.6f}"
            it = self.table.item(r, col)
            return it.text() if it else ""

        for row in self._draw_row_indices():
            out.append(
                (
                    _snap_cell(row, _COL_AREA_REDUCTION),
                    _snap_cell(row, _COL_SEMI_DIE_ANGLE),
                    _snap_cell(row, _COL_LUBRICANT),
                )
            )
        return out

    def _undo_fit_pass_schedule(self) -> None:
        if not self._fit_undo_snapshot:
            return
        draw_rows = self._draw_row_indices()
        self.table.blockSignals(True)
        try:
            for i, (t0, t1, t2) in enumerate(self._fit_undo_snapshot):
                if i >= len(draw_rows):
                    break
                row = draw_rows[i]
                try:
                    r_undo = float(t0) if (t0 or "").strip() else 0.12
                except ValueError:
                    r_undo = 0.12
                self._set_pass_row_area_reduction_spin_programmatic(row, r_undo)
                try:
                    a_undo = float(t1) if (t1 or "").strip() else 12.0
                except ValueError:
                    a_undo = 12.0
                self._set_pass_row_semi_die_angle_spin_programmatic(row, a_undo)
                try:
                    mu_undo = float(t2) if (t2 or "").strip() else 0.06
                except ValueError:
                    mu_undo = 0.06
                self._set_pass_row_lubricant_combo_programmatic(row, mu_undo)
        finally:
            self.table.blockSignals(False)
        self._fit_undo_snapshot = None
        self.undo_fit_btn.setEnabled(False)
        self._refresh_schedule_visuals()

    @staticmethod
    def _sync_spinbox_value_from_line_edit(sb: QDoubleSpinBox) -> None:
        """Force numeric model from the embedded line edit — QAbstractSpinBox often delays until blur; Run must see typed OD/ID."""
        sb.interpretText()
        le = sb.lineEdit()
        if le is None:
            return
        raw = le.text().strip().replace(",", ".")
        if not raw:
            return
        try:
            v = float(raw)
        except ValueError:
            return
        lo, hi = sb.minimum(), sb.maximum()
        v = max(lo, min(hi, v))
        sb.blockSignals(True)
        try:
            sb.setValue(v)
        finally:
            sb.blockSignals(False)

    def _commit_tubing_project_diameter_edits(self) -> None:
        """Ensure OD/ID spinbox .value() matches visible editor text before any geometry read."""
        app = QApplication.instance()
        if app is not None:
            fw = app.focusWidget()
            if fw is not None:
                fw.clearFocus()
            app.processEvents()
        for _sb in (self.in_od_mm, self.in_id_mm, self.out_od_mm, self.out_id_mm):
            self._sync_spinbox_value_from_line_edit(_sb)

    def _on_tubing_diameter_edit_finished(self) -> None:
        """When user finishes editing any Tubing Project OD/ID box, commit text and refresh schedule + strip + suggested row count."""
        self._commit_tubing_project_diameter_edits()
        self._update_geometry_hints()
        self._warn_od_must_exceed_id_if_needed()
        self._update_optuna_derived_label()
        self._update_project_history_panel()
        if self._simulation_backend_kind() == "manual":
            self._refresh_schedule_visuals()
            return
        b = self._pass_schedule_draw_pass_count()
        if b is None:
            self._refresh_schedule_visuals()
            return
        n_draw = max(1, int(b[2]))
        total_need = _pass_table_total_rows_for_draw_count(n_draw)
        if self.table.rowCount() != total_need:
            self._resize_pass_table_from_geometry_suggestion(silent=True)
        else:
            self._refresh_schedule_visuals()

    @staticmethod
    def _clone_pass_inputs(passes: list[PassInput]) -> list[PassInput]:
        return [
            PassInput(
                semi_die_angle_deg=p.semi_die_angle_deg,
                friction_mu=p.friction_mu,
                area_reduction_fraction=p.area_reduction_fraction,
            )
            for p in passes
        ]

    @staticmethod
    def _pass_inputs_close(a: PassInput, b: PassInput) -> bool:
        return (
            abs(a.area_reduction_fraction - b.area_reduction_fraction) <= 1e-4
            and abs(a.semi_die_angle_deg - b.semi_die_angle_deg) <= 0.05
            and abs(a.friction_mu - b.friction_mu) <= 1e-4
        )

    def _pass_modification_strength(self, cur: PassInput, base: PassInput) -> float:
        """0–1 score for how far ``cur`` diverged from ``base`` (for emphasis on the strip)."""
        br = max(abs(base.area_reduction_fraction), 0.04)
        ba = max(abs(base.semi_die_angle_deg), 1.0)
        bm = max(abs(base.friction_mu), 0.02)
        dr = abs(cur.area_reduction_fraction - base.area_reduction_fraction) / br
        da = abs(cur.semi_die_angle_deg - base.semi_die_angle_deg) / ba
        dm = abs(cur.friction_mu - base.friction_mu) / bm
        return float(np.clip(max(dr, da, dm) / 3.0, 0.0, 1.0))

    def _modified_pass_indices_vs_baseline(self) -> set[int]:
        """Pass row indices (0-based) where current table differs from saved baseline."""
        cur = self._read_passes()
        base = self._tubing_project_baseline_passes
        out: set[int] = set()
        if not cur:
            return out
        if not base:
            return set(range(len(cur)))
        for i in range(len(cur)):
            if i >= len(base):
                out.add(i)
                continue
            if not self._pass_inputs_close(cur[i], base[i]):
                out.add(i)
        return out

    def _capture_tubing_project_baseline_from_table(self) -> None:
        """Baseline strip on Tubing Project — updated after generated schedules and Sync."""
        self._tubing_project_baseline_passes = self._clone_pass_inputs(self._read_passes())

    def _sync_modifications_to_tubing_project_baseline(self) -> None:
        self._capture_tubing_project_baseline_from_table()
        self._refresh_schedule_visuals()
        QMessageBox.information(
            self,
            "Sync Modifications",
            "Tubing Project cross-section baseline now matches the current pass schedule table.",
        )

    def _refresh_schedule_visuals(self) -> None:
        """Pass table outputs + cross-section strips + die BOM table."""
        if self._in_refresh_schedule_visuals:
            self._pending_refresh_schedule_visuals = True
            return
        self._in_refresh_schedule_visuals = True
        try:
            self._commit_tubing_project_diameter_edits()
            self._commit_pass_schedule_area_reduction_edits()
            self._commit_pass_schedule_semi_die_angle_edits()
            self._commit_pass_schedule_mandrel_plug_edits()
            # Sync Output OD/ID columns first so the pass table updates even if later drawing fails.
            self._sync_pass_table_outputs()
            self._update_tubing_project_cross_section_strip()
            self._update_pass_schedule_cross_section_strip()
            self._refresh_die_bom(silent=True)
            self._refresh_die_availability_row_styles()
        finally:
            self._in_refresh_schedule_visuals = False
            if self._pending_refresh_schedule_visuals:
                self._pending_refresh_schedule_visuals = False
                QTimer.singleShot(0, self._refresh_schedule_visuals)

    def _pass_schedule_row_draw_indices(self) -> list[int]:
        """Schedule rows with positive area reduction (drawing passes), top to bottom."""
        rows: list[int] = []
        for row in range(self.table.rowCount()):
            if self._is_pass_schedule_incoming_row(row):
                continue
            if self._pass_row_area_reduction_fraction(row) > 0:
                rows.append(row)
        return rows

    def _clear_pass_schedule_die_alert_for_row(self, row: int) -> None:
        base = self.table.palette().color(QPalette.ColorRole.Base)
        brush = QBrush(base)
        col = _PASS_SCHEDULE_DIE_MATCH_COL
        it = self.table.item(row, col)
        if it is not None:
            it.setBackground(brush)
        w = self.table.cellWidget(row, col)
        if w is not None:
            w.setStyleSheet("")
            w.setToolTip("")
        vh = self.table.verticalHeaderItem(row)
        if vh is not None:
            vh.setToolTip("")

    def _apply_pass_schedule_die_alert_for_row(self, row: int, bg_hex: str, tooltip: str) -> None:
        color = QColor(bg_hex)
        brush = QBrush(color)
        col = _PASS_SCHEDULE_DIE_MATCH_COL
        it = self.table.item(row, col)
        if it is not None:
            it.setBackground(brush)
        w = self.table.cellWidget(row, col)
        if w is not None:
            w.setStyleSheet(f"background-color: {bg_hex};")
            w.setToolTip(tooltip)

    def _refresh_die_availability_row_styles(self) -> None:
        """Color only Semi-Die Angle by die inventory match (that column’s cell + tooltip)."""
        for row in range(self.table.rowCount()):
            self._clear_pass_schedule_die_alert_for_row(row)

        draw_rows = self._pass_schedule_row_draw_indices()
        if not draw_rows:
            self.table.viewport().update()
            return

        proj = self._project_dict()
        inv = self._die_inventory_as_dicts()
        tol = float(self.die_inv_angle_tol.value())
        details = draw_pass_match_details(
            proj,
            inv,
            n_draw_passes=len(draw_rows),
            angle_tolerance_deg=tol,
        )
        color_map = {
            "available": _DIE_ALERT_AVAILABLE_BG,
            "unavailable": _DIE_ALERT_UNAVAILABLE_BG,
            "none": _DIE_ALERT_NONE_BG,
            "empty_inventory": _DIE_ALERT_EMPTY_INV_BG,
            "no_geometry": _DIE_ALERT_NO_GEOM_BG,
        }
        for row, (st, tip) in zip(draw_rows, details):
            hx = color_map.get(st, _DIE_ALERT_NO_GEOM_BG)
            self._apply_pass_schedule_die_alert_for_row(row, hx, tip)
        self.table.viewport().update()

    def _style_die_inventory_spin(self, sb: QDoubleSpinBox | QSpinBox) -> None:
        sb.setMinimumHeight(22)
        le = sb.lineEdit()
        if le is not None:
            le.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _on_die_inventory_tolerance_changed(self, *_args: Any) -> None:
        self._refresh_die_availability_row_styles()

    def _on_die_inventory_table_item_changed(self, *_args: Any) -> None:
        self._refresh_die_availability_row_styles()

    def _on_die_inventory_field_changed(self, *_args: Any) -> None:
        self._refresh_die_availability_row_styles()
        self._refresh_die_inventory_schematic()

    def _die_inventory_as_dicts(self) -> list[Dict[str, Any]]:
        """Serialize die inventory table to JSON-friendly dicts."""
        t = self.die_inv_table
        out: list[Dict[str, Any]] = []
        for r in range(t.rowCount()):
            name_it = t.item(r, _DIE_INV_COL_NAME)
            name = (name_it.text() if name_it else "").strip() or "Die"
            did = ""
            if name_it is not None:
                raw_id = name_it.data(Qt.ItemDataRole.UserRole)
                if raw_id is not None:
                    did = str(raw_id)
            if not did:
                did = str(uuid.uuid4())
                if name_it is not None:
                    name_it.setData(Qt.ItemDataRole.UserRole, did)

            sb_a = t.cellWidget(r, _DIE_INV_COL_ALPHA)
            sb_lo = t.cellWidget(r, _DIE_INV_COL_OD_MIN)
            sb_hi = t.cellWidget(r, _DIE_INV_COL_OD_MAX)
            try:
                alpha_deg = float(sb_a.value()) if isinstance(sb_a, QDoubleSpinBox) else 0.0
            except Exception:
                alpha_deg = 0.0
            try:
                od_min_mm = float(sb_lo.value()) if isinstance(sb_lo, QDoubleSpinBox) else 0.0
            except Exception:
                od_min_mm = 0.0
            try:
                od_max_mm = float(sb_hi.value()) if isinstance(sb_hi, QDoubleSpinBox) else 0.0
            except Exception:
                od_max_mm = 0.0

            sb_bl = t.cellWidget(r, _DIE_INV_COL_BEAR)
            try:
                bearing_mm = float(sb_bl.value()) if isinstance(sb_bl, QDoubleSpinBox) else DEFAULT_BEARING_LENGTH_MM
            except Exception:
                bearing_mm = DEFAULT_BEARING_LENGTH_MM

            mat_it = t.item(r, _DIE_INV_COL_MATERIAL)
            sup_it = t.item(r, _DIE_INV_COL_SUPPLIER)
            material = (mat_it.text() if mat_it else "").strip()
            supplier = (sup_it.text() if sup_it else "").strip()

            stock_w = t.cellWidget(r, _DIE_INV_COL_STOCK)
            in_stock = True
            if isinstance(stock_w, QWidget):
                cb = stock_w.findChild(QCheckBox)
                if cb is not None:
                    in_stock = cb.isChecked()

            sb_qty = t.cellWidget(r, _DIE_INV_COL_QUANTITY)
            try:
                quantity = int(sb_qty.value()) if isinstance(sb_qty, QSpinBox) else 0
            except Exception:
                quantity = 0

            notes_it = t.item(r, _DIE_INV_COL_NOTES)
            notes = (notes_it.text() if notes_it else "").strip()

            out.append(
                {
                    "id": did,
                    "name": name,
                    "alpha_deg": alpha_deg,
                    "od_min_mm": od_min_mm,
                    "od_max_mm": od_max_mm,
                    "bearing_length_mm": bearing_mm,
                    "material": material,
                    "supplier": supplier,
                    "in_stock": in_stock,
                    "quantity": quantity,
                    "notes": notes,
                }
            )
        return normalize_die_records(out)

    def _append_die_inventory_row(
        self,
        *,
        die_id: str | None = None,
        name: str = "Die",
        alpha_deg: float = 12.0,
        od_min_mm: float = 0.0,
        od_max_mm: float = 50.0,
        bearing_length_mm: float = DEFAULT_BEARING_LENGTH_MM,
        material: str = "",
        supplier: str = "",
        in_stock: bool = True,
        quantity: int = 0,
        notes: str = "",
    ) -> None:
        t = self.die_inv_table
        r = t.rowCount()
        t.insertRow(r)
        did = die_id or str(uuid.uuid4())
        name_it = QTableWidgetItem(name)
        name_it.setData(Qt.ItemDataRole.UserRole, did)
        name_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setItem(r, _DIE_INV_COL_NAME, name_it)

        sb_a = QDoubleSpinBox()
        sb_a.setRange(0.01, 89.999)
        sb_a.setDecimals(3)
        sb_a.setValue(float(alpha_deg))
        self._style_die_inventory_spin(sb_a)
        sb_a.valueChanged.connect(self._on_die_inventory_field_changed)
        t.setCellWidget(r, _DIE_INV_COL_ALPHA, sb_a)

        sb_lo = QDoubleSpinBox()
        sb_lo.setRange(0.0, 999.999)
        sb_lo.setDecimals(4)
        sb_lo.setValue(float(od_min_mm))
        self._style_die_inventory_spin(sb_lo)
        sb_lo.valueChanged.connect(self._on_die_inventory_field_changed)
        t.setCellWidget(r, _DIE_INV_COL_OD_MIN, sb_lo)

        sb_hi = QDoubleSpinBox()
        sb_hi.setRange(0.0, 999.999)
        sb_hi.setDecimals(4)
        sb_hi.setValue(float(od_max_mm))
        self._style_die_inventory_spin(sb_hi)
        sb_hi.valueChanged.connect(self._on_die_inventory_field_changed)
        t.setCellWidget(r, _DIE_INV_COL_OD_MAX, sb_hi)

        sb_bl = QDoubleSpinBox()
        sb_bl.setRange(0.0, 500.0)
        sb_bl.setDecimals(4)
        sb_bl.setValue(float(bearing_length_mm))
        sb_bl.setSuffix(" mm")
        self._style_die_inventory_spin(sb_bl)
        sb_bl.valueChanged.connect(self._on_die_inventory_field_changed)
        t.setCellWidget(r, _DIE_INV_COL_BEAR, sb_bl)

        mat_it = QTableWidgetItem(material)
        mat_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setItem(r, _DIE_INV_COL_MATERIAL, mat_it)

        sup_it = QTableWidgetItem(supplier)
        sup_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setItem(r, _DIE_INV_COL_SUPPLIER, sup_it)

        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(2, 0, 2, 0)
        cb = QCheckBox()
        cb.setChecked(bool(in_stock))
        cb.stateChanged.connect(self._on_die_inventory_field_changed)
        wl.addStretch(1)
        wl.addWidget(cb, 0, Qt.AlignmentFlag.AlignCenter)
        wl.addStretch(1)
        t.setCellWidget(r, _DIE_INV_COL_STOCK, wrap)

        sb_qty = QSpinBox()
        sb_qty.setRange(0, 999999)
        sb_qty.setValue(max(0, int(quantity)))
        sb_qty.setToolTip("Number of dies in stock (when In stock is checked).")
        self._style_die_inventory_spin(sb_qty)
        sb_qty.valueChanged.connect(self._on_die_inventory_field_changed)
        t.setCellWidget(r, _DIE_INV_COL_QUANTITY, sb_qty)

        notes_it = QTableWidgetItem(notes)
        notes_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setItem(r, _DIE_INV_COL_NOTES, notes_it)

    def _load_die_inventory_from_project(self, proj: Dict[str, Any]) -> None:
        raw = proj.get("die_inventory")
        records = normalize_die_records(raw) if raw else []
        tol = proj.get("die_inventory_angle_tolerance_deg")
        self.die_inv_table.blockSignals(True)
        self.die_inv_angle_tol.blockSignals(True)
        try:
            self.die_inv_table.setRowCount(0)
            for d in records:
                try:
                    q_die = max(0, int(float(d.get("quantity", 0))))
                except (TypeError, ValueError):
                    q_die = 0
                self._append_die_inventory_row(
                    die_id=str(d.get("id") or "") or None,
                    name=str(d.get("name") or "Die"),
                    alpha_deg=float(d.get("alpha_deg", 12.0)),
                    od_min_mm=float(d.get("od_min_mm", 0.0)),
                    od_max_mm=float(d.get("od_max_mm", 0.0)),
                    bearing_length_mm=float(
                        d.get("bearing_length_mm", DEFAULT_BEARING_LENGTH_MM)
                    ),
                    material=str(d.get("material") or ""),
                    supplier=str(d.get("supplier") or ""),
                    in_stock=bool(d.get("in_stock", True)),
                    quantity=q_die,
                    notes=str(d.get("notes") or ""),
                )
            if tol is not None:
                try:
                    self.die_inv_angle_tol.setValue(float(tol))
                except (TypeError, ValueError):
                    self.die_inv_angle_tol.setValue(DEFAULT_ANGLE_TOLERANCE_DEG)
            else:
                self.die_inv_angle_tol.setValue(DEFAULT_ANGLE_TOLERANCE_DEG)
        finally:
            self.die_inv_angle_tol.blockSignals(False)
            self.die_inv_table.blockSignals(False)
        if self.die_inv_table.rowCount() > 0:
            self.die_inv_table.selectRow(0)
        self._refresh_die_inventory_schematic()

    def _die_inventory_selected_row_index(self) -> int:
        sel = self.die_inv_table.selectionModel().selectedRows()
        if sel:
            return int(sel[0].row())
        r = self.die_inv_table.currentRow()
        return int(r) if r >= 0 else -1

    def _on_die_inventory_selection_changed(self) -> None:
        self._refresh_die_inventory_schematic()

    def _update_die_inv_diagram_banner(self) -> None:
        """Hints above the schematic canvas (not inside the matplotlib drawing frame)."""
        lbl = self.die_inv_diagram_banner
        if self.die_inv_table.rowCount() == 0:
            lbl.setVisible(True)
            lbl.setText('No dies listed yet. Click “Add die…” to enter shop tooling.')
            return
        row = self._die_inventory_selected_row_index()
        if row < 0:
            lbl.setVisible(True)
            lbl.setText(
                "Select a row in the table — the diagram will match that die’s α, OD band, and bearing length."
            )
            return
        lbl.setVisible(False)
        lbl.setText("")

    def _update_die_inv_entry_od_label(self) -> None:
        """Entry OD band line above the schematic canvas (not inside the matplotlib frame)."""
        lbl = self.die_inv_entry_od
        if self.die_inv_table.rowCount() == 0:
            lbl.setText("")
            return
        row = self._die_inventory_selected_row_index()
        if row < 0:
            lbl.setText("")
            return
        sb_lo = self.die_inv_table.cellWidget(row, _DIE_INV_COL_OD_MIN)
        sb_hi = self.die_inv_table.cellWidget(row, _DIE_INV_COL_OD_MAX)
        try:
            od_min_mm = float(sb_lo.value()) if isinstance(sb_lo, QDoubleSpinBox) else 0.0
        except Exception:
            od_min_mm = 0.0
        try:
            od_max_mm = float(sb_hi.value()) if isinstance(sb_hi, QDoubleSpinBox) else 0.0
        except Exception:
            od_max_mm = 0.0
        lo = min(od_min_mm, od_max_mm)
        hi = max(od_min_mm, od_max_mm)
        lbl.setText(f"Entry OD band (match range): {lo:.4f} – {hi:.4f} mm")

    def _update_die_inv_stock_badge(self) -> None:
        """Stock status beside the diagram (not drawn inside matplotlib)."""
        badge = self.die_inv_stock_badge
        if self.die_inv_table.rowCount() == 0:
            badge.setText("")
            badge.setStyleSheet("")
            badge.setToolTip("")
            badge.setMinimumWidth(0)
            badge.setVisible(False)
            return
        badge.setVisible(True)
        badge.setMinimumWidth(72)
        row = self._die_inventory_selected_row_index()
        if row < 0:
            badge.setText("Select a row")
            badge.setStyleSheet("color: #868e96; font-size: 11px; padding: 4px;")
            badge.setToolTip("")
            return
        stock_w = self.die_inv_table.cellWidget(row, _DIE_INV_COL_STOCK)
        in_stock = True
        if isinstance(stock_w, QWidget):
            cb = stock_w.findChild(QCheckBox)
            if cb is not None:
                in_stock = cb.isChecked()
        if in_stock:
            badge.setText("In stock")
            badge.setStyleSheet(
                "color: #2e7d32; font-weight: bold; font-size: 13px; padding: 8px;"
            )
        else:
            badge.setText("Not in stock")
            badge.setStyleSheet(
                "color: #e65100; font-weight: bold; font-size: 13px; padding: 8px;"
            )
        badge.setToolTip("From the selected row’s “In stock” checkbox.")

    def _refresh_die_inventory_schematic(self) -> None:
        """Redraw left-panel die schematic from the selected inventory row."""
        if self.die_inv_table.rowCount() == 0:
            self.die_inv_schematic.set_spec(empty_die_schematic_spec(inventory_empty=True))
        else:
            row = self._die_inventory_selected_row_index()
            if row < 0:
                self.die_inv_schematic.set_spec(empty_die_schematic_spec(inventory_empty=False))
            else:
                name_it = self.die_inv_table.item(row, _DIE_INV_COL_NAME)
                name = (name_it.text() if name_it else "").strip() or "Die"
                sb_a = self.die_inv_table.cellWidget(row, _DIE_INV_COL_ALPHA)
                sb_lo = self.die_inv_table.cellWidget(row, _DIE_INV_COL_OD_MIN)
                sb_hi = self.die_inv_table.cellWidget(row, _DIE_INV_COL_OD_MAX)
                sb_bl = self.die_inv_table.cellWidget(row, _DIE_INV_COL_BEAR)
                try:
                    alpha_deg = float(sb_a.value()) if isinstance(sb_a, QDoubleSpinBox) else 12.0
                except Exception:
                    alpha_deg = 12.0
                try:
                    od_min_mm = float(sb_lo.value()) if isinstance(sb_lo, QDoubleSpinBox) else 0.0
                except Exception:
                    od_min_mm = 0.0
                try:
                    od_max_mm = float(sb_hi.value()) if isinstance(sb_hi, QDoubleSpinBox) else 0.0
                except Exception:
                    od_max_mm = 0.0
                try:
                    bl = float(sb_bl.value()) if isinstance(sb_bl, QDoubleSpinBox) else DEFAULT_BEARING_LENGTH_MM
                except Exception:
                    bl = DEFAULT_BEARING_LENGTH_MM
                self.die_inv_schematic.set_spec(
                    DieSchematicSpec(
                        alpha_deg=alpha_deg,
                        bearing_length_mm=bl,
                        od_min_mm=od_min_mm,
                        od_max_mm=od_max_mm,
                        name=name,
                    )
                )
        self._update_die_inv_diagram_banner()
        self._update_die_inv_entry_od_label()
        self._update_die_inv_stock_badge()

    def _die_inventory_add_row(self) -> None:
        self._append_die_inventory_row()
        r = self.die_inv_table.rowCount() - 1
        self.die_inv_table.selectRow(r)
        self._refresh_die_availability_row_styles()
        self._refresh_die_inventory_schematic()

    def _die_inventory_remove_selected_row(self) -> None:
        t = self.die_inv_table
        sel = sorted({idx.row() for idx in t.selectedIndexes()}, reverse=True)
        if not sel:
            QMessageBox.information(self, "Die inventory", "Select one or more rows to remove.")
            return
        for r in sel:
            t.removeRow(r)
        self._refresh_die_availability_row_styles()
        self._refresh_die_inventory_schematic()

    def _prioritize_inventory_dies_for_non_final_passes(self) -> None:
        """
        When die inventory is populated, set semi-die α on passes 1…n−1 from matching shop dies (OD band).

        The last drawing pass is left unchanged so Fit / final trim can use a different tooling angle.
        """
        if getattr(self, "die_inv_table", None) is None:
            return
        inv = self._die_inventory_as_dicts()
        if not inv:
            return
        n_draw = self._draw_pass_row_count()
        if n_draw <= 1:
            return
        proj = self._project_dict()
        updates = inventory_alpha_updates_before_last_pass(
            proj, inv, n_draw_passes=n_draw, prefer_in_stock=True
        )
        if not updates:
            return
        draw_rows = self._draw_row_indices()
        if len(draw_rows) < n_draw:
            return
        self.table.blockSignals(True)
        try:
            for pass_no, alpha in updates:
                if pass_no < 1 or pass_no >= n_draw:
                    continue
                sch_row = draw_rows[pass_no - 1]
                self._set_pass_row_semi_die_angle_spin_programmatic(sch_row, alpha)
        finally:
            self.table.blockSignals(False)

    def _die_inventory_snap_semi_die_angles(self) -> None:
        proj = self._project_dict()
        inv = self._die_inventory_as_dicts()
        if not inv:
            QMessageBox.information(
                self,
                "Die inventory",
                "Add at least one die with OD min/max covering the pass entry annulus OD.",
            )
            return
        die_by_pass: Dict[int, Dict[str, Any]] = {}
        try:
            for row in compute_pass_die_rows(proj):
                if str(row.get("row_kind") or "") != "die":
                    continue
                try:
                    p = int(row.get("pass", 0))
                except (TypeError, ValueError):
                    continue
                if p > 0:
                    die_by_pass[p] = row
        except Exception as exc:
            QMessageBox.warning(self, "Die inventory", f"Could not read schedule dies:\n{exc}")
            return

        draw_rows = self._pass_schedule_row_draw_indices()
        changed = 0
        for i, sch_row in enumerate(draw_rows):
            pass_no = i + 1
            dr = die_by_pass.get(pass_no)
            if dr is None:
                continue
            a = float(dr.get("alpha_deg", 0.0))
            od = float(dr.get("od_before", 0.0))
            na = snap_alpha_deg_from_inventory(a, od, inv)
            if na is None:
                continue
            if abs(na - a) < 1e-7:
                continue
            self._set_pass_row_semi_die_angle_spin_programmatic(sch_row, na)
            changed += 1
        self._refresh_schedule_visuals()
        QMessageBox.information(
            self,
            "Die inventory",
            f"Updated semi-die angle on {changed} pass row(s). "
            "Verify against engineering before saving.",
        )

    def _sync_pass_table_outputs(self) -> None:
        """Fill read-only Output OD/ID columns and Mandrel/Plug spinboxes from simulated geometry after each pass (by row)."""

        def _set_out(row: int, od_txt: str, id_txt: str) -> None:
            self.table.setItem(row, _COL_OUTPUT_OD, self._centered_table_item(od_txt, read_only=True))
            self.table.setItem(row, _COL_OUTPUT_ID, self._centered_table_item(id_txt, read_only=True))
            ot = od_txt.strip()
            it = id_txt.strip()
            if ot == "—" or it == "—":
                self._set_mandrel_plug_placeholder(row)
                return
            if self._is_sink_drawing_method():
                self._set_mandrel_plug_placeholder(row)
                return
            try:
                id_mm = float(it)
            except ValueError:
                self._set_mandrel_plug_placeholder(row)
                return
            self._sync_mandrel_plug_cell_from_output_id_mm(row, id_mm)

        self.table.blockSignals(True)
        try:
            self._sync_pass_schedule_incoming_row_outputs()

            valid_rows: list[int] = []
            for row in range(self.table.rowCount()):
                if self._is_pass_schedule_incoming_row(row):
                    continue
                r = self._pass_row_area_reduction_fraction(row)
                if r > 0:
                    valid_rows.append(row)

            ods_mm, ids_mm, _n_passes, err = self._schedule_geometry_mm()

            for row in range(self.table.rowCount()):
                if self._is_pass_schedule_incoming_row(row):
                    continue
                if err:
                    _set_out(row, "—", "—")
                    continue
                if row not in valid_rows:
                    _set_out(row, "—", "—")
                    continue
                idx = valid_rows.index(row)
                if idx + 1 >= len(ods_mm):
                    _set_out(row, "—", "—")
                    continue
                od_a = ods_mm[idx + 1]
                id_a = ids_mm[idx + 1]
                _set_out(row, f"{od_a:.4f}", f"{id_a:.4f}")
        finally:
            self.table.blockSignals(False)
        self.table.viewport().update()

    def _refresh_die_bom_manual(self) -> None:
        self._refresh_die_bom(silent=False)

    def _refresh_die_bom(self, silent: bool = True) -> None:
        """Fill Pass BOM table: one row per pass, die parameters and tube sizes in/out of each die."""
        self._commit_pass_schedule_mandrel_plug_edits()
        self.bom_table.clearContents()
        self.bom_table.setRowCount(0)
        ods_mm, ids_mm, n_passes, err = self._schedule_geometry_mm()
        pass_rows_and_inputs = list(self._iter_schedule_pass_rows_and_inputs())
        passes = [p for _, p in pass_rows_and_inputs]

        if err:
            self.bom_hint.setText(f"Cannot build BOM: {err}")
            if not silent:
                QMessageBox.warning(self, "Pass BOM", err)
            return

        if n_passes == 0 or len(passes) == 0:
            self.bom_hint.setText("No passes with positive area reduction — add rows on the Pass schedule tab.")
            if not silent:
                QMessageBox.information(
                    self,
                    "Pass BOM",
                    "Add at least one pass (area reduction > 0) on the Pass schedule tab.",
                )
            return

        self.bom_table.setRowCount(n_passes)
        for i, (sch_row, p) in enumerate(pass_rows_and_inputs):
            od_b, id_b = ods_mm[i], ids_mm[i]
            od_a, id_a = ods_mm[i + 1], ids_mm[i + 1]
            pass_no = self._pass_display_number_for_schedule_row(sch_row, i + 1)
            lub = self._lubricant_display_for_schedule_row(sch_row)
            self.bom_table.setItem(i, 0, self._centered_table_item(pass_no))
            self.bom_table.setItem(i, 1, self._centered_table_item(f"{od_b:.4f}"))
            self.bom_table.setItem(i, 2, self._centered_table_item(f"{id_b:.4f}"))
            self.bom_table.setItem(i, 3, self._centered_table_item(f"{od_a:.4f}"))
            self.bom_table.setItem(i, 4, self._centered_table_item(f"{id_a:.4f}"))
            self.bom_table.setItem(i, 5, self._centered_table_item(f"{od_a:.4f}"))
            self.bom_table.setItem(i, 6, self._centered_table_item(f"{p.semi_die_angle_deg:.3f}"))
            self.bom_table.setItem(i, _BOM_COL_LUBRICANT, self._centered_table_item(lub))
            self.bom_table.setItem(i, 8, self._centered_table_item(self._pass_row_mandrel_plug_mm_display(sch_row)))
            self.bom_table.setItem(i, 9, self._centered_table_item(""))

        self.bom_table.resizeColumnToContents(_BOM_COL_LUBRICANT)

        self.bom_hint.setText(
            f"{n_passes} die line(s). OD/ID before and after each pass follow the analytical mild-steel schedule "
            f"(confirm alloy and die steel with engineering)."
        )

    def _export_bom_excel(self) -> None:
        if self.bom_table.rowCount() == 0:
            QMessageBox.information(self, "Pass BOM", "Nothing to export — refresh the BOM when passes exist.")
            return
        suggested = self._export_dialog_initial_path("xlsx")
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export pass BOM", suggested, "Excel workbook (*.xlsx);;All files (*)"
        )
        if not path_str:
            return
        headers = [
            self.bom_table.horizontalHeaderItem(c).text() if self.bom_table.horizontalHeaderItem(c) else ""
            for c in range(self.bom_table.columnCount())
        ]
        rows: list[list[str]] = []
        for r in range(self.bom_table.rowCount()):
            row = []
            for c in range(self.bom_table.columnCount()):
                it = self.bom_table.item(r, c)
                row.append(it.text() if it else "")
            rows.append(row)
        try:
            write_pass_bom_xlsx(Path(path_str), headers, rows)
        except ImportError:
            QMessageBox.warning(
                self,
                "Pass BOM",
                "openpyxl is required for Excel export. Install with: pip install openpyxl",
            )
            return
        except OSError as e:
            QMessageBox.warning(self, "Pass BOM", f"Could not write file: {e}")
            return
        QMessageBox.information(self, "Pass BOM", f"BOM saved to:\n{path_str}")

    def _project_dict(self) -> Dict[str, Any]:
        """Shape expected by :mod:`tubing_master.quotation` and grain/BOM helpers."""
        pass_rows = self._pass_schedule_table_as_dicts()
        out: Dict[str, Any] = {
            "in_od_mm": float(self.in_od_mm.value()),
            "in_id_mm": float(self.in_id_mm.value()),
            "out_od_mm": float(self.out_od_mm.value()),
            "out_id_mm": float(self.out_id_mm.value()),
            "material": self.material_combo.currentText(),
            "drawing_method": self.drawing_method_combo.currentText(),
            "pass_schedule": {
                "simulation_backend": self._simulation_backend_kind(),
                "passes": pass_rows,
            },
            "die_inventory": self._die_inventory_as_dicts(),
            "die_inventory_angle_tolerance_deg": float(self.die_inv_angle_tol.value()),
        }
        if self._material_property_overrides:
            out["material_property_overrides"] = dict(self._material_property_overrides)
        if getattr(self, "_embed_quotation_in_project", False):
            q = getattr(self, "_quotation_snapshot", None)
            if isinstance(q, dict) and q:
                out["quotation"] = dict(q)
        return out

    def _save_quotation_to_project(self) -> None:
        """Embed the live quotation snapshot under the Tubing Project dict for bundle exports."""
        if not self._quotation_snapshot.get("lines"):
            QMessageBox.information(
                self,
                "Quotation",
                "Nothing to save yet — open this tab to populate lines, or click “Recalculate from schedule”.",
            )
            return
        self._recalculate_quotation_totals_from_lines()
        self._embed_quotation_in_project = True
        QMessageBox.information(
            self,
            "Quotation",
            "The current quotation is stored on the Tubing Project. "
            "It will be included in Save To History and Save As New… exports.",
        )

    def _on_main_tab_changed(self, index: int) -> None:
        if index == _TAB_INDEX_OPTIMIZATION:
            self._scale_pass_schedule_strip_with_tabs_height()
            if self.opt_apply_schedule_btn.isEnabled():
                self._refresh_optimization_preview_visuals()
        if index == _TAB_INDEX_FEA:
            self._scale_pass_schedule_strip_with_tabs_height()
            self._fea_sync_pass_select_from_preview()
            self._refresh_fea_cross_section_strip()
            self._refresh_fea_pass_schematic()
        if index == _TAB_INDEX_QUOTATION:
            self._refresh_quotation_from_pass_schedule(interactive=False)

    def _refresh_quotation_from_pass_schedule(self, *, interactive: bool) -> bool:
        """Rebuild quotation lines from Tubing Project + Pass schedule (``rebuild_schedule_rows=True``).

        When ``interactive`` is False (switching to Quotation tab), geometry/finalize failures update the totals label
        only — no modal dialogs.
        """
        err = self._geometry_error_message(require_target=False)
        if err:
            if interactive:
                QMessageBox.warning(self, "Quotation", err)
            else:
                self.quote_totals_label.setText(
                    "Set valid incoming/target OD/ID on Tubing Project to compute quotation. "
                    f"({err})"
                )
            return False
        snap: Dict[str, Any] = dict(self._quotation_snapshot) if self._quotation_snapshot else {}
        snap["version"] = 2
        snap["currency"] = self.quote_currency.text().strip() or "USD"
        snap["stock_length_m"] = float(self.quote_stock_len.value())
        snap["price_per_kg"] = float(self.quote_price_kg.value())
        snap["density_kg_m3"] = float(self.quote_density.value())
        snap["additional_cost"] = float(self.quote_additional_cost.value())
        snap.setdefault("lines", [])
        self._quotation_snapshot = snap
        proj = self._project_dict()
        try:
            result = finalize_quotation_v2(proj, snap, rebuild_schedule_rows=True)
        except Exception as exc:
            if interactive:
                QMessageBox.warning(self, "Quotation", str(exc))
            else:
                self.quote_totals_label.setText(f"Quotation could not be computed: {exc}")
            return False
        self._quotation_snapshot = result
        self._update_quote_stock_price_display()
        self._fill_quotation_table(result)
        self._update_quotation_totals_label()
        return True

    def _recalculate_quotation_from_schedule(self) -> None:
        self._refresh_quotation_from_pass_schedule(interactive=True)

    def _fill_quotation_stock_material_row(self, q: Dict[str, Any], *, row: int = 0) -> None:
        """Calculated incoming stock mass and materials cost (read-only top summary row)."""
        proj = self._project_dict()
        stock_len = float(q.get("stock_length_m", 1.0) or 1.0)
        rho = float(q.get("density_kg_m3", 0.0) or 0.0)
        ppk = float(q.get("price_per_kg", 0.0) or 0.0)
        mass_kg = stock_mass_kg(proj, stock_len, rho)
        stock_mat = float(q.get("stock_material_cost", mass_kg * ppk) or 0.0)
        addl = float(q.get("additional_cost", 0.0) or 0.0)
        mat_cost = float(q.get("materials_cost", stock_mat + addl) or 0.0)
        eff_uc = (mat_cost / mass_kg) if mass_kg > 1e-18 else 0.0
        cur = str(q.get("currency") or "USD")
        self.quote_table.setItem(row, 0, self._centered_table_item("Stock", read_only=True))
        d1 = self._centered_table_item("Incoming Stock Materials", read_only=True)
        d1.setToolTip(
            f"Qty is mass (kg). Unit cost is effective $/kg so Qty × Unit cost = materials line "
            f"({mat_cost:.2f} {cur}: stock {stock_mat:.2f}"
            + (f" + additional {addl:.2f}" if addl > 0 else "")
            + ")."
        )
        self.quote_table.setItem(row, 1, d1)
        self.quote_table.setItem(row, 2, self._centered_table_item("—", read_only=True))
        self.quote_table.setItem(row, 3, self._centered_table_item(f"{mass_kg:.4f}", read_only=True))
        uc_note = self._centered_table_item(f"{eff_uc:.4f}", read_only=True)
        uc_note.setToolTip(d1.toolTip())
        self.quote_table.setItem(row, 4, uc_note)
        self.quote_table.setItem(row, 5, self._centered_table_item("", read_only=True))

    def _make_quotation_unit_cost_spinbox(self, line_ix: int) -> QDoubleSpinBox:
        """Up/down steppers for line-item unit cost on the quotation table."""
        sb = QDoubleSpinBox()
        sb.setRange(0.0, 1e12)
        sb.setDecimals(4)
        sb.setSingleStep(0.0001)
        sb.setKeyboardTracking(False)
        sb.setMinimumHeight(22)
        sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        le = sb.lineEdit()
        if le is not None:
            le.setAlignment(_CENTER_TABLE_ALIGN)
        sb.valueChanged.connect(lambda v, ix=line_ix: self._on_quotation_unit_cost_spin_changed(ix, float(v)))
        return sb

    def _on_quotation_unit_cost_spin_changed(self, line_ix: int, value: float) -> None:
        lines = self._quotation_snapshot.get("lines") or []
        if line_ix < 0 or line_ix >= len(lines):
            return
        lines[line_ix]["unit_cost"] = float(value)
        self._recalculate_quotation_totals_from_lines()

    def _fill_quotation_total_row(self, q: Dict[str, Any], *, row: int) -> None:
        """Read-only footer: sum in the Unit cost column; currency remains in the headline totals label."""
        tot = float(q.get("total", 0.0) or 0.0)
        self.quote_table.setItem(row, 0, self._centered_table_item("Total", read_only=True))
        self.quote_table.setItem(row, 1, self._centered_table_item("", read_only=True))
        self.quote_table.setItem(row, 2, self._centered_table_item("—", read_only=True))
        self.quote_table.setItem(row, 3, self._centered_table_item("—", read_only=True))
        self.quote_table.setItem(row, 4, self._centered_table_item(f"{tot:.2f}", read_only=True))
        self.quote_table.setItem(row, 5, self._centered_table_item("", read_only=True))

    def _fill_quotation_table(self, q: Dict[str, Any]) -> None:
        lines = list(q.get("lines") or [])
        self.quote_table.blockSignals(True)
        try:
            n = len(lines)
            self.quote_table.setRowCount(n + 2)
            self._fill_quotation_stock_material_row(q, row=0)
            for i, ln in enumerate(lines):
                tr = i + 1
                lk = str(ln.get("line_kind") or "pass")
                if lk == "pass" and ln.get("pass") is not None:
                    pass_txt = str(int(ln["pass"]))
                elif lk == "surcharge":
                    pass_txt = "—"
                elif lk == "process_charge":
                    slot = str(ln.get("slot") or "")
                    pass_txt = PROCESS_CHARGE_LABELS.get(slot, slot or "—")
                else:
                    p = ln.get("pass")
                    pass_txt = str(int(p)) if p is not None else ""
                desc = str(ln.get("description", ln.get("item", "")))
                tooling = str(ln.get("dies", ""))
                qty = float(ln.get("qty", 1.0) or 1.0)
                uc = float(ln.get("unit_cost", 0.0) or 0.0)
                comm = str(ln.get("comments", ""))
                self.quote_table.setItem(tr, 0, self._centered_table_item(pass_txt, read_only=True))
                di = self._centered_table_item(desc)
                if lk == "process_charge":
                    di.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsEditable
                    )
                else:
                    di.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self.quote_table.setItem(tr, 1, di)
                if lk == "process_charge":
                    self._apply_process_charge_description_placeholder_style(
                        di, str(ln.get("slot") or ""), desc
                    )
                ti = self._centered_table_item(tooling, read_only=True)
                if lk == "process_charge":
                    ti.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsEditable
                    )
                self.quote_table.setItem(tr, 2, ti)
                qty_ro = lk != "process_charge"
                self.quote_table.setItem(tr, 3, self._centered_table_item(f"{qty:g}", read_only=qty_ro))
                uc_sb = self._make_quotation_unit_cost_spinbox(i)
                uc_sb.blockSignals(True)
                try:
                    uc_sb.setValue(float(uc))
                finally:
                    uc_sb.blockSignals(False)
                self.quote_table.setCellWidget(tr, 4, self._centered_table_cell_widget(uc_sb))
                self.quote_table.setItem(tr, 5, self._centered_table_item(comm))
            self._fill_quotation_total_row(q, row=n + 1)
            self.quote_table.resizeRowsToContents()
        finally:
            self.quote_table.blockSignals(False)

    def _update_quotation_totals_label(self) -> None:
        q = self._quotation_snapshot
        cur = str(q.get("currency") or "USD")
        m = float(q.get("materials_cost", 0.0) or 0.0)
        sm = float(q.get("stock_material_cost", 0.0) or 0.0)
        ad = float(q.get("additional_cost", 0.0) or 0.0)
        dch = float(q.get("drawing_charges", 0.0) or 0.0)
        tot = float(q.get("total", 0.0) or 0.0)
        self.quote_totals_label.setText(
            f"Currency {cur} · Materials: {m:.2f} (stock {sm:.2f} + additional {ad:.2f}) · "
            f"Drawing charges: {dch:.2f} · Total: {tot:.2f}"
        )

    def _recalculate_quotation_totals_from_lines(self) -> None:
        """After editing unit cost or comments: refresh snapshot totals, stock row, and footer row."""
        snap = self._quotation_snapshot
        lines = list(snap.get("lines") or [])
        apply_line_extended(lines)
        snap["lines"] = lines
        snap["currency"] = self.quote_currency.text().strip() or "USD"
        snap["stock_length_m"] = float(self.quote_stock_len.value())
        snap["price_per_kg"] = float(self.quote_price_kg.value())
        snap["density_kg_m3"] = float(self.quote_density.value())
        snap["additional_cost"] = float(self.quote_additional_cost.value())
        proj = self._project_dict()
        mass = stock_mass_kg(proj, snap["stock_length_m"], snap["density_kg_m3"])
        stock_mat = mass * snap["price_per_kg"]
        materials = stock_mat + snap["additional_cost"]
        drawing = sum(float(x.get("extended", 0.0) or 0.0) for x in lines)
        snap["stock_material_cost"] = stock_mat
        snap["materials_cost"] = materials
        snap["drawing_charges"] = drawing
        snap["total"] = materials + drawing
        self._quotation_snapshot = snap
        self.quote_table.blockSignals(True)
        try:
            n = len(lines)
            self.quote_table.setRowCount(n + 2)
            self._fill_quotation_stock_material_row(snap, row=0)
            self._fill_quotation_total_row(snap, row=n + 1)
            self.quote_table.resizeRowsToContents()
        finally:
            self.quote_table.blockSignals(False)
        self._update_quotation_totals_label()

    def _on_quote_table_item_changed(self, item: QTableWidgetItem) -> None:
        col = item.column()
        row = item.row()
        lines = self._quotation_snapshot.get("lines") or []
        n = len(lines)
        if row == 0 or row == n + 1:
            return
        line_ix = row - 1
        if line_ix < 0 or line_ix >= n:
            return
        ln = lines[line_ix]
        lk = str(ln.get("line_kind") or "pass")
        is_proc = lk == "process_charge"

        item.setTextAlignment(_CENTER_TABLE_ALIGN)
        if col == 1:
            if not is_proc:
                return
            ln["description"] = item.text()
            self._apply_process_charge_description_placeholder_style(
                item, str(ln.get("slot") or ""), item.text()
            )
            return
        if col == 2:
            if not is_proc:
                return
            ln["dies"] = item.text()
            return
        if col == 3:
            if not is_proc:
                return
            try:
                ln["qty"] = float(item.text().replace(",", ""))
            except ValueError:
                ln["qty"] = 1.0
            self._recalculate_quotation_totals_from_lines()
            return
        if col == 5:
            ln["comments"] = item.text()
            self._recalculate_quotation_totals_from_lines()
            return

    def _on_quote_economic_inputs_changed(self) -> None:
        self._update_quote_stock_price_display()
        if not self._quotation_snapshot.get("lines"):
            return
        self._quotation_snapshot["stock_length_m"] = float(self.quote_stock_len.value())
        self._quotation_snapshot["price_per_kg"] = float(self.quote_price_kg.value())
        self._quotation_snapshot["density_kg_m3"] = float(self.quote_density.value())
        self._quotation_snapshot["additional_cost"] = float(self.quote_additional_cost.value())
        self._recalculate_quotation_totals_from_lines()

    def _update_quote_stock_price_display(self) -> None:
        """Show mass × price/kg in the Stock & pricing panel (before additional cost)."""
        proj = self._project_dict()
        stock_len = float(self.quote_stock_len.value())
        rho = float(self.quote_density.value())
        ppk = float(self.quote_price_kg.value())
        mass_kg = stock_mass_kg(proj, stock_len, rho)
        stock_price = mass_kg * ppk
        cur = self.quote_currency.text().strip() or "USD"
        self.quote_stock_price_display.setText(f"{stock_price:.2f} {cur}")

    def _sync_quote_density_from_material(self) -> None:
        label = self.material_combo.currentText()
        rho = density_kg_m3_from_properties(label, self._current_material_override())
        self.quote_density.blockSignals(True)
        try:
            self.quote_density.setValue(rho)
        finally:
            self.quote_density.blockSignals(False)

    def _export_quotation_excel(self) -> None:
        q = self._quotation_snapshot
        if not q or not q.get("lines"):
            QMessageBox.information(
                self,
                "Quotation",
                "Nothing to export yet — switch to this tab or click “Recalculate from schedule” after defining passes.",
            )
            return
        suggested = self._export_dialog_initial_path("xlsx")
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export quotation", suggested, "Excel workbook (*.xlsx);;All files (*)"
        )
        if not path_str:
            return
        try:
            write_quotation_xlsx(Path(path_str), q)
        except ImportError:
            QMessageBox.warning(
                self,
                "Quotation",
                "openpyxl is required for Excel export. Install with: pip install openpyxl",
            )
            return
        except OSError as exc:
            QMessageBox.warning(self, "Quotation", f"Could not write file:\n{exc}")
            return
        QMessageBox.information(self, "Quotation", f"Saved:\n{path_str}")

    def _build_cross_section_strip_model(
        self,
        passes: list[PassInput],
        *,
        highlight_modified_pass_indices: Optional[set[int]] = None,
    ) -> CrossSectionStripModel:
        """Assemble render data for :class:`CrossSectionStripWidget` (native Qt, not matplotlib)."""
        ods_mm, ids_mm, n_passes, err = self._schedule_geometry_mm_from_passes(passes)
        if err:
            return CrossSectionStripModel(error_message=err)

        if n_passes == 0:
            return CrossSectionStripModel(
                n_passes=0,
                incoming_od_mm=float(self.in_od_mm.value()),
                incoming_id_mm=float(self.in_id_mm.value()),
                target_od_mm=float(self.out_od_mm.value()),
                target_id_mm=float(self.out_id_mm.value()),
                show_target_hint=self._geometry_error_message(require_target=True) is None,
            )

        baseline = self._tubing_project_baseline_passes
        grain_um: list[float] = []
        grain_src: list[str] = []
        if passes:
            try:
                g0 = tube_from_od_id_m(self.in_od_mm.value() / 1000.0, self.in_id_mm.value() / 1000.0)
                _g, results, _c, _gn = simulate_schedule(
                    g0,
                    self._selected_material(),
                    passes,
                    grain_backend=self._grain_backend_for_simulation(),
                )
                grain_src: list[str] = []
                if len(results) == n_passes:
                    grain_um = [float(r.grain_size_um) for r in results]
                    grain_src = [str(r.grain_source) for r in results]
            except Exception:
                grain_um = []

        segments: list[CrossSectionPassSegment] = []
        for i in range(n_passes):
            is_mod = bool(highlight_modified_pass_indices and i in highlight_modified_pass_indices)
            strength = 0.0
            if is_mod:
                if i < len(baseline):
                    strength = self._pass_modification_strength(passes[i], baseline[i])
                else:
                    strength = 1.0
            segments.append(
                CrossSectionPassSegment(
                    od_mm=float(ods_mm[i + 1]),
                    id_mm=float(ids_mm[i + 1]),
                    is_modified=is_mod,
                    modification_strength=strength,
                    grain_um=grain_um[i] if i < len(grain_um) else None,
                    grain_source=grain_src[i] if i < len(grain_src) else "",
                    show_od_label=(i % 2 == 0),
                )
            )
        return CrossSectionStripModel(n_passes=n_passes, segments=segments)

    def _update_tubing_project_cross_section_strip(self) -> None:
        cur = self._read_passes()
        modified = self._modified_pass_indices_vs_baseline()
        model = self._build_cross_section_strip_model(
            cur, highlight_modified_pass_indices=modified
        )
        self.tubing_cross_section_strip.set_model(model)

    def _update_pass_schedule_cross_section_strip(self) -> None:
        model = self._build_cross_section_strip_model(self._read_passes())
        self.pass_schedule_cross_section_strip.set_model(model)

    def _run_analytical(self) -> None:
        self._commit_tubing_project_diameter_edits()
        self._commit_pass_schedule_area_reduction_edits()
        self._commit_pass_schedule_semi_die_angle_edits()
        self._commit_pass_schedule_mandrel_plug_edits()
        backend = self._simulation_backend_kind()
        require_target = backend != "manual"
        if require_target:
            self._reconcile_target_bore_for_id_driven_fit()
        err = self._geometry_error_message(require_target=require_target)
        if err:
            QMessageBox.warning(self, "Geometry", err)
            return
        try:
            g0 = self._geom()
            g_target = self._target_geometry() if require_target else None
        except ValueError as e:
            QMessageBox.warning(self, "Geometry", str(e))
            return
        mat = self._selected_material()
        passes = self._read_passes()
        if not passes:
            QMessageBox.warning(self, "Passes", "Add at least one pass with positive area reduction.")
            return
        geoms, results, cum, grain_note = simulate_schedule(
            g0, mat, passes, grain_backend=self._grain_backend_for_simulation()
        )
        sim = geoms[-1]
        grain_hdr = grain_note

        if backend == "manual":
            lines = [
                "Pass table is authoritative. Analytical stress, safety factor, and grain estimates still apply.",
                grain_hdr,
                "",
                f"Incoming (start of chain): OD={self.in_od_mm.value():.4f} mm, ID={self.in_id_mm.value():.4f} mm "
                f"(wall={(self.in_od_mm.value() - self.in_id_mm.value()) * 0.5:.4f} mm)",
                "",
                f"Passes: {len(results)}, cumulative equiv. plastic strain ≈ {cum:.3f}",
                "",
            ]
        else:
            assert g_target is not None
            r_goal = implied_area_reduction_fraction(
                g0.outer_diameter_m,
                g0.inner_diameter_m,
                g_target.outer_diameter_m,
                g_target.inner_diameter_m,
            )
            if r_goal >= 0.0:
                goal_line = f"Implied area reduction (incoming → target): {100.0 * r_goal:.2f}%"
            else:
                goal_line = (
                    f"Target annulus is {-100.0 * r_goal:.2f}% larger than incoming "
                    f"(schedule still simulated from incoming geometry)"
                )
            lines = [
                f"Incoming: OD={self.in_od_mm.value():.4f} mm, ID={self.in_id_mm.value():.4f} mm "
                f"(wall={(self.in_od_mm.value() - self.in_id_mm.value()) * 0.5:.4f} mm)",
                f"Target:   OD={self.out_od_mm.value():.4f} mm, ID={self.out_id_mm.value():.4f} mm "
                f"(wall={(self.out_od_mm.value() - self.out_id_mm.value()) * 0.5:.4f} mm)",
                goal_line,
                grain_hdr,
                "",
                f"Passes: {len(results)}, cumulative equiv. plastic strain ≈ {cum:.3f}",
                "",
            ]
        for i, pr in enumerate(results, start=1):
            if pr.grain_source == "damask":
                gtag = " [grain: DAMASK CP]"
            elif pr.grain_source == "analytical_fallback":
                gtag = " [grain: analytical fallback — install DAMASK_grid]"
            else:
                gtag = ""
            line = (
                f"Pass {i}: σ_vm={pr.von_mises_equiv_pa/1e6:.1f} MPa, SF(UTS)={pr.safety_factor_vs_uts:.2f}, "
                f"grain≈{pr.grain_size_um:.1f} μm{gtag}, break-risk proxy={pr.broken_risk_score:.3f}"
            )
            if pr.unloading_stress_mpa is not None:
                line += (
                    f"\n    Nitinol unload: σ_load≈{pr.von_mises_equiv_pa/1e6:.0f} MPa pull, "
                    f"σ_unload≈{pr.unloading_stress_mpa:.0f} MPa, "
                    f"Δσ_hyst≈{pr.hysteresis_mpa:.0f} MPa, "
                    f"springback ε≈{pr.springback_strain:.3f}, "
                    f"ε_perm≈{pr.permanent_strain:.3f}, "
                    f"σ_residual≈{pr.residual_stress_mpa:.1f} MPa"
                )
            lines.append(line)
        if backend == "damask" and not damask_can_run():
            lines.append("")
            lines.append(
                "⚠ DAMASK Grain is selected but DAMASK_grid is not on PATH — numbers match "
                "Built-In Analytical. Install: conda install -c conda-forge damask-grid"
            )
        lines.append("")
        lines.append(
            f"Simulated final: OD={sim.outer_diameter_m*1000:.4f} mm, ID={sim.inner_diameter_m*1000:.4f} mm "
            f"(wall={sim.wall_thickness_m*1000:.4f} mm)"
        )
        if backend != "manual" and g_target is not None:
            d_od_mm = (sim.outer_diameter_m - g_target.outer_diameter_m) * 1000.0
            d_id_mm = (sim.inner_diameter_m - g_target.inner_diameter_m) * 1000.0
            lines.append(f"Δ vs target output: ΔOD={d_od_mm:+.4f} mm, ΔID={d_id_mm:+.4f} mm")
        self.summary.setPlainText("\n".join(lines))

        self._refresh_schedule_visuals()
        self._update_geometry_hints()

    def _update_optuna_derived_label(self) -> None:
        """Show annulus reduction from Tubing Project and pass count from geometry + SF (matches Run Optuna)."""
        err = self._geometry_error_message()
        if err:
            self.opt_derived_label.setText(
                "Set valid incoming and target OD/ID on Tubing Project. Pass count and total reduction "
                "are derived from annulus area in → out and the limits below."
            )
            return
        try:
            g0 = self._geom()
            g1 = self._target_geometry()
        except ValueError:
            self.opt_derived_label.setText("Invalid geometry for optimization preview.")
            return
        r_tot = implied_area_reduction_fraction(
            g0.outer_diameter_m,
            g0.inner_diameter_m,
            g1.outer_diameter_m,
            g1.inner_diameter_m,
        )
        phi = 1.0 - r_tot
        min_sf = float(self.opt_min_sf.value())
        if r_tot <= 1e-12:
            self.opt_derived_label.setText(
                "No annulus reduction needed (target annulus area ≥ incoming). "
                "Use a smaller target OD/ID than incoming for a drawing reduction schedule."
            )
            return
        if r_tot < 0.0:
            self.opt_derived_label.setText(
                "Target annulus is larger than incoming; schedule optimization applies to reductions only."
            )
            return
        n_pass, hit_cap = recommended_pass_count(
            area_ratio_target_to_inlet=phi,
            max_per_pass_r=OPT_SCHEDULE_MAX_PER_PASS_R,
            min_per_pass_r=OPT_SCHEDULE_MIN_PER_PASS_R,
            min_margin_uts=min_sf,
            max_passes_cap=OPT_SCHEDULE_MAX_PASSES,
        )
        margin = max(1.0, min_sf)
        r_eff = max(
            OPT_SCHEDULE_MIN_PER_PASS_R,
            min(OPT_SCHEDULE_MAX_PER_PASS_R, OPT_SCHEDULE_MAX_PER_PASS_R / margin),
        )
        cap_note = (
            f"\nWarning: more than {OPT_SCHEDULE_MAX_PASSES} passes would be needed at this bite/SF; "
            f"capped at {OPT_SCHEDULE_MAX_PASSES}."
            if hit_cap
            else ""
        )
        self.opt_derived_label.setText(
            f"Total annulus area reduction: {100.0 * r_tot:.2f}%  ·  A_out/A_in = {phi:.5f}\n"
            f"Pass count for optimization: {n_pass} (from max per-pass r={OPT_SCHEDULE_MAX_PER_PASS_R:.2f}, "
            f"effective bite ≈ {r_eff:.3f} after SF≥{min_sf:.2f}).{cap_note}"
        )

    def _update_optuna_expected_passes_label(self) -> None:
        if self._optuna_pass_count_override is None:
            self.opt_expected_passes_label.setText(
                "Pass count: automatic from Tubing Project (click Expected passes… to fix die stations)."
            )
        else:
            self.opt_expected_passes_label.setText(
                f"Pass count: {self._optuna_pass_count_override} (your setting — Optuna optimizes area reduction r per pass; α and lubricant fixed from schedule)."
            )

    def _open_expected_passes_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Expected passes for optimization")
        lv = QVBoxLayout(dlg)
        info = QLabel(
            "Optuna searches per-pass area reductions (within bounds) for a chosen number of passes. "
            "Semi-die angle α and lubricant μ are taken from the Pass schedule and held fixed.\n"
            "Use automatic pass count from Tubing Project geometry, or enter how many drawing passes you want."
        )
        info.setWordWrap(True)
        lv.addWidget(info)
        rb_auto = QRadioButton("Automatic (geometry & Min SF)")
        rb_fixed = QRadioButton("Use this many passes:")
        sb = QSpinBox()
        sb.setRange(1, OPT_SCHEDULE_MAX_PASSES)
        b = self._pass_schedule_draw_pass_count()
        derived = max(1, int(b[2])) if b else 8
        sb.setValue(self._optuna_pass_count_override if self._optuna_pass_count_override is not None else derived)
        if self._optuna_pass_count_override is not None:
            rb_fixed.setChecked(True)
        else:
            rb_auto.setChecked(True)

        def _sync_sb() -> None:
            sb.setEnabled(rb_fixed.isChecked())

        rb_auto.toggled.connect(_sync_sb)
        rb_fixed.toggled.connect(_sync_sb)
        _sync_sb()

        lv.addWidget(rb_auto)
        fixed_row = QHBoxLayout()
        fixed_row.addWidget(rb_fixed)
        fixed_row.addWidget(sb)
        lv.addLayout(fixed_row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lv.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if rb_auto.isChecked():
            self._optuna_pass_count_override = None
        else:
            self._optuna_pass_count_override = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(sb.value())))
        self._update_optuna_expected_passes_label()

    def _optuna_fixed_die_and_lubricant(self) -> tuple[float, float]:
        """α and μ held constant during Optuna (from first drawing pass on Pass schedule)."""
        passes = self._read_passes()
        if passes:
            p0 = passes[0]
            return float(p0.semi_die_angle_deg), float(p0.friction_mu)
        return 12.0, 0.06

    def _run_optuna(self) -> None:
        err = self._geometry_error_message()
        if err:
            QMessageBox.warning(self, "Geometry", err)
            return
        try:
            g0 = self._geom()
            g1 = self._target_geometry()
        except ValueError as e:
            QMessageBox.warning(self, "Geometry", str(e))
            return
        r_tot = implied_area_reduction_fraction(
            g0.outer_diameter_m,
            g0.inner_diameter_m,
            g1.outer_diameter_m,
            g1.inner_diameter_m,
        )
        if r_tot <= 1e-12:
            QMessageBox.warning(
                self,
                "Optimization",
                "Target annulus must be smaller than incoming (positive total area reduction).",
            )
            return
        if r_tot < 0.0:
            QMessageBox.warning(
                self,
                "Optimization",
                "Target annulus larger than incoming — use schedule optimization only for reductions.",
            )
            return
        phi = 1.0 - r_tot
        min_sf = float(self.opt_min_sf.value())
        n_auto, hit_cap = recommended_pass_count(
            area_ratio_target_to_inlet=phi,
            max_per_pass_r=OPT_SCHEDULE_MAX_PER_PASS_R,
            min_per_pass_r=OPT_SCHEDULE_MIN_PER_PASS_R,
            min_margin_uts=min_sf,
            max_passes_cap=OPT_SCHEDULE_MAX_PASSES,
        )
        if self._optuna_pass_count_override is not None:
            n_pass = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(self._optuna_pass_count_override)))
            hit_cap = False
        else:
            n_pass = n_auto
        if n_pass == 1 and r_tot + 1e-9 < OPT_SCHEDULE_MIN_PER_PASS_R:
            QMessageBox.warning(
                self,
                "Optimization",
                f"Total area reduction ({r_tot:.4f}) is below the minimum per-pass bound "
                f"({OPT_SCHEDULE_MIN_PER_PASS_R:.2f}). Relax the target geometry or expected pass count.",
            )
            return
        mat = self._selected_material()
        fix_alpha, fix_mu = self._optuna_fixed_die_and_lubricant()
        lub_lbl = self._lubricant_display_label_for_mu(fix_mu)
        cfg = OptimizationConfig(
            n_passes=n_pass,
            target_area_reduction_total=float(r_tot),
            min_per_pass_r=OPT_SCHEDULE_MIN_PER_PASS_R,
            max_per_pass_r=OPT_SCHEDULE_MAX_PER_PASS_R,
            min_semi_die_deg=6.0,
            max_semi_die_deg=18.0,
            min_mu=0.02,
            max_mu=0.12,
            min_margin_uts=min_sf,
            n_trials=int(self.opt_trials.value()),
            fixed_semi_die_angle_deg=fix_alpha,
            fixed_friction_mu=fix_mu,
        )
        self.opt_log.append(f"Running Optuna… (material: {mat.name}, passes={n_pass}, R_tot={r_tot:.4f})")
        self.opt_log.append(
            f"Fixed from Pass schedule: α={fix_alpha:.2f}°, lubricant μ={fix_mu:.3f} ({lub_lbl}). "
            "Optuna varies per-pass area reduction only."
        )
        if self._optuna_pass_count_override is not None:
            self.opt_log.append(
                f"Pass count {n_pass} from Expected passes (automatic recommendation was {n_auto})."
            )
        elif hit_cap:
            self.opt_log.append(
                f"Note: recommended pass count exceeded {OPT_SCHEDULE_MAX_PASSES}; optimization is capped."
            )
        QApplication.processEvents()
        self.opt_run_btn.setEnabled(False)
        try:
            best_passes, _best_results, study = optimize_multi_pass_schedule(
                g0,
                mat,
                cfg,
                trial_callback=lambda _study, _trial: QApplication.processEvents(),
            )
        except RuntimeError as exc:
            self.opt_log.append(f"Optimization failed: {exc}")
            QMessageBox.warning(self, "Optimization", str(exc))
            return
        except Exception as exc:
            self.opt_log.append(f"Optimization failed: {exc}")
            QMessageBox.warning(
                self,
                "Optimization",
                f"Optimization could not complete:\n{exc}",
            )
            return
        finally:
            self.opt_run_btn.setEnabled(True)
        self.opt_log.append(f"Best value: {study.best_value:.4f}")
        for i, p in enumerate(best_passes):
            self.opt_log.append(
                f"  Pass {i+1}: r={p.area_reduction_fraction:.3f}, α={p.semi_die_angle_deg:.1f}°, μ={p.friction_mu:.3f}"
            )
        self.opt_log.append(
            "Review the optimized preview below; edit area reductions if needed, then click "
            "“Apply to Pass Schedule…” to update other tabs. "
            "For tube/die FEA, use the FEA tab."
        )
        try:
            self._populate_optimization_preview(best_passes)
        except Exception as exc:
            self.opt_log.append(f"Could not show optimization preview: {exc}")
            QMessageBox.warning(
                self,
                "Optimization",
                f"Optimization finished but the preview could not be updated:\n{exc}",
            )
            return
        self.tabs.setCurrentIndex(_TAB_INDEX_OPTIMIZATION)
        QApplication.processEvents()

    def _fea_append(self, line: str) -> None:
        self.fea_out.append(line)
        self.fea_out.ensureCursorVisible()

    def _fea_pass_input_from_form(self) -> PassInput:
        fix_alpha, fix_mu = self._optuna_fixed_die_and_lubricant()
        return PassInput(
            semi_die_angle_deg=float(self.fea_alpha.value()),
            friction_mu=float(fix_mu),
            area_reduction_fraction=float(self.fea_area_r.value()),
        )

    def _fea_set_display_passes(self, passes: list[PassInput]) -> None:
        self._fea_display_passes = list(passes)
        self._refresh_fea_cross_section_strip()
        self._refresh_fea_pass_schematic()

    def _on_fea_pass_select_changed(self, *_args) -> None:
        self._refresh_fea_cross_section_strip()
        self._refresh_fea_pass_schematic()

    def _refresh_fea_pass_schematic(self) -> None:
        if not getattr(self, "fea_pass_schematic", None):
            return
        od_in = float(self.fea_od_in.value())
        id_in = float(self.fea_id_in.value())
        if od_in <= id_in or float(self.fea_area_r.value()) <= 0:
            self.fea_pass_schematic.set_spec(
                FeaPassSchematicSpec(
                    placeholder="Set incoming OD/ID and area reduction,\nor Load from Optimization."
                )
            )
            return
        n_total = max(1, len(self._fea_display_passes) or len(self._read_passes_from_table(self.opt_preview_table)))
        n_pass = max(1, min(int(self.fea_pass_select.value()), n_total))
        self.fea_pass_schematic.set_spec(
            FeaPassSchematicSpec(
                tooling=tooling_kind_from_drawing_method(self.drawing_method_combo.currentText()),
                od_in_mm=od_in,
                id_in_mm=id_in,
                area_reduction_fraction=float(self.fea_area_r.value()),
                semi_die_angle_deg=float(self.fea_alpha.value()),
                bearing_length_mm=float(DEFAULT_BEARING_LENGTH_MM),
                pass_number=n_pass,
                pass_total=n_total,
                process_label=self.drawing_method_combo.currentText(),
            )
        )

    def _refresh_fea_cross_section_strip(self) -> None:
        if not getattr(self, "fea_cross_section_strip", None):
            return
        passes = list(self._fea_display_passes)
        if not passes:
            opt_passes = self._read_passes_from_table(self.opt_preview_table)
            if opt_passes:
                passes = opt_passes
            elif float(self.fea_area_r.value()) > 0:
                passes = [self._fea_pass_input_from_form()]
        highlight: set[int] = set()
        n_sel = int(self.fea_pass_select.value()) if getattr(self, "fea_pass_select", None) else 0
        if passes and 1 <= n_sel <= len(passes):
            highlight = {n_sel - 1}
        self.fea_cross_section_strip.set_model(
            self._build_cross_section_strip_model(
                passes, highlight_modified_pass_indices=highlight or None
            )
        )
        self.fea_cross_section_strip.update()

    def _fea_apply_optimization_pass(self) -> bool:
        """Fill single-pass fields from Optimization preview for ``fea_pass_select``. Returns success."""
        passes = self._read_passes_from_table(self.opt_preview_table)
        if not passes:
            QMessageBox.information(
                self,
                "FEA",
                "No optimized preview on the Optimization tab — run optimization there first.",
            )
            return False
        self._fea_sync_pass_select_from_preview()
        n = int(self.fea_pass_select.value())
        if n < 1 or n > len(passes):
            QMessageBox.warning(self, "FEA", f"Select pass 1–{len(passes)}.")
            return False
        p = passes[n - 1]
        err = self._geometry_error_message(require_target=False)
        if err:
            QMessageBox.warning(self, "FEA", err)
            return False
        try:
            g0 = self._geom()
        except ValueError as exc:
            QMessageBox.warning(self, "FEA", str(exc))
            return False
        mat = self._selected_material()
        prefix = passes[: n - 1]
        if prefix:
            geoms, _, _, _ = simulate_schedule(g0, mat, prefix)
            g_before = geoms[len(prefix)]
        else:
            g_before = g0
        self.fea_od_in.setValue(float(g_before.outer_diameter_m * 1000.0))
        self.fea_id_in.setValue(float(g_before.inner_diameter_m * 1000.0))
        self.fea_area_r.setValue(float(p.area_reduction_fraction))
        self.fea_alpha.setValue(float(p.semi_die_angle_deg))
        self._fea_set_display_passes(passes)
        return True

    def _fea_load_from_optimization(self) -> None:
        n = int(self.fea_pass_select.value())
        if self._fea_apply_optimization_pass():
            self._fea_append(
                f"Loaded Optimization pass {n}: "
                f"OD/ID in {self.fea_od_in.value():.4f} / {self.fea_id_in.value():.4f} mm, "
                f"r={self.fea_area_r.value():.4f}, α={self.fea_alpha.value():.2f}°."
            )

    def _run_fea_single_pass_from_optimization(self) -> None:
        if not self._fea_apply_optimization_pass():
            return
        self._run_fea_single_pass(run_label="Single Pass FEA")

    def _run_fea_single_pass(self, *, run_label: str = "Manual FEA") -> None:
        from tubing_master.dolfinx_sim import dolfinx_available

        if not dolfinx_available():
            QMessageBox.warning(
                self,
                "FEA",
                "dolfinx is not available in this Python environment. Install via conda-forge.",
            )
            return
        mat = self._selected_material()
        od_m = float(self.fea_od_in.value()) / 1000.0
        id_m = float(self.fea_id_in.value()) / 1000.0
        if od_m <= id_m:
            QMessageBox.warning(self, "FEA", "Incoming OD must exceed ID.")
            return
        self._fea_append(f"{run_label}: running axisymmetric tube/die FEA…")
        self._fea_set_busy(True)
        try:
            QApplication.processEvents()
            res = run_tube_die_pass_subprocess(
                od_in_m=od_m,
                id_in_m=id_m,
                area_reduction_fraction=float(self.fea_area_r.value()),
                semi_die_angle_deg=float(self.fea_alpha.value()),
                youngs_pa=float(mat.e_mpa) * 1e6,
            )
        finally:
            self._fea_set_busy(False)
        if res.ok:
            vm_mpa = res.max_von_mises_pa / 1e6
            ref_mpa = float(mat.fea_reference_stress_mpa())
            ref_label = "transformation onset" if mat.is_nitinol() else "yield"
            if vm_mpa < 0.9 * ref_mpa:
                verdict = (
                    f"Assessment: below {ref_label} ({ref_mpa:.0f} MPa) — "
                    "pass looks acceptable in elastic FEA."
                )
            elif vm_mpa <= ref_mpa:
                verdict = (
                    f"Assessment: near {ref_label} ({ref_mpa:.0f} MPa) — review before production."
                )
            else:
                verdict = (
                    f"Assessment: above {ref_label} ({ref_mpa:.0f} MPa) — "
                    "try lower r or different α."
                )
            if mat.is_nitinol():
                verdict += " (Linear elastic FEA; superelastic plateau not resolved.)"
            self._fea_append(
                f"Pass {int(self.fea_pass_select.value())}: max σ_vm = {vm_mpa:.2f} MPa "
                f"({ref_label} ≈ {ref_mpa:.0f} MPa) | "
                f"OD out {res.od_out_m * 1000:.4f} mm, ID {res.id_out_m * 1000:.4f} mm | "
                f"L_red ≈ {res.reduction_zone_length_m * 1000:.2f} mm"
            )
            self._fea_append(verdict)
            opt_passes = self._read_passes_from_table(self.opt_preview_table)
            n = int(self.fea_pass_select.value())
            if opt_passes and 1 <= n <= len(opt_passes):
                self._fea_set_display_passes(opt_passes[:n])
            else:
                self._fea_set_display_passes([self._fea_pass_input_from_form()])
        else:
            self._fea_append(f"FEA failed: {res.message}")

    def _run_fea_analyze_pass_schedule(self) -> None:
        from tubing_master.dolfinx_sim import dolfinx_available

        if not dolfinx_available():
            QMessageBox.warning(self, "FEA", "dolfinx is not available.")
            return
        err = self._geometry_error_message(require_target=False)
        if err:
            QMessageBox.warning(self, "FEA", err)
            return
        passes = self._read_passes()
        if not passes:
            QMessageBox.warning(self, "FEA", "No drawing passes on Pass schedule.")
            return
        g0 = self._geom()
        mat = self._selected_material()
        self._fea_append(f"FEA analyzing {len(passes)} pass(es) from Pass schedule…")
        self._fea_set_busy(True)
        try:
            QApplication.processEvents()
            row = verify_pass_schedule_fea(g0, mat, passes)
        finally:
            self._fea_set_busy(False)
        if not row.ok:
            self._fea_append(f"Schedule FEA failed: {row.message}")
            return
        for pr in row.pass_probes:
            self._fea_append(
                f"  Pass {pr.pass_index}: max σ_vm = {pr.max_von_mises_pa / 1e6:.2f} MPa"
            )
        self._fea_append(
            f"Schedule max σ_vm = {row.schedule_max_von_mises_pa / 1e6:.2f} MPa (axisymmetric tube/die)."
        )
        self._fea_set_display_passes(passes)

    def _fea_optimization_config(self, n_pass: int, r_tot: float) -> OptimizationConfig:
        fix_alpha, fix_mu = self._optuna_fixed_die_and_lubricant()
        return OptimizationConfig(
            n_passes=n_pass,
            target_area_reduction_total=float(r_tot),
            min_per_pass_r=OPT_SCHEDULE_MIN_PER_PASS_R,
            max_per_pass_r=OPT_SCHEDULE_MAX_PER_PASS_R,
            min_semi_die_deg=6.0,
            max_semi_die_deg=18.0,
            min_mu=0.02,
            max_mu=0.12,
            min_margin_uts=float(self.opt_min_sf.value()),
            n_trials=int(self.fea_opt_trials.value()),
            fixed_semi_die_angle_deg=fix_alpha,
            fixed_friction_mu=fix_mu,
        )

    def _run_fea_optimization_pure(self) -> None:
        from tubing_master.dolfinx_sim import dolfinx_available

        if not dolfinx_available():
            QMessageBox.warning(self, "FEA", "dolfinx is required for FEA optimization.")
            return
        g0, g1, r_tot, n_pass, mat, err = self._fea_optimization_geometry_bundle()
        if err:
            QMessageBox.warning(self, "FEA", err)
            return
        cfg = self._fea_optimization_config(n_pass, r_tot)
        cfg.n_trials = int(self.fea_opt_trials.value())
        self.fea_out.clear()
        self._fea_append(
            f"FEA Optuna ({cfg.n_trials} trials, {n_pass} passes) — each trial uses tube/die FEA."
        )
        self._fea_set_busy(True)
        try:
            QApplication.processEvents()
            best_passes, study = optimize_multi_pass_schedule_fea(g0, mat, cfg)
        except Exception as exc:
            self._fea_append(f"FEA optimization failed: {exc}")
            return
        finally:
            self._fea_set_busy(False)
        self._fea_present_optimization_result(best_passes, study, prefix="FEA Optuna")

    def _run_fea_optimization_hybrid(self) -> None:
        from tubing_master.dolfinx_sim import dolfinx_available

        g0, g1, r_tot, n_pass, mat, err = self._fea_optimization_geometry_bundle()
        if err:
            QMessageBox.warning(self, "FEA", err)
            return
        cfg = self._fea_optimization_config(n_pass, r_tot)
        cfg.n_trials = max(10, int(self.opt_trials.value()))
        phi = 1.0 - r_tot
        self.fea_out.clear()
        self._fea_append(f"Step 1: analytical Optuna ({cfg.n_trials} trials)…")
        self._fea_set_busy(True)
        try:
            QApplication.processEvents()
            _best, _res, study = optimize_multi_pass_schedule(g0, mat, cfg)
            self._fea_append(f"  Analytical best trial {study.best_trial.number}: {study.best_value:.4f}")
            if not dolfinx_available():
                self._fea_append("dolfinx missing — cannot run FEA top-5 verification.")
                self._fea_present_optimization_result(_best, study, prefix="Analytical")
                return
            self._fea_append(f"Step 2: tube/die FEA on top {HYBRID_FEA_TOP_K} analytical schedules…")
            QApplication.processEvents()
            verified = fea_verify_top_analytical_schedules(g0, mat, cfg, study, phi=phi)
            for row in verified:
                rs = ", ".join(f"{p.area_reduction_fraction:.3f}" for p in row.passes)
                if row.ok:
                    self._fea_append(
                        f"  trial {row.trial_number} (#{row.rank_analytical}): "
                        f"max σ_vm={row.schedule_max_von_mises_pa / 1e6:.1f} MPa | r=[{rs}]"
                    )
                else:
                    self._fea_append(f"  trial {row.trial_number}: {row.message}")
            fea_best = pick_fea_best_schedule(verified)
            if fea_best is not None:
                self._fea_present_optimization_result(
                    list(fea_best.passes),
                    study,
                    prefix="FEA-ranked",
                    fea_note=(
                        f"FEA-best trial {fea_best.trial_number} "
                        f"(max σ_vm={fea_best.schedule_max_von_mises_pa / 1e6:.1f} MPa)."
                    ),
                )
            else:
                self._fea_append("All FEA checks failed — keeping analytical best.")
                self._fea_present_optimization_result(_best, study, prefix="Analytical")
        except Exception as exc:
            self._fea_append(f"Hybrid FEA optimization failed: {exc}")
        finally:
            self._fea_set_busy(False)

    def _fea_optimization_geometry_bundle(self):
        err = self._geometry_error_message()
        if err:
            return None, None, 0.0, 0, None, err
        try:
            g0 = self._geom()
            g1 = self._target_geometry()
        except ValueError as e:
            return None, None, 0.0, 0, None, str(e)
        r_tot = implied_area_reduction_fraction(
            g0.outer_diameter_m,
            g0.inner_diameter_m,
            g1.outer_diameter_m,
            g1.inner_diameter_m,
        )
        if r_tot <= 1e-12:
            return None, None, 0.0, 0, None, "Target must be smaller than incoming."
        phi = 1.0 - r_tot
        n_auto, _hit = recommended_pass_count(
            area_ratio_target_to_inlet=phi,
            max_per_pass_r=OPT_SCHEDULE_MAX_PER_PASS_R,
            min_per_pass_r=OPT_SCHEDULE_MIN_PER_PASS_R,
            min_margin_uts=float(self.opt_min_sf.value()),
            max_passes_cap=OPT_SCHEDULE_MAX_PASSES,
        )
        if self._optuna_pass_count_override is not None:
            n_pass = max(1, min(OPT_SCHEDULE_MAX_PASSES, int(self._optuna_pass_count_override)))
        else:
            n_pass = n_auto
        return g0, g1, r_tot, n_pass, self._selected_material(), None

    def _fea_present_optimization_result(
        self,
        passes: list[PassInput],
        study: Any,
        *,
        prefix: str,
        fea_note: str = "",
    ) -> None:
        self._fea_last_best_passes = list(passes)
        self._fea_set_display_passes(passes)
        self.fea_apply_to_schedule_btn.setEnabled(bool(passes))
        self._fea_append(f"{prefix} best (trial {study.best_trial.number}):")
        for i, p in enumerate(passes):
            self._fea_append(
                f"  Pass {i + 1}: r={p.area_reduction_fraction:.3f}, "
                f"α={p.semi_die_angle_deg:.1f}°, μ={p.friction_mu:.3f}"
            )
        if fea_note:
            self._fea_append(fea_note)
        self._fea_append('Click "Apply FEA-best schedule to Pass schedule…" to copy into the pass table.')

    def _apply_fea_best_to_pass_schedule(self) -> None:
        if not self._fea_last_best_passes:
            QMessageBox.warning(self, "FEA", "Run FEA optimization first.")
            return
        ans = QMessageBox.question(
            self,
            "Apply FEA schedule",
            "Copy the FEA optimization result into the Pass schedule tab?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.table.blockSignals(True)
        try:
            self._apply_pass_inputs_list_to_table(self._fea_last_best_passes)
        finally:
            self.table.blockSignals(False)
        self._capture_tubing_project_baseline_from_table()
        self._refresh_schedule_visuals()
        QMessageBox.information(self, "Pass schedule", "FEA schedule applied to Pass schedule tab.")

    def _fea_set_busy(self, busy: bool) -> None:
        for w in (
            self.fea_manual_btn,
            self.fea_run_pass_btn,
            self.fea_analyze_schedule_btn,
            self.fea_opt_pure_btn,
            self.fea_opt_hybrid_btn,
            self.fea_load_opt_btn,
            self.fea_pass_select,
            self.fea_od_in,
            self.fea_id_in,
            self.fea_area_r,
            self.fea_alpha,
        ):
            w.setEnabled(not busy)

# Backward-compatible aliases from earlier naming
MainWindow = TubingMaster
TubeDrawingExpert = TubingMaster
