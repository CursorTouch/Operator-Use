from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.diagnostics.types import CollisionInfo, ResourceDiagnostic

if TYPE_CHECKING:
    from operator_use.extension.types import ExtensionError, LoadExtensionsResult
    from operator_use.extension.runtime import ExtensionRuntime
    from operator_use.skill.types import LoadSkillsResult


def detect_extension_tool_collisions(result: LoadExtensionsResult) -> list[ResourceDiagnostic]:
    """Detect tool name collisions across loaded extensions (first registrant wins)."""
    seen: dict[str, str] = {}
    diagnostics: list[ResourceDiagnostic] = []
    for ext in result.extensions:
        for tool_name in ext.tools:
            if tool_name in seen:
                diagnostics.append(ResourceDiagnostic(
                    type="collision",
                    message=(
                        f"Tool '{tool_name}' is defined in multiple extensions; "
                        f"'{seen[tool_name]}' takes precedence over '{ext.path}'."
                    ),
                    path=ext.path,
                    collision=CollisionInfo(
                        resource_type="tool",
                        name=tool_name,
                        winner_path=seen[tool_name],
                        loser_path=ext.path,
                    ),
                ))
            else:
                seen[tool_name] = ext.path
    return diagnostics


def detect_extension_command_collisions(result: LoadExtensionsResult) -> list[ResourceDiagnostic]:
    """Detect command name collisions across loaded extensions (first registrant wins)."""
    seen: dict[str, str] = {}
    diagnostics: list[ResourceDiagnostic] = []
    for ext in result.extensions:
        for cmd_name in ext.commands:
            if cmd_name in seen:
                diagnostics.append(ResourceDiagnostic(
                    type="collision",
                    message=(
                        f"Command '{cmd_name}' is defined in multiple extensions; "
                        f"'{seen[cmd_name]}' takes precedence over '{ext.path}'."
                    ),
                    path=ext.path,
                    collision=CollisionInfo(
                        resource_type="command",
                        name=cmd_name,
                        winner_path=seen[cmd_name],
                        loser_path=ext.path,
                    ),
                ))
            else:
                seen[cmd_name] = ext.path
    return diagnostics


def collect_extension_errors(result: LoadExtensionsResult) -> list[ResourceDiagnostic]:
    """Surface extension load-time errors as warning diagnostics."""
    return _errors_to_diagnostics(result.errors)


def collect_runtime_errors(runtime: ExtensionRuntime) -> list[ResourceDiagnostic]:
    """Surface extension runtime handler errors accumulated during the session."""
    return _errors_to_diagnostics(runtime.errors)


def _errors_to_diagnostics(errors: list[ExtensionError]) -> list[ResourceDiagnostic]:
    return [
        ResourceDiagnostic(
            type="error",
            message=f"[{err.event}] {err.error}",
            path=err.extension_path,
        )
        for err in errors
    ]


def run_diagnostics(
    extensions_result: LoadExtensionsResult,
    skills_result: LoadSkillsResult | None = None,
    runtime: ExtensionRuntime | None = None,
) -> list[ResourceDiagnostic]:
    """
    Aggregate all diagnostics across the project in one call.

    Order: load errors → runtime errors → collisions → skill warnings.
    """
    diagnostics: list[ResourceDiagnostic] = []
    diagnostics.extend(collect_extension_errors(extensions_result))
    if runtime is not None:
        diagnostics.extend(collect_runtime_errors(runtime))
    diagnostics.extend(detect_extension_tool_collisions(extensions_result))
    diagnostics.extend(detect_extension_command_collisions(extensions_result))
    if skills_result is not None:
        diagnostics.extend(skills_result.diagnostics)
    return diagnostics
