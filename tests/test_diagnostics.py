"""Tests for the diagnostics module: collision detection and extension error surfacing."""
import pytest
from program.diagnostics.service import (
    detect_extension_tool_collisions,
    detect_extension_command_collisions,
    collect_extension_errors,
    run_diagnostics,
)
from program.extension.types import (
    Extension, ExtensionError, LoadExtensionsResult,
    RegisteredTool, RegisteredCommand, ToolDefinition,
)
from program.skill.types import LoadSkillsResult, ResourceDiagnostic, SourceInfo
from program.tool.types import ToolExecutionMode, ToolResult, ToolInvocation
from pydantic import BaseModel


# ── Factories ─────────────────────────────────────────────────────────────────

def make_source(path: str = "/ext/a.py") -> SourceInfo:
    return SourceInfo(path=path, source="extension")


async def _noop_execute(invocation: ToolInvocation, **_) -> ToolResult:
    return ToolResult(id=invocation.id, content="", is_error=False, metadata={})


class EmptyParams(BaseModel):
    pass


def make_tool_def(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"tool {name}",
        parameters=EmptyParams,
        execute=_noop_execute,
        execution_mode=ToolExecutionMode.Sequential,
    )


def make_extension(path: str, tools: list[str] = (), commands: list[str] = ()) -> Extension:
    ext = Extension(path=path, source_info=make_source(path))
    for t in tools:
        ext.tools[t] = RegisteredTool(definition=make_tool_def(t), source_info=make_source(path))
    for c in commands:
        from program.extension.types import RegisteredCommand
        ext.commands[c] = RegisteredCommand(
            name=c,
            source_info=make_source(path),
            description=None,
            handler=lambda *a: None,
        )
    return ext


def make_result(*extensions: Extension, errors: list[ExtensionError] = ()) -> LoadExtensionsResult:
    return LoadExtensionsResult(extensions=list(extensions), errors=list(errors))


# ── Tool collision tests ───────────────────────────────────────────────────────

class TestToolCollisions:
    def test_no_collision_single_extension(self):
        result = make_result(make_extension("/a.py", tools=["read", "write"]))
        assert detect_extension_tool_collisions(result) == []

    def test_no_collision_distinct_tools(self):
        result = make_result(
            make_extension("/a.py", tools=["read"]),
            make_extension("/b.py", tools=["write"]),
        )
        assert detect_extension_tool_collisions(result) == []

    def test_collision_detected(self):
        result = make_result(
            make_extension("/a.py", tools=["search"]),
            make_extension("/b.py", tools=["search"]),
        )
        diags = detect_extension_tool_collisions(result)
        assert len(diags) == 1
        assert diags[0].type == "collision"
        assert diags[0].collision is not None
        assert diags[0].collision.name == "search"
        assert diags[0].collision.resource_type == "tool"

    def test_collision_winner_is_first_registrant(self):
        result = make_result(
            make_extension("/first.py", tools=["shared"]),
            make_extension("/second.py", tools=["shared"]),
        )
        diags = detect_extension_tool_collisions(result)
        assert diags[0].collision.winner_path == "/first.py"
        assert diags[0].collision.loser_path == "/second.py"

    def test_multiple_collisions(self):
        result = make_result(
            make_extension("/a.py", tools=["x", "y"]),
            make_extension("/b.py", tools=["x", "y"]),
        )
        diags = detect_extension_tool_collisions(result)
        assert len(diags) == 2
        names = {d.collision.name for d in diags}
        assert names == {"x", "y"}

    def test_no_self_collision(self):
        result = make_result(make_extension("/a.py", tools=["same"]))
        assert detect_extension_tool_collisions(result) == []


# ── Command collision tests ────────────────────────────────────────────────────

class TestCommandCollisions:
    def test_no_collision(self):
        result = make_result(
            make_extension("/a.py", commands=["foo"]),
            make_extension("/b.py", commands=["bar"]),
        )
        assert detect_extension_command_collisions(result) == []

    def test_collision_detected(self):
        result = make_result(
            make_extension("/a.py", commands=["compact"]),
            make_extension("/b.py", commands=["compact"]),
        )
        diags = detect_extension_command_collisions(result)
        assert len(diags) == 1
        assert diags[0].collision.resource_type == "command"
        assert diags[0].collision.name == "compact"


# ── Extension error surfacing ──────────────────────────────────────────────────

class TestExtensionErrors:
    def test_no_errors(self):
        result = make_result(make_extension("/a.py"))
        assert collect_extension_errors(result) == []

    def test_error_becomes_error_diagnostic(self):
        err = ExtensionError(
            extension_path="/bad.py",
            event="load",
            error="SyntaxError: unexpected token",
        )
        result = make_result(errors=[err])
        diags = collect_extension_errors(result)
        assert len(diags) == 1
        assert diags[0].type == "error"
        assert diags[0].path == "/bad.py"
        assert "SyntaxError" in diags[0].message

    def test_multiple_errors(self):
        errors = [
            ExtensionError(extension_path=f"/ext{i}.py", event="load", error="err")
            for i in range(3)
        ]
        result = make_result(errors=errors)
        diags = collect_extension_errors(result)
        assert len(diags) == 3


# ── run_diagnostics ───────────────────────────────────────────────────────────

class TestRunDiagnostics:
    def test_empty_result_no_diagnostics(self):
        result = make_result()
        assert run_diagnostics(result) == []

    def test_aggregates_all_sources(self):
        ext_result = make_result(
            make_extension("/a.py", tools=["t"]),
            make_extension("/b.py", tools=["t"], commands=["cmd"]),
            make_extension("/c.py", commands=["cmd"]),
            errors=[ExtensionError(extension_path="/bad.py", event="load", error="oops")],
        )
        skill_result = LoadSkillsResult(
            skills=[],
            diagnostics=[ResourceDiagnostic(type="warning", message="skill warn", path="/sk.md")],
        )
        diags = run_diagnostics(ext_result, skills_result=skill_result)
        types = {d.type for d in diags}
        assert "error" in types       # ext load error
        assert "collision" in types   # tool + cmd collision
        assert "warning" in types     # skill diagnostic
        assert len(diags) >= 4  # 1 ext error + 1 tool collision + 1 cmd collision + 1 skill warn

    def test_without_skills_result(self):
        result = make_result(
            make_extension("/a.py", tools=["dup"]),
            make_extension("/b.py", tools=["dup"]),
        )
        diags = run_diagnostics(result)
        assert len(diags) == 1
        assert diags[0].collision.name == "dup"
