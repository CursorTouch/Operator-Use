from __future__ import annotations

from datetime import datetime, timezone

from operator_use.knowledge.prompts import ingest_index, ingest_read, ingest_synthesize
from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeIngestWorkflow(Workflow):
    name = 'knowledge-ingest'
    description = 'Ingest a source document into the knowledge base.'
    when_to_use = 'Synthesize a source file into knowledge pages.'
    phases = [
        {'name': 'read',       'description': 'Read source document'},
        {'name': 'synthesize', 'description': 'Create folder-based knowledge pages'},
        {'name': 'index',      'description': 'Update index.yaml and log.md'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        source        = ctx.args.get('source', '')
        knowledge_dir = ctx.args.get('knowledge_dir', '')
        source_type   = ctx.args.get('source_type', 'file')

        if source_type == 'text':
            source_content = source
        else:
            async with ctx.phase('read'):
                ctx.log(f'Reading source: {source}')
                read_prompt, read_tools = ingest_read(source, source_type)
                source_content = await ctx.agent(read_prompt, tools=read_tools)

        async with ctx.phase('synthesize'):
            ctx.log('Creating knowledge pages...')
            await ctx.agent(ingest_synthesize(knowledge_dir, source_content), tools=['read', 'write'])

        async with ctx.phase('index'):
            ctx.log('Updating index.yaml and log.md...')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            await ctx.agent(ingest_index(knowledge_dir, source, timestamp), tools=['read', 'write'])

        return f"Ingested '{source}' into knowledge base at '{knowledge_dir}'."
