"""The Printess 1.0's nozzle selections, for the tests."""

from backend.printer import BUILTIN_PROFILES, PrinterProfile

PRINTESS_1 = PrinterProfile.model_validate(BUILTIN_PROFILES["printess-1"])

LEFT = PRINTESS_1.select(["left"])
RIGHT = PRINTESS_1.select(["right"])
BOTH = PRINTESS_1.select(["left", "right"])
