"""Tests for Tool base class, ToolResult, and schema generation."""

import pytest
from pydantic import BaseModel
from typing import Literal

from operator_use.tools.service import Tool, ToolResult


# --- ToolResult ---


def test_tool_result_success():
    r = ToolResult.success_result("done")
    assert r.success is True
    assert r.output == "done"
    assert r.error is None


def test_tool_result_error():
    r = ToolResult.error_result("something went wrong")
    assert r.success is False
    assert r.error == "something went wrong"
    assert r.output is None


def test_tool_result_with_metadata():
    r = ToolResult.success_result("ok", metadata={"key": "value"})
    assert r.metadata == {"key": "value"}


# --- Concrete Tool subclass for testing ---


class SearchParams(BaseModel):
    query: str
    limit: int = 10


class SearchTool(Tool):
    def __init__(self):
        super().__init__(name="search", description="Search the web", model=SearchParams)

    def __call__(self, function):
        self.function = function
        return self


search_tool = SearchTool()


@search_tool
def _search_fn(query: str, limit: int = 10):
    return f"results for {query}"


# --- json_schema ---


def test_json_schema_name():
    assert search_tool.json_schema["name"] == "search"


def test_json_schema_description():
    assert search_tool.json_schema["description"] == "Search the web"


def test_json_schema_has_parameters():
    params = search_tool.json_schema["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert "limit" in params["properties"]


def test_json_schema_no_title():
    schema = search_tool.json_schema
    for prop in schema["parameters"]["properties"].values():
        assert "title" not in prop


def test_json_schema_required_fields():
    schema = search_tool.json_schema
    assert "query" in schema["parameters"]["required"]
    assert "limit" not in schema["parameters"]["required"]


# --- validate_params ---


def test_validate_params_valid():
    errors = search_tool.validate_params({"query": "hello"})
    assert errors == []


def test_validate_params_missing_required():
    errors = search_tool.validate_params({})
    assert any("query" in e for e in errors)


def test_validate_params_wrong_type():
    errors = search_tool.validate_params({"query": "ok", "limit": "not_an_int"})
    assert len(errors) > 0


# --- invoke ---


def test_invoke_success():
    result = search_tool.invoke(query="python", limit=5)
    assert result.success is True
    assert "python" in result.output


def test_invoke_exception_returns_error():
    class BrokenTool(Tool):
        def __init__(self):
            super().__init__(name="broken", description="breaks", model=SearchParams)

    bt = BrokenTool()

    @bt
    def _broken(query: str, limit: int = 10):
        raise RuntimeError("boom")

    result = bt.invoke(query="test")
    assert result.success is False
    assert "boom" in result.error


# --- ainvoke ---


@pytest.mark.asyncio
async def test_ainvoke_sync_function():
    result = await search_tool.ainvoke(query="async test", limit=3)
    assert result.success is True
    assert "async test" in result.output


@pytest.mark.asyncio
async def test_ainvoke_async_function():
    class AsyncTool(Tool):
        def __init__(self):
            super().__init__(name="async_tool", description="async", model=SearchParams)

    at = AsyncTool()

    @at
    async def _async_fn(query: str, limit: int = 10):
        return f"async result for {query}"

    result = await at.ainvoke(query="async", limit=1)
    assert result.success is True
    assert "async result for async" in result.output


@pytest.mark.asyncio
async def test_ainvoke_exception_returns_error():
    class AsyncBrokenTool(Tool):
        def __init__(self):
            super().__init__(name="async_broken", description="breaks", model=SearchParams)

    abt = AsyncBrokenTool()

    @abt
    async def _async_broken(query: str, limit: int = 10):
        raise ValueError("async boom")

    result = await abt.ainvoke(query="test")
    assert result.success is False
    assert "async boom" in result.error


# --- Literal validation ---


class ModeParams(BaseModel):
    mode: Literal["read", "write"]


class ModeTool(Tool):
    def __init__(self):
        super().__init__(name="mode_tool", description="mode", model=ModeParams)


mode_tool = ModeTool()


@mode_tool
def _mode_fn(mode: str):
    return mode


def test_validate_params_literal_error():
    errors = mode_tool.validate_params({"mode": "delete"})
    assert len(errors) > 0
    assert "mode" in errors[0]
