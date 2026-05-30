from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo
from operator_use.memory.workflows.consolidate import MemoryConsolidateWorkflow
from operator_use.workflow.types import WorkflowContext, WorkflowInvocation

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


_USAGE = (
    "Usage:\n"
    "  /memory dream    — consolidate long-term memory: merge duplicates, "
    "resolve contradictions, drop superseded facts (sleep-time consolidation)"
)


async def _dream(registry: CommandRegistry) -> None:
    runtime = registry.runtime
    if runtime is None:
        print("No active runtime.")
        return

    ctx = runtime._context

    # Dreaming operates on the local memories.jsonl store. Other providers
    # (mem0, supermemory) consolidate server-side, so there is nothing to do here.
    manager = getattr(ctx, 'memory_manager', None)
    if manager is None or manager.api is None:
        print('Memory is not active. Enable a provider, e.g. "memory": {"provider": "local"} in settings.')
        return
    from operator_use.memory.api.local import LocalMemoryAPI
    if not isinstance(manager.api, LocalMemoryAPI):
        print(
            f"Dreaming applies only to the local memory store; the active provider "
            f"'{manager.provider_id}' consolidates server-side, so there's nothing to do here."
        )
        return

    loader = ctx.resource_loader
    profile = getattr(loader, '_active_profile', None) if loader else None
    if profile is None:
        print("No active profile — memory requires a profile (start with --agent <name>).")
        return

    store_path = profile.memory_dir / "memories.jsonl"
    if not store_path.exists():
        print(f"No memory store found at {store_path}. Nothing to consolidate.")
        return

    print("Dreaming — consolidating long-term memory (merging duplicates, resolving contradictions)...")
    wf_ctx = WorkflowContext(llm=ctx.llm, tools=[])
    try:
        result = await MemoryConsolidateWorkflow().execute(
            WorkflowInvocation(
                workflow_name='memory-consolidate',
                args={'store_path': str(store_path)},
            ),
            wf_ctx,
        )
    except Exception as e:
        print(f"Consolidation failed: {e}")
        return
    print(result)


async def _handle_memory(registry: CommandRegistry, args: list[str]) -> None:
    subcommand = args[0].lower() if args else ''
    if subcommand == 'dream':
        await _dream(registry)
        return
    print(_USAGE)


command = SlashCommandInfo(
    name='memory',
    description='Manage long-term memory. Subcommand: dream (consolidate the store).',
    handler=_handle_memory,
)
