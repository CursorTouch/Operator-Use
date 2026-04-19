## Changes
<!-- Describe what this PR does -->

## AI Safety & Security Checklist
<!-- Check all that apply. If a box is unchecked, explain why in the PR description. -->

### Input Validation
- [ ] All external inputs (user messages, API responses, file contents) are validated
- [ ] Path operations stay within workspace boundaries

### Least Privilege
- [ ] New tools/features request only the permissions they need
- [ ] No unnecessary filesystem, network, or system access added

### Credential Safety
- [ ] No API keys, tokens, or passwords in code, logs, or LLM context
- [ ] Sensitive data masked in all log output

### Human Oversight
- [ ] Destructive or irreversible actions require user confirmation
- [ ] Agent announces intent before high-risk operations

### AI Output Safety
- [ ] LLM outputs are validated before acting on them
- [ ] Tool outputs are sanitized before re-entering LLM context

### Testing
- [ ] Security tests added/updated for changes
- [ ] No test coverage decrease
