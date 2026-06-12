#!/usr/bin/env bash
# Wrap dist/Tubing Master.app as DMG (drag-to-Applications) and PKG installer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="dist/Tubing Master.app"
if [[ ! -d "$APP" ]]; then
  echo "Missing $APP — run ./packaging/build.sh first." >&2
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS installers can only be built on macOS." >&2
  exit 1
fi

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "/opt/anaconda3/envs/tubing-master/bin/python" ]]; then
    PYTHON="/opt/anaconda3/envs/tubing-master/bin/python"
  else
    PYTHON="python3"
  fi
fi

VERSION="$("$PYTHON" -c "
import re
from pathlib import Path
text = Path('pyproject.toml').read_text(encoding='utf-8')
m = re.search(r'^version\\s*=\\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '0.0.0')
")"
ARCH="$(uname -m)"
STEM="Tubing-Master-${VERSION}-macOS-${ARCH}"
DMG="dist/${STEM}.dmg"
PKG="dist/${STEM}.pkg"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/tubing-master-dmg.XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "Creating macOS installers (version ${VERSION}, ${ARCH})..."

ditto "$APP" "$STAGE/Tubing Master.app"
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create \
  -volname "Tubing Master" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG"

rm -f "$PKG"
pkgbuild \
  --component "$APP" \
  --install-location /Applications \
  --identifier com.tubingmaster.desktop \
  --version "$VERSION" \
  "$PKG"

echo ""
echo "Installers:"
echo "  DMG (recommended): open \"$DMG\""
echo "  PKG (installer):   open \"$PKG\""
du -sh "$DMG" "$PKG"
