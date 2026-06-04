"""WorkflowExecuteContext — injects globals into workflow Python files at exec() time."""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Type, overload

from pydantic import BaseModel, create_model

from operator_use.workflow.types import WorkflowJournal
from operator_use.workflow.types import WorkflowRunRecord

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.subagent.service import Subagent
    from operator_use.tool.types import Tool


class Budget:
    """Tracks agent() call count and actual token usage for a single workflow run."""

    def __init__(self, total: int = 100) -> None:
        self.total = total
        self._spent = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_read_tokens = 0
        self._cache_write_tokens = 0

    def spent(self) -> int:
        """Return the number of agent() calls consumed so far."""
        return self._spent

    def remaining(self) -> int:
        """Return the number of agent() calls remaining before exhaustion."""
        return self.total - self._spent

    def add(self, n: int = 1) -> None:
        """Consume n units from the call budget."""
        self._spent += n

    def add_tokens(self, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> None:
        """Accumulate token counts from an LLM EndEvent."""
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._cache_read_tokens += cache_read_tokens
        self._cache_write_tokens += cache_write_tokens

    def tokens_spent(self) -> dict[str, int]:
        """Return a breakdown of tokens consumed across all agent() and classify() calls."""
        return {
            'input': self._input_tokens,
            'output': self._output_tokens,
            'cache_read': self._cache_read_tokens,
            'cache_write': self._cache_write_tokens,
            'total': self._input_tokens + self._output_tokens,
        }

    def exhausted(self) -> bool:
        """Return True if the call budget is fully consumed."""
        return self._spent >= self.total

    def __repr__(self) -> str:
        t = self.tokens_spent()
        return f'Budget(calls={self._spent}/{self.total}, tokens={t["total"]})'


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
    async def agent(self, prompt: str, schema: None = None, system: str | None = None, tools: list[str] | None = None, resume: bool = False, stall_ms: int | None = None, max_retries: int | None = None, model: str | None = None, provider: str | None = None) -> str: ...
    @overload
    async def agent(self, prompt: str, schema: Type[BaseModel], system: str | None = None, tools: list[str] | None = None, resume: bool = False, stall_ms: int | None = None, max_retries: int | None = None, model: str | None = None, provider: str | None = None) -> BaseModel: ...

    async def agent(
        self,
        prompt: str,
        schema: Type[BaseModel] | None = None,
        system: str | None = None,
        tools: list[str] | None = None,
        resume: bool = False,
        stall_ms: int | None = None,
        max_retries: int | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> str | BaseModel:
        """Invoke an agent task and return the result (str or Pydantic model if schema given).

        Stalled calls (no completion within stall_ms) are retried up to max_retries times.
        Hard-capped by max_agent_calls; raise WorkflowAgentCapError on overflow.
        With resume=True, cache the result in the journal (keyed by prompt + options).
        model/provider override the session default for this single call.
        """
        from operator_use.workflow.types import WorkflowAgentCapError

        stall_ms = self._stall_ms if stall_ms is None else stall_ms
        max_retries = self._max_retries if max_retries is None else max_retries

        # Check hard cap to prevent runaway workflows.
        if self._record.agent_calls >= self._max_agent_calls:
            raise WorkflowAgentCapError(
                f'Workflow exceeded max agent() calls ({self._max_agent_calls}). '
                f'Raise it via args["max_agent_calls"] if this is intentional.'
            )

        # Increment the counter before awaiting to prevent concurrent parallel() calls
        # from all passing the cap check before any of them increments the counter.
        self._record.agent_calls += 1
        self.budget.add()

        # Check the journal (persistent cache) if resume=True.
        opts = {'schema': schema.__name__ if schema else None, 'system': system, 'model': model, 'provider': provider}
        if resume:
            cached = self._journal.get(prompt, opts)
            if cached is not None:
                return schema.model_validate(cached) if schema else cached

        result = await self._run_with_retry(prompt, schema, system, tools, stall_ms, max_retries, model, provider)

        # Store the result in the journal for future resume checks.
        if resume:
            serialized = result.model_dump() if isinstance(result, BaseModel) else result
            self._journal.set(prompt, opts, serialized)
        return result

    async def _run_with_retry(self, prompt, schema, system, tools, stall_ms: int, max_retries: int, model: str | None = None, provider: str | None = None):
        """Run an agent task with timeout and retry on stall, returning result or raising.

        Converts stall_ms to a timeout, calls either structured or unstructured agent,
        and retries up to max_retries times on asyncio.TimeoutError.
        """
        timeout = max(stall_ms, 1) / 1000
        llm = self._make_llm(model, provider)
        last_exc: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                # Structured (with schema) vs unstructured (text result) path.
                if schema is not None:
                    return await asyncio.wait_for(self._agent_structured(prompt, schema, system, llm), timeout=timeout)
                allowed = self._filter_tools(tools)
                subagent = self._make_subagent(llm)
                return await asyncio.wait_for(
                    subagent.run_single(task=prompt, system_prompt=system, tools=allowed, spawn_depth=self._spawn_depth),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                last_exc = exc
                self.log(f'[agent] stalled after {stall_ms}ms (attempt {attempt + 1}/{max_retries + 1})'
                         + ('; retrying' if attempt < max_retries else '; giving up'))
        raise TimeoutError(f'agent() stalled after {max_retries + 1} attempts of {stall_ms}ms each') from last_exc

    @overload
    async def classify(self, prompt: str, *, options: list[str], system: str | None = None, model: str | None = None, provider: str | None = None) -> str: ...
    @overload
    async def classify(self, prompt: str, *, schema: Type[BaseModel], system: str | None = None, model: str | None = None, provider: str | None = None) -> BaseModel: ...

    async def classify(
        self,
        prompt: str,
        *,
        options: list[str] | None = None,
        schema: Type[BaseModel] | None = None,
        system: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> str | BaseModel:
        """Single direct LLM call for classification — no subagent loop, no tool execution.

        Use options=[...] for simple string enum classification (returns str).
        Use schema=MyModel for richer structured output (returns model instance).
        model/provider override the session default for this single call.
        """
        llm = self._make_llm(model, provider)
        if schema is not None:
            return await self._agent_structured(prompt, schema, system, llm)
        if options is not None:
            label_type = Literal[tuple(options)]  # type: ignore[valid-type]
            LabelModel = create_model('Label', label=(label_type, ...))
            result = await self._agent_structured(prompt, LabelModel, system, llm)
            return result.label  # type: ignore[attr-defined]
        # Plain text classify — useful when the caller wants a free-form but cheap single call.
        return await self._agent_structured(prompt, None, system, llm)  # type: ignore[arg-type]

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
        """Append a timestamped log line to the run record."""
        ts = datetime.now().strftime('%H:%M:%S')
        line = f'{ts}  {message}'
        self._record.log_lines.append(line)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _make_llm(self, model: str | None, provider: str | None) -> LLM:
        """Return a new LLM for the given model/provider, or self._llm if neither is set."""
        if model is None:
            return self._llm
        from operator_use.inference.api.text.service import LLM as LLMClass
        return LLMClass(model, provider)

    def _make_subagent(self, llm: LLM) -> Subagent:
        """Return self._subagent if it already uses llm, otherwise a temporary one."""
        if llm is self._llm:
            return self._subagent
        from operator_use.subagent.service import Subagent as SubagentClass
        from operator_use.subagent.types import SubagentSettings
        return SubagentClass(llm=llm, tools=self._tools, settings=SubagentSettings(), hooks=None)

    async def _agent_structured(self, prompt: str, schema, system: str | None, llm: LLM | None = None):
        """Call the LLM with a response_format schema and parse the JSON reply into the schema."""
        from operator_use.inference.types import LLMContext, TextDeltaEvent, ErrorEvent, EndEvent
        from operator_use.message.types import UserMessage

        target = llm if llm is not None else self._llm
        events = await target.invoke(LLMContext(
            messages=[UserMessage.text(prompt)],
            system_prompt=system,
            response_format=schema,
        ))

        text = ''
        for e in events:
            if isinstance(e, TextDeltaEvent):
                text += e.text.content
            elif isinstance(e, EndEvent):
                self.budget.add_tokens(
                    input_tokens=e.input_tokens,
                    output_tokens=e.output_tokens,
                    cache_read_tokens=e.cache_read_tokens,
                    cache_write_tokens=e.cache_write_tokens,
                )
            elif isinstance(e, ErrorEvent):
                raise RuntimeError(f'LLM error during structured call: {e.error}')

        return schema.model_validate_json(text)

    def _filter_tools(self, names: list[str] | None) -> list[Tool]:
        """Return the tool list restricted to the given names, or all tools if names is None."""
        if names is None:
            return self._tools
        allowed = set(names)
        return [t for t in self._tools if t.name in allowed]

    def as_globals(self) -> dict[str, Any]:
        """Return the dict to inject into the workflow exec() namespace."""
        return {
            'agent':    self.agent,
            'classify': self.classify,
            'parallel': self.parallel,
            'pipeline': self.pipeline,
            'workflow': self.workflow,
            'phase':    self.phase,
            'log':      self.log,
            'budget':   self.budget,
            'args':     self.args,
        }
