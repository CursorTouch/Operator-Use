"""WorkflowManager — loads, runs, and tracks Python workflow files.

Each run gets an isolated WorkflowContext and executes as a background asyncio
task. Results are announced back to the originating session via the bus,
using the same channel/chat_id pattern as SubagentManager.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from operator_use.subagent.service import Subagent
from operator_use.subagent.types import SubagentSettings
from operator_use.workflow.types import Workflow
from operator_use.workflow.loader import WorkflowLoader
from operator_use.workflow.types import WorkflowContext, WorkflowInvocation, WorkflowRunRecord, WorkflowStatus

if TYPE_CHECKING:
    from operator_use.bus.service import Bus
    from operator_use.inference.api.text.service import LLM
    from operator_use.tool.types import Tool

logger = logging.getLogger(__name__)


class WorkflowManager:
    """Discovers workflow files, spawns runs, and delivers results via the bus."""

    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        bus: Bus | None = None,
        workflows_dir: Path | None = None,
        workflow_dirs: list[Path] | None = None,
    ) -> None:
        self._llm = llm
        self._tools = [t for t in tools if t.name not in ('subagent', 'workflow')]
        self._bus = bus
        self._workflows_dir = workflows_dir
        # workflow_dirs (full list) takes precedence over the legacy workflows_dir scalar
        dirs: list[Path] = workflow_dirs if workflow_dirs is not None else (
            [workflows_dir] if workflows_dir else []
        )
        self._loader: WorkflowLoader | None = WorkflowLoader(*dirs) if dirs else None
        self._class_workflows: dict[str, Workflow] = {}
        self._subagent = Subagent(
            llm=llm,
            tools=self._tools,
            settings=SubagentSettings(),
            hooks=None,
        )
        self._records: dict[str, WorkflowRunRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def update_tools(self, tools: list[Tool]) -> None:
        """Refresh the tool list used by workflow agent() calls (called on reload)."""
        self._tools = [t for t in tools if t.name not in ('subagent', 'workflow')]
        self._subagent = Subagent(
            llm=self._llm,
            tools=self._tools,
            settings=SubagentSettings(),
            hooks=None,
        )

    def register_workflow(self, workflow: Workflow) -> None:
        """Register a class-based workflow. Takes precedence over same-named file-based ones."""
        self._class_workflows[workflow.name] = workflow

    def list_workflows(self):
        file_based = self._loader.list_with_meta() if self._loader else []
        shadowed = set(self._class_workflows)
        file_based = [(p, m) for p, m in file_based if m.name not in shadowed]
        class_based = [(None, w.meta()) for w in self._class_workflows.values()]
        return class_based + file_based

    def find_workflow(self, name: str) -> Path | None:
        return self._loader.find(name) if self._loader else None

    async def invoke(
        self,
        workflow_name: str,
        args: dict[str, Any] | None = None,
    ) -> str:
        """Start a workflow run in the background. Returns run_id immediately."""
        from operator_use.subagent.manager import _session_channel, _session_chat_id

        # Resolve: class-based takes precedence over file-based
        workflow_instance = self._class_workflows.get(workflow_name)
        path: Path | None = None

        if workflow_instance is None:
            if self._loader is None:
                raise ValueError(
                    f"Workflow '{workflow_name}' not found — no workflows directory is configured."
                )
            path = self._loader.find(workflow_name)
            if path is None:
                candidates = (
                    list(self._class_workflows)
                    + [p.stem for p in self._loader.discover()]
                )
                hint = f"Available: {', '.join(candidates)}" if candidates else "No workflows found."
                raise ValueError(f"Workflow '{workflow_name}' not found. {hint}")

        invocation = WorkflowInvocation(workflow_name=workflow_name, args=args or {})
        record = WorkflowRunRecord(
            run_id=invocation.run_id,
            workflow_name=workflow_name,
            status=WorkflowStatus.running,
            started_at=datetime.now(),
            channel=_session_channel.get(),
            chat_id=_session_chat_id.get(),
        )
        self._records[invocation.run_id] = record

        if workflow_instance is not None:
            t = asyncio.create_task(self._run_class_and_announce(workflow_instance, invocation, record))
        else:
            assert path is not None
            t = asyncio.create_task(self._run_and_announce(path, record, args or {}))

        self._tasks[invocation.run_id] = t
        t.add_done_callback(lambda _: self._tasks.pop(invocation.run_id, None))

        return invocation.run_id

    def cancel(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    def get_record(self, run_id: str) -> WorkflowRunRecord | None:
        return self._records.get(run_id)

    def list_all(self) -> list[WorkflowRunRecord]:
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _make_workflow_context(self) -> WorkflowContext:
        """Build a WorkflowContext from the manager's own llm and tools."""
        return WorkflowContext(llm=self._llm, tools=self._tools)

    async def _run_class_and_announce(
        self,
        workflow: Workflow,
        invocation: WorkflowInvocation,
        record: WorkflowRunRecord,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._run_class(workflow, invocation, record),
                timeout=1800.0,
            )
        except asyncio.CancelledError:
            record.status = WorkflowStatus.cancelled
            record.finished_at = datetime.now()
            logger.info('[%s] workflow "%s" cancelled', record.run_id, record.workflow_name)
        except asyncio.TimeoutError:
            record.status = WorkflowStatus.failed
            record.error = 'timed out after 1800s'
            record.finished_at = datetime.now()
            logger.warning('[%s] workflow "%s" timed out', record.run_id, record.workflow_name)
        except Exception as exc:
            record.status = WorkflowStatus.failed
            record.error = f'{type(exc).__name__}: {exc}'
            record.finished_at = datetime.now()
            logger.error('[%s] workflow "%s" failed: %s', record.run_id, record.workflow_name, exc)

        try:
            await asyncio.shield(self._announce(record))
        except Exception:
            logger.exception('[%s] failed to announce workflow result', record.run_id)

    async def _run_class(
        self,
        workflow: Workflow,
        invocation: WorkflowInvocation,
        record: WorkflowRunRecord,
    ) -> None:
        logger.info('[%s] workflow "%s" started (class-based)', record.run_id, record.workflow_name)
        result = await workflow.execute(invocation, self._make_workflow_context())
        record.result = result
        record.status = WorkflowStatus.completed
        record.finished_at = datetime.now()
        logger.info('[%s] workflow "%s" completed', record.run_id, record.workflow_name)

    async def _run_and_announce(
        self,
        path: Path,
        record: WorkflowRunRecord,
        args: dict[str, Any],
    ) -> None:
        try:
            await asyncio.wait_for(self._run(path, record, args), timeout=1800.0)
        except asyncio.CancelledError:
            record.status = WorkflowStatus.cancelled
            record.finished_at = datetime.now()
            logger.info('[%s] workflow "%s" cancelled', record.run_id, record.workflow_name)
        except asyncio.TimeoutError:
            record.status = WorkflowStatus.failed
            record.error = 'timed out after 1800s'
            record.finished_at = datetime.now()
            logger.warning('[%s] workflow "%s" timed out', record.run_id, record.workflow_name)
        except Exception as exc:
            record.status = WorkflowStatus.failed
            record.error = f'{type(exc).__name__}: {exc}'
            record.finished_at = datetime.now()
            logger.error('[%s] workflow "%s" failed: %s', record.run_id, record.workflow_name, exc)

        try:
            await asyncio.shield(self._announce(record))
        except Exception:
            logger.exception('[%s] failed to announce workflow result', record.run_id)

    async def _run(
        self,
        path: Path,
        record: WorkflowRunRecord,
        args: dict[str, Any],
    ) -> None:
        from operator_use.workflow.context import WorkflowExecuteContext
        from operator_use.workflow.execute import execute
        from operator_use.workflow.types import WorkflowJournal

        runs_base = (self._workflows_dir / '.runs') if self._workflows_dir else Path(tempfile.gettempdir()) / '.operator-workflow-runs'
        run_dir = runs_base / record.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        journal = WorkflowJournal(run_dir=run_dir)
        ctx = WorkflowExecuteContext(
            record=record,
            subagent=self._subagent,
            llm=self._llm,
            tools=self._tools,
            journal=journal,
            args=args,
        )

        logger.info('[%s] workflow "%s" started', record.run_id, record.workflow_name)
        result = await execute(path, ctx)
        record.result = result
        record.status = WorkflowStatus.completed
        record.finished_at = datetime.now()
        logger.info('[%s] workflow "%s" completed', record.run_id, record.workflow_name)

    async def _announce(self, record: WorkflowRunRecord) -> None:
        if not record.channel or not record.chat_id or self._bus is None:
            logger.warning(
                '[%s] no channel/chat_id or bus — result dropped. Result:\n%s',
                record.run_id, record.result,
            )
            return

        status_label = record.status.value
        result_text = record.result or record.error or '(no output)'

        if record.status == WorkflowStatus.completed:
            content = (
                f'[Workflow result — run_id={record.run_id} name="{record.workflow_name}" status={status_label}]\n\n'
                f'{result_text}\n\n'
                f'Tell the user: workflow {record.run_id} ("{record.workflow_name}") completed. '
                f'Summarize the result in 1-2 sentences.'
            )
        else:
            content = (
                f'[Workflow result — run_id={record.run_id} name="{record.workflow_name}" status={status_label}]\n\n'
                f'{result_text}\n\n'
                f'Tell the user: workflow {record.run_id} ("{record.workflow_name}") {status_label}. '
                f'Briefly explain what went wrong based on the output above.'
            )

        from operator_use.bus.types import IncomingMessage, TextPart
        await self._bus.publish_incoming(IncomingMessage(
            channel=record.channel,
            chat_id=record.chat_id,
            parts=[TextPart(content=content)],
            user_id='workflow',
            metadata={'_workflow_result': True, 'run_id': record.run_id},
        ))
