"""Native Qt wire-drawing die schematic (Die inventory tab)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from tubing_master.die_schematic import (
    DieSchematicGeometry,
    DieSchematicSpec,
    compute_die_schematic_geometry,
    empty_die_schematic_spec,
    symmetric_outline,
)
from tubing_master.ui.diagram_palette import (
    ACCENT_BLUE,
    ANNOTATION,
    ANNOTATION_MUTED,
    BG,
    BORE_EDGE,
    BORE_FILL,
    CENTERLINE,
    DIM_LINE,
    METAL_EDGE,
    METAL_HATCH,
    PLACEHOLDER,
    PLOT_BG,
    PLOT_BORDER,
)


class DieSchematicWidget(QWidget):
    """Side-view drawing die profile rendered with QPainter (replaces matplotlib canvas)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec = empty_die_schematic_spec(inventory_empty=True)
        self._geom: Optional[DieSchematicGeometry] = compute_die_schematic_geometry(self._spec)
        self.setMinimumSize(200, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_spec(self, spec: DieSchematicSpec) -> None:
        self._spec = spec
        self._geom = compute_die_schematic_geometry(spec)
        self.update()

    def spec(self) -> DieSchematicSpec:
        return self._spec

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), BG)

        spec = self._spec
        if spec.placeholder:
            p.setPen(PLACEHOLDER)
            p.setFont(QFont(self.font().family(), 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, spec.placeholder)
            if spec.subtitle:
                sub_rect = QRectF(0, self.height() * 0.55, self.width(), self.height() * 0.2)
                p.setFont(QFont(self.font().family(), 9))
                p.setPen(ANNOTATION_MUTED)
                p.drawText(sub_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, spec.subtitle)
            return

        geom = self._geom
        if geom is None:
            return

        w, h = self.width(), self.height()
        margin_l, margin_r = 16, 16
        margin_t, margin_b = 20, 16
        plot_w = max(1.0, w - margin_l - margin_r)
        plot_h = max(1.0, h - margin_t - margin_b)
        plot_rect = QRectF(margin_l, margin_t, plot_w, plot_h)
        p.setPen(QPen(PLOT_BORDER, 1.0))
        p.setBrush(PLOT_BG)
        p.drawRoundedRect(plot_rect, 4, 4)

        z_span = max(geom.z_max - geom.z_min, 1e-9)
        scale = min(plot_w / z_span, plot_h / (2.0 * geom.r_plot_max)) * 0.94
        cx_off = margin_l + (plot_w - z_span * scale) / 2.0
        cy = margin_t + plot_h / 2.0

        def map_pt(z: float, r: float) -> QPointF:
            x = cx_off + (z - geom.z_min) * scale
            y = cy - r * scale
            return QPointF(x, y)

        def to_polygon(zs: Tuple[float, ...], rs: Tuple[float, ...]) -> QPolygonF:
            verts = symmetric_outline(list(zs), list(rs))
            return QPolygonF([map_pt(z, r) for z, r in verts])

        # Centerline (classic long-short dash)
        dash_pen = QPen(CENTERLINE, 0.9)
        dash_pen.setStyle(Qt.PenStyle.CustomDashLine)
        dash_pen.setDashPattern([10.0, 3.0, 2.0, 3.0])
        p.setPen(dash_pen)
        p.drawLine(map_pt(geom.z_min, 0.0), map_pt(geom.z_max, 0.0))

        # Outer die casing — steel grey with diagonal hatch
        outer_poly = to_polygon(geom.zo, geom.ro)
        p.setPen(QPen(METAL_EDGE, 2.0))
        p.setBrush(QBrush(METAL_HATCH, Qt.BrushStyle.BDiagPattern))
        p.drawPolygon(outer_poly)
        # Soft metallic gradient overlay
        grad = QLinearGradient(plot_rect.topLeft(), plot_rect.bottomRight())
        grad.setColorAt(0.0, QColor(200, 206, 214, 70))
        grad.setColorAt(1.0, QColor(140, 148, 160, 90))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPolygon(outer_poly)
        p.setPen(QPen(METAL_EDGE, 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(outer_poly)

        # Bore / inner cavity
        inner_poly = to_polygon(geom.zu, geom.ru)
        p.setPen(QPen(BORE_EDGE, 1.6))
        p.setBrush(BORE_FILL)
        p.drawPolygon(inner_poly)

        # Drawing direction arrow (along centerline)
        z_span_draw = geom.z3 - geom.z_bell0
        z_a0 = geom.z_bell0 + 0.05 * z_span_draw
        z_a1 = geom.z3 - 0.05 * z_span_draw
        p0, p1 = map_pt(z_a0, 0.0), map_pt(z_a1, 0.0)
        p.setPen(QPen(ANNOTATION, 1.2))
        p.setBrush(ANNOTATION)
        p.drawLine(p0, p1)
        self._draw_arrow_head(p, p0, p1, head_len=8.0)

        dir_y = cy + 1.08 * (geom.r_hi + geom.wall) * scale
        p.setPen(ANNOTATION_MUTED)
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(
            QRectF((p0.x() + p1.x()) / 2 - 60, dir_y, 120, 18),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "Drawing direction →",
        )

        # Semi-die angle label
        p.setPen(ACCENT_BLUE)
        p.setFont(QFont(self.font().family(), 8))
        alpha_pt = map_pt(geom.zc, geom.rc_label)
        p.drawText(
            QRectF(alpha_pt.x() - 44, alpha_pt.y() - 16, 88, 32),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            f"α = {geom.alpha_deg:.2f}°\n(semi-die)",
        )

        # Bearing length dimension (below centerline)
        y_dim = -geom.dim_depth
        y_ext = -geom.y_ext_end
        ext_pen = QPen(DIM_LINE, 0.7)
        p.setPen(ext_pen)
        p.drawLine(map_pt(geom.z1, -geom.r_lo), map_pt(geom.z1, y_ext))
        p.drawLine(map_pt(geom.z2, -geom.r_lo), map_pt(geom.z2, y_ext))
        p1d = map_pt(geom.z1, y_dim)
        p2d = map_pt(geom.z2, y_dim)
        p.setPen(QPen(DIM_LINE, 0.95))
        p.drawLine(p1d, p2d)
        self._draw_arrow_head(p, p1d, p2d, head_len=6.0)
        self._draw_arrow_head(p, p2d, p1d, head_len=6.0)

        p.setFont(QFont(self.font().family(), 8))
        p.setPen(ANNOTATION_MUTED)
        p.drawText(
            QRectF((p1d.x() + p2d.x()) / 2 - 70, max(p1d.y(), p2d.y()) + 4, 140, 16),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            f"Bearing Length = {geom.bearing_length_mm:.3f} mm",
        )

        # Title
        title_pt = map_pt(geom.z_bell0, geom.r_hi + geom.wall + 0.18 * max(geom.r_hi, 1.0))
        p.setPen(ANNOTATION)
        title_font = QFont(self.font().family(), 10)
        title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(
            QRectF(title_pt.x(), title_pt.y() - 22, plot_w, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            geom.name,
        )

    @staticmethod
    def _draw_arrow_head(p: QPainter, tail: QPointF, tip: QPointF, *, head_len: float) -> None:
        dx = tip.x() - tail.x()
        dy = tip.y() - tail.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        half_w = head_len * 0.42
        path = QPainterPath()
        path.moveTo(tip)
        path.lineTo(QPointF(tip.x() - ux * head_len + px * half_w, tip.y() - uy * head_len + py * half_w))
        path.lineTo(QPointF(tip.x() - ux * head_len - px * half_w, tip.y() - uy * head_len - py * half_w))
        path.closeSubpath()
        p.fillPath(path, p.pen().color())
