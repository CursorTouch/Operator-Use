from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Type
from pydantic import BaseModel

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.engine.service import Engine
    from operator_use.agent.service import Agent
    from operator_use.session.manager import SessionManager
    from operator_use.resource.loader import ResourceLoader
    from operator_use.extension.runtime import ExtensionRuntime
    from operator_use.hooks.service import Hooks
    from operator_use.subagent.manager import SubagentManager
    from operator_use.workflow.manager import WorkflowManager
    from operator_use.bus.service import Bus
    from operator_use.cron.scheduler import CronScheduler
    from operator_use.mcp.manager import MCPManager
    from operator_use.memory.manager import MemoryManager
    from operator_use.computer.types import Desktop
    from operator_use.browser.client.service import Browser
    from operator_use.process.manager import ProcessManager
    from operator_use.settings.manager import SettingsManager
    from operator_use.auth.channels import ChannelAuthManager
    from operator_use.auth.acp import ACPAuthManager
    from operator_use.acp.manager import ACPSessionManager
    from operator_use.peer.manager import PeerSessionManager
    from operator_use.team.manager import TeamManager


@dataclass
class ToolError:
    """File-level tool load failure with optional stack trace."""
    path: str
    error: str
    stack: str = ''


@dataclass
class LoadToolsResult:
    """Aggregate result of loading tools from one or more directories."""

    tools: list[Tool] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)


class ToolKind(str, Enum):
    """Semantic category used by the engine to apply execution policy to a tool call."""

    Read = "read"
    Edit = "edit"
    Write = "write"
    Execute = "execute"
    Web = "web"
    Cron = "cron"
    Agent = "agent"
    Workflow = "workflow"
    Plan = "plan"
    Automation = "automation"
    MCP = "mcp"
    Unknown = "unknown"


class ToolExecutionMode(str, Enum):
    """Controls how the engine schedules concurrent calls to the same tool."""

    Sequential = "sequential"
    Parallel = "parallel"
    Batch = "batch"


@dataclass
class ToolInvocation:
    """Complete tool call specification with resolved args and execution context."""
    id: str
    params: dict[str, Any] = field(default_factory=dict)
    cwd: str = ""
    name: str = ""


@dataclass
class ToolResult:
    """Tool execution outcome with optional error flag and early termination signal."""
    id: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    terminate: bool = False
    terminate_message: str | None = None

    @classmethod
    def ok(
        cls,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a successful outcome."""
        return cls(id=id, content=content, is_error=False, metadata=metadata or {})

    @classmethod
    def error(
        cls,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a failed outcome."""
        return cls(id=id, content=content, is_error=True, metadata=metadata or {})

ToolExecutionUpdateCallback = Callable[[ToolResult], Awaitable[None]]

AbortSignal = asyncio.Event


@dataclass
class ToolContext:
    """Runtime services available to tools during execution (LLM, agents, managers, etc)."""
    llm: LLM | None = None
    engine: Engine | None = None
    agent: Agent | None = None
    session_manager: SessionManager | None = None
    resource_loader: ResourceLoader | None = None
    extension_runtime: ExtensionRuntime | None = None
    hooks: Hooks | None = None
    subagent_manager: SubagentManager | None = None
    workflow_manager: WorkflowManager | None = None
    bus: Bus | None = None
    cron: CronScheduler | None = None
    mcp_manager: MCPManager | None = None
    memory_manager: MemoryManager | None = None
    desktop: Desktop | None = None
    browser: Browser | None = None
    process_manager: ProcessManager | None = None
    settings_manager: SettingsManager | None = None
    auth_channel_manager: ChannelAuthManager | None = None
    acp_auth_manager: ACPAuthManager | None = None
    acp_session_manager: ACPSessionManager | None = None
    peer_session_manager: PeerSessionManager | None = None
    peer_agents: dict[str, Agent] | None = None
    team_manager: TeamManager | None = None
    spawn_depth: int = 0


class Tool(ABC):
    """Abstract base for tools: executable, schema-validated components with metadata and policy."""
    def __init__(
        self,
        name: str,
        description: str,
        schema: Type[BaseModel],
        kind: ToolKind,
        execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential,
        display_name: str = "",
    ) -> None:
        """Initialize tool with name, description, schema, kind, and execution concurrency policy."""
        self.name = name
        self.description = description
        self.schema = schema
        self.kind = kind
        self.execution_mode = execution_mode
        self.display_name = display_name

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Return a human-readable channel label based on call args; override for intent-specific messages.

        Falls back to display_name then name when not overridden.
        """
        return self.display_name or self.name

    def is_available(self, context: ToolContext) -> bool:
        """Check if tool should be available given current service availability."""
        return True

    def validate(self, params: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate params against schema; return (success, error_list)."""
        try:
            self.schema.model_validate(params)
            return True, []
        except Exception as e:
            from pydantic import ValidationError
            # Format Pydantic errors with field path for clarity
            if isinstance(e, ValidationError):
                errors = [
                    f"{' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                ]
            else:
                errors = [str(e)]
            return False, errors

    def to_json(self) -> dict[str, Any]:
        """Serialize to JSON schema with name, description, and input_schema."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema.model_json_schema(),
        }

    def _is_cancelled(self, signal: Optional[AbortSignal]) -> bool:
        """Check if abort signal has been set."""
        return signal is not None and signal.is_set()

    @abstractmethod
    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Optional[ToolExecutionUpdateCallback] = None,
        signal: Optional[AbortSignal] = None,
        context: Optional[ToolContext] = None,
    ) -> ToolResult:
        """Execute the tool with params; subclasses must override."""
        ...
