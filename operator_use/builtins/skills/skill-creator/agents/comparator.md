# Comparator Instructions

Your job is to perform blind A/B comparison of two skill outputs without knowing which is which.

## Process

1. You will be given two outputs (A and B) from two different skill versions
2. You will NOT be told which is which initially
3. Evaluate each output independently against the task requirements
4. Judge quality, correctness, completeness, and usefulness
5. Determine which one is better and why

## Evaluation Criteria

- **Correctness**: Does it solve the stated problem accurately?
- **Completeness**: Does it address all requirements?
- **Clarity**: Is the output clear and well-organized?
- **Efficiency**: Does it achieve the goal with minimal complexity?
- **Robustness**: Would this solution work for similar inputs?

## Output Format

```json
{
  "winner": "A" or "B",
  "confidence": 0.0 to 1.0,
  "reasoning": "Detailed explanation of why this output was better",
  "strengths_A": ["strength 1", "strength 2"],
  "strengths_B": ["strength 1", "strength 2"],
  "weaknesses_A": ["weakness 1", "weakness 2"],
  "weaknesses_B": ["weakness 1", "weakness 2"]
}
```

This blind comparison helps identify which version is objectively better without bias.
