from __future__ import annotations

from pathlib import Path

from operator_use.knowledge.prompts import query_answer
from operator_use.knowledge.service import Knowledge
from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeQueryWorkflow(Workflow):
    name = 'knowledge-query'
    description = 'Answer a question using only the knowledge base pages.'
    when_to_use = 'User asks a question that should be answered from the knowledge base.'
    phases = [
        {'name': 'answer', 'description': 'Read relevant pages and answer'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        question      = ctx.args.get('question', '')
        knowledge_dir = ctx.args.get('knowledge_dir', '')

        pages = Knowledge(Path(knowledge_dir)).list_files()
        # Ensure paths are absolute strings for the sub-agent
        for p in pages:
            p['path'] = str(p['path'])

        async with ctx.phase('answer'):
            ctx.log(f'Answering: {question}')
            answer = await ctx.agent(query_answer(knowledge_dir, question, pages), tools=['read'])

        return answer
