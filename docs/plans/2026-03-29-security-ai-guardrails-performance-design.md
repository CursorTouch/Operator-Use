# Operator-Use: Security, AI Guardrails & Performance Hardening

> Design document for the comprehensive security, responsible AI, and performance initiative.
> Approved: 2026-03-29 | Author: Richardson | Reviewer: Jeo

---

## Context

Operator-Use is an autonomous AI agent framework that gives LLMs control over desktop, browser, terminal, and filesystem. This power demands enterprise-grade security, responsible AI guardrails, and optimized performance. A full security audit was conducted on 2026-03-29, identifying critical vulnerabilities and architectural gaps.

This document defines the plan to address all findings across 5 phases and 71 GitHub issues.

## Approach: Foundation + Vertical Slices

- **Phase 0:** Lay cross-cutting foundations (CI/CD, test infra, AI principles)
- **Phases 1-4:** Vertical slices by domain. Each issue = fix + guardrail + test + CI green.

## Workflow

```
Create GitHub Issue (detailed, with CWE/OWASP references)
  -> Discuss with Jeo
    -> Implement on richardson/security-hardening branch
      -> PR against main (CI must pass)
        -> Jeo reviews & merges
```

---

## Phase 0 — Foundations

> Everything else builds on this. No fixes ship without CI to catch regressions.

### Track 0.1 — CI/CD Pipeline

| ID | Title | Description |
|---|---|---|
| 0.1.1 | Set up GitHub Actions CI | pytest + ruff lint on every PR against `main` |
| 0.1.2 | Add bandit static security analysis | SAST scan on every PR, fail on HIGH/CRITICAL |
| 0.1.3 | Add gitleaks secret detection | Block PRs that contain API keys or credentials |
| 0.1.4 | Add pip-audit dependency scanning | Flag known CVEs in dependencies |
| 0.1.5 | Add test coverage reporting | Track coverage %, set minimum threshold (60%) |

### Track 0.2 — Test Infrastructure

| ID | Title | Description |
|---|---|---|
| 0.2.1 | Create security test suite scaffold | `tests/security/` with conftest, fixtures, helpers |
| 0.2.2 | Create performance benchmark harness | `tests/benchmarks/` with timing fixtures, baseline recording |
| 0.2.3 | Create adversarial test framework | `tests/adversarial/` for prompt injection, fuzzing, abuse scenarios |
| 0.2.4 | Create e2e test framework | `tests/e2e/` for full agent pipeline tests |

### Track 0.3 — AI Principles Framework

| ID | Title | Description |
|---|---|---|
| 0.3.1 | Create AI_PRINCIPLES.md | Core principles: least privilege, human oversight, transparency, containment, privacy, fail-safe |
| 0.3.2 | Create guardrails module | `operator_use/guardrails/` — action validator, content filter, policy engine base classes |
| 0.3.3 | Add AI ethics review checklist | PR template with mandatory checklist for security and AI safety |

### Dependencies

```
0.1.1 (CI) + 0.2.1 (security tests) + 0.3.2 (guardrails) -> Phase 1 can begin
0.2.2, 0.2.3, 0.2.4 -> parallel, needed by Phase 3 & 4
0.3.1, 0.3.3 -> parallel, guide all future work
```

---

## Phase 1 — Critical Security Fixes

> Fix vulnerabilities from the 2026-03-29 audit. Each fix ships with its own security test.

### Track 1.1 — Input Boundary Enforcement

| ID | Title | Description | Reference |
|---|---|---|---|
| 1.1.1 | Fix path traversal in resolve() | Enforce workspace boundary, block absolute path escape | [OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal), [CWE-22](https://cwe.mitre.org/data/definitions/22.html) |
| 1.1.2 | Sanitize file download filenames & URLs | Validate URL scheme, sanitize filename, add size limits | [CWE-22](https://cwe.mitre.org/data/definitions/22.html) |
| 1.1.3 | Fix XPath injection in browser service | Escape all quote types in XPath parameters | [CWE-643](https://cwe.mitre.org/data/definitions/643.html) |

### Track 1.2 — Execution Controls

| ID | Title | Description | Reference |
|---|---|---|---|
| 1.2.1 | Replace terminal blocklist with allowlist | Allowlist commands + block shell escapes | [CWE-78](https://cwe.mitre.org/data/definitions/78.html) |
| 1.2.2 | Restrict browser JavaScript execution | Human-in-the-loop for script action, block sensitive APIs | [CWE-94](https://cwe.mitre.org/data/definitions/94.html) |
| 1.2.3 | Replace os.system() with subprocess.run() | Safe process spawning in control_center.py | [CWE-78](https://cwe.mitre.org/data/definitions/78.html) |

### Track 1.3 — Authentication & Access

| ID | Title | Description | Reference |
|---|---|---|---|
| 1.3.1 | Default browser to clean profile | No cookie/login data copy unless config opt-in | [CWE-522](https://cwe.mitre.org/data/definitions/522.html) |
| 1.3.2 | Fix allow_from default-deny semantics | Empty list = deny all, add warning log | [CWE-284](https://cwe.mitre.org/data/definitions/284.html) |
| 1.3.3 | Add credential masking in logs | Regex masking for API key patterns in all log output | [CWE-532](https://cwe.mitre.org/data/definitions/532.html) |

### Track 1.4 — Resource Controls

| ID | Title | Description | Reference |
|---|---|---|---|
| 1.4.1 | Add rate limiting to gateway channels | Per-user request throttling with configurable limits | [CWE-770](https://cwe.mitre.org/data/definitions/770.html) |
| 1.4.2 | Add session TTL and auto-expiry | Configurable timeout, encrypted-at-rest option | [CWE-613](https://cwe.mitre.org/data/definitions/613.html) |

### Per-Issue Deliverable

```
1. Code fix in operator_use/
2. Guardrail rule in operator_use/guardrails/
3. Security test in tests/security/
4. SECURITY_ROADMAP.md updated
```

---

## Phase 2 — AI Guardrails & Responsible AI

> Enterprise-grade responsible AI framework.

### Track 2.1 — Human-in-the-Loop Controls

| ID | Title | Description | Reference |
|---|---|---|---|
| 2.1.1 | Build action confirmation system | Intercept high-risk tool calls, ask user approval | [NIST AI 100-1](https://www.nist.gov/artificial-intelligence/ai-100-1) |
| 2.1.2 | Define risk classification for all tools | Classify actions: safe / review / dangerous | [EU AI Act Annex III](https://artificialintelligenceact.eu/annex/3/) |
| 2.1.3 | Add kill switch (/stop command) | Halt all agent activity immediately across channels | — |

### Track 2.2 — Prompt Injection Defense

| ID | Title | Description | Reference |
|---|---|---|---|
| 2.2.1 | Add input classifier for prompt injection | Pre-LLM filter scoring messages for injection patterns | [OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) |
| 2.2.2 | Add canary tokens to system prompts | Detect prompt extraction via embedded tokens | [Simon Willison](https://simonwillison.net/series/prompt-injection/) |
| 2.2.3 | Implement context boundary separation | Clear delimiters between system, tool, and user context | [OWASP LLM02](https://genai.owasp.org/llmrisk/llm02-insecure-output-handling/) |
| 2.2.4 | Add indirect injection defense for web content | Sanitize scraped content before LLM context | [Greshake et al.](https://arxiv.org/abs/2302.12173) |

### Track 2.3 — Content Filtering & Output Safety

| ID | Title | Description | Reference |
|---|---|---|---|
| 2.3.1 | Build output content filter | Scan responses for harmful content, PII, credentials | [OWASP LLM02](https://genai.owasp.org/llmrisk/llm02-insecure-output-handling/) |
| 2.3.2 | Add PII detection and redaction | Mask emails, phone numbers, SSNs, card numbers | [NIST SP 800-188](https://csrc.nist.gov/pubs/sp/800/188/final) |
| 2.3.3 | Add tool output sanitization | Validate tool results before LLM re-ingestion | — |

### Track 2.4 — Abuse Detection & Audit

| ID | Title | Description | Reference |
|---|---|---|---|
| 2.4.1 | Build structured audit log system | JSON logs: timestamp, user, tool, I/O, risk level | [CWE-778](https://cwe.mitre.org/data/definitions/778.html) |
| 2.4.2 | Add anomaly detection for agent behavior | Flag suspicious patterns: rapid file reads, auth access, unusual URLs | — |
| 2.4.3 | Add abuse rate detection | Detect infinite loops, recursive spawning, resource exhaustion | — |

### Track 2.5 — AI Ethics & Governance

| ID | Title | Description | Reference |
|---|---|---|---|
| 2.5.1 | Create AI Ethics Review checklist for PRs | PR template: data minimization, consent, transparency, bias, rollback | [IEEE 7000](https://standards.ieee.org/ieee/7000/6781/) |
| 2.5.2 | Implement transparency logging | Agent announces intent before high-risk actions | — |
| 2.5.3 | Add data minimization to context builder | Strip unnecessary PII/credentials from LLM context | [GDPR Art 5(1)(c)](https://gdpr-info.eu/art-5-gdpr/) |
| 2.5.4 | Create Responsible AI disclosure doc | Public doc: capabilities, limitations, data handling, reporting | [Anthropic Usage Policy](https://www.anthropic.com/policies/usage-policy) |

---

## Phase 3 — Performance

> Measure first, optimize second. Every optimization gets a benchmark regression test.

### Track 3.1 — Baseline Benchmarks

| ID | Title | Description | Reference |
|---|---|---|---|
| 3.1.1 | Create LLM response time benchmarks | Time-to-first-token, total time, tokens/sec per provider | [pytest-benchmark](https://pytest-benchmark.readthedocs.io/) |
| 3.1.2 | Create browser automation benchmarks | Page load, DOM parse, screenshot, action latencies | — |
| 3.1.3 | Create agent pipeline benchmarks | Message-in to response-out, tool call overhead, context build time | — |
| 3.1.4 | Create concurrency stress benchmarks | N simultaneous users, memory per agent, spawn time | — |
| 3.1.5 | Set performance budgets | Pass/fail thresholds per benchmark in CI | — |

### Track 3.2 — LLM Optimization

| ID | Title | Description | Reference |
|---|---|---|---|
| 3.2.1 | Implement prompt/context caching | Cache system prompts, reuse when workspace unchanged | [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) |
| 3.2.2 | Optimize context window usage | Trim tool outputs, compress history, lazy-load bootstrap files | — |
| 3.2.3 | Add token usage tracking and budget | Per-session counters, configurable budget with warning/hard stop | — |
| 3.2.4 | Implement smart model routing | Cheap models for simple tasks, expensive for complex reasoning | — |

### Track 3.3 — Browser & Desktop Speed

| ID | Title | Description |
|---|---|---|
| 3.3.1 | Optimize screenshot pipeline | Reduce resolution, JPEG compression, diff-based captures |
| 3.3.2 | Optimize DOM parsing | Cache DOM tree, incremental updates |
| 3.3.3 | Add connection pooling for browser CDP | Reuse DevTools Protocol connections |
| 3.3.4 | Optimize desktop accessibility tree traversal | Cache tree, prune subtrees, depth limits |

### Track 3.4 — Concurrency & Memory

| ID | Title | Description | Reference |
|---|---|---|---|
| 3.4.1 | Profile and fix memory leaks | Memory profiling for sessions, browser, agent state | [memray](https://github.com/bloomberg/memray) |
| 3.4.2 | Optimize multi-agent message routing | Reduce bus overhead, batch messages, lazy-init agents | — |
| 3.4.3 | Add connection pooling for provider APIs | Shared httpx.AsyncClient with connection limits | — |
| 3.4.4 | Implement graceful degradation under load | Shed low-priority work when active requests exceed threshold | — |

---

## Phase 4 — Comprehensive QA

> The safety net. If it's not tested, it's not secure. If it's not in CI, it doesn't exist.

### Track 4.1 — Unit Test Coverage

| ID | Title | Description |
|---|---|---|
| 4.1.1 | Unit tests for guardrails module | 100% coverage on every policy, filter, validator |
| 4.1.2 | Unit tests for resolve() and filesystem tools | Boundary enforcement, symlinks, unicode, edge cases |
| 4.1.3 | Unit tests for terminal allowlist | Every allowed/blocked pattern, evasion techniques |
| 4.1.4 | Unit tests for gateway access control | allow_from empty/valid/invalid/spoofed scenarios |
| 4.1.5 | Unit tests for content filters | PII detection, credential masking, output sanitization |
| 4.1.6 | Unit tests for rate limiter | Throttling, window expiry, per-user isolation, bursts |

### Track 4.2 — End-to-End Pipeline Tests

| ID | Title | Description |
|---|---|---|
| 4.2.1 | E2E: Message -> Agent -> Tool -> Response | Full pipeline with mock LLM |
| 4.2.2 | E2E: Browser automation flow | Headless Chrome navigate -> interact -> scrape -> verify |
| 4.2.3 | E2E: Multi-agent delegation | Subagent spawn, handoff, message routing |
| 4.2.4 | E2E: Cron scheduling lifecycle | Create -> trigger -> execute -> persist |
| 4.2.5 | E2E: Human-in-the-loop confirmation | Dangerous action -> confirm -> approve/deny -> execute/abort |

### Track 4.3 — Adversarial & Red-Team Testing

| ID | Title | Description | Reference |
|---|---|---|---|
| 4.3.1 | Prompt injection test suite | 50+ injection patterns: ignore, role-play, extraction | [NVIDIA Garak](https://github.com/NVIDIA/garak) |
| 4.3.2 | Indirect injection via web content | Embedded instructions in scraped pages | [Greshake et al.](https://arxiv.org/abs/2302.12173) |
| 4.3.3 | Tool chaining exploitation | Multi-step: read file -> exfil via web -> cover tracks | — |
| 4.3.4 | Resource exhaustion attacks | Fork bombs, infinite spawning, massive downloads | — |
| 4.3.5 | Privilege escalation paths | Workspace escape -> system files -> credential theft | — |

### Track 4.4 — Fuzzing

| ID | Title | Description | Reference |
|---|---|---|---|
| 4.4.1 | Fuzz filesystem tool inputs | Random paths, unicode, null bytes, symlinks | [Hypothesis](https://hypothesis.readthedocs.io/) |
| 4.4.2 | Fuzz terminal command inputs | Generated commands testing allowlist edge cases | — |
| 4.4.3 | Fuzz browser tool parameters | Random action/url/script/xpath combinations | — |
| 4.4.4 | Fuzz gateway message inputs | Malformed, oversized, binary, rapid-fire messages | — |
| 4.4.5 | Fuzz config parsing | Malformed JSON, missing fields, deep nesting | — |

### Track 4.5 — CI/CD Hardening

| ID | Title | Description | Reference |
|---|---|---|---|
| 4.5.1 | Run full test matrix on every PR | Unit + security + e2e + adversarial + fuzzing in parallel | [GH Actions Matrix](https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs) |
| 4.5.2 | Performance regression gate | Fail PR if benchmark regresses >10% | — |
| 4.5.3 | Security scan summary as PR comment | bandit + gitleaks + pip-audit results posted to PR | — |
| 4.5.4 | Test coverage gate | Fail PR if coverage drops below threshold | — |
| 4.5.5 | Nightly adversarial test run | Scheduled full red-team suite, results to Slack/Discord | — |

---

## Summary

| Phase | Issues | Focus |
|---|---|---|
| Phase 0 | 12 | CI/CD + test infra + AI principles |
| Phase 1 | 11 | Critical security vulnerability fixes |
| Phase 2 | 17 | AI guardrails & responsible AI framework |
| Phase 3 | 15 | Performance benchmarks & optimization |
| Phase 4 | 21 | Comprehensive QA & adversarial testing |
| **Total** | **76** | |

## Key References

- [OWASP LLM Top 10](https://genai.owasp.org/)
- [OWASP Top 10 Web](https://owasp.org/www-project-top-ten/)
- [NIST AI Risk Management Framework](https://www.nist.gov/artificial-intelligence/ai-100-1)
- [EU AI Act](https://artificialintelligenceact.eu/)
- [MITRE CWE](https://cwe.mitre.org/)
- [Anthropic Usage Policy](https://www.anthropic.com/policies/usage-policy)
- [GDPR](https://gdpr-info.eu/)
- [IEEE 7000 Ethical AI](https://standards.ieee.org/ieee/7000/6781/)
- [NVIDIA Garak](https://github.com/NVIDIA/garak)
- [Hypothesis Fuzzing](https://hypothesis.readthedocs.io/)
