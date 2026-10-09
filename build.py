#!/usr/bin/env python3
"""Octaris — full build, on macOS or Windows.

Builds the complete desktop app:
  1. PyInstaller bundles the Python/FastAPI backend into a standalone folder
  2. electron-builder packages Electron + the backend (macOS .dmg/.zip,
     Windows NSIS installer)

Prerequisites (in the Python environment you run this with):
  pip install pyinstaller
  Node.js 20 with npm

Usage:
  python build.py [electron-builder options]

Options are passed on to electron-builder, e.g. `python build.py --publish never`
(CI does that on tags, where electron-builder would otherwise try to publish a
GitHub release).

The installers end up in client/dist/.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
CLIENT = ROOT / "client"

# Per OS: the resources/bin folder (config.json's "target"), the binaries that
# must be in it, the npm script that packages the app, and its outputs.
PLATFORMS = {
    "darwin": ("macos", ["CuraEngine", "UltiMaker-Cura"], "build:mac", ["*.dmg", "*.zip"]),
    "win32": ("windows", ["CuraEngine.exe"], "build:win", ["*.exe"]),
}

LFS_POINTER = b"git-lfs.github.com/spec"


def fail(message: str) -> NoReturn:
    print(f"✗ {message}", file=sys.stderr)
    sys.exit(1)


def step(message: str) -> None:
    print(f"\n▸ {message}", flush=True)


def run(cmd: list[str], cwd: Path) -> None:
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        fail(f"{cmd[0]} failed (exit code {result.returncode})")


def check_binaries(target: str, names: list[str]) -> None:
    """Fail early on a missing CuraEngine, or a Git LFS pointer in its place
    (without `git lfs pull` that's what a checkout has, and PyInstaller would
    happily bundle the text file)."""
    bin_dir = ROOT / "resources" / "bin" / target
    for name in names:
        path = bin_dir / name
        if not path.is_file():
            hint = (
                "Copy CuraEngine.exe (and the DLLs next to it) from an UltiMaker Cura 5 install, "
                r"e.g. C:\Program Files\UltiMaker Cura 5.x.x\, into resources\bin\windows\. See README."
                if target == "windows"
                else "See README."
            )
            fail(f"Missing {path.relative_to(ROOT)}. {hint}")
        with path.open("rb") as f:
            if LFS_POINTER in f.read(64):
                fail(f"{path.relative_to(ROOT)} is a Git LFS pointer. Run: git lfs install && git lfs pull")


def npm() -> str:
    # npm is npm.cmd on Windows, which subprocess only finds by its full name
    found = shutil.which("npm")
    if found is None:
        fail("npm not found. Install Node.js 20.")
    return found


def main() -> None:
    if sys.platform not in PLATFORMS:
        fail(f"Building on {sys.platform} isn't supported (macOS and Windows only).")
    target, binaries, npm_script, outputs = PLATFORMS[sys.platform]
    builder_args = sys.argv[1:]

    print("═══════════════════════════════════════════")
    print(f"  Octaris Build ({target})")
    print("═══════════════════════════════════════════")

    check_binaries(target, binaries)

    # ── Step 1: the backend, with PyInstaller ───────────────────────────────
    step("Building backend...")
    # PyInstaller can only bundle packages installed in the Python running it
    run([sys.executable, "-m", "pip", "install", "-e", ".[dev]", "--quiet"], BACKEND)
    for folder in ("build", "dist"):
        shutil.rmtree(BACKEND / folder, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "octaris-backend.spec", "--noconfirm"], BACKEND)

    bundle = BACKEND / "dist" / "octaris-backend"
    executable = bundle / ("octaris-backend.exe" if sys.platform == "win32" else "octaris-backend")
    if not executable.exists():
        fail(f"PyInstaller didn't produce {executable.relative_to(ROOT)}")
    print(f"✓ Backend built: {executable.relative_to(ROOT)}")

    if sys.platform != "win32":
        # PyInstaller doesn't preserve +x on data files
        for name in binaries:
            path = bundle / "_internal" / "resources" / "bin" / target / name
            if path.exists():
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # ── Step 2: the Electron app ────────────────────────────────────────────
    step("Building Electron app...")
    if not (CLIENT / "node_modules").is_dir():
        run([npm(), "install"], CLIENT)
    run([npm(), "run", npm_script, "--", *builder_args], CLIENT)

    print("\n═══════════════════════════════════════════")
    print("  Build complete!")
    print("═══════════════════════════════════════════\n")
    print("Outputs in client/dist/:")
    for pattern in outputs:
        for path in sorted((CLIENT / "dist").glob(pattern)):
            print(f"  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")

    if sys.platform == "darwin":
        app = next((CLIENT / "dist" / "mac-arm64").glob("*.app"), None)
        print("\nTo run the unsigned app for the first time:")
        print(f'  xattr -cr "{app or "dist/mac-arm64/Octaris.app"}"')
    else:
        print("\nThe installer is unsigned: SmartScreen warns on first run (More info → Run anyway).")


if __name__ == "__main__":
    # A Windows console or pipe defaults to a code page without ═ ✓ ✗
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    os.chdir(ROOT)
    main()
