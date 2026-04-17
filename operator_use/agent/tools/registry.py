"""Tool registry for the agent module."""

from operator_use.agent.tools.service import Tool, ToolResult
from pathlib import Path
from typing import Any
import importlib.util
import logging

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for tools, keyed by name. Validates and executes tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._extensions: dict[str, Any] = {}

    def register_tools(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def set_extension(self, name: str, extension: Any) -> None:
        self._extensions[name] = extension

    def unset_extension(self, name: str) -> None:
        self._extensions.pop(name, None)

    def unregister_tools(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.unregister(tool.name)

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        if tool.name is None:
            raise ValueError("Tool name is required")
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        if name not in self._tools:
            raise ValueError(f"Tool '{name}' not found")
        self._tools.pop(name, None)

    def register_workspace_tools(self, tools_dir: Path, skip_existing: bool = True) -> None:
        """Dynamically load and register Tool instances from all *.py files in a directory."""
        if not tools_dir.exists():
            return
        for path in sorted(tools_dir.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(f"_workspace_tool_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, Tool) and attr.function is not None:
                        try:
                            self.register(attr)
                            logger.info(f"Workspace tool loaded | name={attr.name} file={path.name}")
                        except ValueError:
                            logger.warning(f"Workspace tool skipped (name conflict) | name={attr.name}")
            except Exception as e:
                logger.warning(f"Failed to load workspace tool | file={path.name} error={e}")

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get(self, name: str) -> "Tool | Any | None":
        """Get a tool by name. Also checks extensions (e.g. browser, desktop instances)."""
        result = self._tools.get(name)
        if result is not None:
            return result
        return self._extensions.get(name)

    def _merge_params(self, params: dict) -> dict:
        """Merge extensions with params. Params override extensions for same keys."""
        return {**self._extensions, **params}

    def _coerce_params(self, tool: Tool, params: dict) -> tuple[list[str], dict]:
        """Validate and coerce params through the tool's Pydantic model.
        Returns (errors, coerced_params). On validation failure, errors is non-empty."""
        from pydantic import ValidationError

        try:
            instance = tool.model(**params)
            coerced = {
                k: v for k, v in instance.model_dump().items() if k in params or v is not None
            }
            return [], coerced
        except ValidationError:
            errors = tool.validate_params(params)
            return errors, params
        except Exception as e:
            return [str(e)], params

    def execute(self, name: str, params: dict) -> ToolResult:
        """Validate params, execute the tool by name, and return the result."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.error_result(f"Tool '{name}' not found")
        if tool.model is not None:
            errors, coerced = self._coerce_params(tool, params)
            if errors:
                return ToolResult.error_result(f"Validation error: {'; '.join(errors)}")
        else:
            coerced = params
        merged = self._merge_params(coerced)
        try:
            tool_result = tool.invoke(**merged)
            return tool_result
        except Exception as e:
            return ToolResult.error_result(f"Error executing tool '{name}': {e}")

    async def aexecute(self, name: str, params: dict) -> ToolResult:
        """Validate params, execute the tool asynchronously, and return the result."""
        tool = self._tools.get(name)
        if not tool:
            logger.debug(f"Tool '{name}' not found. Available: {list(self._tools.keys())}")
            return ToolResult.error_result(f"Tool '{name}' not found")
        if tool.model is not None:
            errors, coerced = self._coerce_params(tool, params)
            if errors:
                logger.debug(f"Validation error for '{name}': {errors}")
                return ToolResult.error_result(f"Validation error: {'; '.join(errors)}")
        else:
            coerced = params
        merged = self._merge_params(coerced)
        try:
            tool_result = await tool.ainvoke(**merged)
            return tool_result
        except Exception as e:
            logger.debug(f"Exception in tool '{name}': {e}", exc_info=True)
            return ToolResult.error_result(f"Error executing tool '{name}': {e}")
