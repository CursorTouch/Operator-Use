"""TaskPool — manages parallel execution of subagent tasks with concurrency limits.

Inspired by open-multi-agent's AgentPool, this module provides:
- Semaphore-based concurrency limiting
- Task queuing and scheduling
- Dependency-aware task launching
- Automatic resource cleanup
"""

import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class TaskPool:
    """Pool that limits concurrent task execution via semaphore.

    Manages a queue of pending tasks and auto-launches them when:
    1. A semaphore slot becomes available (respects max_concurrent)
    2. All task dependencies are complete

    Usage:
        pool = TaskPool(max_concurrent=5)
        task_id = await pool.submit(coro, task_id, depends_on=[...])
    """

    def __init__(self, max_concurrent: int = 10) -> None:
        """Initialize pool with concurrency limit.

        Args:
            max_concurrent: Maximum number of tasks running simultaneously.
                           Default 10. Set to 0 for unlimited.
        """
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent > 0 else None
        self._pending: dict[str, dict] = {}  # task_id -> {coro, depends_on, event}
        self._running: set[str] = set()
        self._completed: dict[str, bool] = {}  # task_id -> success?
        self._completion_events: dict[str, asyncio.Event] = {}

    def submit(
        self,
        coro: Callable[..., Awaitable] | Awaitable,
        task_id: str,
        depends_on: list[str] | None = None,
    ) -> asyncio.Task:
        """Submit a task to the pool.

        Args:
            coro: Async callable or coroutine to execute
            task_id: Unique task identifier
            depends_on: List of task_ids that must complete first

        Returns:
            asyncio.Task that will execute the coroutine (handles deps + semaphore)
        """
        depends_on = depends_on or []

        # Register completion event for this task
        self._completion_events[task_id] = asyncio.Event()

        # Queue the task
        self._pending[task_id] = {
            "coro": coro,
            "depends_on": depends_on,
        }

        # Create the launcher task that will wait for deps + semaphore
        launcher_task = asyncio.create_task(self._try_launch(task_id))
        return launcher_task

    async def _try_launch(self, task_id: str) -> None:
        """Try to launch a task. Wait for semaphore + dependencies."""
        task_info = self._pending.get(task_id)
        if not task_info:
            return

        # Wait for all dependencies to complete
        for dep_id in task_info["depends_on"]:
            if dep_id in self._completion_events:
                await self._completion_events[dep_id].wait()

        # Wait for semaphore slot (if limited concurrency)
        if self._semaphore:
            async with self._semaphore:
                await self._run_task(task_id)
        else:
            await self._run_task(task_id)

    async def _run_task(self, task_id: str) -> None:
        """Execute a task and handle completion."""
        if task_id not in self._pending:
            return

        task_info = self._pending.pop(task_id)
        coro_or_callable = task_info["coro"]

        self._running.add(task_id)
        try:
            logger.debug(
                f"[{task_id}] launching (pending={len(self._pending)}, "
                f"running={len(self._running)}, concurrent_limit={self.max_concurrent})"
            )
            # Handle both coroutines and callables
            if asyncio.iscoroutine(coro_or_callable) or hasattr(coro_or_callable, "__await__"):
                await coro_or_callable
            else:
                # It's a callable, invoke it
                result = coro_or_callable()
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    await result
            self._completed[task_id] = True
            logger.debug(f"[{task_id}] completed successfully")
        except asyncio.CancelledError:
            self._completed[task_id] = False
            logger.info(f"[{task_id}] cancelled")
        except Exception as e:
            self._completed[task_id] = False
            logger.error(f"[{task_id}] failed: {e}")
        finally:
            self._running.discard(task_id)
            # Signal completion so dependents can proceed
            if task_id in self._completion_events:
                self._completion_events[task_id].set()

    def get_pending_count(self) -> int:
        """Return number of tasks waiting to launch."""
        return len(self._pending)

    def get_running_count(self) -> int:
        """Return number of tasks currently executing."""
        return len(self._running)

    def get_completed_count(self) -> int:
        """Return number of tasks that have finished."""
        return len(self._completed)

    def stats(self) -> dict:
        """Return pool statistics."""
        return {
            "max_concurrent": self.max_concurrent,
            "pending": self.get_pending_count(),
            "running": self.get_running_count(),
            "completed": self.get_completed_count(),
            "total": len(self._completed) + len(self._running) + len(self._pending),
        }

    async def wait_all(self) -> None:
        """Wait for all pending and running tasks to complete."""
        while self._pending or self._running:
            await asyncio.sleep(0.1)

    def cancel_all(self) -> int:
        """Cancel all pending tasks. Returns count cancelled."""
        count = len(self._pending)
        self._pending.clear()
        return count
