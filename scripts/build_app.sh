#!/usr/bin/env bash
# Build a standalone GitOps app with PyInstaller (GitOps.app on macOS, a folder elsewhere).
# Usage: scripts/build_app.sh        Output: dist/GitOps(.app)
set -euo pipefail
cd "$(dirname "$0")/.."

# Qt modules GitOps never imports. Leaving them out cuts the bundle by several hundred MB.
EXCLUDES=(
  PySide6.QtWebEngineCore PySide6.QtWebEngineWidgets PySide6.QtWebEngineQuick PySide6.QtWebChannel
  PySide6.Qt3DCore PySide6.Qt3DRender PySide6.QtQuick PySide6.QtQuick3D PySide6.QtQml PySide6.QtCharts
  PySide6.QtDataVisualization PySide6.QtMultimedia PySide6.QtPdf PySide6.QtPdfWidgets PySide6.QtBluetooth
  PySide6.QtPositioning PySide6.QtSensors PySide6.QtSerialPort PySide6.QtSql PySide6.QtTest
)
args=()
for module in "${EXCLUDES[@]}"; do args+=(--exclude-module "$module"); done

# --paths . : the package is installed editable (an import hook PyInstaller can't follow),
# so point it at the source tree directly.
uv run --with "pyinstaller>=6.10" pyinstaller \
  --noconfirm --clean --windowed \
  --name GitOps \
  --osx-bundle-identifier dev.gitops.app \
  --paths . \
  "${args[@]}" \
  scripts/launch.py

echo "Built: $(ls -d dist/GitOps* | head -1)"
