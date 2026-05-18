from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class TaskPool:
    """
    Semaphore-backed concurrency pool for subagent tasks.

    Tasks can declare dependencies on other task_ids; they are deferred until
    all prerequisites have completed. Pool respects max_concurrent at all times.
    """

    def __init__(self, max_concurrent: int = 10) -> None:
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent > 0 else None
        self._pending: dict[str, dict] = {}
        self._running: set[str] = set()
        self._completed: dict[str, bool] = {}
        self._completion_events: dict[str, asyncio.Event] = {}

    def submit(
        self,
        coro: Callable[..., Awaitable] | Awaitable,
        task_id: str,
        depends_on: list[str] | None = None,
    ) -> asyncio.Task:
        depends_on = depends_on or []
        self._completion_events[task_id] = asyncio.Event()
        self._pending[task_id] = {'coro': coro, 'depends_on': depends_on}
        return asyncio.create_task(self._try_launch(task_id))

    async def _try_launch(self, task_id: str) -> None:
        task_info = self._pending.get(task_id)
        if not task_info:
            return
        for dep_id in task_info['depends_on']:
            if dep_id in self._completion_events:
                await self._completion_events[dep_id].wait()
        if self._semaphore:
            async with self._semaphore:
                await self._run_task(task_id)
        else:
            await self._run_task(task_id)

    async def _run_task(self, task_id: str) -> None:
        if task_id not in self._pending:
            return
        task_info = self._pending.pop(task_id)
        coro = task_info['coro']
        self._running.add(task_id)
        try:
            if asyncio.iscoroutine(coro) or hasattr(coro, '__await__'):
                await coro
            else:
                result = coro()
                if asyncio.iscoroutine(result) or hasattr(result, '__await__'):
                    await result
            self._completed[task_id] = True
        except asyncio.CancelledError:
            self._completed[task_id] = False
            logger.info('[%s] subagent cancelled', task_id)
        except Exception as exc:
            self._completed[task_id] = False
            logger.error('[%s] subagent failed: %s', task_id, exc)
        finally:
            self._running.discard(task_id)
            if task_id in self._completion_events:
                self._completion_events[task_id].set()

    def stats(self) -> dict:
        return {
            'max_concurrent': self.max_concurrent,
            'pending': len(self._pending),
            'running': len(self._running),
            'completed': len(self._completed),
        }
