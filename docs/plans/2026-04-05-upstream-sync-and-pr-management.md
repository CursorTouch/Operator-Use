# Upstream Sync, Conflict Resolution & PR Management Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Synchronize local repo and fork with CursorTouch/Operator-Use upstream, resolve merge conflicts on PRs #25–28, retrieve all PR comments, and prepare PRs for remaining Phase 1 issues (#22, #23, #24).

**Architecture:** Single-contributor fork workflow. All changes flow through `richard-devbot` fork branches → PRs → CursorTouch/Operator-Use main. Jeo is the merge gatekeeper. Never merge your own PRs.

**Tech Stack:** Git, GitHub CLI (`gh`), Python/uv (for lock file regeneration)

---

## Current State (as of 2026-04-05)

| Item | Status |
|---|---|
| `origin` remote | `https://github.com/CursorTouch/Operator-Use.git` ✅ already configured |
| `fork` remote | `https://github.com/richard-devbot/Operator-Use.git` ✅ already configured |
| Local `main` | **51 commits behind** `origin/main` |
| PR #25 | OPEN — Jeo requested rebase (merge conflicts) |
| PR #26 | OPEN — Qodo addressed, waiting for Jeo |
| PR #27 | OPEN — Qodo addressed, waiting for Jeo |
| PR #28 | OPEN — Qodo addressed, waiting for Jeo |
| Conflicts | All 4 branches have 5 conflict markers (in `uv.lock` / dependency files) |
| Phase 1 remaining | Issues #22, #23, #24 — no branch yet |

---

## Phase A: Sync Local Repository with Upstream

### Task 1: Fetch latest upstream changes

```bash
git fetch origin
```

Expected: Downloads 51 new commits from `CursorTouch/Operator-Use` without modifying any local branches.

### Task 2: Fast-forward local main to upstream main

```bash
git checkout main
git merge --ff-only origin/main
```

Expected output: `Fast-forward` — local `main` now matches `origin/main`.

If this fails with "not a fast-forward": your local `main` has diverged. Run:
```bash
git log --oneline main ^origin/main  # see what's only local
```
If those are stale/merged commits, reset hard:
```bash
git reset --hard origin/main
```
If they are real unmerged work, create a branch first:
```bash
git checkout -b richardson/local-work-backup
git checkout main && git reset --hard origin/main
```

### Task 3: Push updated main to personal fork

```bash
git push fork main
```

Expected: `fork/main` now mirrors `origin/main` exactly. Your fork is synchronized.

---

## Phase B: Retrieve All PR Comments

### Task 4: Fetch full Qodo + Jeo comments for all open PRs

Run these to get the full comment text for each PR:

```bash
gh pr view 25 --repo CursorTouch/Operator-Use --comments
```

```bash
gh pr view 26 --repo CursorTouch/Operator-Use --comments
```

```bash
gh pr view 27 --repo CursorTouch/Operator-Use --comments
```

```bash
gh pr view 28 --repo CursorTouch/Operator-Use --comments
```

**Key comment summary (already retrieved):**

| PR | Reviewer | Finding | Status |
|---|---|---|---|
| #25 | Qodo | 2 bugs flagged | ✅ Responded |
| #25 | **Jeo** | **"merge conflicts with main — rebase"** | ⚠️ ACTION REQUIRED |
| #26 | Qodo | 1 bug (download path traversal + size limit) | ✅ Responded |
| #27 | Qodo | 2 bugs (fetch missing from JS block, etc.) | ✅ Responded |
| #28 | Qodo | 2 bugs (`_is_user_allowed` missing) | ✅ Responded |

---

## Phase C: Resolve Merge Conflicts on Existing PRs

**Root cause:** All 4 branches were cut from an older `main`. The upstream `main` has since received 51 new commits (mostly in `uv.lock` and dependency files). The 5 conflict markers are all in `uv.lock` — a machine-generated lock file.

**Strategy:** Rebase each branch onto the new `origin/main`. For `uv.lock` conflicts, always accept the upstream version then regenerate.

**Rebase order (critical):**
1. `richardson/security-hardening` (PR #25) — base branch, rebase FIRST
2. `richardson/phase1-input-validation` (PR #26) — independent, rebase onto main after #25 is done
3. `richardson/phase1-execution-controls` (PR #27) — independent
4. `richardson/phase1-auth-access` (PR #28) — independent

### Task 5: Rebase richardson/security-hardening onto origin/main

```bash
git checkout richardson/security-hardening
git fetch origin
git rebase origin/main
```

**When conflict appears in `uv.lock`:**
```bash
# Accept upstream version for the lock file
git checkout --theirs uv.lock
# Regenerate the lock file (adds your deps on top of theirs)
uv lock
git add uv.lock
git rebase --continue
```

**When conflict appears in `pyproject.toml`:**
```bash
# Open the file and manually merge — keep both your security deps AND upstream additions
# Typical pattern: upstream added new packages, yours added bandit/safety/etc.
# Accept all upstream additions, keep your security additions
nano pyproject.toml   # or your editor
git add pyproject.toml
git rebase --continue
```

If a rebase step fails badly:
```bash
git rebase --abort   # safe exit, nothing lost
```

### Task 6: Force-push rebased security-hardening to fork

```bash
git push fork richardson/security-hardening --force-with-lease
```

`--force-with-lease` is safer than `--force` — it refuses to push if someone else pushed to the same branch in between.

### Task 7: Rebase phase1-input-validation

```bash
git checkout richardson/phase1-input-validation
git rebase origin/main
# Resolve uv.lock: git checkout --theirs uv.lock && uv lock && git add uv.lock && git rebase --continue
git push fork richardson/phase1-input-validation --force-with-lease
```

### Task 8: Rebase phase1-execution-controls

```bash
git checkout richardson/phase1-execution-controls
git rebase origin/main
# Resolve uv.lock: git checkout --theirs uv.lock && uv lock && git add uv.lock && git rebase --continue
git push fork richardson/phase1-execution-controls --force-with-lease
```

### Task 9: Rebase phase1-auth-access

```bash
git checkout richardson/phase1-auth-access
git rebase origin/main
# Resolve uv.lock: git checkout --theirs uv.lock && uv lock && git add uv.lock && git rebase --continue
git push fork richardson/phase1-auth-access --force-with-lease
```

### Task 10: Comment on PR #25 notifying Jeo rebase is done

```bash
gh pr comment 25 --repo CursorTouch/Operator-Use --body "Rebased onto latest main — merge conflicts resolved. Ready for review."
```

---

## Phase D: Open Issues and To-Do Tasks

### Open Issues Map

| Issue | Title | PR? | Phase |
|---|---|---|---|
| #2 | Set up GitHub Actions CI pipeline | #25 | Phase 0.1 |
| #3 | Add bandit static security analysis to CI | #25 | Phase 0.1 |
| #4 | Add gitleaks secret detection to CI | #25 | Phase 0.1 |
| #5 | Add pip-audit dependency vulnerability scanning | #25 | Phase 0.1 |
| #6 | Add test coverage reporting to CI | #25 | Phase 0.1 |
| #7 | Create security test suite scaffold | #25 | Phase 0.2 |
| #8 | Create performance benchmark harness | #25 | Phase 0.2 |
| #9 | Create adversarial test framework | #25 | Phase 0.2 |
| #10 | Create e2e test framework | #25 | Phase 0.2 |
| #11 | Create AI_PRINCIPLES.md | #25 | Phase 0.3 |
| #12 | Create operator_use/guardrails/ module | #25 | Phase 0.3 |
| #13 | Add AI ethics review checklist as PR template | #25 | Phase 0.3 |
| #14 | Fix path traversal in resolve() | #26 | Phase 1.1 |
| #15 | Sanitize file download filenames and URLs | #26 | Phase 1.1 |
| #16 | Fix XPath injection in browser service | #26 | Phase 1.1 |
| #17 | Replace terminal command blocklist with allowlist | #27 | Phase 1.2 |
| #18 | Restrict browser JavaScript execution | #27 | Phase 1.2 |
| #19 | Replace os.system() with subprocess.run() | #27 | Phase 1.2 |
| #20 | Default browser to clean profile | #28 | Phase 1.3 |
| #21 | Fix allow_from to default-deny on empty list | #28 | Phase 1.3 |
| **#22** | **Add credential masking in all log output** | **NONE — needed** | Phase 1.3 |
| **#23** | **Add rate limiting to gateway channels** | **NONE — needed** | Phase 1.4 |
| **#24** | **Add session TTL and auto-expiry** | **NONE — needed** | Phase 1.4 |
| #29 | Agent workspace isolation (Agent Space, PiP monitor) | NONE | Phase 2+ |

**Immediate to-do:** Create PRs for #22, #23, #24 (below).

---

## Phase E: Prepare New PRs for Remaining Phase 1 Issues

**Prerequisite:** Complete Phase C (rebase all branches). All new branches cut from a clean `origin/main`.

### Task 11: Create branch for Phase 1.3.3 — Credential masking (#22)

```bash
git checkout origin/main -b richardson/phase1-credential-masking
```

**Implementation scope:**
- Find all `logger.*` and `print(` calls across `operator_use/`
- Mask tokens, passwords, API keys using a regex filter in a central log formatter
- Key files likely: `operator_use/agent/service.py`, `operator_use/acp/channel.py`, any auth modules

```bash
# After implementing:
git add operator_use/
git commit -m "security: mask credentials in all log output [#22]"
git push fork richardson/phase1-credential-masking
gh pr create \
  --repo CursorTouch/Operator-Use \
  --head richard-devbot:richardson/phase1-credential-masking \
  --base main \
  --title "Phase 1.3.3: Mask credentials in all log output" \
  --body "Closes #22. Adds credential masking to all logger calls."
```

### Task 12: Create branch for Phase 1.4.1 — Rate limiting (#23)

```bash
git checkout origin/main -b richardson/phase1-rate-limiting
```

**Implementation scope:**
- Add rate limiting to `operator_use/acp/channel.py` gateway channels
- Consider `operator_use/acp/server.py` for request-level limiting
- Use a token bucket or sliding window in-process (no Redis dep needed for Phase 1)

```bash
git add operator_use/
git commit -m "security: add rate limiting to gateway channels [#23]"
git push fork richardson/phase1-rate-limiting
gh pr create \
  --repo CursorTouch/Operator-Use \
  --head richard-devbot:richardson/phase1-rate-limiting \
  --base main \
  --title "Phase 1.4.1: Rate limiting on gateway channels" \
  --body "Closes #23. Implements in-process token bucket rate limiting."
```

### Task 13: Create branch for Phase 1.4.2 — Session TTL (#24)

```bash
git checkout origin/main -b richardson/phase1-session-ttl
```

**Implementation scope:**
- Add session creation timestamps and TTL enforcement
- Auto-expire sessions after configurable idle timeout
- Key files: `operator_use/acp/models.py` (session model), `operator_use/acp/server.py` (enforcement)

```bash
git add operator_use/
git commit -m "security: add session TTL and auto-expiry [#24]"
git push fork richardson/phase1-session-ttl
gh pr create \
  --repo CursorTouch/Operator-Use \
  --head richard-devbot:richardson/phase1-session-ttl \
  --base main \
  --title "Phase 1.4.2: Session TTL and auto-expiry" \
  --body "Closes #24. Sessions now auto-expire after configurable TTL."
```

---

## Phase F: Conflict Prevention Strategy

### For every new branch:

1. **Always cut from `origin/main`, never from another feature branch:**
   ```bash
   git fetch origin
   git checkout origin/main -b richardson/<branch-name>
   ```

2. **Sync with main at least every 2 days during active development:**
   ```bash
   git fetch origin
   git rebase origin/main
   ```

3. **Never modify `uv.lock` manually.** If `pyproject.toml` changes, always run `uv lock` to regenerate.

4. **Keep PRs small and scoped to one issue group.** Large PRs accumulate drift.

5. **Before pushing a PR update, check if origin/main has moved:**
   ```bash
   git log --oneline HEAD..origin/main  # shows what you'd conflict with
   ```

### For resolving conflicts in lock files:

Lock files (`uv.lock`) are machine-generated. The correct merge strategy is always:
1. Accept upstream's lock file (`git checkout --theirs uv.lock`)
2. Regenerate with your requirements on top (`uv lock`)
3. Stage and continue (`git add uv.lock && git rebase --continue`)

This ensures your dependencies are added cleanly on top of whatever upstream added.

---

## Full Ordered Execution Sequence

```
A1. git fetch origin
A2. git checkout main && git merge --ff-only origin/main
A3. git push fork main

B4. gh pr view 25/26/27/28 --repo CursorTouch/Operator-Use --comments  (review)

C5.  git checkout richardson/security-hardening && git rebase origin/main (resolve uv.lock) && git push fork --force-with-lease
C6.  git checkout richardson/phase1-input-validation && git rebase origin/main (resolve) && git push fork --force-with-lease
C7.  git checkout richardson/phase1-execution-controls && git rebase origin/main (resolve) && git push fork --force-with-lease
C8.  git checkout richardson/phase1-auth-access && git rebase origin/main (resolve) && git push fork --force-with-lease
C9.  gh pr comment 25 --body "Rebased — conflicts resolved. Ready for review."

E10. git checkout origin/main -b richardson/phase1-credential-masking → implement #22 → push → gh pr create
E11. git checkout origin/main -b richardson/phase1-rate-limiting → implement #23 → push → gh pr create
E12. git checkout origin/main -b richardson/phase1-session-ttl → implement #24 → push → gh pr create

WAIT for Jeo to merge #25 before requesting review on #26–28.
```

---

## PR Readiness Checklist (before each PR)

- [ ] Branch rebased onto latest `origin/main`
- [ ] `git log --oneline HEAD..origin/main` shows empty (no drift)
- [ ] All tests pass locally
- [ ] Qodo review comments addressed
- [ ] PR description closes the relevant issue (`Closes #N`)
- [ ] No self-merge — wait for Jeo

---

## Notes

- **Issue #29** (agent workspace isolation) is Phase 2+ scope — do not create a PR for it yet. Wait until Phase 1 is fully merged.
- **Guardrails API naming** (`assess()` vs `classify_risk()`) and file structure decisions are deferred to Jeo review of PR #25.
- **Human-in-the-loop for browser script** (Qodo PR #27 finding #1) is intentionally Phase 2.1 — not Phase 1.
