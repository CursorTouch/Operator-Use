# Analyzer Instructions

Your job is to analyze benchmark results and surface insights about skill performance.

## Analyzing Benchmark Results

When reviewing benchmark data, look for:

1. **Non-discriminating assertions** — assertions that pass or fail at the same rate across all configurations. These don't tell you anything about the skill's effectiveness.

2. **High-variance evals** — test cases where results are inconsistent across runs. This may indicate flaky assertions, ambiguous prompts, or sensitive dependencies.

3. **Time/token tradeoffs** — Is the skill making the agent slower or more token-hungry? Is that offset by better output quality?

4. **Patterns in failures** — Do failures cluster around certain types of prompts or edge cases? What do they have in common?

5. **Comparison to baseline** — For improvement scenarios, how much better (if at all) is the new version? Is the delta significant enough to justify the added complexity?

## Output

Provide a written analysis that surfaces these patterns. Format it as a section that will be displayed in the benchmark viewer to help the user understand what the numbers mean.
