from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from operator_use.process.manager import ProcessManager


class ProcessSchema(BaseModel):
    action: Literal['start', 'list', 'get', 'stop', 'output'] = Field(
        description=(
            'Action to perform:\n'
            '  start  — launch a shell command as a background process\n'
            '  list   — list all processes and their status\n'
            '  get    — get full details of one process\n'
            '  stop   — terminate a running process (SIGTERM → SIGKILL)\n'
            '  output — read the last N bytes of a process log'
        )
    )
    command: str | None = Field(
        default=None,
        description="Shell command to run. Required for action='start'.",
    )
    description: str | None = Field(
        default=None,
        description="Short label for the process. Required for action='start'.",
    )
    process_id: str | None = Field(
        default=None,
        description="Process ID (e.g. 'p3f7e2c1'). Required for get, stop, output.",
    )
    cwd: str | None = Field(
        default=None,
        description="Working directory for the process. Defaults to the agent's cwd.",
    )
    status_filter: Literal['running', 'completed', 'failed', 'killed'] | None = Field(
        default=None,
        description="Filter list results by status. Omit to list all.",
    )
    max_bytes: int = Field(
        default=12000,
        description="Maximum bytes of output to return for action='output'.",
    )


def _format_record(r) -> dict:
    return {
        'id': r.id,
        'description': r.description,
        'command': r.command,
        'status': r.status,
        'cwd': r.cwd,
        'started_at': r.started_at,
        'ended_at': r.ended_at,
        'return_code': r.return_code,
    }


class ProcessTool(Tool):
    def __init__(self, manager: ProcessManager | None = None) -> None:
        super().__init__(
            name='process',
            description=(
                'Manage long-running background shell processes. '
                'Start a command and let it run without blocking the agent, '
                'check its status, read its output, or stop it at any time.'
            ),
            schema=ProcessSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,        )
        self._manager = manager

    def is_available(self, context) -> bool:
        return (self._manager or context.process_manager) is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        manager = self._manager or (context.process_manager if context else None)
        if manager is None:
            return ToolResult.error(invocation.id, 'Process manager is not available.')

        import json
        from operator_use.process.types import ProcessStatus

        params = invocation.params
        action = params.get('action')

        try:
            match action:
                case 'start':
                    command = params.get('command')
                    description = params.get('description')
                    if not command:
                        return ToolResult.error(invocation.id, "'command' is required for action='start'.")
                    if not description:
                        return ToolResult.error(invocation.id, "'description' is required for action='start'.")
                    record = await manager.create(
                        command=command,
                        description=description,
                        cwd=params.get('cwd'),
                    )
                    return ToolResult.ok(
                        invocation.id,
                        f"Process started.\n{json.dumps(_format_record(record), indent=2)}",
                    )

                case 'list':
                    status_filter = params.get('status_filter')
                    status = ProcessStatus(status_filter) if status_filter else None
                    records = manager.list(status=status)
                    if not records:
                        msg = f"No processes" + (f" with status='{status_filter}'." if status_filter else ".")
                        return ToolResult.ok(invocation.id, msg)
                    return ToolResult.ok(
                        invocation.id,
                        json.dumps([_format_record(r) for r in records], indent=2),
                    )

                case 'get':
                    process_id = params.get('process_id')
                    if not process_id:
                        return ToolResult.error(invocation.id, "'process_id' is required for action='get'.")
                    record = manager.get(process_id)
                    if record is None:
                        return ToolResult.error(invocation.id, f"No process found with id='{process_id}'.")
                    return ToolResult.ok(
                        invocation.id,
                        json.dumps(_format_record(record), indent=2),
                    )

                case 'stop':
                    process_id = params.get('process_id')
                    if not process_id:
                        return ToolResult.error(invocation.id, "'process_id' is required for action='stop'.")
                    record = await manager.stop(process_id)
                    return ToolResult.ok(
                        invocation.id,
                        f"Process '{process_id}' stopped.\n{json.dumps(_format_record(record), indent=2)}",
                    )

                case 'output':
                    process_id = params.get('process_id')
                    if not process_id:
                        return ToolResult.error(invocation.id, "'process_id' is required for action='output'.")
                    output = manager.read_output(process_id, max_bytes=params.get('max_bytes', 12000))
                    if not output:
                        return ToolResult.ok(invocation.id, f"No output yet for process '{process_id}'.")
                    return ToolResult.ok(invocation.id, output)

                case _:
                    return ToolResult.error(invocation.id, f"Unknown action '{action}'.")

        except KeyError as e:
            return ToolResult.error(invocation.id, str(e))
        except Exception as e:
            return ToolResult.error(invocation.id, f"Process operation failed: {e}")


tool = ProcessTool()
