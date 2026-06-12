#!/usr/bin/env bash
# Build Tubing Master with PyInstaller (macOS → Tubing Master.app, Linux → dist folder).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "/opt/anaconda3/envs/tubing-master/bin/python" ]]; then
    PYTHON="/opt/anaconda3/envs/tubing-master/bin/python"
  else
    PYTHON="python3"
  fi
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python not found: $PYTHON" >&2
  exit 1
fi

echo "Using: $($PYTHON --version) at $(command -v "$PYTHON")"
"$PYTHON" -m pip install -q -r requirements.txt -r requirements-build.txt
"$PYTHON" packaging/generate_icons.py

rm -rf build dist
"$PYTHON" -m PyInstaller packaging/tubing_master.spec --noconfirm --clean

echo ""
echo "Build finished. Output:"
if [[ "$(uname -s)" == "Darwin" ]] && [[ -d "dist/Tubing Master.app" ]]; then
  chmod +x packaging/create_mac_installer.sh
  ./packaging/create_mac_installer.sh
  echo ""
  echo "  App: open \"dist/Tubing Master.app\""
  du -sh "dist/Tubing Master.app"
elif [[ -d "dist/Tubing Master" ]]; then
  echo "  dist/Tubing Master/"
  du -sh "dist/Tubing Master"
fi
