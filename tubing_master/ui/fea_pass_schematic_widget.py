"""FEA single-pass schematic — educational tube-drawing diagram (video-style)."""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from tubing_master.die_schematic import symmetric_outline
from tubing_master.fea_pass_schematic import (
    FeaPassSchematicLayout,
    FeaPassSchematicSpec,
    build_fea_pass_schematic_layout,
    tube_wall_band_polygons,
)


class FeaPassSchematicWidget(QWidget):
    """Side-view tube / die / mandrel diagram styled like textbook / YouTube explainers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec = FeaPassSchematicSpec(placeholder="Load a pass to preview tooling.")
        self._layout: Optional[FeaPassSchematicLayout] = build_fea_pass_schematic_layout(self._spec)
        self.setMinimumSize(400, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_spec(self, spec: FeaPassSchematicSpec) -> None:
        self._spec = spec
        self._layout = build_fea_pass_schematic_layout(spec)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(255, 255, 255))

        if self._spec.placeholder:
            p.setPen(QColor(73, 80, 87))
            p.setFont(QFont(self.font().family(), 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._spec.placeholder)
            return

        lay = self._layout
        if lay is None:
            return

        w, h = self.width(), self.height()
        margin_l, margin_r, margin_t, margin_b = 18, 18, 34, 42
        plot_w = max(1.0, w - margin_l - margin_r)
        plot_h = max(1.0, h - margin_t - margin_b)
        z_span = max(lay.z_plot_max - lay.z_plot_min, 1e-9)
        scale = min(plot_w / z_span, plot_h / (2.0 * lay.r_plot_max))
        cx = margin_l + (plot_w - z_span * scale) / 2.0
        cy = margin_t + plot_h / 2.0

        def map_pt(z: float, r: float) -> QPointF:
            return QPointF(cx + (z - lay.z_plot_min) * scale, cy - r * scale)

        def closed_from_half(
            upper: List[Tuple[float, float]], lower: List[Tuple[float, float]]
        ) -> QPolygonF:
            pts = [map_pt(z, r) for z, r in upper] + [map_pt(z, r) for z, r in reversed(lower)]
            return QPolygonF(pts)

        self._draw_title(p, lay, w, margin_l, margin_t, margin_r)
        self._draw_die_block(p, lay, map_pt, closed_from_half)
        self._draw_mandrel(p, lay, map_pt)
        self._draw_tube_walls(p, lay, map_pt)
        self._draw_labels_and_direction(p, lay, map_pt, scale)

    def _draw_title(
        self, p: QPainter, lay: FeaPassSchematicLayout, w: int, ml: int, mt: int, mr: int
    ) -> None:
        tf = QFont(self.font().family(), 10)
        tf.setBold(True)
        p.setFont(tf)
        p.setPen(QColor(33, 37, 41))
        proc = lay.process_label or "Tube drawing"
        p.drawText(
            QRectF(ml, 6, w - ml - mr, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{proc}  ·  Pass {lay.pass_number}/{lay.pass_total}",
        )

    def _draw_die_block(
        self,
        p: QPainter,
        lay: FeaPassSchematicLayout,
        map_pt: Callable[[float, float], QPointF],
        closed_from_half,
    ) -> None:
        g = lay.die
        z0 = g.z_bell0 - 0.06 * (g.z3 - g.z_bell0)
        z1 = g.z3 + 0.10 * (g.z3 - g.z_bell0)
        r_blk = max(lay.r_holder_outer * 0.92, max(g.ro) * 1.02, lay.od_in_mm / 2.0 * 1.15)

        block = closed_from_half([(z0, r_blk), (z1, r_blk)], [(z0, -r_blk), (z1, -r_blk)])
        p.setPen(QPen(QColor(26, 26, 26), 1.8))
        p.setBrush(QBrush(QColor(198, 202, 208), Qt.BrushStyle.BDiagPattern))
        p.drawPolygon(block)

        bore = QPolygonF([map_pt(z, r) for z, r in symmetric_outline(list(g.zu), list(g.ru))])
        p.setPen(QPen(QColor(26, 26, 26), 1.2))
        p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(bore)

    def _draw_mandrel(
        self, p: QPainter, lay: FeaPassSchematicLayout, map_pt: Callable[[float, float], QPointF]
    ) -> None:
        kind = lay.tooling
        if kind == "sink":
            return

        g = lay.die
        r_id_i = lay.id_in_mm / 2.0
        r_id_o = lay.id_out_mm / 2.0
        z0 = lay.z_stock_start
        z1 = g.z3
        outline = QPen(QColor(26, 26, 26), 1.6)
        fill = QColor(255, 255, 255)

        def bar(z_a: float, z_b: float, r_outer: float, r_inner: float) -> QPolygonF:
            return QPolygonF(
                [
                    map_pt(z_a, r_outer),
                    map_pt(z_b, r_outer),
                    map_pt(z_b, r_inner),
                    map_pt(z_a, r_inner),
                    map_pt(z_a, -r_outer),
                    map_pt(z_b, -r_outer),
                    map_pt(z_b, -r_inner),
                    map_pt(z_a, -r_inner),
                ]
            )

        p.setPen(outline)
        p.setBrush(fill)

        if kind in ("mandrel", "floating_plug"):
            r_bar = max(0.08, r_id_i * 0.88)
            p.drawPolygon(bar(z0, z1, r_bar, max(0.02, r_bar * 0.5)))
            return

        z_m_end = g.z1
        r_m = max(0.06, r_id_i * 0.88)
        p.drawPolygon(bar(z0, z_m_end, r_m, max(0.02, r_m * 0.48)))
        z_ps, z_pe = g.z1, min(g.z2, g.z1 + 0.38 * (g.z3 - g.z0))
        r_p = max(0.04, r_id_o * 0.86)
        p.drawPolygon(bar(z_ps, z_pe, r_p, max(0.02, r_p * 0.42)))

    def _draw_tube_walls(
        self,
        p: QPainter,
        lay: FeaPassSchematicLayout,
        map_pt: Callable[[float, float], QPointF],
    ) -> None:
        g = lay.die
        z_end = g.z3 + 0.10 * (g.z3 - lay.z_stock_start)
        upper_band, lower_band = tube_wall_band_polygons(
            lay, z0=lay.z_stock_start, z1=z_end, n_seg=36
        )
        p.setPen(QPen(QColor(26, 26, 26), 1.4))
        p.setBrush(QColor(26, 26, 26))
        p.drawPolygon(QPolygonF([map_pt(z, r) for z, r in upper_band]))
        p.drawPolygon(QPolygonF([map_pt(z, r) for z, r in lower_band]))

    def _draw_labels_and_direction(
        self,
        p: QPainter,
        lay: FeaPassSchematicLayout,
        map_pt: Callable[[float, float], QPointF],
        scale: float,
    ) -> None:
        g = lay.die
        label_font = QFont(self.font().family(), 9)
        p.setFont(label_font)
        p.setPen(QPen(QColor(33, 37, 41), 1.0))

        r_od_i = lay.od_in_mm / 2.0
        r_od_o = lay.od_out_mm / 2.0
        z_initial = (lay.z_stock_start + lay.z_swage_start) / 2.0
        z_final = g.z3 + 0.06 * (g.z3 - g.z_bell0)
        z_die = (g.z_bell0 + g.z2) / 2.0

        self._label_with_leader(
            p,
            "Initial tube",
            map_pt(z_initial, r_od_i * 1.35),
            map_pt(z_initial, r_od_i),
        )
        self._label_with_leader(
            p,
            "Final tube",
            map_pt(z_final, r_od_o * 1.35),
            map_pt(z_final, r_od_o),
        )
        self._label_with_leader(
            p,
            "Die",
            map_pt(z_die, lay.r_holder_outer * 1.08),
            map_pt(z_die, g.r_hi + 0.15 * g.wall),
        )

        if lay.tooling != "sink":
            mandrel_lbl = _mandrel_label(lay.tooling)
            z_m = (g.z1 + g.z2) / 2.0
            r_m = max(lay.id_in_mm / 2.0 * 0.5, 0.06)
            self._label_with_leader(
                p,
                mandrel_lbl,
                map_pt(z_m, -r_m - 0.35 * lay.od_in_mm / 2.0),
                map_pt(z_m, -r_m),
            )

        # Drawing direction (bottom, like the video)
        z_a0 = lay.z_plot_min + 0.12 * (lay.z_plot_max - lay.z_plot_min)
        z_a1 = lay.z_plot_min + 0.32 * (lay.z_plot_max - lay.z_plot_min)
        y_arr = map_pt(0.0, -lay.r_plot_max * 0.92).y()
        p0 = QPointF(map_pt(z_a0, 0.0).x(), y_arr)
        p1 = QPointF(map_pt(z_a1, 0.0).x(), y_arr)
        p.setPen(QPen(QColor(33, 37, 41), 1.5))
        p.setBrush(QColor(33, 37, 41))
        p.drawLine(p0, p1)
        self._arrow_head(p, p0, p1, 10.0)
        p.setFont(QFont(self.font().family(), 9))
        p.drawText(
            QRectF(p1.x() + 6, p1.y() - 18, 130, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Drawing direction",
        )

        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(90, 96, 105))
        p.drawText(
            QRectF(8, self.height() - 22, self.width() - 16, 16),
            Qt.AlignmentFlag.AlignRight,
            f"α = {g.alpha_deg:.1f}°  ·  r = {self._spec.area_reduction_fraction:.3f}",
        )

    @staticmethod
    def _label_with_leader(
        p: QPainter, text: str, label_pt: QPointF, target_pt: QPointF
    ) -> None:
        p.drawLine(label_pt, target_pt)
        dx, dy = target_pt.x() - label_pt.x(), target_pt.y() - label_pt.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length > 1e-6:
            ux, uy = dx / length, dy / length
            hw = 4.0
            tip = target_pt
            path = QPainterPath()
            path.moveTo(tip)
            path.lineTo(QPointF(tip.x() - ux * 7 + uy * hw, tip.y() - uy * 7 - ux * hw))
            path.lineTo(QPointF(tip.x() - ux * 7 - uy * hw, tip.y() - uy * 7 + ux * hw))
            path.closeSubpath()
            p.fillPath(path, p.brush())
        p.drawText(
            QRectF(label_pt.x() - 48, label_pt.y() - 10, 96, 20),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )

    @staticmethod
    def _arrow_head(p: QPainter, tail: QPointF, tip: QPointF, head_len: float) -> None:
        dx, dy = tip.x() - tail.x(), tip.y() - tail.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        hw = head_len * 0.42
        path = QPainterPath()
        path.moveTo(tip)
        path.lineTo(QPointF(tip.x() - ux * head_len + px * hw, tip.y() - uy * head_len + py * hw))
        path.lineTo(QPointF(tip.x() - ux * head_len - px * hw, tip.y() - uy * head_len - py * hw))
        path.closeSubpath()
        p.fillPath(path, p.brush())


def _mandrel_label(kind: str) -> str:
    if kind == "floating_plug":
        return "Floating mandrel"
    if kind == "plug":
        return "Fixed mandrel"
    return "Fixed mandrel"
