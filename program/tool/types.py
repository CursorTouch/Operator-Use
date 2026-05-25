from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Type
from pydantic import BaseModel


@dataclass
class ToolError:
    path: str
    error: str
    stack: str = ''


@dataclass
class LoadToolsResult:
    tools: list[Tool] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)


class ToolKind(str, Enum):
    Read = "read"
    Edit = "edit"
    Write = "write"
    Execute = "execute"
    Web = "web"
    Unknown = "unknown"


class ToolExecutionMode(str, Enum):
    Sequential = "sequential"
    Parallel = "parallel"
    Batch = "batch"


@dataclass
class ToolInvocation:
    id: str
    params: dict[str, Any] = field(default_factory=dict)
    cwd: str = ""
    name: str = ""


@dataclass
class ToolResult:
    id: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    terminate: bool = False

    @classmethod
    def ok(
        cls,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(id=id, content=content, is_error=False, metadata=metadata or {})

    @classmethod
    def error(
        cls,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(id=id, content=content, is_error=True, metadata=metadata or {})

ToolExecutionUpdateCallback = Callable[[ToolResult], Awaitable[None]]

AbortSignal = asyncio.Event


@dataclass
class ToolContext:
    """Runtime services available to tools during execution."""
    llm: Any | None = None
    engine: Any | None = None
    agent: Any | None = None
    session_manager: Any | None = None
    resource_loader: Any | None = None
    extension_runtime: Any | None = None
    hooks: Any | None = None
    subagent_manager: Any | None = None
    bus: Any | None = None
    cron: Any | None = None
    mcp_manager: Any | None = None
    memory_manager: Any | None = None
    process_manager: Any | None = None
    settings_manager: Any | None = None
    auth_manager: Any | None = None
    acp_auth: Any | None = None
    acp_manager: Any | None = None


class Tool(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        schema: Type[BaseModel],
        kind: ToolKind,
        execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential,
    ) -> None:
        self.name = name
        self.description = description
        self.schema = schema
        self.kind = kind
        self.execution_mode = execution_mode

    def validate(self, params: dict[str, Any]) -> tuple[bool, list[str]]:
        try:
            self.schema.model_validate(params)
            return True, []
        except Exception as e:
            from pydantic import ValidationError
            if isinstance(e, ValidationError):
                errors = [
                    f"{' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                ]
            else:
                errors = [str(e)]
            return False, errors

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema.model_json_schema(),
        }

    def _is_cancelled(self, signal: Optional[AbortSignal]) -> bool:
        return signal is not None and signal.is_set()

    @abstractmethod
    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Optional[ToolExecutionUpdateCallback] = None,
        signal: Optional[AbortSignal] = None,
        context: Optional[ToolContext] = None,
    ) -> ToolResult:
        ...
