from __future__ import annotations

import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from operator_use.workflow.types import WorkflowInvocation, WorkflowMeta, WorkflowRunRecord, WorkflowStatus

if TYPE_CHECKING:
    from operator_use.tool.types import ToolContext
    from operator_use.workflow.context import WorkflowContext


class Workflow(ABC):
    """Base class for self-contained, class-based workflows.

    Analogous to Tool — has an execute() method that can be called directly
    from any call site (a tool, a command handler, a test) without the
    WorkflowManager. The manager is only for user-created file-based workflows.

    Subclass and implement run(ctx). Call execute(invocation, context) to run.

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

    @classmethod
    def meta(cls) -> WorkflowMeta:
        return WorkflowMeta(
            name=cls.name,
            description=cls.description,
            when_to_use=getattr(cls, 'when_to_use', None),
            phases=list(getattr(cls, 'phases', [])),
        )

    async def execute(self, invocation: WorkflowInvocation, workflow_context: ToolContext) -> str:
        """Run the workflow standalone — no WorkflowManager needed.

        Builds a WorkflowContext from the provided ToolContext, calls run(ctx),
        and returns the result string. The WorkflowManager is not involved.
        """
        from operator_use.subagent.service import Subagent
        from operator_use.subagent.types import SubagentSettings
        from operator_use.workflow.context import WorkflowContext
        from operator_use.workflow.journal import WorkflowJournal

        if workflow_context.llm is None:
            raise RuntimeError("LLM is not available in the current workflow context.")

        tools = []
        if workflow_context.engine is not None:
            tools = [t for t in workflow_context.engine.tools if t.name not in ('subagent', 'workflow')]

        subagent = Subagent(
            llm=workflow_context.llm,
            tools=tools,
            settings=SubagentSettings(),
        )

        record = WorkflowRunRecord(
            run_id=invocation.run_id,
            workflow_name=invocation.workflow_name,
            status=WorkflowStatus.running,
            started_at=datetime.now(),
        )

        run_dir = Path(tempfile.gettempdir()) / '.operator-workflow-runs' / invocation.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        ctx = WorkflowContext(
            record=record,
            subagent=subagent,
            llm=workflow_context.llm,  # already guarded above
            tools=tools,
            journal=WorkflowJournal(run_dir=run_dir),
            args=invocation.args,
        )

        try:
            result = await self.run(ctx)
            record.status = WorkflowStatus.completed
            record.finished_at = datetime.now()
            record.result = result
            return result
        except Exception:
            record.status = WorkflowStatus.failed
            record.finished_at = datetime.now()
            raise

    @abstractmethod
    async def run(self, ctx: WorkflowContext) -> str:
        """Implement workflow logic here using ctx.agent(), ctx.phase(), ctx.log(), etc."""
        ...
