from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo
from operator_use.knowledge.service import Knowledge

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


def _bold(s: str) -> str:  return f'\033[1m{s}\033[0m'
def _cyan(s: str) -> str:  return f'\033[1;36m{s}\033[0m'
def _dim(s: str) -> str:   return f'\033[2m{s}\033[0m'
def _yellow(s: str) -> str: return f'\033[1;33m{s}\033[0m'

_USAGE = (
    "Usage:\n"
    "  /wiki                    — list all pages\n"
    "  /wiki ingest <source>    — synthesize source into wiki pages (background)\n"
    "  /wiki query <question>   — answer a question from the wiki\n"
    "  /wiki lint               — check for contradictions and stale content (background)\n"
    "  /wiki dream              — consolidate and deduplicate all pages (background)\n"
    "  /wiki log                — show the audit log"
)


def _get_knowledge_dir(registry: CommandRegistry) -> Path | None:
    runtime = registry.runtime
    if runtime is None:
        return None
    profile = runtime._context.resource_loader._active_profile
    return profile.knowledge_dir if profile else None


async def _handle_wiki(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        print("No active runtime.")
        return

    subcommand = args[0].lower() if args else ''

    # /wiki ingest <source>
    if subcommand == 'ingest':
        if len(args) < 2:
            print("Usage: /wiki ingest <path-to-source-file>")
            return
        knowledge_dir = _get_knowledge_dir(registry)
        if knowledge_dir is None:
            print("No active profile — wiki requires a profile (start with --profile <name>).")
            return
        source = ' '.join(args[1:])
        manager = getattr(runtime, 'workflow_manager', None)
        if manager is None:
            print("WorkflowManager not available.")
            return
        try:
            run_id = await manager.invoke('wiki-ingest', args={
                'source': source,
                'knowledge_dir': str(knowledge_dir),
            })
            print(f"Wiki ingest started (run_id: {run_id}).")
            print(f"Source:        {source}")
            print(f"Knowledge dir: {knowledge_dir}")
            print(f"Use /workflows {run_id[:8]} to check progress.")
        except ValueError as e:
            print(f"Error: {e}")
        return

    # /wiki query <question>
    if subcommand == 'query':
        if len(args) < 2:
            print("Usage: /wiki query <question>")
            return
        knowledge_dir = _get_knowledge_dir(registry)
        question = ' '.join(args[1:])
        dir_context = f" at '{knowledge_dir}'" if knowledge_dir else ''
        prompt = (
            f"Answer the following question using only the wiki knowledge base{dir_context}. "
            "Read the relevant wiki pages, cite which page each part of your answer comes from, "
            "and do not use information from outside the wiki.\n\n"
            f"Question: {question}"
        )
        await runtime.invoke(prompt)
        return

    # /wiki lint
    if subcommand == 'lint':
        knowledge_dir = _get_knowledge_dir(registry)
        if knowledge_dir is None:
            print("No active profile — wiki requires a profile (start with --profile <name>).")
            return
        manager = getattr(runtime, 'workflow_manager', None)
        if manager is None:
            print("WorkflowManager not available.")
            return
        try:
            run_id = await manager.invoke('wiki-lint', args={
                'knowledge_dir': str(knowledge_dir),
            })
            print(f"Wiki lint started (run_id: {run_id}).")
            print(f"Use /workflows {run_id[:8]} to check progress.")
        except ValueError as e:
            print(f"Error: {e}")
        return

    # /wiki dream
    if subcommand == 'dream':
        knowledge_dir = _get_knowledge_dir(registry)
        if knowledge_dir is None:
            print("No active profile — wiki requires a profile (start with --profile <name>).")
            return
        manager = getattr(runtime, 'workflow_manager', None)
        if manager is None:
            print("WorkflowManager not available.")
            return
        try:
            run_id = await manager.invoke('wiki-dream', args={
                'knowledge_dir': str(knowledge_dir),
            })
            print(f"Wiki dream started (run_id: {run_id}).")
            print(f"Use /workflows {run_id[:8]} to check progress.")
        except ValueError as e:
            print(f"Error: {e}")
        return

    # /wiki log
    if subcommand == 'log':
        knowledge_dir = _get_knowledge_dir(registry)
        if knowledge_dir is None:
            print("No active profile — wiki requires a profile (start with --profile <name>).")
            return
        log_path = knowledge_dir / 'log.md'
        if not log_path.exists():
            print("No log.md found. Run /wiki ingest to start building the wiki.")
            return
        print()
        print(_bold(f"Wiki log — {log_path}"))
        print(_dim('─' * 60))
        print(log_path.read_text(encoding='utf-8').rstrip())
        print()
        return

    # /wiki (list pages)
    knowledge_dir = _get_knowledge_dir(registry)
    if knowledge_dir is None:
        print("No active profile — wiki requires a profile (start with --profile <name>).")
        print()
        print(_USAGE)
        return

    if not knowledge_dir.exists():
        print(f"Wiki not yet initialised.")
        print(f"Directory: {knowledge_dir}")
        print()
        print(_USAGE)
        return

    knowledge = Knowledge(knowledge_dir)
    files = knowledge.list_files()

    # Also try to load index.yaml for priority/tag metadata
    index_meta: dict[str, dict] = {}
    index_path = knowledge_dir / 'index.yaml'
    if index_path.exists():
        try:
            import yaml
            data = yaml.safe_load(index_path.read_text(encoding='utf-8')) or []
            for entry in data:
                if isinstance(entry, dict) and 'path' in entry:
                    name = str(Path(entry['path']).with_suffix(''))
                    index_meta[name] = entry
        except Exception:
            pass

    if not files:
        print(f"No wiki pages found in {knowledge_dir}.")
        print()
        print(_USAGE)
        return

    print()
    print(_bold(f"Wiki — {knowledge_dir}  ({len(files)} page(s))"))
    print(_dim('─' * 60))

    for f in files:
        name = f['name']
        preview = f.get('preview', '')
        meta = index_meta.get(name, {})
        always = meta.get('always_load', False)
        tags = meta.get('tags', [])

        tag_str = f"  {_dim('[' + ', '.join(tags) + ']')}" if tags else ''
        always_str = f"  {_yellow('always-load')}" if always else ''
        print(f"  {_cyan(name)}{always_str}{tag_str}")
        if preview:
            print(f"    {_dim(preview)}")

    print()
    log_path = knowledge_dir / 'log.md'
    if log_path.exists():
        lines = [l for l in log_path.read_text(encoding='utf-8').splitlines() if l.strip()]
        if lines:
            print(_dim(f"Last entry: {lines[-1].strip()}"))
    print(_dim("/wiki <ingest|query|lint|dream|log> for actions"))
    print()


command = SlashCommandInfo(
    name='wiki',
    description='Manage the wiki knowledge base. Subcommands: ingest, query, lint, dream, log.',
    handler=_handle_wiki,
)
