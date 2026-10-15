#!/usr/bin/env bash
set -euo pipefail

# Octaris — full build. The work is done by build.py (which also runs on
# Windows); this wrapper is kept so `./build.sh` keeps working.
#
# Usage:
#   ./build.sh [electron-builder options]     e.g. ./build.sh --publish never

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/build.py" "$@"
