from __future__ import annotations

import asyncio
import logging
import re
from enum import Enum
from typing import Callable

from backend.serial_manager import SerialError, SerialManager

_BC_RUNTIME = re.compile(r"([BC])(-?\d+\.?\d*)")

logger = logging.getLogger(__name__)


class PrintStatus(str, Enum):
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class QueueWorker:
    def __init__(
        self,
        serial_manager: SerialManager,
        on_event: Callable[[dict], None] | None = None,
    ):
        self._serial = serial_manager
        self._normal_queue: asyncio.Queue[str] = asyncio.Queue()
        self._priority_queue: asyncio.Queue[str] = asyncio.Queue()
        self._status = PrintStatus.IDLE
        self._lines_sent = 0
        self._lines_total = 0
        self._paused = asyncio.Event()
        self._paused.set()  # not paused initially
        self._task: asyncio.Task | None = None
        self._on_event = on_event
        self._flow_rate: float = 100.0  # percentage, same semantics as M221

    @property
    def status(self) -> PrintStatus:
        return self._status

    @property
    def lines_sent(self) -> int:
        return self._lines_sent

    @property
    def lines_total(self) -> int:
        return self._lines_total

    @property
    def flow_rate(self) -> float:
        return self._flow_rate

    def set_flow_rate(self, rate: float) -> None:
        """Set runtime flow rate as percentage (100 = normal)."""
        self._flow_rate = max(0, rate)

    def _emit(self, event: dict) -> None:
        if self._on_event:
            self._on_event(event)

    def load_gcode(self, lines: list[str]) -> None:
        self.flush_normal()
        self._lines_sent = 0
        self._lines_total = len(lines)
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith(";"):
                self._normal_queue.put_nowait(stripped)
        self._lines_total = self._normal_queue.qsize()

    def enqueue_priority(self, command: str) -> None:
        self._priority_queue.put_nowait(command.strip())

    def flush_normal(self) -> None:
        while not self._normal_queue.empty():
            try:
                self._normal_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._status = PrintStatus.PRINTING
        self._paused.set()
        self._emit({"type": "status", "value": self._status.value})
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        if self._status == PrintStatus.PRINTING:
            self._status = PrintStatus.PAUSED
            self._paused.clear()
            self._emit({"type": "status", "value": self._status.value})

    def resume(self) -> None:
        if self._status in (PrintStatus.PAUSED, PrintStatus.STOPPED):
            self._status = PrintStatus.PRINTING
            self._paused.set()
            self._emit({"type": "status", "value": self._status.value})

    async def stop(self) -> None:
        self.enqueue_priority("M410")
        self.flush_normal()
        self._status = PrintStatus.STOPPED
        self._paused.set()  # unblock worker so it can exit
        self._emit({"type": "status", "value": self._status.value})

    async def _run(self) -> None:
        logger.info("Queue worker started: %d lines", self._lines_total)

        try:
            while True:
                # Always service priority queue first
                try:
                    cmd = self._priority_queue.get_nowait()
                    await self._send(cmd)
                    continue
                except asyncio.QueueEmpty:
                    pass

                if self._status == PrintStatus.STOPPED:
                    break

                # Wait if paused
                await self._paused.wait()

                if self._status == PrintStatus.STOPPED:
                    break

                # Try to get from normal queue
                try:
                    line = await asyncio.wait_for(
                        self._normal_queue.get(), timeout=0.05
                    )
                except asyncio.TimeoutError:
                    if self._normal_queue.empty() and self._status == PrintStatus.PRINTING:
                        self._status = PrintStatus.COMPLETED
                        self._emit({"type": "status", "value": self._status.value})
                        break
                    continue

                await self._send(self._apply_flow_rate(line))
                self._lines_sent += 1
                self._emit({
                    "type": "progress",
                    "lines_sent": self._lines_sent,
                    "lines_total": self._lines_total,
                })

        except Exception:
            logger.exception("Queue worker error")
        finally:
            logger.info(
                "Queue worker finished: status=%s, sent=%d/%d",
                self._status.value,
                self._lines_sent,
                self._lines_total,
            )

    def _apply_flow_rate(self, line: str) -> str:
        """Scale B/C values by the current flow rate percentage."""
        if self._flow_rate == 100.0:
            return line
        multiplier = self._flow_rate / 100.0

        def scale(m):
            axis = m.group(1)
            val = float(m.group(2)) * multiplier
            return f"{axis}{val:g}"

        return _BC_RUNTIME.sub(scale, line)

    async def _send(self, line: str) -> None:
        try:
            await self._serial.send_line(line)
        except SerialError:
            logger.error("Failed to send: %s", line)
            self._emit({"type": "disconnected"})
            self._status = PrintStatus.STOPPED
