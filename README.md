# Tubing Master

Desktop app for **tube drawing** pass scheduling, analytical stress and safety-factor estimates, multi-pass optimization, process BOM, quotations, and die inventory — built with **Python** and **PySide6**.

## Features

- **Tubing project** — incoming / target geometry, drawing method, material presets (including Nitinol superelastic model)
- **Pass schedule** — manual editing, area-reduction fitting, die-inventory angle matching
- **Optimization** — multi-pass schedule search with [Optuna](https://optuna.org/)
- **Process BOM & quotation** — line items, stock mass, Excel export
- **Die inventory** — semi-die records with schematic views
- **Optional simulation backends** (dev / conda environments):
  - **dolfinx** — axisymmetric tube/die FEA
  - **DAMASK** — crystal-plasticity grain evolution via external `DAMASK_grid`

The packaged desktop build ships the **core** workflow only. FEA and DAMASK require separate installs (see [docs/PACKAGING.md](docs/PACKAGING.md)).

**User guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — workflow, tab-by-tab features, calculation logic, and development roadmap.

## Requirements

- Python **3.10+**
- See [requirements.txt](requirements.txt) for runtime dependencies

Optional:

- [requirements-damask.txt](requirements-damask.txt) — DAMASK-related Python deps
- dolfinx / FEniCSx — conda-forge only; not in the default pip set

## Run from source

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Or with conda:

```bash
conda create -n tubing-master python=3.12
conda activate tubing-master
pip install -r requirements.txt
python main.py
```

## Tests

```bash
pip install pytest
pytest tests/
```

## Build installers

macOS and Windows desktop bundles are built with PyInstaller. See **[docs/PACKAGING.md](docs/PACKAGING.md)** for:

- `./packaging/build.sh` (macOS → `.app`, `.dmg`, `.pkg`)
- `packaging\build.bat` (Windows → folder + `.exe`)
- CI artifacts on version tags (`v*`)

## Project layout

| Path | Purpose |
|------|---------|
| `tubing_master/` | Application package |
| `tubing_master/ui/` | Qt main window and widgets |
| `docs/` | User guide, packaging notes |
| `packaging/` | PyInstaller spec, icons, build scripts |
| `tests/` | Unit tests |
| `Projects/` | Example / local project history (dev mode) |

When installed as a frozen app, user data is stored under the OS application-support folder (see packaging doc).

## License

[MIT](LICENSE)
