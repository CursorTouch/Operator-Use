"""WorkflowContext — injects globals into workflow Python files at exec() time."""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Type

from pydantic import BaseModel

from program.workflow.journal import WorkflowJournal
from program.workflow.types import WorkflowRunRecord

if TYPE_CHECKING:
    from program.inference.api.text.service import LLM
    from program.subagent.service import Subagent
    from program.tool.types import Tool


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


class WorkflowContext:
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
    ) -> None:
        self._record = record
        self._subagent = subagent
        self._llm = llm
        self._tools = tools
        self._journal = journal
        self.args = args
        self._spawn_depth = spawn_depth
        self.budget = Budget(total=int(args.get('budget', 100)))

    # ── Workflow globals ───────────────────────────────────────────────────────

    async def agent(
        self,
        prompt: str,
        schema: Type[BaseModel] | None = None,
        system: str | None = None,
        tools: list[str] | None = None,
        resume: bool = False,
    ):
        """Run a single agent task. Returns str or a parsed Pydantic model if schema given."""
        opts = {'schema': schema.__name__ if schema else None, 'system': system}
        if resume:
            cached = self._journal.get(prompt, opts)
            if cached is not None:
                return schema.model_validate(cached) if schema else cached

        if schema is not None:
            result = await self._agent_structured(prompt, schema, system)
        else:
            allowed = self._filter_tools(tools)
            result = await self._subagent.run_single(
                task=prompt,
                system_prompt=system,
                tools=allowed,
                spawn_depth=self._spawn_depth,
            )

        self._record.agent_calls += 1
        self.budget.add()

        if resume:
            serialized = result.model_dump() if isinstance(result, BaseModel) else result
            self._journal.set(prompt, opts, serialized)
        return result

    async def parallel(self, *thunks, concurrency: int = 5):
        """Run async thunks concurrently (barrier — waits for all). Returns list of results."""
        sem = asyncio.Semaphore(concurrency)

        async def _run(thunk):
            async with sem:
                return await thunk()

        return list(await asyncio.gather(*[_run(t) for t in thunks]))

    async def pipeline(self, items, *stages, concurrency: int = 5):
        """Pass each item through stages independently. Returns list of final values."""
        sem = asyncio.Semaphore(concurrency)

        async def _apply(stage, item):
            if inspect.iscoroutinefunction(stage):
                return await stage(item)
            return stage(item)

        async def _process(item):
            async with sem:
                for stage in stages:
                    item = await _apply(stage, item)
                return item

        return list(await asyncio.gather(*[_process(item) for item in items]))

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
        from program.inference.types import LLMContext, TextDeltaEvent, ErrorEvent
        from program.message.types import UserMessage

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
            'phase':    self.phase,
            'log':      self.log,
            'budget':   self.budget,
            'args':     self.args,
        }
