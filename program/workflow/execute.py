"""execute.py — load and run workflow Python files."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from program.workflow.types import WorkflowMeta

if TYPE_CHECKING:
    from program.workflow.context import WorkflowContext


def load_meta(path: Path) -> WorkflowMeta:
    """Extract `meta = {...}` from a workflow file without executing the body."""
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'meta':
                    try:
                        meta_dict = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(meta_dict, dict):
                        continue
                    return WorkflowMeta(
                        name=str(meta_dict.get('name', path.stem)),
                        description=str(meta_dict.get('description', '')),
                        when_to_use=meta_dict.get('when_to_use'),
                        phases=list(meta_dict.get('phases', [])),
                    )
    return WorkflowMeta(name=path.stem, description='')


async def execute(path: Path, ctx: WorkflowContext) -> str:
    """Exec the workflow file and await its `run()` function. Returns the result string."""
    source = path.read_text(encoding='utf-8')
    code = compile(source, str(path), 'exec')

    namespace: dict = {'__builtins__': __builtins__}
    namespace.update(ctx.as_globals())

    exec(code, namespace)  # noqa: S102

    run_fn = namespace.get('run')
    if run_fn is None:
        raise ValueError(f"Workflow '{path.name}' has no top-level 'run' function.")

    if inspect.iscoroutinefunction(run_fn):
        result = await run_fn()
    else:
        result = run_fn()

    return str(result) if result is not None else ''
