"""Project-local resource loading: <cwd>/.operator/<type>/ scanned automatically."""
from pathlib import Path

from operator_use.resource.loader import ResourceLoader
from operator_use.resource.types import ResourceLoaderOptions


def _make_loader(cwd: Path) -> ResourceLoader:
    return ResourceLoader(ResourceLoaderOptions(cwd=cwd, config_dir=cwd / ".operator"))


_TOOL_SRC = '''
from pydantic import BaseModel
from operator_use.tool.types import Tool, ToolKind, ToolResult


class Params(BaseModel):
    text: str


class ShoutTool(Tool):
    def __init__(self):
        super().__init__(name="project_shout", description="Shout.", schema=Params, kind=ToolKind.Read)

    async def execute(self, invocation, tool_execution_update_callback=None, signal=None, context=None):
        return ToolResult.ok(invocation.id, "OK")


tool = ShoutTool()
'''


class TestProjectResourceDir:
    def test_returns_dir_when_present(self, tmp_path):
        (tmp_path / ".operator" / "tools").mkdir(parents=True)
        loader = _make_loader(tmp_path)
        assert loader._project_resource_dir("tools") == tmp_path / ".operator" / "tools"

    def test_returns_none_when_absent(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader._project_resource_dir("tools") is None

    def test_returns_none_when_no_operator_dir(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader._project_resource_dir("extensions") is None


class TestProjectToolLoading:
    def test_loads_tool_from_project_operator_dir(self, tmp_path):
        tools_dir = tmp_path / ".operator" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "shout.py").write_text(_TOOL_SRC, encoding="utf-8")

        loader = _make_loader(tmp_path)
        loader._reload_tools()

        names = {t.name for t in loader.get_tools()}
        assert "project_shout" in names

    def test_no_project_tools_when_dir_absent(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader._reload_tools()
        names = {t.name for t in loader.get_tools()}
        assert "project_shout" not in names  # only builtins loaded


_HOOK_SRC = '''
async def _on_end(event):
    return None

hooks = [("agent_end", _on_end)]
'''


class TestProjectHookLoading:
    def test_loads_hook_from_project_operator_dir(self, tmp_path):
        hooks_dir = tmp_path / ".operator" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "logger.py").write_text(_HOOK_SRC, encoding="utf-8")

        loader = _make_loader(tmp_path)
        loader._reload_hooks()

        events = {event_type for event_type, _ in loader.get_hooks()}
        assert "agent_end" in events
