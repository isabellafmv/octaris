# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Octaris backend (FastAPI + uvicorn).

Produces a single-folder bundle (not one-file) for faster startup.
The Electron app spawns the resulting executable.
"""

import platform
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # octaris/

block_cipher = None

a = Analysis(
    ["backend/main.py"],
    pathex=[str(PROJECT_ROOT / "backend")],
    binaries=[],
    datas=[
        # Slicer profile + CuraEngine definition files
        (str(PROJECT_ROOT / "context"), "context"),
        # App config
        (str(PROJECT_ROOT / "config.json"), "."),
        # CuraEngine binaries (macOS)
        (str(PROJECT_ROOT / "resources" / "bin" / "macos"), str(Path("resources", "bin", "macos"))),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "backend.routers.calibration",
        "backend.routers.extrusion",
        "backend.routers.gcode",
        "backend.routers.jog",
        "backend.routers.print_control",
        "backend.routers.serial",
        "backend.routers.upload",
        "backend.routers.ws",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gi", "gtk", "gobject", "_tkinter", "tkinter"],
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
    name="octaris-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # needed for subprocess stdout/stderr
    target_arch=platform.machine(),  # arm64 on Apple Silicon, x86_64 on Intel
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="octaris-backend",
)
