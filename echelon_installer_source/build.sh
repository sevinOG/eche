#!/usr/bin/env bash
# Mirror of build.bat — always: python -m PyInstaller
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$SCRIPT_DIR")"
OUT="$SCRIPT_DIR/dist/Echelon-Installer"

if [ -x "$WORKSPACE/echelon_source/.venv/bin/python" ]; then
  PY="$WORKSPACE/echelon_source/.venv/bin/python"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PY="$SCRIPT_DIR/.venv/bin/python"
else
  PY="python3"
fi

echo "[INFO] Using $PY"
"$PY" -c "import PyInstaller" 2>/dev/null || "$PY" -m pip install -q pyinstaller
"$PY" -c "import PyQt6" 2>/dev/null || "$PY" -m pip install -q PyQt6

rm -rf "$SCRIPT_DIR/build" "$SCRIPT_DIR/dist"
cd "$SCRIPT_DIR"
"$PY" -m PyInstaller --noconfirm --clean build.spec

mkdir -p "$SCRIPT_DIR/final"
if [ -f "$SCRIPT_DIR/dist/Echelon-Installer" ]; then
  cp -f "$SCRIPT_DIR/dist/Echelon-Installer" "$SCRIPT_DIR/final/"
elif [ -f "$SCRIPT_DIR/dist/Echelon-Installer/Echelon-Installer" ]; then
  cp -f "$SCRIPT_DIR/dist/Echelon-Installer/Echelon-Installer" "$SCRIPT_DIR/final/"
fi
echo "[DONE] See dist/ and final/"
