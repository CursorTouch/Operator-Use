"""Heartbeat service: background loop that reads HEARTBEAT.md on an interval."""

import asyncio
from pathlib import Path
from typing import Awaitable, Callable
from datetime import datetime
import logging

HEARTBEAT_INTERVAL = 0.5 * 60 * 60  # 30 minutes
HEARTBEAT_FILENAME = "HEARTBEAT.md"

HEARTBEAT_PROMPT = """
Read the HEARTBEAT.md file in your workspace (if it exists).
Follow any instructions or tasks mentioned in there.
If there are no instructions or tasks, do nothing.
"""

logger = logging.getLogger(__name__)


class Heartbeat:
    """Background task that reads HEARTBEAT.md from the workspace on a schedule."""

    def __init__(
        self,
        workspace: Path,
        interval: float = HEARTBEAT_INTERVAL,
        on_heartbeat: Callable[[str], Awaitable[None]] | None = None,
    ):
        """
        Args:
            workspace: Agent workspace directory containing HEARTBEAT.md.
            interval: Seconds between ticks (default 1800 = 30 minutes).
            on_heartbeat: Optional async callback called with markdown content on each heartbeat.
        """
        self.workspace = workspace
        self.interval = interval
        self.on_heartbeat = on_heartbeat
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def _heartbeat_path(self) -> Path:
        return self.workspace / HEARTBEAT_FILENAME

    def _read_content(self) -> str:
        """Read HEARTBEAT.md content. Returns empty string if file does not exist."""
        path = self._heartbeat_path()
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    async def _loop(self) -> None:
        """Background loop: sleep → read HEARTBEAT.md → invoke callback → repeat."""
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                if not self._running:
                    break
                if self._read_content():
                    await self.trigger_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")

    async def trigger_heartbeat(self) -> None:
        """Trigger a heartbeat."""
        if not self.on_heartbeat:
            return None
        logger.info(f"Heartbeat triggered at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        await self.on_heartbeat(HEARTBEAT_PROMPT)

    def start(self) -> None:
        """Start the background heartbeat loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Heartbeat started, beats every {(self.interval) / 60} minutes")

    def stop(self) -> None:
        """Stop the background heartbeat loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Heartbeat stopped")
