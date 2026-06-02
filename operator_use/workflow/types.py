from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operator_use.workflow.context import WorkflowExecuteContext


class WorkflowAgentCapError(RuntimeError):
    """Raised when a workflow run exceeds its hard agent()-call cap (runaway-loop guard)."""


@dataclass
class WorkflowContext:
    """Carries the runtime dependencies a Workflow needs to execute.

    Passed to Workflow.execute() — either built from a ToolContext (when called
    from a tool) or directly by WorkflowManager (when run as a background task).
    """
    llm: Any
    tools: list
    # Runner for inline nested workflows: async (name, args, spawn_depth, record) -> str.
    # Populated by WorkflowManager; None when no manager is available.
    nested_workflow: Any = None
    # Current nesting depth, threaded so the one-level-deep guard holds for class workflows.
    spawn_depth: int = 1
    # Caller's run record — threaded into nested class-based workflows so logs
    # and the agent()-call cap are unified with the parent run.
    record: Any = None  # WorkflowRunRecord | None


@dataclass
class WorkflowInvocation:
    """Carries the arguments for a single workflow run — analogous to ToolInvocation."""
    workflow_name: str
    args: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: f'wf_{uuid.uuid4().hex[:8]}')


class WorkflowStatus(StrEnum):
    """Lifecycle states for a workflow run."""

    running   = 'running'
    completed = 'completed'
    failed    = 'failed'
    cancelled = 'cancelled'


@dataclass
class WorkflowMeta:
    """Static metadata for a workflow, extracted from its `meta` dict or class attributes."""

    name: str
    description: str
    when_to_use: str | None = None
    phases: list[dict] = field(default_factory=list)
    deliver: bool = True


@dataclass
class WorkflowRunRecord:
    """Mutable runtime record for one workflow run — updated in-place as the run progresses."""

    run_id: str
    workflow_name: str
    status: WorkflowStatus
    started_at: datetime
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None
    log_lines: list[str] = field(default_factory=list)
    agent_calls: int = 0
    current_phase: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    deliver: bool = True


class WorkflowJournal:
    """Write-through cache keyed by (prompt, opts) SHA-256. Persists to disk."""

    def __init__(self, run_dir: Path | None = None) -> None:
        self._cache: dict[str, Any] = {}
        self._path = run_dir / 'journal.json' if run_dir else None
        if self._path and self._path.exists():
            try:
                self._cache = json.loads(self._path.read_text())
            except Exception:
                self._cache = {}

    def get(self, prompt: str, opts: dict) -> Any | None:
        """Return a cached result for (prompt, opts), or None on a miss."""
        return self._cache.get(self._key(prompt, opts))

    def set(self, prompt: str, opts: dict, result: Any) -> None:
        """Store a result for (prompt, opts) and flush to disk if a path is configured."""
        self._cache[self._key(prompt, opts)] = result
        if self._path:
            try:
                self._path.write_text(json.dumps(self._cache, indent=2))
            except Exception:
                pass

    def _key(self, prompt: str, opts: dict) -> str:
        """Produce a stable 16-hex-char key from prompt + options."""
        payload = json.dumps({'prompt': prompt, **opts}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class Workflow(ABC):
    """Base class for self-contained, class-based workflows.

    Analogous to Tool — override execute() with your logic.
    Call build_context() inside execute() to get a WorkflowExecuteContext
    with agent(), phase(), log(), args, etc. injected.

    Class attributes:
        name        — unique name used to invoke the workflow
        description — shown in listings
        when_to_use — optional LLM hint
        phases      — list of {name, description} dicts for progress tracking
    """

    name: str
    description: str
    when_to_use: str | None = None
    phases: list[dict] = []
    deliver: bool = True

    @classmethod
    def meta(cls) -> WorkflowMeta:
        """Build a WorkflowMeta from the class-level attributes."""
        return WorkflowMeta(
            name=cls.name,
            description=cls.description,
            when_to_use=getattr(cls, 'when_to_use', None),
            phases=list(getattr(cls, 'phases', [])),
            deliver=getattr(cls, 'deliver', True),
        )

    async def build_context(
        self,
        invocation: WorkflowInvocation,
        workflow_context: WorkflowContext,
    ) -> WorkflowExecuteContext:
        """Build a WorkflowExecuteContext from the provided WorkflowContext.

        Call this inside execute() to get ctx with agent(), phase(), log(), etc.
        """
        from operator_use.subagent.service import Subagent
        from operator_use.subagent.types import SubagentSettings
        from operator_use.workflow.context import WorkflowExecuteContext

        tools = [t for t in workflow_context.tools if t.name not in ('subagent', 'workflow')]

        subagent = Subagent(
            llm=workflow_context.llm,
            tools=tools,
            settings=SubagentSettings(),
        )

        record = workflow_context.record or WorkflowRunRecord(
            run_id=invocation.run_id,
            workflow_name=invocation.workflow_name,
            status=WorkflowStatus.running,
            started_at=datetime.now(),
        )

        run_dir = Path(tempfile.gettempdir()) / '.operator-workflow-runs' / invocation.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        return WorkflowExecuteContext(
            record=record,
            subagent=subagent,
            llm=workflow_context.llm,
            tools=tools,
            journal=WorkflowJournal(run_dir=run_dir),
            args=invocation.args,
            spawn_depth=workflow_context.spawn_depth,
            nested_workflow=workflow_context.nested_workflow,
        )

    @abstractmethod
    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        """Implement the workflow logic here.

        Call build_context(invocation, workflow_context) to get a WorkflowExecuteContext,
        then use ctx.agent(), ctx.phase(), ctx.log(), ctx.args, etc.
        """
        ...
