"""execute.py — load and run workflow Python files."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from operator_use.workflow.types import WorkflowMeta

if TYPE_CHECKING:
    from operator_use.workflow.context import WorkflowExecuteContext


def load_meta(path: Path) -> WorkflowMeta:
    """Parse `meta = {...}` dict from a workflow file using AST, without executing code.

    Falls back to stem-based metadata if the assignment is missing or invalid.
    """
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    # Walk the AST looking for a top-level `meta = {...}` assignment.
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
                        deliver=bool(meta_dict.get('deliver', True)),
                    )
    # No valid meta found; use filename as the name.
    return WorkflowMeta(name=path.stem, description='')


async def execute(path: Path, ctx: WorkflowExecuteContext) -> str:
    """Compile and execute a workflow file, invoke its run() function, return stringified result."""
    source = path.read_text(encoding='utf-8')
    code = compile(source, str(path), 'exec')

    # Build the execution namespace with workflow globals (agent, phase, log, etc).
    namespace: dict = {'__builtins__': __builtins__}
    namespace.update(ctx.as_globals())

    # Execute the user's workflow code to define 'run' and any other helpers.
    exec(code, namespace)  # noqa: S102

    run_fn = namespace.get('run')
    if run_fn is None:
        raise ValueError(f"Workflow '{path.name}' has no top-level 'run' function.")

    # Call run() — support both sync and async functions.
    if inspect.iscoroutinefunction(run_fn):
        result = await run_fn()
    else:
        result = run_fn()

    return str(result) if result is not None else ''
