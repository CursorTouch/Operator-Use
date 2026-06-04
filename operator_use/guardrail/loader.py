from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

from operator_use.guardrail.types import Guardrail, GuardrailError, LoadGuardrailsResult


def load_guardrail_from_file(path: Path) -> tuple[list[Guardrail], list[GuardrailError]]:
    """Load Guardrail instances from a single Python file.

    The file must export one of:
      - `guardrail`: a Guardrail instance or a callable returning one
      - `guardrails`: a list of Guardrail instances
    """
    errors: list[GuardrailError] = []
    str_path = str(path)

    try:
        spec = importlib.util.spec_from_file_location(f"_guardrail_{path.stem}", path)
        if spec is None or spec.loader is None:
            errors.append(GuardrailError(path=str_path, error=f"Cannot create module spec for {path}"))
            return [], errors

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            sys.modules.pop(spec.name, None)
            raise

        loaded: list[Guardrail] = []

        if hasattr(module, "guardrails"):
            value = module.guardrails
            if callable(value):
                value = value()
            if isinstance(value, list):
                loaded.extend(g for g in value if isinstance(g, Guardrail))
        elif hasattr(module, "guardrail"):
            value = module.guardrail
            if callable(value) and not isinstance(value, Guardrail):
                value = value()
            if isinstance(value, Guardrail):
                loaded.append(value)

        if not loaded:
            errors.append(GuardrailError(
                path=str_path,
                error='Guardrail file must export a Guardrail instance as "guardrail" or a list as "guardrails"',
            ))

        return loaded, errors

    except Exception:
        errors.append(GuardrailError(
            path=str_path,
            error=traceback.format_exc().strip().splitlines()[-1],
            stack=traceback.format_exc(),
        ))
        return [], errors


def load_guardrails_from_dir(directory: Path) -> LoadGuardrailsResult:
    """Discover and load all *.py guardrail files in a directory."""
    guardrails: list[Guardrail] = []
    errors: list[GuardrailError] = []

    if not directory.is_dir():
        return LoadGuardrailsResult(guardrails=guardrails, errors=errors)

    for file in sorted(directory.glob("*.py")):
        if file.name.startswith("_"):
            continue
        file_guardrails, file_errors = load_guardrail_from_file(file)
        guardrails.extend(file_guardrails)
        errors.extend(file_errors)

    return LoadGuardrailsResult(guardrails=guardrails, errors=errors)


def load_guardrails(dirs: list[Path]) -> LoadGuardrailsResult:
    """Load guardrails from multiple directories, deduplicating by name (first wins)."""
    seen: set[str] = set()
    all_guardrails: list[Guardrail] = []
    all_errors: list[GuardrailError] = []

    for directory in dirs:
        result = load_guardrails_from_dir(directory)
        all_errors.extend(result.errors)
        for g in result.guardrails:
            if g.name not in seen:
                seen.add(g.name)
                all_guardrails.append(g)

    return LoadGuardrailsResult(guardrails=all_guardrails, errors=all_errors)
