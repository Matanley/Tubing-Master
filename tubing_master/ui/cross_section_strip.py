"""Native Qt cross-section strip (tubing wall evolution by pass) — Tubing Project & Pass Schedule tabs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from tubing_master.ui.diagram_palette import (
    ANNOTATION,
    ANNOTATION_MUTED,
    BG,
    CENTERLINE,
    INCOMING_EDGE,
    INCOMING_FILL,
    MODIFIED_EDGE,
    MODIFIED_FILL_DARK,
    MODIFIED_FILL_LIGHT,
    PASS_BLUE_DARK,
    PASS_BLUE_LIGHT,
    PASS_EDGE,
    PLACEHOLDER,
    PLOT_BG,
    PLOT_BORDER,
)


def _lerp_rgb(c0: Tuple[int, int, int], c1: Tuple[int, int, int], t: float) -> QColor:
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        int(c0[0] + (c1[0] - c0[0]) * t),
        int(c0[1] + (c1[1] - c0[1]) * t),
        int(c0[2] + (c1[2] - c0[2])),
    )


def _pass_blue_color(pass_index: int, n_passes: int) -> QColor:
    """Darker Blues ramp from first pass to final pass."""
    if n_passes <= 1:
        return QColor(*PASS_BLUE_LIGHT)
    t = pass_index / (n_passes - 1)
    return _lerp_rgb(PASS_BLUE_LIGHT, PASS_BLUE_DARK, t)


def _modified_pass_colors(strength: float) -> Tuple[QColor, QColor, float]:
    face = _lerp_rgb(MODIFIED_FILL_LIGHT, MODIFIED_FILL_DARK, strength)
    edge = MODIFIED_EDGE if strength > 0.55 else QColor(148, 120, 72)
    lw = 1.2 + 2.0 * strength
    return face, edge, lw


@dataclass
class CrossSectionPassSegment:
    od_mm: float
    id_mm: float
    is_modified: bool = False
    modification_strength: float = 0.0
    grain_um: Optional[float] = None
    grain_source: str = ""  # "damask" | "analytical" | "analytical_fallback"
    show_od_label: bool = False


@dataclass
class CrossSectionStripModel:
    """Precomputed layout for :class:`CrossSectionStripWidget`."""

    error_message: Optional[str] = None
    n_passes: int = 0
    segments: List[CrossSectionPassSegment] = field(default_factory=list)
    # Zero-pass preview (incoming only)
    incoming_od_mm: float = 0.0
    incoming_id_mm: float = 0.0
    target_od_mm: float = 0.0
    target_id_mm: float = 0.0
    show_target_hint: bool = False


class CrossSectionStripWidget(QWidget):
    """Side-view hollow tube strip drawn with QPainter (classic engineering grey style)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = CrossSectionStripModel()
        self.setMinimumSize(400, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_model(self, model: CrossSectionStripModel) -> None:
        self._model = model
        self.update()

    def model(self) -> CrossSectionStripModel:
        return self._model

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), BG)

        m = self._model
        if m.error_message:
            p.setPen(PLACEHOLDER)
            p.setFont(QFont(self.font().family(), 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, m.error_message)
            return

        w, h = self.width(), self.height()
        if w < 20 or h < 20:
            return

        margin_l, margin_r = 12, 12
        margin_t, margin_b = 12, 32
        plot_w = max(1, w - margin_l - margin_r)
        plot_h = max(1, h - margin_t - margin_b)
        plot_rect = QRectF(margin_l, margin_t, plot_w, plot_h)
        p.setPen(QPen(PLOT_BORDER, 1.0))
        p.setBrush(PLOT_BG)
        p.drawRoundedRect(plot_rect, 4, 4)

        cx_base = margin_l
        cy = margin_t + plot_h / 2.0

        if m.n_passes == 0:
            self._paint_single_incoming(p, m, cx_base, cy, plot_w, plot_h)
            return

        n = m.n_passes
        od_seg = [s.od_mm for s in m.segments]
        half_max = max((od / 2.0 for od in od_seg), default=0.05)
        if half_max <= 0 or not (half_max == half_max):
            half_max = 0.05
        pad_r = max(0.06 * half_max, 0.08)
        y_scale = (plot_h / 2.0 - 8) / (half_max + pad_r)

        def x_center(pass_i: int) -> float:
            return cx_base + (pass_i + 0.5) * (plot_w / n)

        def x_edge(pass_i: int) -> float:
            return cx_base + pass_i * (plot_w / n)

        # Pass separators (behind walls)
        for i in range(n):
            x1 = x_edge(i + 1)
            seg = m.segments[i]
            if seg.is_modified:
                sep_color = QColor(180, 148, 96)
                sep_w = 1.1
            else:
                sep_color = QColor(148, 178, 214)
                sep_w = 0.75
            p.setPen(QPen(sep_color, sep_w))
            p.drawLine(int(x1), int(margin_t + 2), int(x1), int(margin_t + plot_h - 2))

        # Walls
        for i, seg in enumerate(m.segments):
            x0, x1 = x_edge(i), x_edge(i + 1)
            ho = seg.od_mm / 2.0
            hi = max(seg.id_mm / 2.0, 1e-9)
            if ho <= hi:
                continue
            if seg.is_modified:
                face, edge, lw = _modified_pass_colors(seg.modification_strength)
            else:
                face = _pass_blue_color(i, n)
                edge = PASS_EDGE
                lw = 1.1
            pen = QPen(edge, lw)
            p.setPen(pen)
            p.setBrush(face)
            p.drawRect(QRectF(x0, cy - ho * y_scale, x1 - x0, (ho - hi) * y_scale))
            p.drawRect(QRectF(x0, cy + hi * y_scale, x1 - x0, (ho - hi) * y_scale))

        # Centerline
        cl_pen = QPen(CENTERLINE, 0.85)
        cl_pen.setStyle(Qt.PenStyle.CustomDashLine)
        cl_pen.setDashPattern([8.0, 3.0, 2.0, 3.0])
        p.setPen(cl_pen)
        p.drawLine(margin_l + 2, int(cy), margin_l + plot_w - 2, int(cy))

        # Pass numbers
        p.setPen(ANNOTATION)
        lbl_font = QFont(self.font().family(), 7)
        p.setFont(lbl_font)
        y_pass = margin_t + plot_h + 12
        for i in range(n):
            p.drawText(
                QRectF(x_edge(i), y_pass - 8, plot_w / n, 16),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                str(i + 1),
            )

        # Grain labels (every other pass, on the bore centerline)
        grain_font = QFont(self.font().family(), 7)
        p.setFont(grain_font)
        for i, seg in enumerate(m.segments):
            if i % 2 == 1 or seg.grain_um is None:
                continue
            y_txt = cy
            src = (seg.grain_source or "").strip()
            if src == "damask":
                tag = "DAMASK"
            elif src == "analytical_fallback":
                tag = "analytical*"
            else:
                tag = ""
            text = (
                f"Grain {seg.grain_um:.1f} µm ({tag})"
                if tag
                else f"Grain Size {seg.grain_um:.1f} µm"
            )
            tw = 118 if tag else 88
            th = 16
            rx = x_center(i) - tw / 2
            ry = y_txt - th / 2
            p.setPen(QPen(PLOT_BORDER, 0.6))
            p.setBrush(QColor(252, 252, 253, 235))
            p.drawRoundedRect(QRectF(rx, ry, tw, th), 3, 3)
            p.setPen(ANNOTATION)
            p.drawText(
                QRectF(rx, ry, tw, th),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        # OD labels (every other pass, above wall)
        od_font = QFont(self.font().family(), 7)
        p.setFont(od_font)
        for i, seg in enumerate(m.segments):
            if not seg.show_od_label:
                continue
            y_label = cy - (seg.od_mm / 2.0) * y_scale - 14
            color = MODIFIED_EDGE if seg.is_modified else QColor(30, 64, 110)
            p.setPen(color)
            p.drawText(
                QRectF(x_edge(i), y_label, plot_w / n, 14),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                f"OD {seg.od_mm:.2f}",
            )

    def _paint_single_incoming(
        self,
        p: QPainter,
        m: CrossSectionStripModel,
        cx_base: float,
        cy: float,
        plot_w: float,
        plot_h: float,
    ) -> None:
        ho = m.incoming_od_mm / 2.0
        hi = max(m.incoming_id_mm / 2.0, 1e-9)
        half_m = max(ho, 0.05)
        y_scale = (plot_h / 2.0 - 8) / (half_m * 1.15)
        x0, x1 = cx_base + 2, cx_base + plot_w - 2
        p.setPen(QPen(INCOMING_EDGE, 1.15))
        p.setBrush(INCOMING_FILL)
        if ho > hi:
            p.drawRect(QRectF(x0, cy - ho * y_scale, x1 - x0, (ho - hi) * y_scale))
            p.drawRect(QRectF(x0, cy + hi * y_scale, x1 - x0, (ho - hi) * y_scale))
        cl_pen = QPen(CENTERLINE, 0.85)
        cl_pen.setStyle(Qt.PenStyle.CustomDashLine)
        cl_pen.setDashPattern([8.0, 3.0, 2.0, 3.0])
        p.setPen(cl_pen)
        p.drawLine(int(x0), int(cy), int(x1), int(cy))
        if m.show_target_hint:
            p.setPen(ANNOTATION_MUTED)
            hint_font = QFont(self.font().family(), 8)
            p.setFont(hint_font)
            hint = f"Target OD={m.target_od_mm:.3f} mm · ID={m.target_id_mm:.3f} mm"
            p.drawText(
                QRectF(cx_base, cy + plot_h / 2 - 8, plot_w, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                hint,
            )
