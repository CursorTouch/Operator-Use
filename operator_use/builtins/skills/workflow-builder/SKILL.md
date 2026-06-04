---
name: workflow-builder
description: Generates Python workflow files using the Operator workflow DSL. Knows all available patterns (classify-and-act, fan-out-and-synthesize, adversarial-verify, generate-and-filter, tournament, loop-until-done) and produces ready-to-run .py files saved to the workflows directory.
---

# Workflow Builder

When the user asks you to create, build, or generate a workflow, use this skill to produce a correct `.py` workflow file and save it to the workflows directory.

## Workflow file format

Every workflow file has:
1. A top-level `meta` dict with `name`, `description`, `when_to_use`, and optional `phases` list.
2. An `async def run()` entry point.
3. DSL globals injected at runtime — **never import them**.

Available DSL globals:

| Global | Purpose |
|---|---|
| `await agent(prompt, schema=None, system=None, tools=None, resume=False, stall_ms=180000, max_retries=5, model=None, provider=None)` | Full subagent turn with tool execution. Returns `str` or Pydantic instance. |
| `await classify(prompt, *, options=None, schema=None, system=None, model=None, provider=None)` | Single direct LLM call — no tool loop. Use for routing/labelling. Returns `str` (options) or Pydantic instance (schema). |
| `await parallel(*thunks, concurrency=5, return_exceptions=False)` | Run zero-arg async callables concurrently. |
| `await pipeline(items, *stages, concurrency=5)` | Pass each item through staged transforms. |
| `await workflow(name, args=None)` | Run another workflow inline (one level deep). |
| `async with phase("name"):` | Label the current phase in run status. |
| `log("message")` | Append timestamped line to run log. |
| `budget` | `.spent()` / `.remaining()` / `.exhausted()` / `.tokens_spent()` — call count + token tracking. |
| `args` | `dict` of invocation arguments. |

## Pattern catalogue

Pick the right pattern based on the user's description. Generate the full `.py` file for the chosen pattern.

---

### 1. Classify-and-act

Route input to specialized handlers based on a cheap single-call classification.

```python
meta = {
    "name": "classify-and-act",
    "description": "Route a task to a specialized handler based on its type.",
    "when_to_use": "Input could be one of several distinct task types each needing different handling.",
    "phases": [
        {"name": "classify", "description": "Determine task type"},
        {"name": "execute",  "description": "Run the appropriate handler"},
    ],
}

async def run():
    task = args.get("task", "")

    async with phase("classify"):
        kind = await classify(
            f"Classify this task into one category.\nTask: {task}",
            options=["research", "code", "summarize", "other"],
            model="claude-haiku-4-5",
        )
        log(f"classified as: {kind}")

    async with phase("execute"):
        if kind == "research":
            return await agent(f"Research the following thoroughly:\n{task}")
        elif kind == "code":
            return await agent(f"Write code to accomplish:\n{task}")
        elif kind == "summarize":
            return await agent(f"Summarize the following:\n{task}")
        else:
            return await agent(task)
```

---

### 2. Fan-out-and-synthesize

Split a large task into independent subtasks, run them in parallel, then merge.

```python
meta = {
    "name": "fan-out-synthesize",
    "description": "Split a task into parallel subtasks then synthesize results.",
    "when_to_use": "Task can be broken into independent parts that can run simultaneously.",
    "phases": [
        {"name": "split",     "description": "Divide into subtasks"},
        {"name": "execute",   "description": "Run subtasks in parallel"},
        {"name": "synthesize","description": "Merge results"},
    ],
}

from pydantic import BaseModel

class Subtasks(BaseModel):
    items: list[str]

async def run():
    topic = args.get("topic", "")

    async with phase("split"):
        plan = await agent(
            f"Break this task into 3-5 independent subtasks: {topic}",
            schema=Subtasks,
        )
        log(f"split into {len(plan.items)} subtasks")

    async with phase("execute"):
        results = await parallel(
            *[lambda item=item: agent(item) for item in plan.items],
        )

    async with phase("synthesize"):
        combined = "\n\n".join(f"[{i+1}] {r}" for i, r in enumerate(results))
        return await agent(f"Synthesize these results into a coherent response:\n\n{combined}")
```

---

### 3. Adversarial verify

A producer agent generates output; a separate verifier agent critiques it against a rubric. Fresh context prevents self-preferential bias.

```python
meta = {
    "name": "adversarial-verify",
    "description": "Generate output then verify it with an independent critic agent.",
    "when_to_use": "Output quality matters and the generator should not also be the judge.",
    "phases": [
        {"name": "generate", "description": "Produce initial output"},
        {"name": "verify",   "description": "Independent critique against rubric"},
        {"name": "revise",   "description": "Incorporate feedback"},
    ],
}

async def run():
    task    = args.get("task", "")
    rubric  = args.get("rubric", "Is the output accurate, complete, and well-structured?")

    async with phase("generate"):
        draft = await agent(task)
        log("draft produced")

    async with phase("verify"):
        feedback = await agent(
            f"Critique this output against the rubric below. Be specific about what is wrong or missing.\n\n"
            f"RUBRIC:\n{rubric}\n\nOUTPUT:\n{draft}"
        )
        log("feedback received")

    async with phase("revise"):
        return await agent(
            f"Revise the following output based on the critic's feedback.\n\n"
            f"ORIGINAL:\n{draft}\n\nFEEDBACK:\n{feedback}"
        )
```

---

### 4. Generate-and-filter

Produce many candidates, evaluate each against a rubric, keep the best.

```python
meta = {
    "name": "generate-and-filter",
    "description": "Generate multiple candidates then filter to the highest-quality ones.",
    "when_to_use": "Exploring a solution space where quality is judged by a rubric.",
    "phases": [
        {"name": "generate", "description": "Produce candidates"},
        {"name": "filter",   "description": "Score and select"},
    ],
}

from pydantic import BaseModel

class Score(BaseModel):
    score: int          # 1-10
    keep: bool
    reason: str

async def run():
    prompt = args.get("prompt", "")
    n      = int(args.get("n", 5))
    rubric = args.get("rubric", "Is this high quality and useful?")

    async with phase("generate"):
        candidates = await parallel(
            *[lambda: agent(prompt) for _ in range(n)],
        )
        log(f"generated {len(candidates)} candidates")

    async with phase("filter"):
        async def score_one(candidate):
            return await agent(
                f"Score this candidate 1-10 and decide whether to keep it.\n\n"
                f"RUBRIC:\n{rubric}\n\nCANDIDATE:\n{candidate}",
                schema=Score,
            )

        scores = await parallel(*[lambda c=c: score_one(c) for c in candidates])
        kept = [c for c, s in zip(candidates, scores) if s.keep]
        log(f"kept {len(kept)}/{len(candidates)} candidates")

        if not kept:
            return candidates[0]
        return "\n\n---\n\n".join(kept)
```

---

### 5. Tournament

Run N agents on the same task, then use pairwise judging to find the winner.

```python
meta = {
    "name": "tournament",
    "description": "Run multiple agents on the same task and pick the best via pairwise judging.",
    "when_to_use": "Taste-based or subjective tasks where comparing pairs is easier than absolute scoring.",
    "phases": [
        {"name": "generate", "description": "Produce N attempts"},
        {"name": "judge",    "description": "Pairwise tournament"},
    ],
}

from pydantic import BaseModel

class Pick(BaseModel):
    winner: int     # 1 or 2

async def run():
    task = args.get("task", "")
    n    = int(args.get("n", 4))

    async with phase("generate"):
        attempts = await parallel(*[lambda: agent(task) for _ in range(n)])
        log(f"generated {len(attempts)} attempts")

    async with phase("judge"):
        # Single-elimination bracket
        bracket = list(attempts)
        round_num = 1
        while len(bracket) > 1:
            winners = []
            pairs = zip(bracket[::2], bracket[1::2])
            results = await parallel(*[
                lambda a=a, b=b: agent(
                    f"Which response is better for this task?\n\nTASK:\n{task}\n\n"
                    f"RESPONSE 1:\n{a}\n\nRESPONSE 2:\n{b}",
                    schema=Pick,
                )
                for a, b in pairs
            ])
            for i, pick in enumerate(results):
                winners.append(bracket[i * 2] if pick.winner == 1 else bracket[i * 2 + 1])
            # carry over a bye if odd number
            if len(bracket) % 2 == 1:
                winners.append(bracket[-1])
            log(f"round {round_num}: {len(bracket)} → {len(winners)}")
            bracket = winners
            round_num += 1

        return bracket[0]
```

---

### 6. Loop-until-done

Spawn agents repeatedly until a stop condition is met rather than a fixed count.

```python
meta = {
    "name": "loop-until-done",
    "description": "Run an agent in a loop until a completion condition is satisfied.",
    "when_to_use": "Work that requires iteration until a quality bar is met or findings are exhausted.",
    "phases": [
        {"name": "loop", "description": "Iterative refinement"},
    ],
}

from pydantic import BaseModel

class LoopResult(BaseModel):
    output: str
    done: bool
    reason: str

async def run():
    task        = args.get("task", "")
    max_iters   = int(args.get("max_iterations", 10))
    accumulated = ""

    async with phase("loop"):
        for i in range(max_iters):
            if budget.exhausted():
                log("budget exhausted — stopping")
                break

            result = await agent(
                f"Continue working on this task. Stop when complete.\n\n"
                f"TASK:\n{task}\n\nWORK SO FAR:\n{accumulated or '(none yet)'}",
                schema=LoopResult,
            )
            accumulated = result.output
            log(f"iteration {i+1}: done={result.done} — {result.reason}")

            if result.done:
                break

    return accumulated
```

---

## How to save a workflow

When generating a workflow, use the `write` tool to save it to the configured workflows directory (typically `~/.operator/workflows/<name>.py` or `<project>/.operator/workflows/<name>.py`).

Always:
1. Ask the user what the workflow should do if not already clear.
2. Choose the best-matching pattern from the catalogue above.
3. Adapt the template to the specific task (rename variables, adjust prompts, add phases).
4. Save the file and confirm the path to the user.
5. Tell the user they can run it with: `workflow run <name>` or via the `workflow` tool with `{"action": "run", "name": "<name>"}`.
