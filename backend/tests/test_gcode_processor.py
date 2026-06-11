from pathlib import Path

import pytest

from backend.gcode_processor import (
    GcodeValidationError,
    build_footer,
    build_preamble,
    clamp_feed_rates,
    extract_time_metadata,
    insert_layer_depressurize,
    process_gcode,
    strip_footer,
    strip_header,
    substitute_extrusion,
    validate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_time_metadata():
    raw = ";FLAVOR:Marlin\n;TIME:847\nG0 X10"
    assert extract_time_metadata(raw) == 847


def test_extract_time_metadata_missing():
    assert extract_time_metadata("G0 X10\nG1 X20") is None


def test_strip_header():
    lines = [
        ";FLAVOR:Marlin",
        ";TIME:847",
        "M82 ;absolute extrusion mode",
        "G28 ;Home",
        "G92 E0",
        "G0 F600 X10 Y10 Z0.3",
        "G1 F200 X20 Y10 E0.5",
    ]
    result = strip_header(lines)
    assert result[0] == "G0 F600 X10 Y10 Z0.3"
    assert len(result) == 2


def test_strip_footer():
    lines = [
        "G1 F200 X10 Y10 E4.0",
        "M107",
        "M104 S0",
        "M140 S0",
        ";Retract the filament",
        "G92 E1",
        "G1 E-1 F300",
        "G28 X0 Y0",
        "M84",
        "M82 ;absolute extrusion mode",
        "M104 S0",
        ";End of Gcode",
    ]
    result = strip_footer(lines)
    assert len(result) == 1
    assert result[0] == "G1 F200 X10 Y10 E4.0"


def test_substitute_extrusion_left():
    """Left mode: E values should be negated for B axis."""
    lines = ["G1 X10 E0.5 F200", "G1 X20 E1.0 F200"]
    result = substitute_extrusion(lines, "left")
    assert result[0] == "G1 X10 B-0.5 F200"
    assert result[1] == "G1 X20 B-1 F200"


def test_substitute_extrusion_right():
    """Right mode: E values should also be negated for C axis."""
    lines = ["G1 X10 E0.5 F200"]
    result = substitute_extrusion(lines, "right")
    assert result[0] == "G1 X10 C-0.5 F200"


def test_substitute_extrusion_both():
    lines = [
        "G1 X10 E0.5",
        "T1",
        "G1 X20 E1.0",
        "T0",
        "G1 X30 E1.5",
    ]
    result = substitute_extrusion(lines, "both")
    assert "B-0.5" in result[0]  # B is negated
    assert result[1] == "T1"
    assert "C-1" in result[2]    # C is also negated
    assert result[3] == "T0"
    assert "B-1.5" in result[4]  # B is negated


def test_substitute_extrusion_negative_e():
    """Negative E values (retractions) become positive B (retract = opposite of extrude)."""
    lines = ["G1 X10 E-0.5 F200"]
    result = substitute_extrusion(lines, "left")
    assert result[0] == "G1 X10 B0.5 F200"


def test_clamp_feed_rates():
    lines = ["G1 X10 F600", "G1 X20 F350", "G1 X30 F1200"]
    result, log = clamp_feed_rates(lines)
    assert "F400" in result[0]
    assert "F350" in result[1]
    assert "F400" in result[2]
    assert len(log) == 2


def test_clamp_feed_rates_no_change():
    lines = ["G1 X10 F200"]
    result, log = clamp_feed_rates(lines)
    assert result[0] == "G1 X10 F200"
    assert len(log) == 0


def test_build_preamble_left():
    preamble = build_preamble("left")
    non_comment = [l for l in preamble if not l.strip().startswith(";")]
    assert non_comment[0] == "G90"
    joined = "".join(preamble)
    assert "B-0.2" in joined  # B pressurizes in negative direction
    assert "G92 B0" in joined  # reset after pressurization


def test_build_preamble_right():
    preamble = build_preamble("right")
    assert "C-0.2" in "".join(preamble)  # C pressurizes in negative direction


def test_build_footer_left():
    footer = build_footer("left")
    joined = "".join(footer)
    assert "B0.2" in joined  # depressurize (positive = retract for B)
    assert "Z5" in joined  # clearance
    assert "X0 Y0" in joined  # return to origin


def test_build_footer_right():
    footer = build_footer("right")
    joined = "".join(footer)
    assert "C0.2" in joined  # depressurize (positive = retract for C)
    assert "A5" in joined  # right mode uses A axis for Z


def test_validate_passes():
    lines = [
        "; Octaris — preamble",
        "G90 ; absolute positioning",
        "G1 X10 B0.5 F200",
    ]
    validate(lines)  # should not raise


def test_validate_fails_leftover_e():
    lines = [
        "; Octaris — preamble",
        "G90 ; absolute positioning",
        "G1 X10 E0.5 F200",
    ]
    with pytest.raises(GcodeValidationError, match="Unsubstituted E command"):
        validate(lines)


def test_validate_fails_high_f():
    lines = [
        "; Octaris — preamble",
        "G90 ; absolute positioning",
        "G1 X10 B0.5 F600",
    ]
    with pytest.raises(GcodeValidationError, match="exceeds 400"):
        validate(lines)


def test_validate_fails_no_g90():
    lines = ["G91", "G1 X10"]
    with pytest.raises(GcodeValidationError, match="G90"):
        validate(lines)


def test_insert_layer_depressurize_skips_first():
    """First G0 Z is initial positioning — no depressurize."""
    lines = [
        "G0 F300 X10 Y10 Z0.3",
        "G1 F200 X20 Y10 B0.5",
    ]
    result = insert_layer_depressurize(lines, "left")
    assert result[0] == "G0 F300 X10 Y10 Z0.3"
    assert "depressurize" not in " ".join(result)


def test_insert_layer_depressurize_wraps_second():
    """Second G0 Z is a layer change — should be wrapped."""
    lines = [
        "G0 F300 X10 Y10 Z0.3",
        "G1 F200 X20 Y10 B-0.5",
        "G0 F300 X10 Y10 Z0.5",
        "G1 F200 X20 Y10 B-1.0",
    ]
    result = insert_layer_depressurize(lines, "left")
    joined = "\n".join(result)
    assert "depressurize" in joined
    assert "repressurize" in joined
    # The G0 Z0.5 should still be present
    assert "G0 F300 X10 Y10 Z0.5" in joined
    # B0.2 = depressurize (retract), B-0.2 = repressurize (push)
    assert "B0.2" in joined
    assert "B-0.2" in joined


def test_insert_layer_depressurize_right_mode():
    """Right mode uses A axis for Z — should detect G0 with A."""
    lines = [
        "G0 F300 X10 Y10 A0.3",
        "G1 F200 X20 Y10 C-0.5",
        "G0 F300 X10 Y10 A0.5",
        "G1 F200 X20 Y10 C-1.0",
    ]
    result = insert_layer_depressurize(lines, "right")
    joined = "\n".join(result)
    assert "C0.2" in joined   # depressurize (retract = positive for C)
    assert "C-0.2" in joined  # repressurize (push = negative for C)


def test_process_gcode_integration():
    raw = (FIXTURES / "raw_sample.gcode").read_text()
    result = process_gcode(raw, "left")

    assert result.time_estimate_s == 847

    # Preamble should start with G90
    non_comment = [l for l in result.lines if not l.strip().startswith(";")]
    assert non_comment[0].startswith("G90")

    # No E commands should remain
    for line in result.lines:
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        assert "E" not in stripped, f"Unsubstituted E found: {stripped}"

    # F clamping log should have entries (F600 and F1200 in raw)
    assert len(result.feed_log) > 0

    # B substitutions should be negative (extrusion direction)
    found_b = False
    for line in result.lines:
        if "B-0." in line or "B-1." in line or "B-2." in line:
            found_b = True
            break
    assert found_b, "No negative B substitution found"

    # Footer should return to origin
    last_lines = "\n".join(result.lines[-5:])
    assert "X0 Y0" in last_lines


def test_process_gcode_right():
    raw = (FIXTURES / "raw_sample.gcode").read_text()
    result = process_gcode(raw, "right")

    # Should use C axis for extrusion (negated)
    found_c = False
    for line in result.lines:
        if "C-0." in line or "C-1." in line or "C-2." in line:
            found_c = True
            break
    assert found_c, "No negative C substitution found"

    # Footer should use A axis for Z
    last_lines = "\n".join(result.lines[-5:])
    assert "A5" in last_lines
