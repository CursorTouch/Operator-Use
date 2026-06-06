# Workflow DSL — Complete Reference

Complete API documentation for the Operator Workflow DSL globals.

## Overview

Every workflow has access to injected globals — **never import them**. They're provided by the runtime.

## Core Functions

### agent()

Run a full subagent turn with the agent loop and tool execution.

```python
result = await agent(
    prompt,
    schema=None,
    system=None,
    tools=None,
    resume=False,
    stall_ms=180000,
    max_retries=5,
    model=None,
    provider=None
)
```

**Parameters:**
- `prompt` (str) — Task for the agent. Required.
- `schema` (Pydantic model) — Response format. If provided, LLM returns structured data.
- `system` (str) — Custom system prompt. Optional.
- `tools` (list) — Available tools. Optional; inherits from parent if omitted.
- `resume` (bool) — Resume from incomplete previous turn. Default: False.
- `stall_ms` (int) — Timeout in milliseconds. Default: 180000 (3 minutes).
- `max_retries` (int) — Retry attempts on error. Default: 5.
- `model` (str) — Override model (e.g., "gpt-4"). Optional.
- `provider` (str) — Override provider (e.g., "openai"). Optional.

**Returns:**
- `str` — Plain text response
- Pydantic model instance — If schema provided

**Example:**
```python
# Simple task
result = await agent("Summarize the following text: ...")

# With structure
from pydantic import BaseModel

class Summary(BaseModel):
    title: str
    points: list[str]

summary = await agent(
    "Summarize this...",
    schema=Summary
)
print(summary.title)
print(summary.points)
```

### classify()

Single direct LLM call for routing or labeling — no tool loop.

```python
result = await classify(
    prompt,
    options=None,
    schema=None,
    system=None,
    model=None,
    provider=None
)
```

**Parameters:**
- `prompt` (str) — Classification task. Required.
- `options` (list[str]) — Category choices. If provided, returns matching option string.
- `schema` (Pydantic model) — Response format. Alternative to options.
- `system` (str) — Custom system prompt. Optional.
- `model` (str) — Override model. Optional; uses haiku for speed.
- `provider` (str) — Override provider. Optional.

**Returns:**
- `str` — One of options (if options provided)
- Pydantic model instance — If schema provided

**Differences from agent():**
- Cheaper (no tool loop)
- Faster (single LLM call)
- Best for: routing, classification, labeling

**Example:**
```python
# Option-based
kind = await classify(
    "Is this email spam, promotional, or important?",
    options=["spam", "promotional", "important"]
)

# Schema-based
from pydantic import BaseModel

class Classification(BaseModel):
    category: str
    confidence: float

result = await classify(
    "Classify this text...",
    schema=Classification
)
```

### parallel()

Run zero-argument async callables concurrently.

```python
results = await parallel(
    *thunks,
    concurrency=5,
    return_exceptions=False
)
```

**Parameters:**
- `*thunks` — Zero-arg async functions to run. Required.
- `concurrency` (int) — Max concurrent tasks. Default: 5.
- `return_exceptions` (bool) — Catch exceptions and return them. Default: False.

**Returns:**
- `list` — Results in same order as thunks

**Example:**
```python
# Run 3 agents in parallel
results = await parallel(
    lambda: agent("Task 1"),
    lambda: agent("Task 2"),
    lambda: agent("Task 3"),
    concurrency=3
)

# With items
items = ["apple", "banana", "cherry"]
results = await parallel(
    *[lambda item=item: agent(f"Describe: {item}") for item in items],
    concurrency=5
)
```

### pipeline()

Pass items through a series of transform stages sequentially.

```python
results = await pipeline(
    items,
    *stages,
    concurrency=5
)
```

**Parameters:**
- `items` (list) — Input items to transform. Required.
- `*stages` — Async transform functions. Required.
- `concurrency` (int) — Parallel items per stage. Default: 5.

**Returns:**
- `list` — Items after all stages

**Example:**
```python
async def validate(text):
    return await agent(f"Check if valid: {text}")

async def enhance(text):
    return await agent(f"Improve: {text}")

texts = ["draft 1", "draft 2", "draft 3"]

# Pass through validate, then enhance
results = await pipeline(
    texts,
    validate,
    enhance,
    concurrency=3
)
```

### workflow()

Run another workflow inline (one level deep).

```python
result = await workflow(name, args=None)
```

**Parameters:**
- `name` (str) — Workflow name (filename without .py). Required.
- `args` (dict) — Arguments to pass. Optional.

**Returns:**
- Workflow output

**Example:**
```python
# Call another workflow
summary = await workflow("summarize", args={"text": large_text})

# Use in main workflow
result = await workflow("classify", args={"task": user_input})
if result == "research":
    deeper = await workflow("research", args={"topic": user_input})
```

### phase()

Label the current phase in run status and logs.

```python
async with phase("name"):
    # work here
    pass
```

**Parameters:**
- `name` (str) — Phase label. Required.

**Returns:**
- Async context manager

**Example:**
```python
async with phase("research"):
    data = await agent("Research...")

async with phase("analysis"):
    analysis = await agent("Analyze...")

async with phase("summary"):
    summary = await agent("Summarize...")
```

### log()

Append timestamped line to run log.

```python
log("message")
```

**Parameters:**
- `message` (str) — Message to log. Required.

**Returns:**
- None

**Example:**
```python
log("Starting research phase")
log(f"Found {count} results")
log("Analysis complete")
```

## State Tracking

### args

Dictionary of invocation arguments.

```python
task = args.get("task", "default")
n = int(args.get("n", 5))
rubric = args.get("rubric", "default rubric")
```

### budget

Token and call tracking.

```python
# Check status
spent = budget.spent()          # Total calls
remaining = budget.remaining() # Calls left
exhausted = budget.exhausted() # True if limit reached
tokens = budget.tokens_spent() # Total tokens used

# Use in loops
if budget.exhausted():
    log("Budget exhausted, stopping")
    break
```

## Pydantic Models

Structures for schema-based responses:

```python
from pydantic import BaseModel

class MyResponse(BaseModel):
    field1: str
    field2: int
    field3: list[str]

result = await agent("...", schema=MyResponse)
print(result.field1)
print(result.field2)
```

## Complete Workflow Structure

Every workflow must have:

```python
# Metadata dict
meta = {
    "name": "workflow-name",
    "description": "What it does",
    "when_to_use": "When to run it",
    "phases": [
        {"name": "phase1", "description": "..."},
        {"name": "phase2", "description": "..."},
    ]
}

# Async entry point
async def run():
    # Access args
    task = args.get("task", "")
    
    # Use phases
    async with phase("phase1"):
        result1 = await agent(f"Do something: {task}")
    
    async with phase("phase2"):
        result2 = await agent(f"Do another thing: {result1}")
    
    # Log progress
    log("Complete")
    
    # Return final result
    return result2
```

## Best Practices

### Use classify() for routing

```python
# Good — cheap, fast
kind = await classify(prompt, options=[...])

# Avoid — expensive, slow
kind = await agent("What type of task is this?")
```

### Use parallel() for independent work

```python
# Good — runs concurrently
results = await parallel(
    lambda: agent("Task 1"),
    lambda: agent("Task 2"),
    lambda: agent("Task 3"),
)

# Avoid — runs sequentially
results = [
    await agent("Task 1"),
    await agent("Task 2"),
    await agent("Task 3"),
]
```

### Monitor budget in loops

```python
for i in range(max_iterations):
    if budget.exhausted():
        log("Budget limit reached")
        break
    
    result = await agent(task)
```

### Use phases for clarity

```python
async with phase("research"):
    data = await agent("...")

async with phase("analysis"):
    analysis = await agent("...")
```

Phases help users understand where you are in the workflow.

## Error Handling

### Retry logic

Use `max_retries` parameter:

```python
result = await agent(
    prompt,
    max_retries=5  # Retry up to 5 times
)
```

### Timeout handling

Use `stall_ms` parameter:

```python
result = await agent(
    prompt,
    stall_ms=30000  # 30 second timeout
)
```

### Exception handling

```python
results = await parallel(
    task1,
    task2,
    task3,
    return_exceptions=True  # Catch exceptions
)

for result in results:
    if isinstance(result, Exception):
        log(f"Task failed: {result}")
    else:
        log(f"Task succeeded: {result}")
```

## Performance Tips

1. **Use classify() for routing** — much cheaper than agent()
2. **Parallelize independent work** — use parallel() or pipeline()
3. **Batch similar tasks** — fewer LLM calls
4. **Monitor budget** — check exhausted() in loops
5. **Set reasonable timeouts** — avoid hanging forever
6. **Use structured schemas** — easier to validate and parse

## Common Patterns

See **references/patterns.md** for detailed walkthroughs of all 6 workflow patterns.

See **references/examples.md** for real-world workflow examples.
