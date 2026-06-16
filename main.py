#!/usr/bin/env python3
"""Launch Tubing Master desktop UI."""

from __future__ import annotations

import multiprocessing
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from tubing_master.app_paths import app_icon_path, dispatch_frozen_subprocess_if_needed, is_frozen
from tubing_master.ui.main_window import MainWindow

try:
    from importlib.metadata import version as _pkg_version

    _APP_VERSION = _pkg_version("tubing-master")
except Exception:
    _APP_VERSION = ""


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("Tubing Master")
    app.setOrganizationName("Tubing Master")
    icon_path = app_icon_path()
    if icon_path is not None:
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
    win = MainWindow()
    if icon_path is not None:
        win.setWindowIcon(app_icon)
    title = "Tubing Master"
    if _APP_VERSION:
        title = f"{title} {_APP_VERSION}"
    if is_frozen():
        title = f"{title} (installed)"
    win.setWindowTitle(title)
    win.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    dispatch_frozen_subprocess_if_needed()
    main()
