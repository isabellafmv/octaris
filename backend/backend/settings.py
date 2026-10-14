"""Settings changed in the app, kept across restarts.

Unlike config.json (bundled with the app, and read-only once installed),
these live in a settings.json in the app's data folder (see logs.data_dir).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from backend.logs import data_dir
from backend.schemas import Settings

logger = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


def default_settings_path() -> Path:
    return data_dir() / SETTINGS_FILE


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_settings_path()
        self.current = self._load()

    def _load(self) -> Settings:
        try:
            return Settings.model_validate(json.loads(self.path.read_text()))
        except FileNotFoundError:
            return Settings()
        except (OSError, ValueError, ValidationError):
            logger.exception("Couldn't read %s; using the default settings", self.path)
            return Settings()

    def save(self, settings: Settings) -> None:
        """Write `settings` (atomically, so a crash can't leave half a file)
        and make them current. Raises OSError if they can't be written."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(settings.model_dump_json(indent=2))
        os.replace(temporary, self.path)
        self.current = settings
