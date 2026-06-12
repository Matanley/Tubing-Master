# PyInstaller spec — Tubing Master (macOS .app / Windows folder + .exe)
# Run from repo root: pyinstaller packaging/tubing_master.spec --noconfirm

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

REPO = Path(SPECPATH).resolve().parent
ENTRY = REPO / "main.py"
TEMPLATES = REPO / "tubing_master" / "damask_templates"
ICON_DIR = REPO / "packaging" / "icons"
ICON_ICNS = ICON_DIR / "icon.icns"
ICON_ICO = ICON_DIR / "icon.ico"
ICON_FILE = ICON_ICO if sys.platform == "win32" else ICON_ICNS
ICON_ARG = str(ICON_FILE) if ICON_FILE.is_file() else None

pyside_datas, pyside_binaries, pyside_hidden = collect_all("PySide6")

datas = list(pyside_datas)
if TEMPLATES.is_dir():
    datas.append((str(TEMPLATES), "tubing_master/damask_templates"))

hiddenimports = (
    collect_submodules("tubing_master")
    + list(pyside_hidden)
    + [
        "optuna.samplers._tpe.sampler",
        "sqlalchemy.sql.default_comparator",
    ]
)

excludes = [
    "matplotlib",
    "dolfinx",
    "fenics",
    "mpi4py",
    "petsc4py",
    "vtk",
    "PyQt5",
    "PyQt6",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(REPO)],
    binaries=pyside_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tubing Master",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ARG,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Tubing Master",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Tubing Master.app",
        icon=ICON_ARG,
        bundle_identifier="com.tubingmaster.desktop",
        info_plist={
            "CFBundleName": "Tubing Master",
            "CFBundleDisplayName": "Tubing Master",
            "NSHighResolutionCapable": True,
        },
    )
