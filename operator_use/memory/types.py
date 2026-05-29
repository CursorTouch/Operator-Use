from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM


@dataclass
class MemoryOptions:
    enabled: bool = True
    max_prompt_chars: int = 6000
    sync_turns: bool = True
    prefetch: bool = True
    root_dir: Path | None = None
    user_id: str | None = None
    agent_id: str | None = None
    api_key_env: str | None = None
    config: dict[str, Any] | None = None


@dataclass
class MemoryContext:
    cwd: Path | None = None
    session_id: str = ""
    user_id: str | None = None
    project_memory_dir: Path | None = None
    global_memory_dir: Path | None = None
    metadata: dict[str, Any] | None = None
    llm: "LLM | None" = None


@dataclass
class MemorySearchResult:
    source: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] | None = None
