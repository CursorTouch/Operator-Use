# Installation & Setup

## Location

The skill-creator is installed in the Operator builtin skills directory:
```
/Users/jeomon/Desktop/Program-Python/operator_use/builtins/skills/skill-creator/
```

## What's Included

- **SKILL.md** — Complete skill instructions (~9000 words)
- **agents/** — Subagent instructions for grading, analyzing, and comparing
- **references/** — Schema documentation for JSON structures
- **scripts/** — Python utilities for benchmarking and packaging
- **LICENSE.txt** — Apache 2.0 license
- **README.md** — Overview and directory structure
- **INSTALLATION.md** — This file

## How It Works

The skill-creator guides users through creating new skills with this workflow:

1. **Define** → Understand intent, requirements, edge cases
2. **Write** → Create SKILL.md with clear instructions
3. **Test** → Create test prompts and run them with/without the skill
4. **Evaluate** → Review outputs, grade assertions, generate benchmarks
5. **Improve** → Refine based on feedback, iterate
6. **Optimize** → Tune skill description for better triggering
7. **Package** → Export as .skill file for distribution

## Key Features

- **Structured workflow** — Clear phases with decision points
- **Subagent integration** — Parallel test execution and grading
- **Quantitative evaluation** — Pass rates, token usage, timing metrics
- **Iterative improvement** — HTML viewer for human feedback
- **Blind comparison** — Objectively compare two skill versions
- **Description optimization** — Auto-tune triggering via eval-based search

## Using the Skill

When the skill-creator is available in your Operator installation, you can invoke it by:

1. Starting a new project/workflow that involves creating a skill
2. Mentioning you want to create, test, or improve a skill
3. The system will load the skill-creator and guide you through the process

## Dependencies

- Python 3.7+ (for scripts)
- Subagent support (for parallel test execution)
- Web browser (for HTML eval viewer, optional in headless mode)

## Extending

The skill-creator is designed to be modular. You can:

- Add new agent templates in `agents/`
- Extend reference documentation in `references/`
- Add utility scripts in `scripts/`
- Create asset templates in `assets/` (currently empty)

## Questions?

Refer to the sections in SKILL.md:
- **Creating a skill** — Full workflow from intent to packaging
- **Running test cases** — How to spawn and manage evals
- **Improving the skill** — How to use feedback for iteration
- **Description optimization** — How to tune skill triggering
- **Platform-specific** — Claude.ai, Cowork, Claude Code guidance
