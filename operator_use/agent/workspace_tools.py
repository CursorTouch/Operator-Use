import importlib.util
import logging
from pathlib import Path

from operator_use.tools.service import Tool

logger = logging.getLogger(__name__)


def load_workspace_tools(tools_dir: Path) -> list[Tool]:
    """Load Tool instances from all *.py files in workspace/tools/."""
    if not tools_dir.exists():
        return []
    tools = []
    for path in sorted(tools_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"_workspace_tool_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, Tool) and attr.function is not None:
                    tools.append(attr)
                    logger.info(f"Workspace tool loaded | name={attr.name} file={path.name}")
        except Exception as e:
            logger.warning(f"Failed to load workspace tool | file={path.name} error={e}")
    return tools
