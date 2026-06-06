# Workflow Builder Skill

Create and generate Python workflows using the Operator workflow DSL.

## Quick Start

The Workflow Builder helps you create multi-step workflows that coordinate agents, parallelize tasks, and iterate intelligently.

```python
# Example: Classify-and-act workflow
meta = {
    "name": "my-classifier",
    "description": "Route a task based on its type",
    "when_to_use": "When input could be multiple task types",
}

async def run():
    task = args.get("task", "")
    
    async with phase("classify"):
        kind = await classify(
            f"What type of task is this? {task}",
            options=["research", "code", "write"],
        )
    
    async with phase("execute"):
        if kind == "research":
            return await agent(f"Research: {task}")
        elif kind == "code":
            return await agent(f"Write code: {task}")
        else:
            return await agent(f"Write: {task}")
```

## Available Patterns

1. **Classify-and-Act** — Route to different handlers
2. **Fan-Out-and-Synthesize** — Split, parallelize, merge
3. **Adversarial Verify** — Generate + critique + revise
4. **Generate-and-Filter** — Many candidates, keep best
5. **Tournament** — Pairwise competition
6. **Loop-Until-Done** — Iterate until complete

## Documentation

- **SKILL.md** — Full reference with all 6 patterns and DSL globals
- **references/dsl-reference.md** — DSL functions and globals
- **references/patterns.md** — Pattern descriptions and when to use
- **references/examples.md** — Real-world workflow examples
- **references/troubleshooting.md** — Common issues and solutions

## Creating a Workflow

1. Identify your pattern from the 6 available
2. Use SKILL.md as a template
3. Adapt prompts and logic to your task
4. Save as `~/.operator/workflows/<name>.py`
5. Run with `workflow run <name>`

## Core DSL Functions

- `agent()` — Run a subagent with tools
- `classify()` — Single LLM call for routing
- `parallel()` — Run tasks concurrently
- `pipeline()` — Pass items through stages
- `workflow()` — Call another workflow
- `phase()` — Label workflow stages
- `log()` — Add timestamped messages
- `budget` — Track spend and remaining

## See Also

- SKILL.md — Complete pattern catalogue
- references/dsl-reference.md — DSL API docs
- references/patterns.md — When to use each pattern
- references/examples.md — Real-world examples
