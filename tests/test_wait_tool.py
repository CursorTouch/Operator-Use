from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from operator_use.tool.loader import load_tool_from_file
from operator_use.tool.types import ToolInvocation, ToolResult

def test_wait_tool_loading():
    tool_path = Path(__file__).parent.parent / 'operator_use' / 'builtins' / 'tools' / 'wait.py'
    tools, errors = load_tool_from_file(tool_path)
    
    assert not errors
    assert len(tools) == 1
    wait_tool = tools[0]
    assert wait_tool.name == 'wait'
    assert wait_tool.schema.model_json_schema()['type'] == 'object'
    assert 'seconds' in wait_tool.schema.model_json_schema()['properties']

@pytest.mark.asyncio
async def test_wait_tool_execution():
    tool_path = Path(__file__).parent.parent / 'operator_use' / 'builtins' / 'tools' / 'wait.py'
    tools, _ = load_tool_from_file(tool_path)
    wait_tool = tools[0]
    
    # Valid call
    invocation = ToolInvocation(id="1", name="wait", params={"seconds": 0.1})
    res = await wait_tool.execute(invocation)
    
    assert not res.is_error
    assert "Successfully waited for 0.1 seconds" in res.content

@pytest.mark.asyncio
async def test_wait_tool_validation():
    tool_path = Path(__file__).parent.parent / 'operator_use' / 'builtins' / 'tools' / 'wait.py'
    tools, _ = load_tool_from_file(tool_path)
    wait_tool = tools[0]
    
    # Missing parameter
    res1 = await wait_tool.execute(ToolInvocation(id="2", name="wait", params={}))
    assert res1.is_error
    assert "required" in res1.content
    
    # Negative seconds
    res2 = await wait_tool.execute(ToolInvocation(id="3", name="wait", params={"seconds": -1}))
    assert res2.is_error
    assert "greater than 0" in res2.content
    
    # Invalid type
    res3 = await wait_tool.execute(ToolInvocation(id="4", name="wait", params={"seconds": "abc"}))
    assert res3.is_error
    assert "must be a number" in res3.content
