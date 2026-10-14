"""The Printess 1.0's nozzle selections and axes, for the tests."""

from backend.printer import BUILTIN_PROFILES, PrinterProfile

PRINTESS_1 = PrinterProfile.model_validate(BUILTIN_PROFILES["printess-1"])

LEFT = PRINTESS_1.select(["left"])
RIGHT = PRINTESS_1.select(["right"])
BOTH = PRINTESS_1.select(["left", "right"])

# X, Y, heights Z and A, plungers B and C
AXES = PRINTESS_1.axes
