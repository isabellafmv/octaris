from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from backend.config import load_config
from backend.gcode_processor import ProcessedGcode, SyringeMode, process_gcode

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_PATH = PROJECT_ROOT / "context" / "octaris_settings.json"


class SlicingError(Exception):
    pass


async def slice_stl(
    stl_path: Path,
    syringe_mode: SyringeMode,
    profile_path: Path | None = None,
) -> ProcessedGcode:
    if profile_path is None:
        profile_path = PROFILE_PATH

    if not stl_path.exists():
        raise SlicingError(f"STL file not found: {stl_path}")

    if not profile_path.exists():
        raise SlicingError(f"Slicer profile not found: {profile_path}")

    config = load_config()
    bin_dir = PROJECT_ROOT / "resources" / "bin" / config.target
    bundled = next(
        (p for name in ("UltiMaker-Cura", "CuraEngine") if (p := bin_dir / name).exists()),
        None,
    )
    cura_app_bin = Path("/Applications/UltiMaker Cura.app/Contents/Frameworks/CuraEngine")

    if config.target == "rpi" and bundled:
        if not os.access(bundled, os.X_OK):
            os.chmod(bundled, 0o755)
        cura_bin = str(bundled)
    elif cura_app_bin.exists():
        # Use Cura's own binary on macOS — it has its definition files wired up
        cura_bin = str(cura_app_bin)
    elif bundled:
        if not os.access(bundled, os.X_OK):
            os.chmod(bundled, 0o755)
        cura_bin = str(bundled)
    else:
        cura_bin = shutil.which("CuraEngine")
    if cura_bin is None:
        raise SlicingError("CuraEngine not found — place binary in resources/bin/macos/")

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as tmp:
        output_path = Path(tmp.name)

    cmd = [
        cura_bin,
        "slice",
        "-j", str(profile_path),
        "-e0",
    ]
    if syringe_mode == "both":
        cmd.append("-e1")
    cmd.extend(["-o", str(output_path), "-l", str(stl_path)])

    logger.info("Running CuraEngine: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        out = stdout.decode("utf-8", errors="replace").strip()
        logger.error("CuraEngine failed (rc=%d):\nSTDERR: %s\nSTDOUT: %s", proc.returncode, err, out)
        # Skip the version/copyright header lines to surface the real error
        relevant = err or "\n".join(
            line for line in out.splitlines()
            if not any(kw in line for kw in ("version", "Copyright", "GNU", "Free Software", "warranty"))
        )
        raise SlicingError(f"Slicing failed: {(relevant or out)[:1500]}")

    raw_gcode = output_path.read_text()
    output_path.unlink(missing_ok=True)

    logger.info("CuraEngine succeeded. Gcode lines: %d. First 3 lines: %s",
                len(raw_gcode.splitlines()),
                raw_gcode.splitlines()[:3])

    return process_gcode(raw_gcode, syringe_mode)
