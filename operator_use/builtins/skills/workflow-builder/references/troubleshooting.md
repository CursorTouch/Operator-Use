# Workflow Builder — Troubleshooting Guide

Common issues and how to fix them.

## Module/Import Errors

### Error: `NameError: name 'agent' is not defined`

**Problem:** Trying to import DSL functions.

```python
# ❌ WRONG
from workflow_dsl import agent, phase, log

async def run():
    result = await agent(...)  # Error!
```

**Solution:** DSL globals are injected at runtime. Never import them.

```python
# ✅ RIGHT
async def run():
    result = await agent(...)  # Works - already available
```

---

### Error: `NameError: name 'args' is not defined`

**Problem:** Accessing args outside run() function.

```python
# ❌ WRONG
prompt = args.get("prompt", "")  # Outside run()

async def run():
    pass
```

**Solution:** Access args only inside run().

```python
# ✅ RIGHT
async def run():
    prompt = args.get("prompt", "")  # Inside run()
```

---

### Error: `ImportError: cannot import name 'BaseModel'`

**Problem:** Pydantic not available.

**Solution:** Pydantic is pre-installed. Check you're not shadowing it.

```python
# ✅ CORRECT
from pydantic import BaseModel

class MyModel(BaseModel):
    field: str
```

---

## Schema/Response Errors

### Error: `ValidationError: ... is not a valid integer`

**Problem:** Schema validation failed.

```python
# ❌ WRONG - LLM returned non-integer for score
class Score(BaseModel):
    score: int  # Expects 1-10

result = await agent(
    "Rate this on a scale of 1-10",
    schema=Score,
)  # "eight" → ValidationError
```

**Solution:** Be explicit in prompt about format.

```python
# ✅ RIGHT
class Score(BaseModel):
    score: int

result = await agent(
    "Rate this on a scale of 1-10. Return a number, not a word. "
    "Example: 7",
    schema=Score,
)
```

---

### Error: `ValidationError: value is not a valid list`

**Problem:** Expecting list, got single item.

```python
# ❌ WRONG
class Items(BaseModel):
    items: list[str]

result = await agent(
    "List three items",
    schema=Items,
)  # LLM returns {"items": "apple"} instead of {"items": ["apple", ...]}
```

**Solution:** Be specific about format in prompt.

```python
# ✅ RIGHT
class Items(BaseModel):
    items: list[str]

result = await agent(
    "Return exactly three items as a JSON list. "
    'Example: {"items": ["apple", "banana", "cherry"]}',
    schema=Items,
)
```

---

### Error: `ValidationError: extra fields not permitted`

**Problem:** LLM returned extra fields not in schema.

```python
# ❌ WRONG
class Score(BaseModel):
    score: int

result = await agent(
    "Rate this",
    schema=Score,
)  # LLM returns {"score": 7, "reason": "..."}
```

**Solution:** Either accept extra fields or explicitly exclude them.

```python
# Option 1: Accept extra fields
class Score(BaseModel):
    score: int
    
    class Config:
        extra = "ignore"  # Ignore unknown fields

# Option 2: Add the field
class Score(BaseModel):
    score: int
    reason: str = ""  # Optional field
```

---

## Async/Concurrency Issues

### Error: `RuntimeError: no running event loop`

**Problem:** Not in async context.

```python
# ❌ WRONG
def run():  # Not async!
    result = await agent(...)
```

**Solution:** run() must be async.

```python
# ✅ RIGHT
async def run():  # async!
    result = await agent(...)
```

---

### Error: `RuntimeError: ... was destroyed but it is pending!`

**Problem:** Task cancelled or timed out.

```python
# ❌ WRONG - Very long agent call
result = await agent(
    "Do something very complex that takes 5 minutes",
    stall_ms=30000  # Only waits 30 seconds
)
```

**Solution:** Increase timeout or simplify task.

```python
# ✅ RIGHT
result = await agent(
    "Do something complex",
    stall_ms=300000  # 5 minutes
)
```

---

### Error: `Task was destroyed but it is pending!` in parallel()

**Problem:** One task times out or fails.

```python
# ❌ WRONG - Doesn't handle failures
results = await parallel(
    task1,
    task2,
    task3,
)
```

**Solution:** Catch exceptions.

```python
# ✅ RIGHT
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
        log(f"Task succeeded")
```

---

## Budget/Cost Issues

### Error: `BudgetExhausted: token limit reached`

**Problem:** Workflow exceeded token budget.

```python
# ❌ WRONG - Unbounded loop
for i in range(1000):
    result = await agent("Do something")
```

**Solution:** Check budget, limit iterations.

```python
# ✅ RIGHT
max_iters = 10  # Reasonable limit

for i in range(max_iters):
    if budget.exhausted():
        log("Budget exhausted")
        break
    
    result = await agent("Do something")
```

---

### Error: `BudgetExhausted: call limit reached`

**Problem:** Too many agent calls.

```python
# ❌ WRONG - Creates 1000 parallel tasks
results = await parallel(
    *[lambda: agent(task) for _ in range(1000)]
)
```

**Solution:** Limit concurrency.

```python
# ✅ RIGHT
results = await parallel(
    *[lambda: agent(task) for _ in range(100)],
    concurrency=5  # Max 5 at a time
)
```

---

## Logic Errors

### Issue: Variable undefined in parallel lambda

**Problem:** Lambda closure captures loop variable.

```python
# ❌ WRONG - All lambdas see last item
for item in items:
    tasks.append(lambda: agent(f"Process: {item}"))

results = await parallel(*tasks)  # All process last item!
```

**Solution:** Use default argument.

```python
# ✅ RIGHT
tasks = [
    lambda i=item: agent(f"Process: {i}")
    for item in items
]

results = await parallel(*tasks)  # Each gets correct item
```

---

### Issue: Phase labels don't show in logs

**Problem:** Not using phases.

```python
# ❌ WRONG
async def run():
    result1 = await agent(...)
    result2 = await agent(...)
```

**Solution:** Wrap in phase() blocks.

```python
# ✅ RIGHT
async def run():
    async with phase("step1"):
        result1 = await agent(...)
    
    async with phase("step2"):
        result2 = await agent(...)
```

---

### Issue: Pipeline not chaining correctly

**Problem:** Stages aren't building on previous results.

```python
# ❌ WRONG - Stages don't access previous output
async def stage1(text):
    return await agent(f"Step 1: {text}")

async def stage2(text):
    # Ignores output from stage1
    return await agent("Step 2: some other text")

results = await pipeline(items, stage1, stage2)
```

**Solution:** Stages must accept and return items.

```python
# ✅ RIGHT
async def stage1(text):
    return await agent(f"Step 1: {text}")

async def stage2(text):  # Receives output from stage1
    return await agent(f"Step 2: {text}")  # Uses it

results = await pipeline(items, stage1, stage2)
```

---

## File/Configuration Issues

### Issue: Workflow file not found

**Problem:** File saved wrong location.

```
❌ WRONG:
/home/user/my-workflow.py

✅ RIGHT:
~/.operator/workflows/my-workflow.py
   or
project/.operator/workflows/my-workflow.py
```

**Solution:** Save to correct location.

---

### Issue: Workflow has syntax error

**Problem:** Python syntax is invalid.

**Solution:** Check syntax:

```bash
python3 -m py_compile ~/.operator/workflows/my-workflow.py
```

If error, fix the syntax and try again.

---

### Issue: Meta dict invalid

**Problem:** Missing required fields.

```python
# ❌ WRONG - Missing required fields
meta = {
    "name": "my-workflow",
    # Missing: description, when_to_use, phases
}
```

**Solution:** Include all required fields.

```python
# ✅ RIGHT
meta = {
    "name": "my-workflow",
    "description": "What it does",
    "when_to_use": "When to use it",
    "phases": [
        {"name": "phase1", "description": "..."},
    ],
}
```

---

## Performance Issues

### Problem: Workflow very slow

**Cause 1: Sequential instead of parallel**

```python
# ❌ SLOW - Sequential
result1 = await agent("Task 1")
result2 = await agent("Task 2")
result3 = await agent("Task 3")
# Total time: sum of all tasks
```

**Solution:** Parallelize independent work.

```python
# ✅ FAST - Parallel
results = await parallel(
    lambda: agent("Task 1"),
    lambda: agent("Task 2"),
    lambda: agent("Task 3"),
)
# Total time: max of all tasks
```

**Cause 2: Using full agent for routing**

```python
# ❌ SLOW - Full agent call for classification
kind = await agent(
    f"What type is this? {input}",
    model="claude-opus-4"  # Big expensive model
)
```

**Solution:** Use classify() with cheap model.

```python
# ✅ FAST - Single LLM call
kind = await classify(
    f"What type is this? {input}",
    options=["type1", "type2", "type3"],
    model="claude-haiku-4-5"  # Fast cheap model
)
```

**Cause 3: Too much concurrency**

```python
# ❌ WRONG - Too many concurrent tasks
results = await parallel(
    *[lambda: agent(task) for task in tasks],
    concurrency=1000  # Rate limited!
)
```

**Solution:** Use reasonable concurrency.

```python
# ✅ RIGHT
results = await parallel(
    *[lambda: agent(task) for task in tasks],
    concurrency=5  # Reasonable
)
```

---

### Problem: High token usage

**Cause 1: Unnecessary agent calls**

```python
# ❌ WASTEFUL
for item in items:
    result = await agent(f"Evaluate: {item}")  # Many calls
```

**Solution:** Batch when possible.

```python
# ✅ EFFICIENT
combined = "\n".join(items)
result = await agent(f"Evaluate all:\n{combined}")  # One call
```

**Cause 2: Long prompts**

```python
# ❌ WASTEFUL - Entire document repeated
result = await agent(f"Summarize: {ENTIRE_DOCUMENT}")
```

**Solution:** Extract key parts first.

```python
# ✅ EFFICIENT
summary = await agent(f"Extract key points: {DOCUMENT}")
final = await agent(f"Summarize these points: {summary}")
```

**Cause 3: No result_timeout**

```python
# ❌ WASTEFUL - Retries indefinitely
result = await agent(task, max_retries=100)
```

**Solution:** Reasonable retry limit.

```python
# ✅ EFFICIENT
result = await agent(task, max_retries=3)
```

---

## Getting Help

### Check run logs

```bash
# View workflow run details
workflow status <run_id>

# Check logs
workflow output <run_id>
```

### Test incrementally

Don't build complex workflow all at once:

1. Test one phase at a time
2. Verify schema works
3. Check budget usage
4. Then add next phase

### Debug with logging

```python
async def run():
    log("Starting")
    log(f"Received args: {args}")
    
    async with phase("debug"):
        result = await agent("test")
        log(f"Result: {result}")
    
    log("Done")
```

Check logs to see progression.

### Common Gotchas

1. **Forgot to use async** — `def run()` should be `async def run()`
2. **Imported DSL functions** — Don't import agent, phase, log, etc.
3. **Lambda closure bug** — Use `lambda x=x:` not `lambda x:`
4. **Missing schema Config** — Add `class Config: extra = "ignore"`
5. **Over-aggressive concurrency** — Start with `concurrency=5`

---

## Still Stuck?

1. Check **SKILL.md** for pattern examples
2. See **dsl-reference.md** for function docs
3. Look at **patterns.md** for when to use each pattern
4. Review **examples.md** for real implementations
5. Check run logs with `workflow status <run_id>`
