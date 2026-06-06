# Workflow Builder — Real-World Examples

Complete, copy-ready workflow examples for common tasks.

## Example 1: Email Support Triage

**Route support emails to correct handler.**

```python
# save as: ~/.operator/workflows/support-triage.py

meta = {
    "name": "support-triage",
    "description": "Triage support emails to correct handler",
    "when_to_use": "Incoming support emails need routing",
    "phases": [
        {"name": "classify", "description": "Determine issue type"},
        {"name": "respond", "description": "Route to handler"},
    ],
}

async def run():
    email = args.get("email", "")
    sender = args.get("sender", "customer")
    
    async with phase("classify"):
        category = await classify(
            f"What type of support issue is this?\n\n{email}",
            options=["billing", "technical", "feature_request", "general"],
            model="claude-haiku-4-5",
        )
        log(f"Classified as: {category}")
    
    async with phase("respond"):
        if category == "billing":
            return await agent(
                f"Respond professionally to this billing question from {sender}:\n\n{email}\n\n"
                f"Be helpful and offer specific solutions."
            )
        elif category == "technical":
            return await agent(
                f"Troubleshoot this technical issue from {sender}:\n\n{email}\n\n"
                f"Provide step-by-step solutions."
            )
        elif category == "feature_request":
            return await agent(
                f"Thank them for this feature request and explain how to submit:\n\n{email}"
            )
        else:
            return await agent(
                f"Respond helpfully to this general inquiry:\n\n{email}"
            )
```

**Run it:**
```
workflow run support-triage --args '{"email": "...", "sender": "user@example.com"}'
```

---

## Example 2: Product Comparison Analysis

**Analyze competing products from multiple angles.**

```python
# save as: ~/.operator/workflows/product-comparison.py

meta = {
    "name": "product-comparison",
    "description": "Compare products from multiple perspectives",
    "when_to_use": "Need thorough product analysis",
    "phases": [
        {"name": "plan", "description": "Decide perspectives"},
        {"name": "analyze", "description": "Research each angle"},
        {"name": "synthesize", "description": "Comprehensive report"},
    ],
}

from pydantic import BaseModel

class Analysis(BaseModel):
    perspectives: list[str]

async def run():
    product1 = args.get("product1", "")
    product2 = args.get("product2", "")
    
    async with phase("plan"):
        plan = await agent(
            f"What are 4 perspectives to compare these products?\n"
            f"Product 1: {product1}\n"
            f"Product 2: {product2}",
            schema=Analysis,
        )
        log(f"Will analyze from {len(plan.perspectives)} perspectives")
    
    async with phase("analyze"):
        async def analyze_angle(perspective):
            return await agent(
                f"Compare these products from a {perspective} perspective:\n"
                f"Product 1: {product1}\n"
                f"Product 2: {product2}\n\n"
                f"Be specific about strengths and weaknesses."
            )
        
        analyses = await parallel(
            *[lambda p=p: analyze_angle(p) for p in plan.perspectives],
            concurrency=4,
        )
        log(f"Completed all {len(analyses)} analyses")
    
    async with phase("synthesize"):
        combined = "\n\n".join(
            f"**{plan.perspectives[i]}**\n{analyses[i]}"
            for i in range(len(analyses))
        )
        
        return await agent(
            f"Create a comprehensive comparison report combining these analyses:\n\n{combined}\n\n"
            f"Include: summary, key differences, recommendation, risks to consider."
        )
```

**Run it:**
```
workflow run product-comparison --args '{
  "product1": "Product A - description",
  "product2": "Product B - description"
}'
```

---

## Example 3: Code Review with Revisions

**Generate code, review, and improve.**

```python
# save as: ~/.operator/workflows/code-review.py

meta = {
    "name": "code-review",
    "description": "Generate code with independent review",
    "when_to_use": "Need high-quality code implementation",
    "phases": [
        {"name": "generate", "description": "Write initial code"},
        {"name": "review", "description": "Independent code review"},
        {"name": "improve", "description": "Address feedback"},
    ],
}

async def run():
    task = args.get("task", "")
    language = args.get("language", "Python")
    
    async with phase("generate"):
        code = await agent(
            f"Write clean, well-documented {language} code for:\n{task}\n\n"
            f"Include comments and follow best practices."
        )
        log("Code generated")
    
    async with phase("review"):
        feedback = await agent(
            f"Review this {language} code. Find bugs, style issues, efficiency problems.\n\n"
            f"Original task: {task}\n\n"
            f"Code:\n```\n{code}\n```\n\n"
            f"Provide specific, actionable feedback."
        )
        log("Review complete")
    
    async with phase("improve"):
        improved = await agent(
            f"Fix the code based on this feedback:\n\n{feedback}\n\n"
            f"Original code:\n```\n{code}\n```\n\n"
            f"Return only the improved code with brief comments explaining changes."
        )
        
        return improved
```

**Run it:**
```
workflow run code-review --args '{
  "task": "Function to parse JSON and extract specific fields",
  "language": "Python"
}'
```

---

## Example 4: Generate and Pick Best

**Generate multiple marketing headlines, pick the best.**

```python
# save as: ~/.operator/workflows/headline-generator.py

meta = {
    "name": "headline-generator",
    "description": "Generate and filter headlines",
    "when_to_use": "Need compelling headline for content",
    "phases": [
        {"name": "generate", "description": "Create headline variations"},
        {"name": "evaluate", "description": "Score and pick best"},
    ],
}

from pydantic import BaseModel

class HeadlineScore(BaseModel):
    click_appeal: int  # 1-10
    accuracy: int      # 1-10
    uniqueness: int    # 1-10
    should_keep: bool
    feedback: str

async def run():
    content = args.get("content", "")
    n = int(args.get("n", 5))
    audience = args.get("audience", "general readers")
    
    async with phase("generate"):
        headlines = await parallel(
            *[lambda: agent(
                f"Write a catchy, clickable headline for this content.\n"
                f"Audience: {audience}\n\n"
                f"Content: {content}"
            ) for _ in range(n)],
            concurrency=5,
        )
        log(f"Generated {len(headlines)} headlines")
    
    async with phase("evaluate"):
        async def score_headline(headline):
            return await agent(
                f"Score this headline (1-10 for each criterion):\n\n"
                f"Headline: {headline}\n\n"
                f"Content: {content}\n"
                f"Target audience: {audience}\n\n"
                f"Criteria:\n"
                f"- Click appeal: How likely to get clicks?\n"
                f"- Accuracy: Does it match the content?\n"
                f"- Uniqueness: Is it fresh/different?",
                schema=HeadlineScore,
            )
        
        scores = await parallel(
            *[lambda h=h: score_headline(h) for h in headlines],
            concurrency=5,
        )
        
        # Pick best overall
        best_idx = max(
            range(len(scores)),
            key=lambda i: scores[i].click_appeal + scores[i].accuracy + scores[i].uniqueness
        )
        
        best = headlines[best_idx]
        best_score = scores[best_idx]
        
        log(f"Picked headline {best_idx + 1}")
        log(f"Scores - Click: {best_score.click_appeal}, Accuracy: {best_score.accuracy}, Unique: {best_score.uniqueness}")
        
        return f"BEST HEADLINE:\n{best}\n\nFEEDBACK: {best_score.feedback}"
```

**Run it:**
```
workflow run headline-generator --args '{
  "content": "Our study shows working 4 days per week increases productivity",
  "audience": "HR managers",
  "n": 5
}'
```

---

## Example 5: Tournament — Best Product Pitch

**Competition between 4 different pitches.**

```python
# save as: ~/.operator/workflows/pitch-tournament.py

meta = {
    "name": "pitch-tournament",
    "description": "Find best pitch via competitive judging",
    "when_to_use": "Multiple pitch approaches need comparing",
    "phases": [
        {"name": "generate", "description": "Create pitch variations"},
        {"name": "compete", "description": "Pairwise tournament"},
    ],
}

from pydantic import BaseModel

class Judge(BaseModel):
    winner: int  # 1 or 2
    reason: str

async def run():
    product = args.get("product", "")
    problem = args.get("problem", "")
    audience = args.get("audience", "investors")
    n = int(args.get("n", 4))
    
    async with phase("generate"):
        pitches = await parallel(
            *[lambda: agent(
                f"Write a compelling {audience} pitch for:\n"
                f"Product: {product}\n"
                f"Problem: {problem}\n\n"
                f"Make it unique and persuasive."
            ) for _ in range(n)],
            concurrency=5,
        )
        log(f"Generated {n} pitches")
    
    async with phase("compete"):
        bracket = list(pitches)
        round_num = 1
        
        while len(bracket) > 1:
            winners = []
            pairs = list(zip(bracket[::2], bracket[1::2]))
            
            async def judge_pair(pitch_a, pitch_b):
                return await agent(
                    f"Which pitch is more compelling to {audience}?\n\n"
                    f"PITCH A:\n{pitch_a}\n\n"
                    f"PITCH B:\n{pitch_b}\n\n"
                    f"Criteria: compelling, clear value, persuasive.",
                    schema=Judge,
                )
            
            results = await parallel(
                *[lambda a=a, b=b: judge_pair(a, b) for a, b in pairs],
                concurrency=5,
            )
            
            for i, judge in enumerate(results):
                idx = i * 2 + (judge.winner - 1)
                winners.append(bracket[idx])
                log(f"Match {i+1}: Pitch {judge.winner} wins ({judge.reason[:50]}...)")
            
            # Handle odd bracket
            if len(bracket) % 2 == 1:
                winners.append(bracket[-1])
            
            log(f"Round {round_num}: {len(bracket)} → {len(winners)}")
            bracket = winners
            round_num += 1
        
        return f"WINNING PITCH:\n{bracket[0]}"
```

**Run it:**
```
workflow run pitch-tournament --args '{
  "product": "AI writing assistant for technical documentation",
  "problem": "Technical teams waste 20% of time writing docs",
  "audience": "tech company CTOs",
  "n": 4
}'
```

---

## Example 6: Iterative Research

**Research a topic until comprehensive.**

```python
# save as: ~/.operator/workflows/research-deepdive.py

meta = {
    "name": "research-deepdive",
    "description": "Research topic iteratively until comprehensive",
    "when_to_use": "Need deep understanding of complex topic",
    "phases": [
        {"name": "research", "description": "Iterative investigation"},
    ],
}

from pydantic import BaseModel

class ResearchStep(BaseModel):
    findings: str
    is_comprehensive: bool
    gaps: str
    next_focus: str

async def run():
    topic = args.get("topic", "")
    max_iterations = int(args.get("max_iterations", 5))
    research = ""
    
    async with phase("research"):
        for iteration in range(max_iterations):
            if budget.exhausted():
                log("Budget exhausted, stopping")
                break
            
            result = await agent(
                f"Research this topic further. When comprehensive, set is_comprehensive=true.\n\n"
                f"TOPIC: {topic}\n\n"
                f"RESEARCH SO FAR:\n{research or '(starting fresh)'}\n\n"
                f"Focus on: discovering new information, identifying gaps, ensuring completeness.",
                schema=ResearchStep,
            )
            
            research = result.findings
            log(f"Iteration {iteration + 1}: comprehensive={result.is_comprehensive}")
            
            if result.is_comprehensive:
                log("Research is comprehensive, complete")
                break
            else:
                log(f"Gaps found: {result.gaps}")
                log(f"Next focus: {result.next_focus}")
    
    return f"COMPREHENSIVE RESEARCH:\n{research}"
```

**Run it:**
```
workflow run research-deepdive --args '{
  "topic": "The history and impact of the World Wide Web",
  "max_iterations": 5
}'
```

---

## Example 7: Multi-Stage Pipeline

**Complex task: Analyze → Validate → Enhance → Summarize**

```python
# save as: ~/.operator/workflows/multi-stage.py

meta = {
    "name": "multi-stage",
    "description": "Process documents through validation, enhancement, summarization",
    "when_to_use": "Complex multi-step processing pipeline",
    "phases": [
        {"name": "analyze", "description": "Understand content"},
        {"name": "validate", "description": "Check accuracy"},
        {"name": "enhance", "description": "Improve clarity"},
        {"name": "summarize", "description": "Create summary"},
    ],
}

async def run():
    document = args.get("document", "")
    
    async with phase("analyze"):
        analysis = await agent(
            f"Analyze this document and extract key information:\n\n{document}"
        )
        log("Analysis complete")
    
    async with phase("validate"):
        validation = await agent(
            f"Check the accuracy and completeness of this analysis:\n\n{analysis}\n\n"
            f"Original: {document}\n\n"
            f"Report any errors or gaps."
        )
        log("Validation complete")
    
    async with phase("enhance"):
        enhanced = await agent(
            f"Improve this analysis based on validation feedback:\n\n"
            f"Original analysis:\n{analysis}\n\n"
            f"Feedback:\n{validation}"
        )
        log("Enhancement complete")
    
    async with phase("summarize"):
        summary = await agent(
            f"Create a concise 1-paragraph summary of the key findings:\n\n{enhanced}"
        )
        
        return f"SUMMARY:\n{summary}\n\nFULL ANALYSIS:\n{enhanced}"
```

**Run it:**
```
workflow run multi-stage --args '{
  "document": "Long technical report here..."
}'
```

---

## Example 8: Content Moderation

**Classify and handle content appropriately.**

```python
# save as: ~/.operator/workflows/content-moderation.py

meta = {
    "name": "content-moderation",
    "description": "Moderate user content",
    "when_to_use": "Need to review and handle user submissions",
    "phases": [
        {"name": "classify", "description": "Determine content type"},
        {"name": "evaluate", "description": "Check compliance"},
        {"name": "decide", "description": "Action decision"},
    ],
}

async def run():
    content = args.get("content", "")
    platform = args.get("platform", "community forum")
    
    async with phase("classify"):
        category = await classify(
            f"What category is this content?\n\n{content}",
            options=["advertising", "discussion", "help_request", "inappropriate", "spam"],
            model="claude-haiku-4-5",
        )
        log(f"Classified as: {category}")
    
    async with phase("evaluate"):
        if category in ["inappropriate", "spam"]:
            analysis = await agent(
                f"Analyze why this content violates {platform} policies:\n\n{content}"
            )
        else:
            analysis = await agent(
                f"Verify this {category} content is appropriate for {platform}:\n\n{content}"
            )
        log("Evaluation complete")
    
    async with phase("decide"):
        if category == "spam":
            return f"ACTION: Remove\nREASON: Spam detected\nANALYSIS: {analysis}"
        elif category == "inappropriate":
            return f"ACTION: Review\nREASON: Potentially inappropriate\nANALYSIS: {analysis}"
        else:
            return f"ACTION: Approve\nCATEGORY: {category}\nANALYSIS: {analysis}"
```

**Run it:**
```
workflow run content-moderation --args '{
  "content": "User submitted content here...",
  "platform": "developer community"
}'
```

---

## Quick Copy-and-Paste Template

Use this for any new workflow:

```python
# save as: ~/.operator/workflows/my-workflow.py

meta = {
    "name": "my-workflow",
    "description": "What this workflow does",
    "when_to_use": "When you'd use it",
    "phases": [
        {"name": "phase1", "description": "First phase"},
        {"name": "phase2", "description": "Second phase"},
    ],
}

async def run():
    # Get input arguments
    task = args.get("task", "default task")
    
    # Phase 1
    async with phase("phase1"):
        result1 = await agent(f"Do something: {task}")
        log("Phase 1 complete")
    
    # Phase 2
    async with phase("phase2"):
        result2 = await agent(f"Do something else: {result1}")
        log("Phase 2 complete")
    
    return result2
```

---

## How to Run These Workflows

**Save to workflows directory:**
```bash
# Save to user workflows
~/.operator/workflows/

# OR save to project workflows
.operator/workflows/
```

**Run a workflow:**
```bash
# Simple
workflow run support-triage

# With arguments
workflow run support-triage --args '{"email": "..."}'
```

**Use from another workflow:**
```python
result = await workflow("support-triage", args={"email": ...})
```

---

## Tips

- Start simple (classify-and-act) before complex patterns
- Use haiku model for fast classify() calls
- Parallelize independent work with parallel()
- Monitor budget.exhausted() in loops
- Log progress with meaningful messages
- Use Pydantic models for structured responses
- Test with small args before large ones
