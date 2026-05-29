from __future__ import annotations

from datetime import datetime, timezone

from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeDreamWorkflow(Workflow):
    name = 'knowledge-dream'
    description = 'Consolidate the knowledge base — deduplicate, merge near-identical pages, remove stale content.'
    when_to_use = 'Clean up and consolidate the knowledge base after multiple ingests.'
    phases = [
        {'name': 'read',        'description': 'Read all knowledge pages'},
        {'name': 'consolidate', 'description': 'Merge, deduplicate, clean'},
        {'name': 'index',       'description': 'Rebuild index.yaml and log.md'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        knowledge_dir = ctx.args.get('knowledge_dir', '')

        async with ctx.phase('read'):
            ctx.log('Reading knowledge pages...')
            overview = await ctx.agent(
                f"List every .md file in '{knowledge_dir}' with: "
                "filename | one-sentence topic summary | approximate word count.",
                tools=['read'],
            )

        async with ctx.phase('consolidate'):
            ctx.log('Consolidating knowledge base...')
            await ctx.agent(
                f"Consolidate the knowledge base at '{knowledge_dir}'.\n\n"
                f"Current pages:\n{overview}\n\n"
                "Apply these rules:\n"
                "1. **Merge near-duplicates** — merge pages covering the same concept, "
                "keep the more complete one, delete the other.\n"
                "2. **Deduplicate within pages** — remove repeated facts, keep the most "
                "accurate statement.\n"
                "3. **Remove stale content** — delete claims contradicted by newer pages.\n"
                "4. **Tighten cross-links** — fix broken references, remove links to "
                "deleted pages, add missing links between related pages.\n"
                "5. Write all changed files. Delete merged-away files.\n\n"
                "Make minimal changes — preserve the existing structure and voice.",
                tools=['read', 'write'],
            )

        async with ctx.phase('index'):
            ctx.log('Rebuilding index.yaml and updating log.md...')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            await ctx.agent(
                f"Rebuild the knowledge index and audit log in '{knowledge_dir}':\n"
                "1. Read the current index.yaml.\n"
                "2. List all .md files now present.\n"
                "3. Rewrite index.yaml to exactly match the current files — add missing "
                "entries, remove deleted ones, preserve existing settings.\n"
                f"4. Append to '{knowledge_dir}/log.md':\n"
                f"   - {timestamp} | dream | consolidation pass completed\n"
                "Write both files.",
                tools=['read', 'write'],
            )

        return f"Knowledge dream complete at '{knowledge_dir}'."
