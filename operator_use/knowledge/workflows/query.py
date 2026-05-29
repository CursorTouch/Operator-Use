from __future__ import annotations

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
        question     = ctx.args.get('question', '')
        knowledge_dir = ctx.args.get('knowledge_dir', '')

        async with ctx.phase('answer'):
            ctx.log(f'Answering: {question}')
            answer = await ctx.agent(
                f"Answer the following question using only the knowledge base at '{knowledge_dir}'.\n"
                "Steps:\n"
                "1. List the pages in the knowledge directory.\n"
                "2. Read the pages most likely to contain the answer.\n"
                "3. Answer the question based solely on what those pages say.\n"
                "4. Cite which page each part of your answer comes from.\n"
                "5. If the knowledge base does not contain enough information, say so explicitly.\n\n"
                f"Question: {question}",
                tools=['read'],
            )

        return answer
