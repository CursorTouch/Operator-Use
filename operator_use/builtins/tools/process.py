from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from operator_use.process.manager import ProcessManager


class ProcessSchema(BaseModel):
    action: Literal['start', 'spawn_agent', 'write', 'list', 'get', 'stop', 'output'] = Field(
        description=(
            'Action to perform:\n'
            '  start        — launch a shell command as a background process\n'
            '  spawn_agent  — spawn a background agent (operator acp serve) with an initial prompt\n'
            '  write        — send a follow-up prompt to a running agent process\n'
            '  list         — list all processes and their status\n'
            '  get          — get full details of one process\n'
            '  stop         — terminate a running process (SIGTERM → SIGKILL)\n'
            '  output       — read the last N bytes of a process log'
        )
    )
    command: str | None = Field(
        default=None,
        description="Shell command to run. Required for action='start'.",
    )
    description: str | None = Field(
        default=None,
        description="Short label for the process. Required for action='start' and 'spawn_agent'.",
    )
    process_id: str | None = Field(
        default=None,
        description="Process ID. Required for get, stop, output, write.",
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
    prompt: str | None = Field(
        default=None,
        description="Initial prompt for action='spawn_agent', or follow-up for action='write'.",
    )
    provider: str | None = Field(
        default=None,
        description="LLM provider for action='spawn_agent' (e.g. 'anthropic').",
    )
    model: str | None = Field(
        default=None,
        description="Model ID for action='spawn_agent' (e.g. 'claude-sonnet-4-6').",
    )

    @model_validator(mode='after')
    def _check_fields(self) -> 'ProcessSchema':
        if self.action == 'start':
            missing = [f for f, v in [('command', self.command), ('description', self.description)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='start'.")
        elif self.action == 'spawn_agent':
            missing = [f for f, v in [('prompt', self.prompt), ('description', self.description),
                                       ('provider', self.provider), ('model', self.model)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='spawn_agent'.")
        elif self.action == 'write':
            missing = [f for f, v in [('process_id', self.process_id), ('prompt', self.prompt)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='write'.")
        elif self.action in {'get', 'stop', 'output'} and not self.process_id:
            raise ValueError(f"'process_id' is required for action='{self.action}'.")
        return self


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
                'Manage long-running background processes — both shell commands and AI agents.\n'
                '  start       — launch a shell command in the background\n'
                '  spawn_agent — spawn a background agent (operator acp serve) with an initial prompt; '
                'output is written to a disk log file\n'
                '  write       — send a follow-up prompt to a running agent process\n'
                '  output      — read the process log (shell: memory buffer; agent: disk log)\n'
                '  list/get    — inspect processes by status or id\n'
                '  stop        — terminate a process'
            ),
            schema=ProcessSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,        )
        self._manager = manager

    def get_display_name(self, args: dict) -> str:
        action = args.get('action', '')
        command = args.get('command', '') or ''
        description = args.get('description', '') or ''
        process_id = args.get('process_id', '') or ''
        if action == 'start': return f"Starting: {description or command[:40]}" if (description or command) else "Starting process"
        if action == 'spawn_agent': return f"Spawning agent: {description[:40]}" if description else "Spawning agent"
        if action == 'write': return f"Writing to: {process_id}" if process_id else "Writing to process"
        if action == 'list': return "Listing processes"
        if action == 'get': return f"Getting process: {process_id}" if process_id else "Getting process"
        if action == 'stop': return f"Stopping: {process_id}" if process_id else "Stopping process"
        if action == 'output': return f"Reading output: {process_id}" if process_id else "Reading output"
        return "Process"

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

                case 'spawn_agent':
                    prompt = params.get('prompt')
                    description = params.get('description')
                    provider = params.get('provider')
                    model = params.get('model')
                    if not all([prompt, description, provider, model]):
                        return ToolResult.error(invocation.id, "'prompt', 'description', 'provider', and 'model' are required for action='spawn_agent'.")
                    record = await manager.create_agent(
                        prompt=prompt,
                        description=description,
                        provider=provider,
                        model=model,
                        cwd=params.get('cwd'),
                    )
                    return ToolResult.ok(
                        invocation.id,
                        f"Agent process spawned. Output will be written to disk.\n{json.dumps(_format_record(record), indent=2)}",
                    )

                case 'write':
                    process_id = params.get('process_id')
                    prompt = params.get('prompt')
                    if not process_id:
                        return ToolResult.error(invocation.id, "'process_id' is required for action='write'.")
                    if not prompt:
                        return ToolResult.error(invocation.id, "'prompt' is required for action='write'.")
                    await manager.write(process_id, prompt)
                    return ToolResult.ok(invocation.id, f"Prompt sent to agent process '{process_id}'.")

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
