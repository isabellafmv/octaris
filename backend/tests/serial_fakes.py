"""Helpers shared by the fake serial ports in the tests."""
from __future__ import annotations

import queue
import re
from unittest.mock import MagicMock

from backend.serial_manager import SerialManager, checksum

_NUMBERED = re.compile(r"^N(\d+) (.*)\*(\d+)$")


def unframe(raw: str) -> tuple[int | None, str]:
    """Split "N5 G1 X1*34" into (5, "G1 X1"); an unnumbered line gives (None, line).

    Asserts the checksum is right, so a fake that uses this also checks framing.
    """
    match = _NUMBERED.match(raw)
    if not match:
        return None, raw
    body = raw.rsplit("*", 1)[0]
    assert int(match.group(3)) == checksum(body), f"bad checksum in {raw!r}"
    return int(match.group(1)), match.group(2)


def attach(manager: SerialManager, fake, port: str = "/dev/fake") -> None:
    """Hand an already "open" fake port to `manager`, as connect() would,
    without the temperature monitor. Call from a running event loop."""
    manager._attach(fake, port)


def mock_port() -> MagicMock:
    """A MagicMock serial port that answers every written line with "ok"."""
    replies: queue.SimpleQueue[bytes] = queue.SimpleQueue()
    port = MagicMock()
    port.is_open = True
    port.write.side_effect = lambda data: replies.put(b"ok\n")

    def readline() -> bytes:
        try:
            return replies.get(timeout=0.01)
        except queue.Empty:
            return b""

    port.readline.side_effect = readline
    return port
