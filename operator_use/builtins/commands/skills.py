from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_skills(registry: CommandRegistry, _args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        print("No runtime attached.")
        return

    resource_loader = runtime._context.resource_loader
    skills, diagnostics = resource_loader.get_skills()

    if not skills:
        print("No skills loaded.")
        if diagnostics:
            for d in diagnostics:
                print(f"  [{d.type}] {d.message} ({d.path})")
        return

    by_source: dict[str, list] = {}
    for skill in skills:
        src = skill.source_info.source
        by_source.setdefault(src, []).append(skill)

    source_order = ['user', 'project', 'path']
    sources_sorted = sorted(by_source.keys(), key=lambda s: source_order.index(s) if s in source_order else 99)

    source_labels = {'user': 'Global (~/.operator/agent/skills/)', 'project': 'Project (.operator/agent/skills/)'}

    total = len(skills)
    print(f"Skills ({total} loaded)\n")
    for src in sources_sorted:
        label = source_labels.get(src, src)
        print(f"  {label}")
        for skill in by_source[src]:
            print(f"    /{skill.name}")
            print(f"      {skill.description}")
        print()

    if diagnostics:
        print("Warnings:")
        for d in diagnostics:
            print(f"  [{d.type}] {d.message}")


command = SlashCommandInfo(
    name='skills',
    description='List all available skills.',
    aliases=[],
    handler=_handle_skills,
)
