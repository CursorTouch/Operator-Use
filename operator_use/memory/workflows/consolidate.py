from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class MemoryConsolidateWorkflow(Workflow):
    name = 'memory-consolidate'
    description = 'Deduplicate and compress the local memory store.'
    when_to_use = (
        'Run periodically to deduplicate facts, resolve contradictions, and '
        'compress stale entries in the local memories.jsonl file.'
    )
    phases = [
        {'name': 'consolidate', 'description': 'Deduplicate and merge memories'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        store_path = ctx.args.get('store_path', '')

        if not store_path:
            raise ValueError("memory-consolidate requires a 'store_path' argument.")

        path = Path(store_path)
        if not path.exists():
            return f"No memory store found at '{store_path}'. Nothing to consolidate."

        entries = _load(path)
        if len(entries) < 2:
            return "Memory store has fewer than 2 entries — nothing to consolidate."

        ctx.log(f'Consolidating {len(entries)} memory entries at {store_path}')

        entries_text = "\n".join(
            f'[{e["id"]}] {e["content"]}' for e in entries
        )

        prompt = (
            "You are consolidating a long-term memory store. "
            "Below is the full list of memory entries, each prefixed with its ID.\n\n"
            f"{entries_text}\n\n"
            "Your task:\n"
            "1. Merge near-duplicate facts into a single canonical statement.\n"
            "2. Remove facts that are clearly superseded by a newer, more specific entry.\n"
            "3. Resolve contradictions — keep the more specific or recent version.\n"
            "4. Do NOT remove facts just because they are old; only remove genuine duplicates "
            "or contradictions.\n\n"
            "Output ONLY a JSON array of objects, one per surviving fact, with these exact keys:\n"
            '  {"id": "<original_id_or_new_uuid>", "content": "<fact text>"}\n'
            "Preserve the original ID where the fact is unchanged. "
            "For merged facts use any of the source IDs. No other text."
        )

        async with ctx.phase('consolidate'):
            raw = await ctx.agent(prompt, tools=[])

        surviving = _parse_agent_output(raw)
        if not surviving:
            ctx.log('Agent returned no parseable output — aborting consolidation.')
            return "Consolidation aborted: could not parse agent output."

        id_map = {e["id"]: e for e in entries}
        now = datetime.now(timezone.utc)
        merged: list[dict] = []
        for item in surviving:
            original = id_map.get(item["id"], {})
            merged.append({
                "id": item["id"],
                "content": item["content"],
                "source": original.get("source", "consolidated"),
                "created_at": original.get("created_at", now.isoformat()),
                "created_ts": original.get("created_ts", now.timestamp()),
                "consolidated_at": now.isoformat(),
            })

        path.write_text(
            "\n".join(json.dumps(e) for e in merged) + "\n",
            encoding="utf-8",
        )

        removed = len(entries) - len(merged)
        return (
            f"Consolidated '{store_path}': {len(entries)} → {len(merged)} entries "
            f"({removed} removed/merged)."
        )


def _load(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _parse_agent_output(raw: str) -> list[dict]:
    import re
    raw = raw.strip()
    # Try to find a JSON array anywhere in the response.
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            items = json.loads(match.group())
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict) and "id" in i and "content" in i]
        except json.JSONDecodeError:
            pass
    return []
