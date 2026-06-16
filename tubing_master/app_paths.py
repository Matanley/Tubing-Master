"""Install paths for dev (repo cwd) vs PyInstaller frozen builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "Tubing Master"


def is_frozen() -> bool:
    """True when running inside a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def dispatch_frozen_subprocess_if_needed() -> None:
    """
    PyInstaller re-executes the app entry point for every ``subprocess`` child.

    Without handling ``-c`` / ``-m`` here, each probe or worker spawn would open
    another full Qt window and recurse indefinitely.
    """
    if not is_frozen():
        return

    argv = sys.argv[1:]
    if not argv:
        return

    if argv[0] == "-c":
        if len(argv) < 2:
            raise SystemExit(2)
        try:
            exec(argv[1], {"__name__": "__main__"})
        except SystemExit:
            raise
        except Exception:
            raise SystemExit(1) from None
        raise SystemExit(0)

    if argv[0] == "-m":
        if len(argv) < 2:
            raise SystemExit(2)
        import runpy

        module = argv[1]
        sys.argv = [module, *argv[2:]]
        try:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        except SystemExit:
            raise
        except Exception:
            raise SystemExit(1) from None
        raise SystemExit(0)

    # Headless worker entry points (if the bundle is invoked without ``-m``).
    if "--worker-pass" in argv or "--worker-schedule" in argv:
        import runpy

        sys.argv = ["tubing_master.fea_tube_die", *argv]
        try:
            runpy.run_module("tubing_master.fea_tube_die", run_name="__main__", alter_sys=True)
        except SystemExit:
            raise
        except Exception:
            raise SystemExit(1) from None
        raise SystemExit(0)

    if "--pass-json" in argv:
        import runpy

        sys.argv = ["tubing_master.damask_support", *argv]
        try:
            runpy.run_module("tubing_master.damask_support", run_name="__main__", alter_sys=True)
        except SystemExit:
            raise
        except Exception:
            raise SystemExit(1) from None
        raise SystemExit(0)


def use_app_data_dirs() -> bool:
    """
    Use OS application-support folders for Projects / suggested_projects.

    Always on for frozen builds; in dev, set ``TUBING_MASTER_APP_DATA=1`` to test.
    """
    if is_frozen():
        return True
    return os.environ.get("TUBING_MASTER_APP_DATA", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def bundle_root() -> Path:
    """Read-only bundle root (``sys._MEIPASS`` when frozen, else package parent)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def user_data_root() -> Path:
    """Writable per-user data root (created on demand)."""
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / _APP_NAME
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) / _APP_NAME if base else Path.home() / "AppData" / "Local" / _APP_NAME
    else:
        root = Path.home() / ".local" / "share" / _APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def projects_dir() -> Path:
    """Project history index, snapshots, and Save-to-History storage."""
    if use_app_data_dirs():
        d = user_data_root() / "Projects"
    else:
        d = Path.cwd() / "Projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def suggested_projects_dir() -> Path:
    """Judgment / Fetch History copies."""
    if use_app_data_dirs():
        d = user_data_root() / "suggested_projects"
    else:
        d = Path.cwd() / "suggested_projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_export_dir() -> Path:
    """Initial folder for Save As / Excel export dialogs."""
    docs = Path.home() / "Documents" / _APP_NAME
    if use_app_data_dirs():
        d = docs
    else:
        d = Path.cwd()
    d.mkdir(parents=True, exist_ok=True)
    return d


def damask_templates_dir() -> Path:
    """Bundled DAMASK template files (read-only)."""
    pkg = Path(__file__).resolve().parent / "damask_templates"
    if pkg.is_dir():
        return pkg
    alt = bundle_root() / "tubing_master" / "damask_templates"
    if alt.is_dir():
        return alt
    return pkg


def app_icon_path() -> Path | None:
    """Window / taskbar icon shipped under ``tubing_master/assets/icon.png``."""
    candidates = [
        Path(__file__).resolve().parent / "assets" / "icon.png",
        bundle_root() / "tubing_master" / "assets" / "icon.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None
