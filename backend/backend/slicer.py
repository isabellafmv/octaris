from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from backend.gcode_processor import ProcessedGcode, SyringeMode, process_gcode

logger = logging.getLogger(__name__)

PROFILE_PATH = Path(__file__).resolve().parent.parent.parent / "context" / "octaris_settings.curaprofile"


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

    cura_bin = shutil.which("CuraEngine")
    if cura_bin is None:
        raise SlicingError("CuraEngine not found on PATH")

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
        error_msg = stderr.decode("utf-8", errors="replace")
        logger.error("CuraEngine failed: %s", error_msg)
        raise SlicingError(f"Slicing failed: {error_msg[:200]}")

    raw_gcode = output_path.read_text()
    output_path.unlink(missing_ok=True)

    return process_gcode(raw_gcode, syringe_mode)
