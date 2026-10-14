"""Settings changed in the app, kept across restarts.

Unlike config.json (bundled with the app, and read-only once installed),
these live in a settings.json in the app's data folder (see logs.data_dir):
the Setup screen's settings (schemas.Settings) and, under "printer", the
selected printer profile and the Custom profile edited in the app.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.logs import data_dir
from backend.schemas import Settings

logger = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


class PrinterSettings(BaseModel):
    """The printer profile chosen in the app, and the Custom profile."""

    # The id of the selected profile; None: config.json's printer_profile
    selected: str | None = None
    # The Custom profile (a printer.PrinterProfile), as saved. Checked
    # when it is used, so a profile that no longer validates is dropped
    # with a log message rather than making settings.json unreadable.
    custom: dict[str, Any] | None = None


def default_settings_path() -> Path:
    return data_dir() / SETTINGS_FILE


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_settings_path()
        self.current, self.printer = self._load()

    def _load(self) -> tuple[Settings, PrinterSettings]:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return Settings(), PrinterSettings()
        except (OSError, ValueError):
            logger.exception("Couldn't read %s; using the default settings", self.path)
            return Settings(), PrinterSettings()
        if not isinstance(data, dict):
            logger.error("%s isn't a JSON object; using the default settings", self.path)
            return Settings(), PrinterSettings()
        try:
            settings = Settings.model_validate(data)
        except ValidationError:
            logger.exception("Couldn't read the settings in %s; using the defaults", self.path)
            settings = Settings()
        try:
            printer = PrinterSettings.model_validate(data.get("printer") or {})
        except ValidationError:
            logger.exception("Couldn't read the printer settings in %s; using the defaults", self.path)
            printer = PrinterSettings()
        return settings, printer

    def save(self, settings: Settings) -> None:
        """Write `settings` (atomically, so a crash can't leave half a file)
        and make them current. Raises OSError if they can't be written."""
        self._write(settings, self.printer)
        self.current = settings

    def save_printer(self, printer: PrinterSettings) -> None:
        """The same for the printer settings."""
        self._write(self.current, printer)
        self.printer = printer

    def _write(self, settings: Settings, printer: PrinterSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {**settings.model_dump(), "printer": printer.model_dump()}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        os.replace(temporary, self.path)
