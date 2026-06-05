from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo
from operator_use.memory.workflows.consolidate import MemoryConsolidateWorkflow
from operator_use.workflow.types import WorkflowContext, WorkflowInvocation

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


_USAGE = (
    "Usage:\n"
    "  /memory dream       — consolidate long-term memory: merge duplicates, "
    "resolve contradictions, drop superseded facts (sleep-time consolidation)\n"
    "  /memory obs         — show observational memory status and token clock progress\n"
    "  /memory obs full    — view full observation and reflection ledger"
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

    # Rebuild the embedding index from the consolidated store so the live provider
    # and vectors.json are fresh — the next task's recall uses the post-dream vectors.
    try:
        n = manager.api.reindex()
        print(f"Re-embedded {n} memories — recall is ready with fresh vectors.")
    except Exception as e:
        print(f"(note: could not rebuild the vector index: {e})")


async def _obs_status(registry: CommandRegistry) -> None:
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        print("No active session.")
        return
    agent = getattr(runtime.current_session, "agent", None)
    pipeline = getattr(getattr(agent, "_compaction", None), "obs_pipeline", None)
    if pipeline is None:
        print("Observational memory is not active (strategy is not 'observational').")
        return
    session_manager = getattr(agent, "_session_manager", None)
    if session_manager is None:
        return
    s = pipeline.status_dict(session_manager.get_branch())
    print(f"Observational memory: {'enabled' if s['enabled'] else 'disabled'}")
    print(f"  Pipeline:         {'running' if s['in_flight'] else 'idle'}")
    if s["last_error"]:
        print(f"  Last error:       {s['last_error']}")
    print(f"  Observations:     {s['observations']} ({s['pool_tokens']} tokens)")
    print(f"  Reflections:      {s['reflections']} ({s['reflection_tokens']} tokens)")
    print(f"  Observer clock:   {s['tokens_since_obs_coverage']} / {s['observe_threshold']} tokens")
    print(f"  Reflector clock:  {s['tokens_since_ref_coverage']} / {s['reflect_threshold']} tokens")
    if s["latest_obs_coverage_id"]:
        print(f"  Obs coverage:     up to {s['latest_obs_coverage_id']}")
    if s["latest_ref_coverage_id"]:
        print(f"  Ref coverage:     up to {s['latest_ref_coverage_id']}")


async def _obs_view(registry: CommandRegistry, full: bool) -> None:
    from operator_use.compaction.strategy.observational.ledger import fold_ledger, render_summary
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        print("No active session.")
        return
    agent = getattr(runtime.current_session, "agent", None)
    pipeline = getattr(getattr(agent, "_compaction", None), "obs_pipeline", None)
    if pipeline is None:
        print("Observational memory is not active (strategy is not 'observational').")
        return
    session_manager = getattr(agent, "_session_manager", None)
    if session_manager is None:
        return
    fold = fold_ledger(session_manager.get_branch())
    if not fold.active_observations and not fold.reflections:
        print("No observational memory recorded yet.")
        return
    if full:
        print(render_summary(fold.active_observations, fold.reflections))
    else:
        print(f"Observations: {len(fold.active_observations)}  Reflections: {len(fold.reflections)}")
        if fold.reflections:
            print("\n--- Reflections ---")
            for r in fold.reflections:
                print(f"[{r.id}] {r.content}")
        if fold.active_observations:
            recent = fold.active_observations[-5:]
            print(f"\n--- Latest observations ({len(recent)} of {len(fold.active_observations)}) ---")
            for o in recent:
                print(f"[{o.id}] {o.timestamp} [{o.relevance}] {o.content}")
        if len(fold.active_observations) > 5:
            print("\nUse '/memory obs full' to see all observations.")


async def _handle_memory(registry: CommandRegistry, args: list[str]) -> None:
    subcommand = args[0].lower() if args else ''
    if subcommand == 'dream':
        await _dream(registry)
        return
    if subcommand == 'obs':
        sub2 = args[1].lower() if len(args) > 1 else ''
        if sub2 == 'full':
            await _obs_view(registry, full=True)
        else:
            await _obs_status(registry)
        return
    print(_USAGE)


command = SlashCommandInfo(
    name='memory',
    description='Manage memory. Subcommands: dream (consolidate long-term store), obs (observational memory status/view).',
    handler=_handle_memory,
)
