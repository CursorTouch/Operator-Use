from __future__ import annotations

import json

import pytest

from program.builtins.tools.memory import MemoryTool
from program.memory.types import MemorySearchResult
from program.tool.types import ToolContext, ToolInvocation


class _MemoryManager:
    def __init__(self):
        self.remembered = []
        self.forgotten = []

    def search(self, query: str, *, limit: int = 5):
        return [MemorySearchResult(source="local", content=f"found {query}", score=1.0)]

    async def remember(self, content: str, metadata=None):
        self.remembered.append((content, metadata))
        return "local/memory.md"

    async def forget(self, memory_id: str):
        self.forgotten.append(memory_id)
        return True

@pytest.mark.asyncio
async def test_memory_tool_search_returns_common_result_json():
    tool = MemoryTool()
    manager = _MemoryManager()

    result = await tool.execute(
        ToolInvocation(id="1", name="memory", params={"action": "search", "query": "pytest"}),
        context=ToolContext(memory_manager=manager),
    )

    assert not result.is_error
    data = json.loads(result.content)
    assert data[0]["content"] == "found pytest"


@pytest.mark.asyncio
async def test_memory_tool_remember_uses_manager():
    tool = MemoryTool()
    manager = _MemoryManager()

    result = await tool.execute(
        ToolInvocation(id="1", name="memory", params={"action": "remember", "content": "Use pytest."}),
        context=ToolContext(memory_manager=manager),
    )

    assert not result.is_error
    assert "local/memory.md" in result.content
    assert manager.remembered[0][0] == "Use pytest."


@pytest.mark.asyncio
async def test_memory_tool_forget_uses_manager():
    tool = MemoryTool()
    manager = _MemoryManager()

    result = await tool.execute(
        ToolInvocation(id="1", name="memory", params={"action": "forget", "memory_id": "m1"}),
        context=ToolContext(memory_manager=manager),
    )

    assert not result.is_error
    assert manager.forgotten == ["m1"]


@pytest.mark.asyncio
async def test_memory_tool_errors_without_manager():
    tool = MemoryTool()

    result = await tool.execute(
        ToolInvocation(id="1", name="memory", params={"action": "search", "query": "pytest"}),
        context=ToolContext(),
    )

    assert result.is_error
