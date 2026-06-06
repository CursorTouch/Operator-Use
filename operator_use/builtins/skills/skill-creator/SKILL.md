---
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
---

# Skill Creator

A skill for creating new skills and iteratively improving them.

## Overview

At a high level, the process of creating a skill goes like this:

- Decide what you want the skill to do and roughly how it should do it
- Write a draft of the skill
- Create a few test prompts and run claude-with-access-to-the-skill on them
- Help the user evaluate the results both qualitatively and quantitatively
- While the runs happen in the background, draft quantitative evals
- Use the `eval-viewer/generate_review.py` script to show results
- Rewrite the skill based on feedback from evaluation
- Repeat until satisfied
- Expand the test set and try again at larger scale

Your job when using this skill is to figure out where the user is in this process and help them progress through these stages.

## How to Use This Skill

### When the User is Starting Out

If they say "I want to make a skill for X":

- Help narrow down what they mean
- Write a draft
- Write test cases
- Figure out how they want to evaluate
- Run all the prompts
- Iterate based on feedback

### When the User Already Has a Draft

Go straight to the eval/iterate part of the loop.

### Being Flexible

If the user says "I don't need to run a bunch of evaluations, just vibe with me", that's fine too. Adjust your approach.

### After the Skill is Done

Optionally run the skill description improver to optimize triggering accuracy.

---

## Communicating with the User

The skill creator is used by people with varying familiarity with technical jargon.

**Pay attention to context cues** to understand how to phrase your communication:

- "evaluation" and "benchmark" are generally OK
- For "JSON" and "assertion", wait for clear signals the user knows what those are before using them without explanation
- It's OK to briefly explain terms if you're unsure

---

## Creating a Skill

### Step 1: Capture Intent

Start by understanding the user's intent. If the conversation already contains a workflow to capture (e.g., "turn this into a skill"), extract answers from the history first.

Ask these questions:

1. **What should this skill enable Claude to do?**
2. **When should this skill trigger?** (what user phrases/contexts)
3. **What's the expected output format?**
4. **Should we set up test cases?**
   - Skills with objectively verifiable outputs (file transforms, data extraction, code generation) benefit from test cases
   - Skills with subjective outputs (writing style, art) often don't need them

### Step 2: Interview and Research

Ask proactive questions about:
- Edge cases
- Input/output formats
- Example files
- Success criteria
- Dependencies

Wait to write test prompts until you've got these details sorted out. Check available MCPs — use subagents to research in parallel if helpful.

### Step 3: Write the SKILL.md

Fill in these components:

- **name**: Skill identifier
- **description**: When to trigger, what it does (primary triggering mechanism)
  - Include both what the skill does AND specific contexts for when to use it
  - Make descriptions "pushy" — Claude tends to undertrigger skills
  - Example: Instead of "Build a dashboard", write "Build a dashboard. Use this whenever the user mentions dashboards, visualization, metrics, or displaying company data, even if they don't explicitly ask for a 'dashboard.'"
- **compatibility**: Required tools, dependencies (optional)
- **The rest**: Full instructions

---

## Skill Writing Guide

### Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/ - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/ - Files used in output (templates, icons, fonts)
```

### Progressive Disclosure

Skills use a three-level loading system:

1. **Metadata** (name + description) — Always in context (~100 words)
2. **SKILL.md body** — In context whenever skill triggers (<500 lines ideal)
3. **Bundled resources** — As needed (unlimited, scripts can execute without loading)

**Key patterns:**

- Keep SKILL.md under 500 lines
- If approaching this limit, add hierarchy with clear pointers to follow-up resources
- Reference files clearly with guidance on when to read them
- For large files (>300 lines), include a table of contents

**Domain organization example:**

```
cloud-deploy/
├── SKILL.md (workflow + selection)
└── references/
    ├── aws.md
    ├── gcp.md
    └── azure.md
```

Claude reads only the relevant reference file.

### Principle of Lack of Surprise

Skills must not contain malware, exploit code, or content that compromises security. A skill's contents should match its description. Don't create misleading skills or enable unauthorized access/exfiltration.

### Writing Patterns

**Use imperative form** in instructions.

**Defining output formats:**

```markdown
## Report structure

ALWAYS use this exact template:

# [Title]
## Executive summary
## Key findings
## Recommendations
```

**Examples pattern:**

```markdown
## Commit message format

**Example 1:**
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

### Writing Style

- Explain **why** things are important instead of just saying MUST
- Use theory of mind — LLMs are smart
- Make skills general, not specific to narrow examples
- Draft, then review with fresh eyes and improve
- Avoid heavy-handed ALL CAPS and rigid structures

---

## Test Cases

After writing the skill draft, create 2-3 realistic test prompts — the kind of thing a real user would actually say.

Share them with the user: "Here are a few test cases I'd like to try. Do these look right, or do you want to add more?"

Save test cases to `evals/evals.json`. Don't write assertions yet — just prompts. You'll draft assertions while the runs are in progress.

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": []
    }
  ]
}
```

See `references/schemas.md` for the full schema (including the `assertions` field).

---

## Running and Evaluating Test Cases

**This section is one continuous sequence — don't stop partway through.**

Do NOT use `/skill-test` or any other testing skill.

### Directory Structure

Put results in `-workspace/` as a sibling to the skill directory:
```
iteration-1/
├── eval-0/
├── eval-1/
├── eval-2/
```

Each test case gets its own directory. Create directories as you go, not upfront.

### Step 1: Spawn All Runs (With-Skill AND Baseline)

**Launch everything at once** in the same turn — don't spawn with-skill first, then baselines later.

For each test case, spawn two subagents:

**With-skill run:**
```
Execute this task:
- Skill path: [path]
- Task: [prompt]
- Input files: [list]
- Save outputs to: /iteration-N/eval-N/with_skill/outputs/
- Outputs to save: [list]
```

**Baseline run** (same prompt, but baseline varies):
- **Creating a new skill**: no skill. Same prompt, no skill path, save to `without_skill/outputs/`
- **Improving existing skill**: old version. Snapshot first (`cp -r /skill-snapshot/`), point baseline to snapshot, save to `old_skill/outputs/`

**For each test case, create `eval_metadata.json`:**

```json
{
  "eval_id": 0,
  "eval_name": "descriptive-name-here",
  "prompt": "The user's task prompt",
  "assertions": []
}
```

Give each eval a descriptive name based on what it's testing — not just "eval-0".

### Step 2: Draft Assertions While Runs Are In Progress

Don't wait idle. Use this time productively.

Draft quantitative assertions for each test case. If assertions already exist, review and explain them.

**Good assertions are:**
- Objectively verifiable
- Have descriptive names
- Read clearly in the benchmark viewer so someone glancing immediately understands what's being checked

**Subjective skills** (writing style, design) are better evaluated qualitatively — don't force assertions onto these.

Update `eval_metadata.json` files and `evals/evals.json` with assertions.

Explain to the user what they'll see in the viewer — both qualitative outputs and quantitative benchmark.

### Step 3: Capture Timing Data as Runs Complete

When each subagent task completes, you receive `total_tokens` and `duration_ms`. Save immediately:

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3
}
```

**This is the only opportunity to capture this data.** Process each notification as it arrives.

### Step 4: Grade, Aggregate, and Launch Viewer

**Once all runs are done:**

#### A. Grade Each Run

Spawn a grader subagent (or grade inline) that reads `agents/grader.md` and evaluates each assertion.

Save results to `grading.json` in each run directory:

```json
{
  "run_id": "eval-0-with_skill",
  "expectations": [
    {
      "text": "Assertion description",
      "passed": true,
      "evidence": "Why this passed or failed"
    }
  ]
}
```

**Field names are critical:** `text`, `passed`, `evidence` — the viewer depends on these exact names.

For assertions that can be checked programmatically, write a script rather than eyeballing.

#### B. Aggregate Into Benchmark

Run the aggregation script:

```bash
python -m scripts.aggregate_benchmark /iteration-N --skill-name my-skill
```

This produces `benchmark.json` and `benchmark.md` with pass_rate, time, and tokens for each configuration (mean ± stddev and delta).

See `references/schemas.md` for the exact schema. Put each with_skill version before its baseline.

#### C. Do an Analyst Pass

Read the benchmark data and surface patterns the aggregate stats might hide.

See `agents/analyzer.md` for what to look for:
- Non-discriminating assertions (always pass/fail regardless of skill)
- High-variance evals (possibly flaky)
- Time/token tradeoffs

#### D. Launch the Viewer

```bash
nohup python /eval-viewer/generate_review.py \
  /iteration-N \
  --skill-name "my-skill" \
  --benchmark /iteration-N/benchmark.json \
  > /dev/null 2>&1 &
VIEWER_PID=$!
```

For iteration 2+, also pass `--previous-workspace /iteration-N-1`.

**Headless/Cowork environments:** Use `--static /path/to/output.html` to write standalone HTML instead of starting a server.

Feedback downloads as `feedback.json` when the user clicks "Submit All Reviews". Copy it into the workspace directory for the next iteration.

#### E. Tell the User

"I've opened the results in your browser. Two tabs:
- **Outputs**: Click through test cases, leave feedback
- **Benchmark**: Quantitative comparison

When done, come back and let me know."

### What the User Sees in the Viewer

**Outputs tab** (one test case at a time):
- Prompt: the task given
- Output: files produced, rendered inline where possible
- Previous Output (iteration 2+): collapsed section of last iteration
- Formal Grades (if grading done): collapsed assertion pass/fail
- Feedback: auto-saving textbox
- Previous Feedback (iteration 2+): comments from last time

**Benchmark tab** (stats summary):
- Pass rates, timing, token usage for each configuration
- Per-eval breakdowns
- Analyst observations

Navigation via prev/next buttons or arrow keys. Click "Submit All Reviews" to save feedback.

### Step 5: Read the Feedback

When the user tells you they're done, read `feedback.json`:

```json
{
  "reviews": [
    {"run_id": "eval-0-with_skill", "feedback": "missing axis labels", "timestamp": "..."},
    {"run_id": "eval-1-with_skill", "feedback": "", "timestamp": "..."},
    {"run_id": "eval-2-with_skill", "feedback": "perfect!", "timestamp": "..."}
  ],
  "status": "complete"
}
```

Empty feedback = user thought it was fine.

Focus improvements on test cases with specific complaints.

Kill the viewer server when done:

```bash
kill $VIEWER_PID 2>/dev/null
```

---

## Improving the Skill

This is the heart of the loop. You've run the tests, the user has reviewed, now you refine based on feedback.

### How to Think About Improvements

#### 1. Generalize from Feedback

You're creating skills for a million uses across many different prompts. You're iterating on a few examples to move faster. The user knows these examples inside-out and can assess new outputs quickly.

But if the skill only works for those examples, it's useless.

Rather than fiddly, overfitty changes, try different metaphors or working patterns. It's cheap to try and you might land on something great.

#### 2. Keep the Prompt Lean

Remove things that aren't pulling their weight. Read transcripts, not just outputs. If the skill is making the model waste time on unproductive things, get rid of the parts causing that.

#### 3. Explain the Why

Explain **why** you're asking the model to do things. LLMs are smart with good theory of mind. When given a good harness, they go beyond rote instructions.

Even if feedback is terse or frustrated, understand the task and transmit this understanding into instructions.

If you're writing ALWAYS or NEVER in all caps or using super rigid structures, that's a yellow flag. Reframe and explain the reasoning — that's more humane and effective.

#### 4. Look for Repeated Work

Read transcripts from test runs. Notice if subagents independently wrote similar helper scripts or took the same multi-step approach.

If all 3 tests resulted in writing `create_docx.py` or `build_chart.py`, that's a strong signal to bundle that script. Write it once, put it in `scripts/`, tell the skill to use it.

This saves every future invocation from reinventing the wheel.

### The Iteration Loop

After improving the skill:

1. Apply improvements to the skill
2. Rerun all test cases into a new `iteration-N/` directory (including baselines)
   - **New skills**: baseline is always `without_skill` (no skill)
   - **Existing skills**: use your judgment on what makes sense as baseline
3. Launch the reviewer with `--previous-workspace /iteration-N-1`
4. Wait for user review
5. Read new feedback, improve again, repeat

Keep going until:
- The user says they're happy
- The feedback is all empty (everything looks good)
- You're not making meaningful progress

---

## Advanced: Blind Comparison

For rigorous comparison between two skill versions (e.g., "is the new version actually better?"), use blind comparison.

Read `agents/comparator.md` and `agents/analyzer.md` for details.

**Basic idea:** Give two outputs to an independent agent without saying which is which. Let it judge quality. Analyze why the winner won.

Optional, requires subagents, most users won't need it.

---

## Description Optimization

The `description` field in SKILL.md frontmatter is the primary mechanism determining whether Claude invokes a skill.

After creating or improving a skill, offer to optimize the description for better triggering accuracy.

### Step 1: Generate Trigger Eval Queries

Create 20 eval queries — a mix of should-trigger and should-not-trigger. Save as JSON:

```json
[
  {"query": "the user prompt", "should_trigger": true},
  {"query": "another prompt", "should_trigger": false}
]
```

**Queries must be realistic** — the kind of thing a Claude user would actually type. Concrete, specific, with detail (file paths, context, column names, company names, URLs, backstory).

Mix lengths. Include lowercase, abbreviations, typos, casual speech. Focus on edge cases.

**Bad:** `"Format this data"`, `"Extract text from PDF"`, `"Create a chart"`

**Good:** `"ok so my boss sent me this xlsx file (Q4 sales final FINAL v2.xlsx) and wants me to add a column showing profit margin as percentage. Revenue is column C, costs are column D"`

**Should-trigger queries (8-10):** Cover different phrasings (formal, casual). Include cases where user doesn't explicitly name the skill but clearly needs it. Uncommon use cases. Cases where this skill competes with another but should win.

**Should-not-trigger queries (8-10):** Near-misses — share keywords/concepts with the skill but need something different. Adjacent domains. Ambiguous phrasing. Cases where the query touches something the skill does but in a context where another tool is better.

**Don't make should-not-trigger obviously irrelevant.** "Write a fibonacci function" as a negative test for a PDF skill is too easy. Negatives should be genuinely tricky.

### Step 2: Review with User

Present the eval set for review using HTML template:

1. Read template from `assets/eval_review.html`
2. Replace placeholders:
   - `__EVAL_DATA_PLACEHOLDER__` → JSON array (no quotes, JS variable)
   - `__SKILL_NAME_PLACEHOLDER__` → skill name
   - `__SKILL_DESCRIPTION_PLACEHOLDER__` → current description
3. Write to temp file (e.g., `/tmp/eval_review_skill-creator.html`) and open it
4. User edits queries, toggles should-trigger, adds/removes entries, clicks "Export Eval Set"
5. File downloads to `~/Downloads/eval_set.json` (check Downloads for latest)

Bad eval queries → bad descriptions. This step matters.

### Step 3: Run the Optimization Loop

Tell the user: "This will take some time — I'll run the optimization loop in the background and check on it periodically."

Save eval set to workspace, then run in background:

```bash
python -m scripts.run_loop \
  --eval-set /path/to/eval_set.json \
  --skill-path /path/to/skill \
  --model claude-haiku \
  --max-iterations 5 \
  --verbose
```

Use the model ID from your system prompt so the test matches what the user experiences.

Periodically tail the output to give the user updates on iteration count and scores.

**Handles the full optimization loop automatically:**
- Splits eval set into 60% train, 40% held-out test
- Evaluates current description (runs each query 3 times for reliable trigger rate)
- Calls Claude to propose improvements based on failures
- Re-evaluates each new description on train and test
- Iterates up to 5 times
- Opens HTML report showing results per iteration
- Returns JSON with `best_description` (selected by test score to avoid overfitting)

### How Skill Triggering Works

Skills appear in Claude's `available_skills` list with name + description. Claude decides whether to consult a skill based on description.

**Key insight:** Claude only consults skills for tasks it can't easily handle alone. Simple one-step queries like "read this PDF" may not trigger a skill even if description matches perfectly — Claude can do it directly.

Complex, multi-step, specialized queries reliably trigger skills when description matches.

**This means:** Eval queries should be substantive enough that Claude would benefit from consulting a skill. Simple queries like "read file X" are poor test cases.

### Step 4: Apply the Result

Take `best_description` from JSON output and update skill's SKILL.md frontmatter.

Show user before/after and report scores.

---

## Packaging

Check if you have access to `present_files` tool. If yes:

```bash
python -m scripts.package_skill
```

Direct the user to the resulting `.skill` file so they can install it.

---

## Platform-Specific Instructions

### Claude.ai

Core workflow is the same (draft → test → review → improve → repeat), but mechanics change without subagents:

**Running test cases:** No parallel execution. For each test case, read SKILL.md, then follow its instructions yourself. One at a time. Less rigorous than subagents but useful sanity check. Skip baseline runs.

**Reviewing results:** If no browser display, skip browser reviewer. Present results directly in conversation. Show prompt and output. If output is a file (docx, xlsx), save it and tell user where to download it. Ask feedback inline.

**Benchmarking:** Skip quantitative benchmarking — it relies on baselines which aren't meaningful without subagents. Focus on qualitative feedback.

**Iteration loop:** Same as before — improve, rerun, get feedback — just without browser reviewer. Still organize results into iteration directories.

**Description optimization:** Requires `claude` CLI tool (`claude -p`) available only in Claude Code. Skip if on Claude.ai.

**Blind comparison:** Requires subagents. Skip.

**Packaging:** `package_skill.py` works anywhere with Python and filesystem. User can download the .skill file.

**Updating existing skill:**
- Preserve original name. Use skill's directory name and `name` frontmatter unchanged
- Copy to writable location before editing (`/tmp/skill-name/`), edit there, package from copy
- Direct writes may fail due to permissions

### Cowork

You have subagents, so main workflow works:

- For severe timeout issues, run test prompts in series rather than parallel
- No browser/display, so use `--static /path/to/output.html` for eval viewer
- Create standalone HTML file, proffer link user can click in browser
- **IMPORTANT:** GENERATE THE EVAL VIEWER *BEFORE* evaluating inputs yourself. Get them in front of human ASAP
- Feedback downloads as file. Read it from Downloads (may need to request access)
- Packaging works — `package_skill.py` just needs Python and filesystem
- Description optimization should work in Cowork (uses `claude -p` via subprocess)
- Save description optimization until skill is finished and user agrees it's good

---

## Reference Files

### Agents Directory

Instructions for specialized subagents. Read when you need to spawn the relevant subagent:

- `agents/grader.md` — How to evaluate assertions against outputs
- `agents/comparator.md` — How to do blind A/B comparison between two outputs
- `agents/analyzer.md` — How to analyze why one version beat another

### References Directory

Additional documentation:

- `references/schemas.md` — JSON structures for evals.json, grading.json, benchmark.json, etc.

---

## Core Loop Summary

**Repeating this one more time for emphasis:**

1. Figure out what the skill is about
2. Draft or edit the skill
3. Run claude-with-access-to-the-skill on test prompts
4. With the user, evaluate the outputs:
   - Create benchmark.json
   - Run `eval-viewer/generate_review.py` to help user review
   - Run quantitative evals
5. Repeat until satisfied
6. Package the final skill and return it to the user

**If you have a TodoList:** Add steps to make sure you don't forget.

**If in Cowork:** Specifically add "Create evals JSON and run `eval-viewer/generate_review.py` so human can review test cases" to make sure it happens.

**Good luck!**
