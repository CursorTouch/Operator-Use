# Schema Reference

## evals.json

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": [],
      "assertions": [
        {
          "text": "Assertion description",
          "name": "assertion-name"
        }
      ]
    }
  ]
}
```

## eval_metadata.json (per test case)

```json
{
  "eval_id": 0,
  "eval_name": "descriptive-name",
  "prompt": "The user's task prompt",
  "assertions": [
    {
      "text": "Assertion description",
      "name": "assertion-name"
    }
  ]
}
```

## timing.json

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3
}
```

## grading.json

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

## benchmark.json

```json
{
  "configurations": [
    {
      "name": "with_skill",
      "results": [
        {
          "eval_id": 0,
          "pass_rate": 1.0,
          "total_tokens_mean": 1234,
          "duration_ms_mean": 5000
        }
      ]
    },
    {
      "name": "without_skill",
      "results": []
    }
  ]
}
```
