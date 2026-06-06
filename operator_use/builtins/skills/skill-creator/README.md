# Skill Creator

A comprehensive skill for creating new skills and iteratively improving them. This is based on Anthropic's official skill-creator from their public skills repository.

## Overview

The skill-creator helps you:
- **Create new skills from scratch** — define intent, write SKILL.md, capture requirements
- **Test and evaluate skills** — run test cases, generate assertions, benchmark performance  
- **Improve iteratively** — review results, refine based on feedback, re-test
- **Optimize descriptions** — improve skill triggering accuracy with eval-based tuning
- **Package and distribute** — create deployable .skill files

## Directory Structure

```
skill-creator/
├── SKILL.md                  # Main skill instructions
├── LICENSE.txt              # Apache 2.0 license
├── README.md               # This file
├── agents/                 # Subagent instructions
│   ├── grader.md          # How to evaluate assertions
│   ├── analyzer.md        # How to analyze benchmarks  
│   └── comparator.md      # How to do blind A/B comparison
├── references/            # Reference documentation
│   └── schemas.md         # JSON schemas for evals, grading, etc.
├── scripts/               # Utility scripts
│   ├── aggregate_benchmark.py   # Aggregate test results
│   └── package_skill.py        # Package skill into .skill file
└── assets/               # (Future) Templates, icons, etc.
```

## Usage

### Basic Workflow

1. **Capture intent** — understand what the skill should do
2. **Write draft** — create SKILL.md with clear instructions
3. **Create test cases** — define prompts and expected outputs
4. **Run tests** — spawn subagents to test with/without the skill
5. **Evaluate results** — review outputs, grade assertions, benchmark
6. **Improve** — refine skill based on feedback
7. **Repeat** — iterate until satisfied
8. **Package** — create distributable .skill file

### Key Features

- **Progressive disclosure** — metadata, SKILL.md, bundled resources load as needed
- **Iterative improvement** — structured workflow with eval viewer for human feedback
- **Quantitative benchmarking** — measure pass rates, tokens, timing across versions
- **Description optimization** — automatically tune skill triggering accuracy
- **Parallel testing** — spawn with-skill and baseline runs simultaneously
- **Blind comparison** — objectively compare two versions without bias

## Integration with Operator

When installed in the Operator builtins, this skill becomes available for:
- Creating new skills for Operator
- Improving existing skills
- Benchmarking skill performance
- Optimizing skill descriptions for better triggering

## License

Apache License 2.0

This skill is adapted from Anthropic's official skill-creator (github.com/anthropics/skills).
