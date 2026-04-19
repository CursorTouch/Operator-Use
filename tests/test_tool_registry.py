"""Tests for ToolRegistry — register, execute, extensions, coercion."""

import pytest
from pydantic import BaseModel

from operator_use.agent.tools.registry import ToolRegistry
from operator_use.agent.tools.service import Tool


# --- Helpers ---


class EchoParams(BaseModel):
    message: str
    repeat: int = 1


class EchoTool(Tool):
    def __init__(self):
        super().__init__(name="echo", description="Echoes a message", model=EchoParams)

    def __call__(self, fn):
        self.function = fn
        return self


echo_tool = EchoTool()


@echo_tool
def _echo(message: str, repeat: int = 1, **kwargs):
    return (message + " ") * repeat


class NoModelTool(Tool):
    def __init__(self):
        super().__init__(name="no_model", description="No model", model=None)

    def __call__(self, fn):
        self.function = fn
        return self


no_model_tool = NoModelTool()


@no_model_tool
def _no_model(**kwargs):
    return "raw"


# --- Register / Unregister ---


def test_register_and_list():
    reg = ToolRegistry()
    reg.register(echo_tool)
    tools = reg.list_tools()
    assert any(t.name == "echo" for t in tools)


def test_register_duplicate_raises():
    reg = ToolRegistry()
    reg.register(echo_tool)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(echo_tool)


def test_register_no_name_raises():
    reg = ToolRegistry()
    t = Tool.__new__(Tool)
    t.name = None
    t.description = "x"
    t.model = None
    t.function = None
    with pytest.raises(ValueError, match="name is required"):
        reg.register(t)


def test_unregister():
    reg = ToolRegistry()
    reg.register(echo_tool)
    reg.unregister("echo")
    assert reg.get("echo") is None


def test_unregister_nonexistent_raises():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="not found"):
        reg.unregister("ghost")


def test_get_returns_tool():
    reg = ToolRegistry()
    reg.register(echo_tool)
    assert reg.get("echo") is echo_tool


def test_get_missing_returns_none():
    reg = ToolRegistry()
    assert reg.get("missing") is None


# --- Extensions ---


def test_set_extension():
    reg = ToolRegistry()
    reg.set_extension("_workspace", "/tmp")
    assert reg._extensions["_workspace"] == "/tmp"


def test_unset_extension():
    reg = ToolRegistry()
    reg.set_extension("_key", "val")
    reg.unset_extension("_key")
    assert "_key" not in reg._extensions


def test_unset_nonexistent_extension_no_error():
    reg = ToolRegistry()
    reg.unset_extension("nope")  # Should not raise


def test_extensions_merged_into_params():
    reg = ToolRegistry()
    reg.register(echo_tool)
    reg.set_extension("_workspace", "/some/path")
    result = reg.execute("echo", {"message": "hi"})
    assert result.success is True


# --- execute ---


def test_execute_success():
    reg = ToolRegistry()
    reg.register(echo_tool)
    result = reg.execute("echo", {"message": "hello", "repeat": 2})
    assert result.success is True
    assert "hello" in result.output


def test_execute_missing_tool():
    reg = ToolRegistry()
    result = reg.execute("nonexistent", {})
    assert result.success is False
    assert "not found" in result.error


def test_execute_validation_error():
    reg = ToolRegistry()
    reg.register(echo_tool)
    result = reg.execute("echo", {})  # missing required 'message'
    assert result.success is False
    assert "Validation error" in result.error


def test_execute_no_model_tool():
    reg = ToolRegistry()
    reg.register(no_model_tool)
    result = reg.execute("no_model", {})
    assert result.success is True
    assert result.output == "raw"


# --- aexecute ---


@pytest.mark.asyncio
async def test_aexecute_success():
    reg = ToolRegistry()
    reg.register(echo_tool)
    result = await reg.aexecute("echo", {"message": "async", "repeat": 1})
    assert result.success is True
    assert "async" in result.output


@pytest.mark.asyncio
async def test_aexecute_missing_tool():
    reg = ToolRegistry()
    result = await reg.aexecute("ghost", {})
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_aexecute_validation_error():
    reg = ToolRegistry()
    reg.register(echo_tool)
    result = await reg.aexecute("echo", {"message": 123, "repeat": "not_int"})
    assert result.success is False


@pytest.mark.asyncio
async def test_aexecute_async_tool():
    class AsyncEchoTool(Tool):
        def __init__(self):
            super().__init__(name="async_echo", description="async echo", model=EchoParams)

        def __call__(self, fn):
            self.function = fn
            return self

    at = AsyncEchoTool()

    @at
    async def _async_echo(message: str, repeat: int = 1, **kwargs):
        return f"async:{message}"

    reg = ToolRegistry()
    reg.register(at)
    result = await reg.aexecute("async_echo", {"message": "hi"})
    assert result.success is True
    assert "async:hi" in result.output


# --- register_tools / unregister_tools ---


def test_register_tools_list():
    reg = ToolRegistry()
    reg.register_tools([echo_tool])
    assert reg.get("echo") is not None


def test_unregister_tools_list():
    reg = ToolRegistry()
    reg.register_tools([echo_tool])
    reg.unregister_tools([echo_tool])
    assert reg.get("echo") is None
