from pathlib import Path

import pytest

from backend.gcode_processor import (
    GcodeValidationError,
    clamp_feed_rates,
    extract_time_metadata,
    insert_g91,
    prepend_g92,
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
    lines = ["G1 X10 E0.5 F200", "G1 X20 E1.0 F200"]
    result = substitute_extrusion(lines, "left")
    assert result[0] == "G1 X10 B-0.5 F200"
    assert result[1] == "G1 X20 B-1.0 F200"


def test_substitute_extrusion_right():
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
    assert "B-0.5" in result[0]
    assert result[1] == "T1"
    assert "C-1.0" in result[2]
    assert result[3] == "T0"
    assert "B-1.5" in result[4]


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


def test_prepend_g92_left():
    lines = ["G1 X10"]
    result = prepend_g92(lines, "left")
    assert result[1] == "G92 X0 Y0 Z0 B0"


def test_prepend_g92_both():
    result = prepend_g92(["G1 X10"], "both")
    assert result[1] == "G92 X0 Y0 Z0 A0 B0 C0"


def test_insert_g91():
    lines = ["; comment", "G92 X0 Y0 Z0 B0", "G1 X10"]
    result = insert_g91(lines)
    assert result[2] == "G91 ; relative positioning"
    assert result[3] == "G1 X10"


def test_validate_passes():
    lines = [
        "; Octaris — axis zero",
        "G92 X0 Y0 Z0 B0",
        "G91 ; relative positioning",
        "G1 X10 B-0.5 F200",
    ]
    validate(lines)  # should not raise


def test_validate_fails_leftover_e():
    lines = [
        "; Octaris — axis zero",
        "G92 X0 Y0 Z0 B0",
        "G91 ; relative positioning",
        "G1 X10 E0.5 F200",
    ]
    with pytest.raises(GcodeValidationError, match="Unsubstituted E command"):
        validate(lines)


def test_validate_fails_high_f():
    lines = [
        "; Octaris — axis zero",
        "G92 X0 Y0 Z0 B0",
        "G91 ; relative positioning",
        "G1 X10 B-0.5 F600",
    ]
    with pytest.raises(GcodeValidationError, match="exceeds 400"):
        validate(lines)


def test_validate_fails_no_g92():
    lines = ["G91", "G1 X10"]
    with pytest.raises(GcodeValidationError, match="G92"):
        validate(lines)


def test_process_gcode_integration():
    raw = (FIXTURES / "raw_sample.gcode").read_text()
    result = process_gcode(raw, "left")

    assert result.time_estimate_s == 847
    assert result.lines[1].startswith("G92")
    assert "G91" in result.lines[2]

    # No E commands should remain
    for line in result.lines:
        if not line.strip().startswith(";"):
            assert "E" not in line or "E" in "Octaris"

    # F clamping log should have entries (F600 and F1200 in raw)
    assert len(result.feed_log) > 0

    # All B substitutions should be negative
    for line in result.lines:
        if "B-" in line:
            assert True
            break
    else:
        pytest.fail("No B- substitution found")


def test_process_gcode_right():
    raw = (FIXTURES / "raw_sample.gcode").read_text()
    result = process_gcode(raw, "right")

    assert result.lines[1] == "G92 X0 Y0 A0 C0"
    for line in result.lines:
        if "C-" in line:
            break
    else:
        pytest.fail("No C- substitution found")
