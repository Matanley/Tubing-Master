"""Shared classic engineering-drawing palette for Qt schematic widgets."""

from __future__ import annotations

from typing import Tuple

from PySide6.QtGui import QColor


# Panel / paper
BG = QColor(232, 234, 237)
PLOT_BG = QColor(246, 247, 249)
PLOT_BORDER = QColor(176, 182, 191)

# Tooling metal (hatched die block)
METAL_FILL = QColor(168, 174, 184)
METAL_HATCH = QColor(128, 136, 148)
METAL_EDGE = QColor(64, 70, 80)

# Bore / cavity (die schematic accent)
BORE_FILL = QColor(248, 251, 255)
BORE_EDGE = QColor(13, 71, 161)

# Tube stock (FEA side-view wall bands)
TUBE_FILL = QColor(32, 82, 148)
TUBE_EDGE = QColor(15, 42, 88)

# Engineering accent (α labels, highlights)
ACCENT_BLUE = QColor(13, 71, 161)

# Lines & text
CENTERLINE = QColor(136, 144, 154)
DIM_LINE = QColor(108, 116, 126)
ANNOTATION = QColor(48, 54, 62)
ANNOTATION_MUTED = QColor(108, 116, 126)
PLACEHOLDER = QColor(108, 116, 126)

# Cross-section pass progression (darker Blues ramp — richer than original light palette)
PASS_BLUE_LIGHT: Tuple[int, int, int] = (88, 142, 196)   # ~#5890c4
PASS_BLUE_DARK: Tuple[int, int, int] = (22, 72, 132)     # ~#164884
PASS_EDGE = QColor(15, 42, 88)                             # deep navy

# Incoming / zero-pass preview
INCOMING_FILL = QColor(62, 112, 168)
INCOMING_EDGE = QColor(18, 48, 96)

# Schedule change highlight (warm accent on grey base)
MODIFIED_FILL_LIGHT: Tuple[int, int, int] = (228, 222, 208)
MODIFIED_FILL_DARK: Tuple[int, int, int] = (196, 168, 120)
MODIFIED_EDGE = QColor(120, 92, 48)
