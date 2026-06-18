#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Octaris — Full Build Script
#
# Builds the complete desktop app:
#   1. PyInstaller bundles the Python/FastAPI backend into a standalone binary
#   2. electron-builder packages Electron + the backend into a macOS .app
#
# Prerequisites:
#   pip install pyinstaller
#   cd client && npm install
#
# Usage:
#   ./build.sh
#
# After building, the .dmg and .zip are in client/dist/.
# For unsigned builds, run this to allow opening:
#   xattr -cr "/path/to/Octaris.app"
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════"
echo "  Octaris Build"
echo "═══════════════════════════════════════════"

# ── Step 1: Build the backend with PyInstaller ──────────────────────────────
echo ""
echo "▸ Building backend..."

cd backend

# Ensure backend dependencies are installed in the active Python env
# (PyInstaller can only bundle packages it can find in the current env)
pip install -e ".[dev]" --quiet

# Clean previous build
rm -rf build dist

pyinstaller octaris-backend.spec --noconfirm

echo "✓ Backend built: dist/octaris-backend/octaris-backend"

# Make CuraEngine executable (PyInstaller doesn't preserve +x on data files)
chmod +x dist/octaris-backend/_internal/resources/bin/macos/CuraEngine 2>/dev/null || true
chmod +x dist/octaris-backend/_internal/resources/bin/macos/UltiMaker-Cura 2>/dev/null || true

cd "$SCRIPT_DIR"

# ── Step 2: Build the Electron app ─────────────────────────────────────────
echo ""
echo "▸ Building Electron app..."

cd client

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
  npm install
fi

# Build renderer + main TypeScript, then package with electron-builder
npm run build:mac

echo ""
echo "═══════════════════════════════════════════"
echo "  Build complete!"
echo "═══════════════════════════════════════════"
echo ""
echo "Outputs in client/dist/:"
ls -lh dist/*.dmg dist/*.zip 2>/dev/null || echo "(check client/dist/ for output files)"
echo ""
echo "To run the unsigned app for the first time:"
echo "  xattr -cr \"$(find dist/mac-arm64 -name '*.app' -maxdepth 1 2>/dev/null | head -1 || echo 'dist/mac-arm64/Octaris.app')\""
echo ""
