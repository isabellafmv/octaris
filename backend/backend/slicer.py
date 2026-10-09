from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import TypedDict

from backend.config import PROJECT_ROOT, load_config
from backend.gcode_processor import MAX_FEED, PRESSURIZE_MM, ProcessedGcode, SyringeMode, process_gcode

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "context" / "octaris_settings.json"

PRINT_BED_MM = (60.0, 60.0, 60.0)  # X, Y, Z limits (must match octaris_settings.json)

# Layer height as a fraction of nozzle diameter
DEFAULT_LAYER_HEIGHT_RATIO = 0.8

# Set to the print speed: speed_print and speed_travel, and the speeds Cura
# derives from them. CuraEngine doesn't evaluate the definitions' formulas, so
# these would otherwise keep fdmprinter's defaults (walls at 30-60 mm/s).
# Layer-change Z moves keep speed_z_hop; the feed cap clamps them.
PRINT_SPEED_SETTINGS = (
    "speed_print",
    "speed_travel",
    "speed_infill",
    "speed_wall",
    "speed_wall_0",
    "speed_wall_x",
    "speed_wall_0_roofing",
    "speed_wall_x_roofing",
    "speed_wall_0_flooring",
    "speed_wall_x_flooring",
    "speed_topbottom",
    "speed_roofing",
    "speed_flooring",
    "speed_support",
    "speed_support_infill",
    "speed_support_interface",
    "speed_support_roof",
    "speed_support_bottom",
    "speed_prime_tower",
    "speed_ironing",
    "speed_layer_0",
    "speed_print_layer_0",
    "speed_travel_layer_0",
    "skirt_brim_speed",
)


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

    xs, ys, zs = zip(*vertices, strict=True)
    dims = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    labels = ("X", "Y", "Z")
    for dim, limit, label in zip(dims, PRINT_BED_MM, labels, strict=True):
        if dim > limit:
            raise SlicingError(
                f"Model is too large for the print bed: "
                f"{label} dimension is {dim:.1f} mm (limit {limit:.0f} mm). "
                f"Print bed is {PRINT_BED_MM[0]:.0f} × {PRINT_BED_MM[1]:.0f} × {PRINT_BED_MM[2]:.0f} mm."
            )


def _check_3mf(path: Path) -> None:
    """Validate that the file is a readable 3MF archive with a model inside."""
    if not zipfile.is_zipfile(path):
        raise SlicingError("File is not a valid 3MF archive.")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if not any(n.endswith(".model") for n in names):
            raise SlicingError("3MF archive does not contain a 3D model file.")


def _windows_cura_installs() -> list[Path]:
    """CuraEngine.exe of each UltiMaker Cura install under Program Files, newest first."""
    roots = {root for var in ("ProgramFiles", "ProgramW6432") if (root := os.environ.get(var))}
    found = {
        exe
        for root in roots
        for exe in Path(root).glob("*/CuraEngine.exe")
        if exe.parent.name.lower().startswith("ultimaker cura")
    }

    def version(exe: Path) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", exe.parent.name))

    return sorted(found, key=version, reverse=True)


def _find_cura_engine() -> str:
    """Locate the CuraEngine binary, raising SlicingError if not found."""
    config = load_config()
    bin_dir = PROJECT_ROOT / "resources" / "bin" / config.target

    if config.target == "windows":
        exe = next((p for p in [bin_dir / "CuraEngine.exe", *_windows_cura_installs()] if p.exists()), None)
        if exe:
            return str(exe)
        found = shutil.which("CuraEngine")
        if found is None:
            raise SlicingError(
                f"CuraEngine not found — place CuraEngine.exe in {bin_dir} or install UltiMaker Cura"
            )
        return found

    bundled = next(
        (p for name in ("UltiMaker-Cura", "CuraEngine") if (p := bin_dir / name).exists()),
        None,
    )
    cura_app_bin = Path("/Applications/UltiMaker Cura.app/Contents/Frameworks/CuraEngine")

    if config.target == "rpi" and bundled:
        if not os.access(bundled, os.X_OK):
            os.chmod(bundled, 0o755)
        return str(bundled)
    if cura_app_bin.exists():
        return str(cura_app_bin)
    if bundled:
        if not os.access(bundled, os.X_OK):
            os.chmod(bundled, 0o755)
        return str(bundled)
    found = shutil.which("CuraEngine")
    if found is None:
        raise SlicingError(f"CuraEngine not found — place binary in {bin_dir}")
    return found


# CuraEngine processes still running, so a shutdown can stop them
_running: set[asyncio.subprocess.Process] = set()


async def stop_slicing() -> None:
    """Kill any CuraEngine still running; its slice_model() raises SlicingError."""
    for proc in list(_running):
        if proc.returncode is None:
            logger.info("Stopping CuraEngine (pid %d)", proc.pid)
            proc.kill()
    for proc in list(_running):
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("CuraEngine (pid %d) did not exit", proc.pid)


class PrintSettings(TypedDict, total=False):
    """The print settings an upload may override; None keeps the profile's value."""

    nozzle_diameter: float | None
    syringe_diameter: float | None
    layer_height: float | None
    pressurize_mm: float | None
    flow_multiplier: float | None
    travel_retract_multiplier: float | None
    # mm/s, for both printing and travel moves
    print_speed: float | None


async def slice_model(
    model_path: Path,
    syringe_mode: SyringeMode,
    nozzle_diameter: float | None = None,
    syringe_diameter: float | None = None,
    layer_height: float | None = None,
    pressurize_mm: float | None = None,
    flow_multiplier: float | None = None,
    travel_retract_multiplier: float | None = None,
    print_speed: float | None = None,
    max_feed: float = MAX_FEED,
    profile_path: Path | None = None,
) -> ProcessedGcode:
    """Slice an STL or 3MF file and return processed G-code.

    Accepts both .stl and .3mf inputs.  For 3MF files the extruder-to-mesh
    mapping embedded in the archive is used by CuraEngine, so multi-material
    prints are supported when syringe_mode is "both".

    `print_speed` (mm/s) sets Cura's speed_print and speed_travel (see
    PRINT_SPEED_SETTINGS); `max_feed` (mm/min) is the cap every feed rate
    is clamped to.
    """
    if profile_path is None:
        profile_path = PROFILE_PATH

    if not model_path.exists():  # noqa: ASYNC240 - a quick metadata call
        raise SlicingError(f"Model file not found: {model_path}")

    if not profile_path.exists():
        raise SlicingError(f"Slicer profile not found: {profile_path}")

    is_3mf = model_path.suffix.lower() == ".3mf"

    if is_3mf:
        _check_3mf(model_path)
    else:
        _check_stl_dimensions(model_path)

    cura_bin = _find_cura_engine()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as tmp:
        output_path = Path(tmp.name)

    # Derive layer height from nozzle diameter if not explicitly set
    if nozzle_diameter is not None and layer_height is None:
        layer_height = round(nozzle_diameter * DEFAULT_LAYER_HEIGHT_RATIO, 3)

    # Only use dual-extruder slicing for 3MF files (which carry material
    # assignments).  Single STL in "both" mode is sliced as single-extruder;
    # gcode_processor mirrors the extrusion to both axes in post-processing.
    dual = is_3mf and syringe_mode == "both"
    extruder_count = 2 if dual else 1

    extruder_left_path = profile_path.parent / "octaris_extruder_left.def.json"
    extruder_right_path = profile_path.parent / "octaris_extruder_right.def.json"

    cmd = [
        cura_bin,
        "slice",
        "-j",
        str(profile_path),
        "-s",
        f"machine_extruder_count={extruder_count}",
    ]

    # Extruder 0 settings
    cmd.append("-e0")
    if dual and extruder_left_path.exists():
        cmd.extend(["-j", str(extruder_left_path)])

    # Override nozzle/layer settings on the command line so the static
    # profile doesn't need to be regenerated for each nozzle tip.
    if nozzle_diameter is not None:
        cmd.extend(["-s", f"machine_nozzle_size={nozzle_diameter}"])
        cmd.extend(["-s", f"line_width={nozzle_diameter}"])
    if syringe_diameter is not None:
        cmd.extend(["-s", f"material_diameter={syringe_diameter}"])
        cmd.extend(["-s", "material_flow=100"])
    if layer_height is not None:
        cmd.extend(["-s", f"layer_height={layer_height}"])
        cmd.extend(["-s", f"layer_height_0={layer_height}"])
    if print_speed is not None:
        for setting in PRINT_SPEED_SETTINGS:
            cmd.extend(["-s", f"{setting}={print_speed}"])

    if dual:
        # Extruder 1 with its definition and same settings
        cmd.append("-e1")
        if extruder_right_path.exists():
            cmd.extend(["-j", str(extruder_right_path)])
        if nozzle_diameter is not None:
            cmd.extend(["-s", f"machine_nozzle_size={nozzle_diameter}"])
            cmd.extend(["-s", f"line_width={nozzle_diameter}"])
        if syringe_diameter is not None:
            cmd.extend(["-s", f"material_diameter={syringe_diameter}"])
            cmd.extend(["-s", "material_flow=100"])
        if layer_height is not None:
            cmd.extend(["-s", f"layer_height={layer_height}"])
            cmd.extend(["-s", f"layer_height_0={layer_height}"])
        if print_speed is not None:
            for setting in PRINT_SPEED_SETTINGS:
                cmd.extend(["-s", f"{setting}={print_speed}"])

    cmd.extend(["-o", str(output_path), "-l", str(model_path)])

    logger.info("Running CuraEngine: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _running.add(proc)
    try:
        stdout, stderr = await proc.communicate()
    finally:
        _running.discard(proc)

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        out = stdout.decode("utf-8", errors="replace").strip()
        logger.error("CuraEngine failed (rc=%d):\nSTDERR: %s\nSTDOUT: %s", proc.returncode, err, out)
        relevant = err or "\n".join(
            line
            for line in out.splitlines()
            if not any(kw in line for kw in ("version", "Copyright", "GNU", "Free Software", "warranty"))
        )
        raise SlicingError(f"Slicing failed: {(relevant or out)[:1500]}")

    raw_gcode = await asyncio.to_thread(output_path.read_text)
    output_path.unlink(missing_ok=True)  # noqa: ASYNC240 - a quick metadata call

    logger.info(
        "CuraEngine succeeded. Gcode lines: %d. First 3 lines: %s",
        len(raw_gcode.splitlines()),
        raw_gcode.splitlines()[:3],
    )

    return process_gcode(
        raw_gcode,
        syringe_mode,
        pressurize_mm=pressurize_mm or PRESSURIZE_MM,
        flow_multiplier=flow_multiplier or 1.0,
        travel_retract_multiplier=travel_retract_multiplier or 3.0,
        max_feed=max_feed,
    )


# Keep old name as alias for backward compatibility
slice_stl = slice_model
