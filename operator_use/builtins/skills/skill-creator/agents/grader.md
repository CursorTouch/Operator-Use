# Grader Instructions

Your job is to evaluate skill outputs against a set of assertions. For each assertion, determine if it passed or failed based on the actual output.

## Process

1. Read the test prompt
2. Examine the output produced by the skill
3. For each assertion in the test case:
   - Determine if it's satisfied by the output
   - Mark as `passed: true/false`
   - Provide evidence for why it passed or failed

## Output Format

Save results as `grading.json` in the run directory with this structure:

```json
{
  "run_id": "eval-0-with_skill",
  "expectations": [
    {
      "text": "Assertion description",
      "passed": true,
      "evidence": "Explanation of why this passed or failed"
    }
  ]
}
```

The fields `text`, `passed`, and `evidence` are required — the viewer depends on these exact names.

## Tips

- Be objective and evidence-based
- If an assertion is subjective, note that in the evidence
- For assertions that can be checked programmatically, prefer running a script over manual evaluation
