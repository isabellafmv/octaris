"""Log files: the backend's own rotating log, and one serial traffic file per print.

Both live in the app data folder (~/Library/Application Support/Octaris/logs
on macOS), or under OCTARIS_DATA_DIR if that is set.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

import platformdirs

LOG_FILE = "octaris-backend.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def data_dir() -> Path:
    """The app's writable data folder."""
    override = os.environ.get("OCTARIS_DATA_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_data_dir("Octaris", appauthor=False))


def log_dir() -> Path:
    return data_dir() / "logs"


def print_log_dir() -> Path:
    return log_dir() / "prints"


def setup_logging(level: int = logging.INFO) -> Path:
    """Log to stderr and to a rotating file in log_dir(); returns the file's path."""
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_FILE
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)
    for handler in _handlers:
        root.removeHandler(handler)
        handler.close()
    _handlers[:] = [
        RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    for handler in _handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return path


# The handlers setup_logging() added, so calling it again replaces them
_handlers: list[logging.Handler] = []


class PrintTrafficLog:
    """The serial traffic of the print running now, written to its own file.

    Written from the serial reader thread and the event loop alike, so every
    write takes a lock.
    """

    def __init__(self, directory: Path | None = None):
        self._directory = directory
        self._lock = threading.Lock()
        self._file: TextIO | None = None

    @property
    def directory(self) -> Path:
        return self._directory or print_log_dir()

    def start(self, session_id: int) -> Path:
        """Start a file for print session `session_id`; returns its path."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.directory / f"print-{session_id}-{stamp}.log"
        self._open(path)
        return path

    def resume(self, path: str | Path) -> None:
        """Carry on writing to an earlier print's file (a stop was resumed)."""
        with self._lock:
            if self._file is not None and Path(self._file.name) == Path(path):
                return
        self._open(Path(path))

    def _open(self, path: Path) -> None:
        self.stop()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file = path.open("a", encoding="utf-8")
        except OSError:
            logging.getLogger(__name__).exception("Can't write the print's serial log to %s", path)
            return
        with self._lock:
            self._file = file

    def stop(self) -> None:
        with self._lock:
            file, self._file = self._file, None
        if file is not None:
            file.close()

    def write(self, timestamp: str, direction: str, content: str, line_number: int | None) -> None:
        with self._lock:
            if self._file is None:
                return
            arrow = ">" if direction == "sent" else "<"
            number = f"N{line_number} " if line_number is not None else ""
            self._file.write(f"{timestamp} {arrow} {number}{content}\n")
            self._file.flush()
