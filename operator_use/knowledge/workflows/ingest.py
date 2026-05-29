from __future__ import annotations

from datetime import datetime, timezone

from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeIngestWorkflow(Workflow):
    name = 'knowledge-ingest'
    description = 'Ingest a source document into the knowledge base.'
    when_to_use = 'Synthesize a source file into knowledge pages.'
    phases = [
        {'name': 'read',       'description': 'Read source document'},
        {'name': 'synthesize', 'description': 'Update knowledge pages'},
        {'name': 'index',      'description': 'Update index.yaml and log.md'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        source        = ctx.args.get('source', '')
        knowledge_dir = ctx.args.get('knowledge_dir', '')

        async with ctx.phase('read'):
            ctx.log(f'Reading source: {source}')
            source_content = await ctx.agent(
                f"Read the file at '{source}' and return its full content verbatim. "
                "Do not summarize.",
                tools=['read'],
            )

        async with ctx.phase('synthesize'):
            ctx.log('Updating knowledge pages...')
            await ctx.agent(
                f"You are maintaining a knowledge base in '{knowledge_dir}'.\n\n"
                "Steps:\n"
                "1. List all existing .md files in the knowledge directory.\n"
                "2. Read each page to understand current coverage.\n"
                "3. Update or create pages to incorporate the source content below.\n"
                "4. Add cross-links between related pages.\n"
                "5. Write all changes to disk.\n\n"
                f"Source content:\n{source_content}",
                tools=['read', 'write'],
            )

        async with ctx.phase('index'):
            ctx.log('Updating index.yaml and log.md...')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            await ctx.agent(
                f"Update the knowledge index and audit log in '{knowledge_dir}':\n"
                f"1. Read '{knowledge_dir}/index.yaml' and add any new pages created. "
                "Preserve existing entries.\n"
                f"2. Append to '{knowledge_dir}/log.md':\n"
                f"   - {timestamp} | ingest | source: {source}\n"
                "Write both files.",
                tools=['read', 'write'],
            )

        return f"Ingested '{source}' into knowledge base at '{knowledge_dir}'."
