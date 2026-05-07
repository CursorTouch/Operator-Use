from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Type
from pydantic import BaseModel


class ToolKind(str, Enum):
    Read = "read"
    Write = "write"
    Execute = "execute"
    Web = "web"


@dataclass
class ToolInvocation:
    params: dict[str, Any] = field(default_factory=dict)
    cwd: str = ""


@dataclass
class ToolResult:
    content: Any = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        content: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(content=content, is_error=False, metadata=metadata or {})

    @classmethod
    def error(
        cls,
        content: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(content=content, is_error=True, metadata=metadata or {})

OnUpdateCallback = Callable[[ToolResult], None]

AbortSignal = asyncio.Event


class Tool(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        schema: Type[BaseModel],
        kind: ToolKind,
    ) -> None:
        self.name = name
        self.description = description
        self.schema = schema
        self.kind = kind

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
    async def execute(self, invocation: ToolInvocation, on_update: Optional[OnUpdateCallback] = None, signal: Optional[AbortSignal] = None) -> ToolResult:
        ...
