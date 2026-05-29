"""WorkflowExecuteContext — injects globals into workflow Python files at exec() time."""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Type, overload

from pydantic import BaseModel

from operator_use.workflow.types import WorkflowJournal
from operator_use.workflow.types import WorkflowRunRecord

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.subagent.service import Subagent
    from operator_use.tool.types import Tool


class Budget:
    def __init__(self, total: int = 100) -> None:
        self.total = total
        self._spent = 0

    def spent(self) -> int:
        return self._spent

    def remaining(self) -> int:
        return self.total - self._spent

    def add(self, n: int = 1) -> None:
        self._spent += n

    def exhausted(self) -> bool:
        return self._spent >= self.total

    def __repr__(self) -> str:
        return f'Budget(spent={self._spent}/{self.total})'


class WorkflowExecuteContext:
    """Holds all runtime state for one workflow run and exposes the workflow API."""

    def __init__(
        self,
        record: WorkflowRunRecord,
        subagent: Subagent,
        llm: LLM,
        tools: list[Tool],
        journal: WorkflowJournal,
        args: dict[str, Any],
        spawn_depth: int = 1,
        nested_workflow: Any = None,
    ) -> None:
        self._record = record
        self._subagent = subagent
        self._llm = llm
        self._tools = tools
        self._journal = journal
        self.args = args
        self._spawn_depth = spawn_depth
        # Runs another workflow inline: async (name, args, spawn_depth, record) -> str
        self._nested_workflow = nested_workflow
        self.budget = Budget(total=int(args.get('budget', 100)))
        # Run-knob defaults (settings → seeded into args; per-call params still override).
        self._max_agent_calls = int(args.get('max_agent_calls', 1000))
        self._stall_ms = int(args.get('stall_ms', 180_000))
        self._max_retries = int(args.get('max_retries', 5))
        self._concurrency = int(args.get('concurrency', 5))

    # ── Workflow globals ───────────────────────────────────────────────────────

    @overload
    async def agent(self, prompt: str, schema: None = None, system: str | None = None, tools: list[str] | None = None, resume: bool = False, stall_ms: int | None = None, max_retries: int | None = None) -> str: ...
    @overload
    async def agent(self, prompt: str, schema: Type[BaseModel], system: str | None = None, tools: list[str] | None = None, resume: bool = False, stall_ms: int | None = None, max_retries: int | None = None) -> BaseModel: ...

    async def agent(
        self,
        prompt: str,
        schema: Type[BaseModel] | None = None,
        system: str | None = None,
        tools: list[str] | None = None,
        resume: bool = False,
        stall_ms: int | None = None,
        max_retries: int | None = None,
    ) -> str | BaseModel:
        """Run a single agent task. Returns str or a parsed Pydantic model if schema given.

        A stalled call (no completion within `stall_ms`) is cancelled and retried up
        to `max_retries` times before raising. Hard-capped by `max_agent_calls`.
        `stall_ms`/`max_retries` default to the run's configured values when omitted."""
        from operator_use.workflow.types import WorkflowAgentCapError

        stall_ms = self._stall_ms if stall_ms is None else stall_ms
        max_retries = self._max_retries if max_retries is None else max_retries

        if self._record.agent_calls >= self._max_agent_calls:
            raise WorkflowAgentCapError(
                f'Workflow exceeded max agent() calls ({self._max_agent_calls}). '
                f'Raise it via args["max_agent_calls"] if this is intentional.'
            )

        opts = {'schema': schema.__name__ if schema else None, 'system': system}
        if resume:
            cached = self._journal.get(prompt, opts)
            if cached is not None:
                return schema.model_validate(cached) if schema else cached

        result = await self._run_with_retry(prompt, schema, system, tools, stall_ms, max_retries)

        self._record.agent_calls += 1
        self.budget.add()

        if resume:
            serialized = result.model_dump() if isinstance(result, BaseModel) else result
            self._journal.set(prompt, opts, serialized)
        return result

    async def _run_with_retry(self, prompt, schema, system, tools, stall_ms: int, max_retries: int):
        """Produce one agent result, retrying on stall (timeout) up to max_retries times."""
        timeout = max(stall_ms, 1) / 1000
        last_exc: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                if schema is not None:
                    return await asyncio.wait_for(self._agent_structured(prompt, schema, system), timeout=timeout)
                allowed = self._filter_tools(tools)
                return await asyncio.wait_for(
                    self._subagent.run_single(task=prompt, system_prompt=system, tools=allowed, spawn_depth=self._spawn_depth),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                last_exc = exc
                self.log(f'[agent] stalled after {stall_ms}ms (attempt {attempt + 1}/{max_retries + 1})'
                         + ('; retrying' if attempt < max_retries else '; giving up'))
        raise TimeoutError(f'agent() stalled after {max_retries + 1} attempts of {stall_ms}ms each') from last_exc

    async def parallel(self, *thunks, concurrency: int | None = None, return_exceptions: bool = False):
        """Run async thunks concurrently (barrier — waits for all). Returns list of results.

        By default a failing thunk cancels its siblings and re-raises (fail-fast).
        With return_exceptions=True the call never rejects: each failed thunk's slot
        holds its exception instead, so partial results survive."""
        sem = asyncio.Semaphore(self._concurrency if concurrency is None else concurrency)

        async def _run(thunk):
            async with sem:
                return await thunk()

        coros = [_run(t) for t in thunks]
        if return_exceptions:
            return list(await asyncio.gather(*coros, return_exceptions=True))
        return await self._gather_or_cancel(coros)

    async def workflow(self, name: str, args: dict[str, Any] | None = None) -> str:
        """Run another workflow inline and return its result (one level deep only)."""
        if self._nested_workflow is None:
            raise RuntimeError('Nested workflows are not available in this context.')
        return await self._nested_workflow(name, args or {}, self._spawn_depth + 1, self._record)

    async def pipeline(self, items, *stages, concurrency: int | None = None):
        """Pass each item through stages independently. Returns list of final values."""
        sem = asyncio.Semaphore(self._concurrency if concurrency is None else concurrency)

        async def _apply(stage, item):
            if inspect.iscoroutinefunction(stage):
                return await stage(item)
            return stage(item)

        async def _process(item):
            async with sem:
                for stage in stages:
                    item = await _apply(stage, item)
                return item

        return await self._gather_or_cancel([_process(item) for item in items])

    @staticmethod
    async def _gather_or_cancel(coros: list) -> list:
        """gather() that cancels still-running siblings when one fails, instead
        of leaving them orphaned (running on, with their results/exceptions
        dropped). Re-raises the first error unchanged."""
        tasks = [asyncio.ensure_future(c) for c in coros]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    @asynccontextmanager
    async def phase(self, name: str):
        """Context manager that sets the current phase label and logs entry/exit."""
        prev = self._record.current_phase
        self._record.current_phase = name
        self.log(f'[phase] {name}')
        try:
            yield
        finally:
            self._record.current_phase = prev

    def log(self, message: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S')
        line = f'{ts}  {message}'
        self._record.log_lines.append(line)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _agent_structured(self, prompt: str, schema, system: str | None):
        from operator_use.inference.types import LLMContext, TextDeltaEvent, ErrorEvent
        from operator_use.message.types import UserMessage

        events = await self._llm.invoke(LLMContext(
            messages=[UserMessage.text(prompt)],
            system_prompt=system,
            response_format=schema,
        ))

        text = ''
        for e in events:
            if isinstance(e, TextDeltaEvent):
                text += e.text.content
            elif isinstance(e, ErrorEvent):
                raise RuntimeError(f'LLM error during structured call: {e.error}')

        return schema.model_validate_json(text)

    def _filter_tools(self, names: list[str] | None) -> list[Tool]:
        if names is None:
            return self._tools
        allowed = set(names)
        return [t for t in self._tools if t.name in allowed]

    def as_globals(self) -> dict[str, Any]:
        """Return the dict to inject into the workflow exec() namespace."""
        return {
            'agent':    self.agent,
            'parallel': self.parallel,
            'pipeline': self.pipeline,
            'workflow': self.workflow,
            'phase':    self.phase,
            'log':      self.log,
            'budget':   self.budget,
            'args':     self.args,
        }
