from __future__ import annotations

import asyncio
import logging
import os
import shutil
import struct
import tempfile
from pathlib import Path

from backend.config import PROJECT_ROOT, load_config
from backend.gcode_processor import PRESSURIZE_MM, ProcessedGcode, SyringeMode, process_gcode

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "context" / "octaris_settings.json"

PRINT_BED_MM = (60.0, 60.0, 60.0)  # X, Y, Z limits (must match octaris_settings.json)

# Layer height as a fraction of nozzle diameter
DEFAULT_LAYER_HEIGHT_RATIO = 0.8


class SlicingError(Exception):
    pass


def _check_stl_dimensions(stl_path: Path) -> None:
    """Raise SlicingError if the STL bounding box exceeds the print bed."""
    data = stl_path.read_bytes()

    vertices: list[tuple[float, float, float]] = []

    # Binary STL: 80-byte header + 4-byte triangle count + N * 50-byte triangles
    # ASCII STL starts with "solid" (but some binary files also start with "solid",
    # so check the size matches the binary layout first).
    is_binary = False
    if len(data) >= 84:
        num_triangles = struct.unpack_from("<I", data, 80)[0]
        expected_size = 84 + num_triangles * 50
        if expected_size == len(data):
            is_binary = True

    if is_binary:
        offset = 84
        for _ in range(num_triangles):
            # skip 12-byte normal, then read 3 vertices of 3 floats each
            offset += 12
            for _ in range(3):
                x, y, z = struct.unpack_from("<fff", data, offset)
                vertices.append((x, y, z))
                offset += 12
            offset += 2  # attribute byte count
    else:
        # ASCII STL: parse "vertex x y z" lines
        for line in data.decode("utf-8", errors="replace").splitlines():
            parts = line.strip().split()
            if len(parts) == 4 and parts[0] == "vertex":
                try:
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except ValueError:
                    pass

    if not vertices:
        raise SlicingError("Could not read any vertices from the STL file.")

    xs, ys, zs = zip(*vertices)
    dims = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    labels = ("X", "Y", "Z")
    for dim, limit, label in zip(dims, PRINT_BED_MM, labels):
        if dim > limit:
            raise SlicingError(
                f"Model is too large for the print bed: "
                f"{label} dimension is {dim:.1f} mm (limit {limit:.0f} mm). "
                f"Print bed is {PRINT_BED_MM[0]:.0f} × {PRINT_BED_MM[1]:.0f} × {PRINT_BED_MM[2]:.0f} mm."
            )


async def slice_stl(
    stl_path: Path,
    syringe_mode: SyringeMode,
    nozzle_diameter: float | None = None,
    syringe_diameter: float | None = None,
    layer_height: float | None = None,
    pressurize_mm: float | None = None,
    flow_multiplier: float | None = None,
    profile_path: Path | None = None,
) -> ProcessedGcode:
    if profile_path is None:
        profile_path = PROFILE_PATH

    if not stl_path.exists():
        raise SlicingError(f"STL file not found: {stl_path}")

    if not profile_path.exists():
        raise SlicingError(f"Slicer profile not found: {profile_path}")

    _check_stl_dimensions(stl_path)

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

    # Derive layer height from nozzle diameter if not explicitly set
    if nozzle_diameter is not None and layer_height is None:
        layer_height = round(nozzle_diameter * DEFAULT_LAYER_HEIGHT_RATIO, 3)

    cmd = [
        cura_bin,
        "slice",
        "-j", str(profile_path),
        "-e0",
    ]
    if syringe_mode == "both":
        cmd.append("-e1")

    # Override nozzle/layer settings on the command line so the static
    # profile doesn't need to be regenerated for each nozzle tip.
    if nozzle_diameter is not None:
        cmd.extend(["-s", f"machine_nozzle_size={nozzle_diameter}"])
        cmd.extend(["-s", f"line_width={nozzle_diameter}"])
    if syringe_diameter is not None:
        cmd.extend(["-s", f"material_diameter={syringe_diameter}"])
        # Ensure material_flow is 100% — the volumetric calculation via
        # material_diameter already accounts for syringe cross-section.
        cmd.extend(["-s", "material_flow=100"])
    if layer_height is not None:
        cmd.extend(["-s", f"layer_height={layer_height}"])
        cmd.extend(["-s", f"layer_height_0={layer_height}"])

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

    return process_gcode(
        raw_gcode,
        syringe_mode,
        pressurize_mm=pressurize_mm or PRESSURIZE_MM,
        flow_multiplier=flow_multiplier or 1.0,
    )
