# Workflow Patterns — When and How to Use Each

Complete guide to the 6 workflow patterns with decision trees and examples.

## Pattern Selection Guide

### Quick Decision Tree

```
Does your task have discrete branches?
├─ YES → Classify-and-Act
└─ NO

Can you split it into independent pieces?
├─ YES → Fan-Out-and-Synthesize
└─ NO

Does quality need independent verification?
├─ YES → Adversarial Verify
└─ NO

Do you want to try multiple candidates?
├─ YES → Need to pick best?
│        ├─ YES → Generate-and-Filter
│        └─ NO  → Compare all pairs?
│                 └─ YES → Tournament
└─ NO → Iterate until done?
        ├─ YES → Loop-Until-Done
        └─ NO  → Single agent (no workflow needed)
```

---

## 1. Classify-and-Act

**Route input to specialized handlers based on its type.**

### When to Use

- ✅ Input can be one of several distinct types
- ✅ Different types need different handling
- ✅ Classification is simpler than doing the full task
- ✅ Want to avoid wasting tokens on wrong handler

### Example Problems

- Email routing (spam, promotional, urgent)
- Support ticket triage (billing, technical, general)
- Content moderation (approve, flag, remove)
- Task routing (research, code, design, other)

### How It Works

1. **Classify** — Single LLM call to determine type
2. **Execute** — Run appropriate handler for that type

### Template

```python
meta = {
    "name": "my-classifier",
    "description": "Route input to the right handler",
    "when_to_use": "Input type determines handler",
    "phases": [
        {"name": "classify", "description": "Determine type"},
        {"name": "execute", "description": "Run handler"},
    ],
}

async def run():
    input_text = args.get("input", "")
    
    async with phase("classify"):
        kind = await classify(
            f"Classify this:\n{input_text}",
            options=["type1", "type2", "type3"],
            model="claude-haiku-4-5",  # Use fast model
        )
        log(f"Classified as: {kind}")
    
    async with phase("execute"):
        if kind == "type1":
            return await agent(f"Handle type 1:\n{input_text}")
        elif kind == "type2":
            return await agent(f"Handle type 2:\n{input_text}")
        else:
            return await agent(f"Handle type 3:\n{input_text}")
```

### Cost Optimization

- Use `classify()` not `agent()` — single LLM call
- Use fast model like haiku for routing
- Full agent for handling only
- Total cost: 1 fast call + 1 full agent call

### Real Example

```python
meta = {
    "name": "email-router",
    "description": "Triage incoming emails",
    "when_to_use": "Route emails to correct handler",
}

async def run():
    email = args.get("email", "")
    
    async with phase("classify"):
        category = await classify(
            f"Categorize this email:\n{email}",
            options=["billing", "technical", "general"],
            model="claude-haiku-4-5",
        )
    
    async with phase("respond"):
        if category == "billing":
            return await agent(f"Respond to billing question:\n{email}")
        elif category == "technical":
            return await agent(f"Troubleshoot technical issue:\n{email}")
        else:
            return await agent(f"Respond to general inquiry:\n{email}")
```

---

## 2. Fan-Out-and-Synthesize

**Split a large task into independent subtasks, run in parallel, merge results.**

### When to Use

- ✅ Task can be split into independent pieces
- ✅ Pieces can run simultaneously
- ✅ Need to combine multiple perspectives
- ✅ Pieces are similar enough to parallelize

### Example Problems

- Analysis from multiple angles (economic, social, environmental)
- Competitive analysis (compare 5 products)
- Multi-aspect research (history, current state, future)
- Brainstorming from different viewpoints

### How It Works

1. **Split** — Break task into 3-5 independent subtasks
2. **Execute** — Run all subtasks in parallel
3. **Synthesize** — Merge results into coherent answer

### Template

```python
meta = {
    "name": "fan-out",
    "description": "Split, parallelize, synthesize",
    "when_to_use": "Task has independent pieces",
    "phases": [
        {"name": "split", "description": "Create subtasks"},
        {"name": "execute", "description": "Run in parallel"},
        {"name": "synthesize", "description": "Merge results"},
    ],
}

from pydantic import BaseModel

class Subtasks(BaseModel):
    tasks: list[str]

async def run():
    main_task = args.get("task", "")
    
    async with phase("split"):
        plan = await agent(
            f"Break this into 3-5 independent subtasks:\n{main_task}",
            schema=Subtasks,
        )
        log(f"Split into {len(plan.tasks)} subtasks")
    
    async with phase("execute"):
        results = await parallel(
            *[lambda t=t: agent(t) for t in plan.tasks],
            concurrency=5,
        )
        log(f"All {len(results)} subtasks complete")
    
    async with phase("synthesize"):
        combined = "\n\n---\n\n".join(
            f"[Subtask {i+1}]\n{r}" 
            for i, r in enumerate(results)
        )
        return await agent(
            f"Synthesize these results into one coherent response:\n\n{combined}"
        )
```

### Cost Optimization

- Parallel execution saves time (not cost)
- Each subtask is an agent call
- Synthesis is another agent call
- Total: N + 1 agent calls (vs 1 if sequential)

### Real Example

```python
meta = {
    "name": "product-analysis",
    "description": "Analyze product from multiple angles",
}

from pydantic import BaseModel

class Perspectives(BaseModel):
    perspectives: list[str]

async def run():
    product = args.get("product", "")
    
    async with phase("split"):
        plan = await agent(
            f"What are 5 different perspectives to analyze this product?\n{product}",
            schema=Perspectives,
        )
    
    async with phase("analyze"):
        analyses = await parallel(
            *[lambda p=p: agent(f"Analyze from this perspective: {p}\n\nProduct: {product}") 
              for p in plan.perspectives],
            concurrency=5,
        )
    
    async with phase("synthesize"):
        combined = "\n\n---\n\n".join(
            f"{plan.perspectives[i]}:\n{analyses[i]}"
            for i in range(len(analyses))
        )
        return await agent(
            f"Create a balanced analysis combining all these perspectives:\n\n{combined}"
        )
```

---

## 3. Adversarial Verify

**Generate output, then have independent verifier critique it.**

### When to Use

- ✅ Output quality is critical
- ✅ Self-critique is biased (generator prefers own work)
- ✅ Have clear rubric/standards
- ✅ Want to catch errors before delivery

### Example Problems

- Code review (generate → peer review)
- Content creation (draft → critical edit)
- Analysis (research → fact-check)
- Decisions (proposal → devil's advocate)

### How It Works

1. **Generate** — Create output
2. **Verify** — Independent agent critiques against rubric
3. **Revise** — Generator incorporates feedback

### Template

```python
meta = {
    "name": "adversarial",
    "description": "Generate, verify, revise",
    "when_to_use": "Quality matters and self-critique biases",
    "phases": [
        {"name": "generate", "description": "Create output"},
        {"name": "verify", "description": "Independent critique"},
        {"name": "revise", "description": "Improve based on feedback"},
    ],
}

async def run():
    task = args.get("task", "")
    rubric = args.get("rubric", "Is this accurate, complete, and well-written?")
    
    async with phase("generate"):
        draft = await agent(task)
        log("Draft created")
    
    async with phase("verify"):
        feedback = await agent(
            f"Critique this against the rubric. Be specific about problems.\n\n"
            f"RUBRIC:\n{rubric}\n\nOUTPUT:\n{draft}"
        )
        log("Feedback gathered")
    
    async with phase("revise"):
        final = await agent(
            f"Revise based on feedback. Address all concerns.\n\n"
            f"ORIGINAL:\n{draft}\n\nFEEDBACK:\n{feedback}"
        )
        log("Revision complete")
        return final
```

### Cost Optimization

- 3 agent calls total (generate, verify, revise)
- Only worth it if quality really matters
- Can skip verify for simple tasks
- Consider verify only for important outputs

### Real Example

```python
meta = {
    "name": "code-review",
    "description": "Generate code with verification",
}

async def run():
    task = args.get("task", "")
    
    async with phase("generate"):
        code = await agent(f"Write Python code for:\n{task}")
    
    async with phase("review"):
        feedback = await agent(
            f"Review this code for bugs, style, and efficiency:\n\n{code}\n\n"
            f"Original task: {task}"
        )
    
    async with phase("improve"):
        improved = await agent(
            f"Fix the code based on this feedback:\n\n{feedback}\n\n"
            f"Original code:\n{code}"
        )
        return improved
```

---

## 4. Generate-and-Filter

**Produce many candidates, evaluate each against rubric, keep best.**

### When to Use

- ✅ Exploring solution space
- ✅ Quality is subjective or varies widely
- ✅ Have clear rubric for good vs bad
- ✅ Diversity matters (multiple perspectives)

### Example Problems

- Content generation (write 5 versions, pick best)
- Design concepts (generate ideas, evaluate)
- Business strategies (brainstorm options)
- Creative writing (try different approaches)

### How It Works

1. **Generate** — Create N candidates in parallel
2. **Filter** — Score each against rubric
3. **Return** — Best ones or top candidate

### Template

```python
meta = {
    "name": "generate-filter",
    "description": "Generate candidates, filter by quality",
    "when_to_use": "Need diverse solutions, keep best",
    "phases": [
        {"name": "generate", "description": "Create candidates"},
        {"name": "filter", "description": "Score and select"},
    ],
}

from pydantic import BaseModel

class Score(BaseModel):
    score: int  # 1-10
    keep: bool
    reason: str

async def run():
    prompt = args.get("prompt", "")
    n = int(args.get("n", 5))
    rubric = args.get("rubric", "Is this high quality?")
    
    async with phase("generate"):
        candidates = await parallel(
            *[lambda: agent(prompt) for _ in range(n)],
            concurrency=5,
        )
        log(f"Generated {len(candidates)} candidates")
    
    async with phase("filter"):
        async def score_one(candidate):
            return await agent(
                f"Score this candidate 1-10.\n\n"
                f"RUBRIC:\n{rubric}\n\nCANDIDATE:\n{candidate}",
                schema=Score,
            )
        
        scores = await parallel(
            *[lambda c=c: score_one(c) for c in candidates],
            concurrency=5,
        )
        
        kept = [c for c, s in zip(candidates, scores) if s.keep]
        log(f"Kept {len(kept)}/{n} candidates")
        
        return "\n\n---\n\n".join(kept) if kept else candidates[0]
```

### Cost Optimization

- N generation calls + N scoring calls = 2N total calls
- More expensive than single agent
- Worth it for high-stakes creative tasks
- Reduce N to save tokens

### Real Example

```python
meta = {
    "name": "headline-generator",
    "description": "Generate and filter headlines",
}

from pydantic import BaseModel

class Rating(BaseModel):
    score: int
    keep: bool
    feedback: str

async def run():
    article = args.get("article", "")
    n = int(args.get("n", 5))
    
    async with phase("generate"):
        headlines = await parallel(
            *[lambda: agent(f"Write a catchy headline for:\n{article}") 
              for _ in range(n)],
            concurrency=5,
        )
    
    async with phase("evaluate"):
        async def rate(headline):
            return await agent(
                f"Rate this headline on:\n"
                f"1. Click-worthiness (1-10)\n"
                f"2. Accuracy (1-10)\n"
                f"3. Uniqueness (1-10)\n\n"
                f"Headline: {headline}\n\n"
                f"Article: {article}",
                schema=Rating,
            )
        
        ratings = await parallel(
            *[lambda h=h: rate(h) for h in headlines],
            concurrency=5,
        )
        
        best = max(zip(headlines, ratings), key=lambda x: x[1].score)
        return best[0]
```

---

## 5. Tournament

**Run N agents on same task, use pairwise judging to find winner.**

### When to Use

- ✅ Task is subjective (no right answer)
- ✅ Different approaches worth comparing
- ✅ Pairwise comparison is easier than absolute scoring
- ✅ Want best overall output (not multiple good ones)

### Example Problems

- Writing quality (taste-based)
- Product positioning
- Marketing copy
- Creative solutions
- Best explanation

### How It Works

1. **Generate** — N agents attempt task
2. **Judge** — Pairwise tournament bracket
3. **Return** — Winner

### Template

```python
meta = {
    "name": "tournament",
    "description": "Compare agents pairwise to find best",
    "when_to_use": "Subjective task needing best overall output",
    "phases": [
        {"name": "generate", "description": "N attempts"},
        {"name": "judge", "description": "Pairwise tournament"},
    ],
}

from pydantic import BaseModel

class Pick(BaseModel):
    winner: int  # 1 or 2

async def run():
    task = args.get("task", "")
    n = int(args.get("n", 4))
    
    async with phase("generate"):
        attempts = await parallel(
            *[lambda: agent(task) for _ in range(n)],
            concurrency=5,
        )
        log(f"Generated {n} attempts")
    
    async with phase("judge"):
        bracket = list(attempts)
        round_num = 1
        
        while len(bracket) > 1:
            winners = []
            pairs = list(zip(bracket[::2], bracket[1::2]))
            
            results = await parallel(
                *[lambda a=a, b=b: agent(
                    f"Which is better?\n\nOPTION 1:\n{a}\n\nOPTION 2:\n{b}",
                    schema=Pick,
                ) for a, b in pairs],
                concurrency=5,
            )
            
            for i, pick in enumerate(results):
                idx = i * 2 + (pick.winner - 1)
                winners.append(bracket[idx])
            
            # Handle odd bracket
            if len(bracket) % 2 == 1:
                winners.append(bracket[-1])
            
            log(f"Round {round_num}: {len(bracket)} → {len(winners)}")
            bracket = winners
            round_num += 1
    
    return bracket[0]
```

### Cost Optimization

- N generation + log₂(N) * N/2 comparisons
- For N=4: 4 + 2 + 1 = 7 calls total
- For N=8: 8 + 4 + 2 + 1 = 15 calls total
- Grows logarithmically with bracket rounds

### Real Example

```python
meta = {
    "name": "best-pitch",
    "description": "Find best product pitch via tournament",
}

from pydantic import BaseModel

class Judge(BaseModel):
    winner: int
    reason: str

async def run():
    product = args.get("product", "")
    n = int(args.get("n", 4))
    audience = args.get("audience", "tech investors")
    
    async with phase("generate"):
        pitches = await parallel(
            *[lambda: agent(
                f"Write an elevator pitch for:\nProduct: {product}\nAudience: {audience}"
            ) for _ in range(n)],
            concurrency=5,
        )
    
    async with phase("tournament"):
        bracket = list(pitches)
        round_num = 1
        
        while len(bracket) > 1:
            winners = []
            pairs = list(zip(bracket[::2], bracket[1::2]))
            
            results = await parallel(
                *[lambda a=a, b=b: agent(
                    f"Which pitch is better for {audience}?\n\n"
                    f"PITCH 1:\n{a}\n\nPITCH 2:\n{b}",
                    schema=Judge,
                ) for a, b in pairs],
                concurrency=5,
            )
            
            for i, judge in enumerate(results):
                idx = i * 2 + (judge.winner - 1)
                winners.append(bracket[idx])
            
            if len(bracket) % 2 == 1:
                winners.append(bracket[-1])
            
            bracket = winners
            round_num += 1
    
    return bracket[0]
```

---

## 6. Loop-Until-Done

**Iterate until a stop condition is met.**

### When to Use

- ✅ Unknown number of iterations needed
- ✅ Stop when quality bar is met
- ✅ Each iteration builds on previous
- ✅ Have clear stop condition

### Example Problems

- Iterative refinement (improve until done)
- Data exploration (analyze until exhausted)
- Planning (expand until complete)
- Debugging (fix until passing)

### How It Works

1. **Loop** — Repeat until done condition met
2. **Track** — Accumulate work
3. **Stop** — When completion reached or max iterations hit

### Template

```python
meta = {
    "name": "loop",
    "description": "Iterate until complete",
    "when_to_use": "Unknown iterations, stop when done",
    "phases": [
        {"name": "loop", "description": "Iterative work"},
    ],
}

from pydantic import BaseModel

class LoopResult(BaseModel):
    output: str
    done: bool
    reason: str

async def run():
    task = args.get("task", "")
    max_iters = int(args.get("max_iterations", 10))
    accumulated = ""
    
    async with phase("loop"):
        for i in range(max_iters):
            if budget.exhausted():
                log("Budget limit reached")
                break
            
            result = await agent(
                f"Continue this work. Stop when complete.\n\n"
                f"TASK:\n{task}\n\nWORK SO FAR:\n{accumulated or '(starting)'}",
                schema=LoopResult,
            )
            
            accumulated = result.output
            log(f"Iteration {i+1}: done={result.done} ({result.reason})")
            
            if result.done:
                break
    
    return accumulated
```

### Cost Optimization

- Each iteration is one agent call
- Total cost depends on iterations needed
- Monitor budget.exhausted() to stop early
- Set reasonable max_iterations limit

### Real Example

```python
meta = {
    "name": "research-deepdive",
    "description": "Research a topic until comprehensive",
}

from pydantic import BaseModel

class ResearchStep(BaseModel):
    findings: str
    done: bool
    next_topic: str

async def run():
    topic = args.get("topic", "")
    max_iters = int(args.get("max_iterations", 5))
    research = ""
    
    async with phase("research"):
        for i in range(max_iters):
            if budget.exhausted():
                log("Budget exhausted")
                break
            
            result = await agent(
                f"Research this topic more. When comprehensive, set done=true.\n\n"
                f"TOPIC: {topic}\n\n"
                f"RESEARCH SO FAR:\n{research or '(starting)'}",
                schema=ResearchStep,
            )
            
            research = result.findings
            log(f"Iteration {i+1}: done={result.done}")
            
            if result.done:
                break
    
    return research
```

---

## Comparison Table

| Pattern | When to Use | Cost | Parallelizable |
|---------|------------|------|-----------------|
| Classify-and-Act | Route to handlers | Low (2 calls) | No |
| Fan-Out-and-Synthesize | Split independent work | High (N+1 calls) | Yes |
| Adversarial Verify | Quality matters | Medium (3 calls) | No |
| Generate-and-Filter | Explore solutions | High (2N calls) | Yes |
| Tournament | Subjective best | Very High (N + log N) | Yes |
| Loop-Until-Done | Iterate to quality | Medium (varies) | No |

---

## Decision Checklist

Before picking a pattern:

- [ ] Is the task decomposable? → Fan-Out-and-Synthesize
- [ ] Do I need routing logic? → Classify-and-Act
- [ ] Does quality need verification? → Adversarial Verify
- [ ] Should I try multiple approaches? → Generate-and-Filter or Tournament
- [ ] Is it iterative/exploratory? → Loop-Until-Done
- [ ] None of above? → Use single agent (not a workflow)

See **dsl-reference.md** for complete DSL API.
See **examples.md** for real-world implementations.
