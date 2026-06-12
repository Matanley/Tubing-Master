# Packaging Tubing Master (PyInstaller)

Ship a **double-click desktop app** on macOS and Windows without asking users to install conda.

## What gets bundled (Tier 1 — core)

- Tubing Project, Pass Schedule, Optimization (Optuna), Process BOM, Quotation, Die inventory
- Native Qt diagrams (no matplotlib)
- Excel export (openpyxl)

## What is **not** bundled

| Feature | Reason |
|---------|--------|
| **dolfinx / FEA** | Conda-only stack (MPI, PETSc); too large and fragile to freeze |
| **DAMASK** | External `DAMASK_grid` binary; install separately if needed |

The FEA tab will report dolfinx as unavailable in the packaged app unless you ship a custom build that includes it.

## Prerequisites

- Python **3.10+** with dependencies:

```bash
pip install -r requirements.txt -r requirements-build.txt
```

Or use your conda env:

```bash
conda activate tubing-master
pip install -r requirements-build.txt
```

## Build

Icons are generated automatically from `packaging/icons/icon_1024.png`:

```bash
python packaging/generate_icons.py
```

This applies a **macOS-style squircle** (rounded square) and inset safe-area padding so the tube/die graphic is not clipped in the Dock. Outputs:

- `icon_mac_square.png` — square master for `.icns` (macOS applies its own mask)
- `icon_squircle_preview.png` — preview of the rounded-square look
- `icon.icns`, `icon.ico`, `tubing_master/assets/icon.png`

To replace the artwork, edit `icon_1024.png` and re-run the script (or `./packaging/build.sh`).

### macOS / Linux

```bash
chmod +x packaging/build.sh
./packaging/build.sh
```

- **macOS app:** `dist/Tubing Master.app`
- **macOS installers** (created automatically after the app build):
  - **DMG** (recommended): `dist/Tubing-Master-<version>-macOS-<arch>.dmg` — open the disk image, drag **Tubing Master** into **Applications**
  - **PKG**: `dist/Tubing-Master-<version>-macOS-<arch>.pkg` — double-click to run the macOS installer (installs to `/Applications`)
- **Linux:** `dist/Tubing Master/` (run `Tubing Master` inside)

To rebuild installers only (after `dist/Tubing Master.app` already exists):

```bash
./packaging/create_mac_installer.sh
```

### Windows

```cmd
packaging\build.bat
```

Output: `dist\Tubing Master\Tubing Master.exe`

### Manual PyInstaller

From the repo root:

```bash
pyinstaller packaging/tubing_master.spec --noconfirm --clean
```

## User data locations (installed app)

When running a **frozen** build, projects are **not** stored next to the `.exe` / `.app`:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Tubing Master/Projects` |
| Windows | `%LOCALAPPDATA%\Tubing Master\Projects` |
| Linux | `~/.local/share/Tubing Master/Projects` |

Judgment / Fetch History copies: `.../suggested_projects/` under the same root.

Export dialogs default to `~/Documents/Tubing Master/` (created if missing).

**Development** (running `python main.py` from the repo) still uses `./Projects` and `./suggested_projects` unless you set:

```bash
export TUBING_MASTER_APP_DATA=1   # test installed-style paths
```

## Distribution checklist

1. **Smoke test** the built app: launch, set geometry, run optimization, refresh BOM, export Excel.
2. **macOS:** code-sign and **notarize** the `.app` for Gatekeeper (Apple Developer account).
3. **Windows:** optional Authenticode signing on the `.exe`.
4. **DMG / PKG:** built automatically on macOS via `packaging/create_mac_installer.sh` (sign/notarize before shipping).
5. **Windows MSI:** optional Inno Setup or WiX wrapper around `dist/Tubing Master/`.
6. **CI:** see `.github/workflows/package.yml` (if present) for automated builds.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: optuna` | Build with the same Python you use for development; reinstall `requirements.txt`. |
| Blank window / Qt plugins | Spec uses `collect_all('PySide6')`; rebuild with PyInstaller ≥ 6. |
| FEA subprocess fails | Expected without dolfinx; use conda dev env for FEA work. |
| Huge bundle size | Normal for PySide6 (~150–300 MB). Exclude matplotlib/dolfinx already in spec. |

## Version

Application version is set in `pyproject.toml` (`project.version`).
