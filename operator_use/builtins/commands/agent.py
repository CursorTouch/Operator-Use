from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_agent(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        return

    profiles = runtime.get_agent_profiles()
    active = runtime.get_active_agent_profile()

    if not args:
        if not profiles:
            print("No agent profiles found.")
            print("Place AGENT.md files in ~/.operator/agent/agents/<name>/ to define profiles.")
            return
        print(f"Agent profiles ({len(profiles)}):")
        for name, p in sorted(profiles.items()):
            marker = " *" if (active and active.name == name) else ""
            model_note = f"  model={p.model_id}" if p.model_id else ""
            tools_note = f"  tools={','.join(p.tools)}" if p.tools else ""
            print(f"  {'→' if (active and active.name == name) else ' '} {name}{marker}{model_note}{tools_note}")
            if p.description:
                print(f"      {p.description}")
        if active:
            print(f"\nActive profile: {active.name}")
        return

    name = args[0]

    if name in ('-', 'clear', 'none'):
        agent = runtime._context.agent
        if agent is None:
            return
        if agent.get_active_profile() is None:
            print("No active profile to clear.")
            return
        await agent.clear_profile()
        runtime._context.session_manager.append_custom_info('agent_profile', {'name': None})
        print("Agent profile cleared — using default system prompt and tools.")
        return

    profile = profiles.get(name)
    if not profile:
        available = ', '.join(sorted(profiles.keys())) or '(none)'
        print(f"Unknown profile '{name}'. Available: {available}")
        return

    agent = runtime._context.agent
    if agent is None:
        return
    await agent.apply_profile(profile)
    runtime._context.session_manager.append_custom_info('agent_profile', {'name': name})
    parts = [f"Switched to profile '{name}'."]
    if profile.model_id:
        parts.append(f"Model: {profile.model_id}.")
    if profile.tools:
        parts.append(f"Tools: {', '.join(profile.tools)}.")
    print(' '.join(parts))


command = SlashCommandInfo(
    name='agent',
    description='Switch agent profile (/agent <name>) or list available profiles (/agent).',
    handler=_handle_agent,
    aliases=['persona'],
)
