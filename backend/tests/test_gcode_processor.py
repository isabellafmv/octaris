from pathlib import Path

import pytest

from backend.gcode_processor import (
    GcodeValidationError,
    build_footer,
    build_preamble,
    clamp_feed_rates,
    extract_time_metadata,
    insert_layer_depressurize,
    insert_travel_retract,
    parse,
    process_gcode,
    render,
    scale_flow,
    substitute_extrusion,
    to_relative,
    trim_to_print,
    validate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def run(step, lines, *args):
    """Run a processing step on G-code text, returning text."""
    return render(step(parse(lines), *args))


def test_extract_time_metadata():
    raw = ";FLAVOR:Marlin\n;TIME:847\nG0 X10"
    assert extract_time_metadata(raw) == 847


def test_extract_time_metadata_missing():
    assert extract_time_metadata("G0 X10\nG1 X20") is None


def test_trim_drops_start_code():
    lines = [
        ";FLAVOR:Marlin",
        ";TIME:847",
        "M82 ;absolute extrusion mode",
        "G28 ;Home",
        "G92 E0",
        "G0 F600 X10 Y10 Z0.3",
        "G1 F200 X20 Y10 E0.5",
    ]
    result = run(trim_to_print, lines)
    # The modes in effect at the start are kept, Marlin's defaults if unset.
    assert result == ["G90", "M82 ;absolute extrusion mode", *lines[-2:]]


def test_trim_keeps_cura_relative_extrusion_mode():
    lines = ["M83 ;relative extrusion mode", "G92 E0", "G0 F600 X10 Y10 Z0.3", "G1 X20 E0.5"]
    assert run(trim_to_print, lines) == ["G90", "M83 ;relative extrusion mode", *lines[2:]]


def test_trim_drops_end_code():
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
    result = run(trim_to_print, lines)
    assert result == ["G90", "M82", "G1 F200 X10 Y10 E4.0"]


def test_substitute_extrusion_left():
    """Left mode: E values should be negated for B axis."""
    lines = ["G1 X10 E0.5 F200", "G1 X20 E1.0 F200"]
    result = run(substitute_extrusion, lines, "left")
    assert result[0] == "G1 X10 B-0.5 F200"
    assert result[1] == "G1 X20 B-1 F200"


def test_substitute_extrusion_right():
    """Right mode: E values should also be negated for C axis."""
    lines = ["G1 X10 E0.5 F200"]
    result = run(substitute_extrusion, lines, "right")
    assert result[0] == "G1 X41 C-0.5 F200"  # X10 + 31mm nozzle offset


def test_substitute_extrusion_both():
    lines = [
        "G1 X10 E0.5",
        "T1",
        "G1 X20 E1.0",
        "T0",
        "G1 X30 E1.5",
    ]
    result = run(substitute_extrusion, lines, "both")
    assert "B-0.5" in result[0]  # B is negated
    assert result[1] == "T1"
    assert "C-1" in result[2]  # C is also negated
    assert result[3] == "T0"
    assert "B-1.5" in result[4]  # B is negated


def test_substitute_extrusion_negative_e():
    """Negative E values (retractions) become positive B (retract = opposite of extrude)."""
    lines = ["G1 X10 E-0.5 F200"]
    result = run(substitute_extrusion, lines, "left")
    assert result[0] == "G1 X10 B0.5 F200"


def test_clamp_feed_rates():
    lines = ["G1 X10 F600", "G1 X20 F350", "G1 X30 F1200"]
    cmds, log = clamp_feed_rates(parse(lines))
    result = render(cmds)
    assert "F400" in result[0]
    assert "F350" in result[1]
    assert "F400" in result[2]
    assert len(log) == 2


def test_clamp_feed_rates_no_change():
    lines = ["G1 X10 F200"]
    cmds, log = clamp_feed_rates(parse(lines))
    result = render(cmds)
    assert result[0] == "G1 X10 F200"
    assert len(log) == 0


def test_build_preamble_left():
    preamble = build_preamble("left")
    non_comment = [line for line in preamble if not line.strip().startswith(";")]
    assert non_comment[0] == "G91"
    assert "G90" not in preamble
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
        "G91 ; relative positioning",
        "G1 X10 B0.5 F200",
    ]
    validate(parse(lines))  # should not raise


def test_validate_fails_leftover_e():
    lines = [
        "; Octaris — preamble",
        "G91 ; relative positioning",
        "G1 X10 E0.5 F200",
    ]
    with pytest.raises(GcodeValidationError, match="Unsubstituted E command"):
        validate(parse(lines))


def test_validate_fails_high_f():
    lines = [
        "; Octaris — preamble",
        "G91 ; relative positioning",
        "G1 X10 B0.5 F600",
    ]
    with pytest.raises(GcodeValidationError, match="exceeds 400"):
        validate(parse(lines))


def test_validate_fails_no_g91():
    lines = ["G90", "G1 X10"]
    with pytest.raises(GcodeValidationError, match="must start with G91"):
        validate(parse(lines))


@pytest.mark.parametrize("absolute", ["G90", "G53"])
def test_validate_rejects_absolute_moves_after_the_start(absolute):
    lines = ["G91", "G1 X10", absolute, "G1 X0"]
    with pytest.raises(GcodeValidationError, match=f"Line 3: {absolute} .* must stay relative"):
        validate(parse(lines))


def test_insert_layer_depressurize_skips_first():
    """First G0 Z is initial positioning — no depressurize."""
    lines = [
        "G0 F300 X10 Y10 Z0.3",
        "G1 F200 X20 Y10 B0.5",
    ]
    result = run(insert_layer_depressurize, lines, "left")
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
    result = run(insert_layer_depressurize, lines, "left")
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
    result = run(insert_layer_depressurize, lines, "right")
    joined = "\n".join(result)
    assert "C0.2" in joined  # depressurize (retract = positive for C)
    assert "C-0.2" in joined  # repressurize (push = negative for C)


def test_process_gcode_integration():
    raw = (FIXTURES / "raw_sample.gcode").read_text()
    result = process_gcode(raw, "left")

    assert result.time_estimate_s == 847

    # Relative from the first line on, and never absolute again
    non_comment = [line for line in result.lines if not line.strip().startswith(";")]
    assert non_comment[0].startswith("G91")
    assert not any(line.startswith(("G90", "G92")) for line in non_comment)
    assert result.start_position == {"X": 0, "Y": 0, "Z": 0}

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

    # Footer should return to origin: back from where the print ended
    assert result.lines[-1] == "G1 X-10 Y-10 F300 ; return to origin"
    assert result.state_after[-1].pos["X"] == result.state_after[-1].pos["Y"] == 0


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


def test_insert_travel_retract_wraps_g0():
    """In-layer G0 travel moves should get retract/prime brackets."""
    lines = [
        "G1 X10 Y10 B-0.5 F200",
        "G0 X50 Y50 F300",  # travel move — should trigger retract
        "G1 X60 Y60 B-1.0 F200",
    ]
    result = run(insert_travel_retract, lines, "left")
    joined = "\n".join(result)
    # Should have retract before travel and prime after
    assert "; retract" in joined
    assert "; prime" in joined
    # B0.2 = retract (opposite of extrude), B-0.2 = prime
    assert "B0.2" in joined
    assert "B-0.2" in joined


def test_insert_travel_retract_skips_layer_change():
    """G0 moves with Z (layer changes) should NOT get retract brackets."""
    lines = [
        "G1 X10 Y10 B-0.5 F200",
        "G0 X50 Y50 Z0.5 F300",  # layer change — skip
        "G1 X60 Y60 B-1.0 F200",
    ]
    result = run(insert_travel_retract, lines, "left")
    joined = "\n".join(result)
    assert "; retract" not in joined
    assert "; prime" not in joined


def test_insert_travel_retract_consecutive_g0():
    """Consecutive G0 moves should only retract once."""
    lines = [
        "G1 X10 Y10 B-0.5 F200",
        "G0 X30 Y30 F300",
        "G0 X50 Y50 F300",  # second travel — already retracted
        "G1 X60 Y60 B-1.0 F200",
    ]
    result = run(insert_travel_retract, lines, "left")
    assert result.count("G1 B0.2 F400 ; retract B") == 1
    assert result.count("G1 B-0.2 F400 ; prime B") == 1


def test_substitute_both_mirror():
    """Single STL in 'both' mode: E values duplicated to B and C."""
    lines = ["G1 X10 E0.5 F200", "G0 X20 Y20 F300"]
    result = run(substitute_extrusion, lines, "both")
    assert "B-0.5" in result[0]
    assert "C-0.5" in result[0]
    # G0 travel has no E — should pass through unchanged
    assert result[1] == "G0 X20 Y20 F300"


def test_substitute_both_multi():
    """3MF multi-material: T0→B, T1→C with X offset."""
    lines = [
        "T0",
        "G1 X10 E0.5 F200",
        "T1",
        "G1 X10 E0.3 F200",
    ]
    result = run(substitute_extrusion, lines, "both")
    # T0 section → B axis, no X offset
    assert "B-0.5" in result[1]
    assert "X10" in result[1]
    # T1 section → C axis, X shifted by NOZZLE_OFFSET_X
    assert "C-0.3" in result[3]
    assert "X41" in result[3]  # 10 + 31mm offset


def test_build_preamble_both():
    """Both mode should pressurize B and C."""
    from backend.gcode_processor import build_preamble

    lines = build_preamble("both")
    joined = "\n".join(lines)
    assert "B-" in joined  # pressurize B (negative direction)
    assert "C-" in joined  # pressurize C (negative direction)
    assert "G92 B0" in joined
    assert "G92 C0" in joined


def test_build_footer_both():
    """Both mode should depressurize B and C."""
    from backend.gcode_processor import build_footer

    lines = build_footer("both")
    joined = "\n".join(lines)
    # Depressurize is opposite of extrude: positive for B/C
    assert "depressurize B" in joined
    assert "depressurize C" in joined


def test_insert_travel_retract_both():
    """Both mode should retract/prime both B and C."""
    lines = [
        "G1 X10 B-0.5 C-0.5 F200",
        "G0 X50 F300",
        "G1 X60 B-1.0 C-1.0 F200",
    ]
    result = run(insert_travel_retract, lines, "both")
    joined = "\n".join(result)
    assert "; retract B" in joined
    assert "; retract C" in joined
    assert "; prime B" in joined
    assert "; prime C" in joined


def test_numbers_without_leading_zero():
    """Cura writes e.g. "X-.5" and "E.01234"; both must be converted."""
    result = run(substitute_extrusion, ["G1 X-.5 Y1 E.01234 F200"], "right")
    assert result == ["G1 X30.5 Y1 C-0.01234 F200"]
    with pytest.raises(GcodeValidationError, match="Unsubstituted E"):
        validate(parse(["G91", "G1 X1 E.5"]))


def test_trim_drops_any_end_code_after_the_last_move():
    lines = [
        "G0 F300 X1 Y1 Z0.2",
        "G1 F200 X2 Y1 E0.5",
        ";TIME_ELAPSED:12.3",
        "M82 ;absolute extrusion mode",
        ";End of Gcode",
    ]
    assert run(trim_to_print, lines) == ["G90", "M82", *lines[:2]]


def test_unchanged_lines_are_written_back_verbatim():
    lines = ["G0 F300 X1  Y1 Z0.20 ; start", "G1 F200 X2 Y1 B-.5"]
    assert run(substitute_extrusion, lines, "left") == lines


def test_scale_flow():
    assert scale_flow("G1 F200 X10 Y20 B-1.5", 0.8) == "G1 F200 X10 Y20 B-1.2"
    assert scale_flow("G1 B-0.2 F400 ; pressurize B", 0.5) == "G1 B-0.1 F400 ; pressurize B"
    assert scale_flow("G1 X1 Y1", 0.5) == "G1 X1 Y1"
    assert scale_flow("G1 B-1.5", 1.0) == "G1 B-1.5"
    # Fixed point, never an exponent Marlin can't read, and no digits lost
    assert scale_flow("G1 X1 B-0.00005", 0.8) == "G1 X1 B-0.00004"
    assert scale_flow("G1 X1 B-10.55675", 1.5) == "G1 X1 B-15.835125"
    # Only moves: a coordinate reset or a setting isn't a distance to push
    assert scale_flow("G92 B-4", 0.5) == "G92 B-4"
    assert scale_flow("M92 B800", 0.5) == "M92 B800"


# --- relative conversion -----------------------------------------------------------


def test_to_relative_writes_distances_behind_one_g91():
    lines = ["G90", "M82", "G0 F300 X10 Y10 Z0.3", "G1 X20 Y10 B-0.5 F200", "G1 X20.25 Y5 B-0.75"]
    assert run(to_relative, lines) == [
        "G91 ; relative positioning for the whole print",
        "G0 F300 X10 Y10 Z0.3",
        "G1 X10 B-0.5 F200",  # unchanged axes are left out
        "G1 X0.25 Y-5 B-0.25",
    ]


def test_to_relative_follows_the_programs_modes():
    lines = [
        "G90",
        "M83",  # B carries relative E
        "G1 X5 B-0.5",
        "G1 X6 B-0.5",
        "G91",  # everything relative
        "G1 X1 B0.2",
        "G90",  # positioning absolute again; B stays relative (M83)
        "G1 X10 B-0.2",
        "M82",
        "G92 B0",  # absolute B, counted from here
        "G1 X11 B-1",
        "G92 X0",  # X renumbered: the next X2 is 2 mm on
        "G1 X2",
    ]
    assert run(to_relative, lines) == [
        "G91 ; relative positioning for the whole print",
        "G1 X5 B-0.5",
        "G1 X1 B-0.5",
        "G1 X1 B0.2",
        "G1 X3 B-0.2",
        "G1 X1 B-1",
        "G1 X2",
    ]


def test_to_relative_starts_from_the_given_position():
    assert run(to_relative, ["G90", "G1 X10 Y10"], {"X": 4, "Y": 0}) == [
        "G91 ; relative positioning for the whole print",
        "G1 X6 Y10",
    ]


def test_to_relative_rounding_never_accumulates():
    # 1000 steps of 0.0004 mm: each one rounds to 0 on its own, but the
    # absolute targets they reach don't, and those are what's written.
    lines = ["G91", *["G1 X0.0004"] * 1000, "G1 B-0.000004"]
    moves = run(to_relative, lines)[1:]
    assert sum(float(line.split("X")[1]) for line in moves if "X" in line) == pytest.approx(0.4)
    assert len(moves) == 400  # one 0.001 move each time the target rounds up; B rounds to 0


def test_to_relative_drops_moves_that_go_nowhere():
    lines = ["G90", "G1 X1 Y1", "G1 X1 ; same place", "G1 Y1", "G1 X1 F200"]
    assert run(to_relative, lines) == [
        "G91 ; relative positioning for the whole print",
        "G1 X1 Y1",
        "; same place",
        "G1 F200",  # keeps the feed rate it sets
    ]


def test_to_relative_rejects_homing():
    with pytest.raises(GcodeValidationError, match="Homing"):
        to_relative(parse(["G90", "G1 X1", "G28 X"]))


def test_relative_extrusion_file_converts_like_absolute_one():
    """Cura with relative_extrusion writes M83 and per-move E; the same
    model with absolute E must give the same program."""
    absolute = (FIXTURES / "raw_sample.gcode").read_text()
    previous = 0.0
    relative_lines = []
    for line in absolute.splitlines():
        if line.startswith("M82"):
            line = "M83 ;relative extrusion mode"
        elif line.startswith("G92 E"):
            previous = float(line.split("E")[1])
            continue  # nothing to reset with relative E
        elif " E" in line and line.startswith("G1"):
            head, e = line.split(" E")
            e_abs = float(e.split()[0])
            line, previous = f"{head} E{e_abs - previous:.5f}", e_abs
        relative_lines.append(line)

    assert process_gcode("\n".join(relative_lines), "left").lines == process_gcode(absolute, "left").lines


def test_right_nozzle_offset_is_in_the_first_move():
    result = process_gcode((FIXTURES / "raw_sample.gcode").read_text(), "right")
    first_move = next(line for line in result.lines if line.startswith("G0"))
    # X10 + the 31 mm offset, from the zero point, on the right nozzle's height motor
    assert first_move == "G0 F400 X41 Y10 A0.3"
    assert result.start_position == {"X": 0, "Y": 0, "A": 0}


LAB_FILE = """; lab file
G91
G1 X5 Y5 B-0.5 F200 ; first
G92 B0

G1 X5 B-0.5 F200"""


def test_lab_relative_file_is_kept_as_it_is():
    result = process_gcode(LAB_FILE, "right")
    assert result.lines == LAB_FILE.splitlines()
    assert result.start_position is None  # starts wherever the head is
    assert result.extrusion_axes == ("B",)


@pytest.mark.parametrize(
    "raw",
    [
        "G90\nG1 X5 B-0.5",  # absolute
        "G1 X5 B-0.5\nG91\nG1 X5",  # a move before the G91
        "G91\nG1 X5 E0.5",  # slicer E, not substituted yet
        "G91\nG1 X5\nG90\nG1 X0",  # switches back
    ],
)
def test_other_files_are_converted(raw):
    assert process_gcode(raw, "left").lines != raw.splitlines()
