---
name: github-cli
description: Use when working with GitHub repositories, managing PRs/issues, or performing git operations — commands, flags, authentication, API queries, and context injection patterns
---

# GitHub CLI (gh)

## Overview

**GitHub CLI is a command-line interface to GitHub that replaces browser workflows.** Unlike git, which manages local repositories, `gh` automates GitHub-specific operations: creating/managing PRs and issues, reviewing code, searching repos, and querying the GitHub API directly.

**Core principle:** `gh` commands are context-aware—they detect your current repo and user automatically, reducing boilerplate.

## When to Use

- Creating, updating, closing, or reviewing pull requests
- Creating, managing, or closing issues
- Searching repositories or filtering PRs/issues
- Querying GitHub API without wrestling with GraphQL/REST endpoints
- Running GitHub operations from scripts or automation

## Key Commands

| Task | Command | Notes |
|------|---------|-------|
| Create PR | `gh pr create --title "..." --body "..."` | Auto-detects base branch (usually main) |
| List PRs | `gh pr list --state open` | Filters available; `--json` for machine-readable output |
| View PR | `gh pr view <number>` | Shows status, checks, reviews, comments |
| Add comment | `gh pr comment <number> --body "..."` | Markdown-safe; Markdown renders on GitHub |
| Request review | `gh pr review --request-review @user` | Can request multiple: `@user1 @user2` |
| Check PR status | `gh pr checks <number>` | Shows CI checks (tests, linters, etc.) |
| Search issues | `gh issue list --search "..." --json fields` | Uses GitHub search syntax |
| Query API | `gh api /repos/{owner}/{repo}/pulls` | Direct REST API access with token auto-injection |

## Common Patterns

### Pattern 1: Auto-Squash When Creating PR

**Reality:** `gh pr create` does NOT have a squash flag. Squashing is a **git operation**, not a GitHub operation.

**DO NOT TRY:**
```bash
gh pr create --squash --title "Performance improvements"      # ❌ --squash flag does not exist
gh pr create --auto-squash --title "Performance improvements" # ❌ This flag does not exist either
```

**DO THIS INSTEAD:**

**Option A: Squash locally before creating PR**
```bash
# Step 1: Interactive rebase to squash/fixup commits (if needed)
git rebase -i main

# Step 2: Create PR with clean history
gh pr create --title "Performance improvements" --body "Details here" --base main
```

**Option B: Create PR with all commits, squash at merge time**
```bash
# Create PR as-is
gh pr create --title "Performance improvements" --body "Details here" --base main

# Later, when merging on GitHub: click "Squash and merge" button (not --squash flag)
```

**When to squash before PR:**
- You have many "fixup" or "WIP" commits that clutter history
- Team prefers clean linear history on main

**When NOT to squash:**
- PR has intentionally separate commits (different features, refactors, docs)
- Team prefers preserving commit history for blame/bisect
- You want to preserve individual commit messages for main branch

### Pattern 2: Filter PRs by Reviewer

**Reality:** `gh pr list` does NOT have a `--reviewer` or `--review-requested` filter. These flags do not exist and will error.

**DO NOT TRY:**
```bash
gh pr list --reviewer @alice           # ❌ This flag does not exist
gh pr list --review-requested          # ❌ This flag does not exist
gh pr list --assignee @alice           # ⚠️ This gets ASSIGNED PRs, not reviews
```

**DO THIS INSTEAD:**

```bash
# Get PRs where you're a reviewer (any review state, across all repos)
gh api search/issues --search-query "type:pr reviewed-by:@me state:open" --json number,title

# Get PRs with pending review requested from you (across all repos)
gh api search/issues --search-query "type:pr review-requested:@me state:open" --json number,title

# If you're in a single repo, filter by repo name:
gh api search/issues --search-query "type:pr review-requested:@me repo:owner/repo-name state:open" --json number,title
```

**Verification:**
```bash
# Check the command returned results
gh api search/issues --search-query "type:pr review-requested:@me" --json number,title | head -5
```

**Why `gh api` instead of `gh pr list`:**
- `gh pr list` filters only the repo in your current directory
- Search API (`gh api search/issues`) queries across all accessible repos
- The search endpoint supports `reviewed-by:@me` and `review-requested:@me` syntax
- Injected token handles authentication automatically

### Pattern 3: Add Comments with Mentions and Links

**CRITICAL: Always verify after posting. Typos and escaping errors silently fail.**

```bash
# Simple mention and link
gh pr comment 42 --body "cc @alice — see #99 for details"

# Multi-line comment (use heredoc to avoid escaping issues)
gh pr comment 42 --body "$(cat <<'EOF'
Reviewed and approved.

cc @alice

See #99 for related work.
EOF
)"
```

**Verification (mandatory—do this every time):**
```bash
# Check the comment posted and rendered correctly
gh pr view 42 --json comments --jq '.comments[-1] | {body, author: .author.login}'
```

**What to check in the output:**
- ✅ `@alice` rendered as a link (not plain text)
- ✅ `#99` rendered as a link (not plain text)
- ✅ Body matches what you intended
- ✅ Author is your username

**If verification shows a typo or malformed link:**
- The mention was posted but DIDN'T ping the person
- Delete the comment and repost: `gh pr comment 42 --body "Deleting to repost correctly"` then repost
- This 20-second verification prevents hours of back-and-forth

**Why not skip verification under time pressure?**
- Typos silently fail (comment posts, mention doesn't ping)
- Shell escaping errors silently change the text
- Verification takes 10 seconds and saves hours of debugging
- **When in doubt, verify.**

**Common mistake:** "I'll skip verification because I'm under time pressure." This is backwards—time pressure makes verification MORE important, not less.

## Context Injection

`gh` automatically injects context from your current environment:

| Context | Behavior |
|---------|----------|
| `$GH_TOKEN` or auth cached | No token flag needed; commands just work |
| Current git directory | `gh` detects repo owner and name; no `--repo` needed |
| Current git branch | `gh pr create` defaults base to main; use `--base` to override |
| GitHub username | Injected into search queries (`@me` resolves to your username) |

### Breakage Points

If a command says "repository not found":
1. Verify you're in a git repo: `git status`
2. Verify the repo has a GitHub remote: `git remote -v`
3. Check authentication: `gh auth status`

## API Queries (gh api)

Use `gh api` to reach the GitHub REST API directly without managing tokens or endpoints:

```bash
# Syntax: gh api [flags] <endpoint>
gh api /repos/{owner}/{repo}/pulls --json number,title,createdAt

# Query with jq for filtering
gh api search/issues --search-query "type:pr is:open" | jq '.items[] | {number, title}'

# POST data (create a resource)
gh api /repos/{owner}/{repo}/issues -f title="Bug" -f body="Description"
```

**Flags:**
- `--method GET/POST/PATCH/DELETE` — HTTP verb
- `-f field=value` — Set fields (auto-quotes strings)
- `--json fields` — Return only these fields (faster)
- `-H "header: value"` — Custom headers

## Red Flags - STOP Before Running

These are symptoms you're about to make a mistake:

- ❌ **"I'll use `gh pr create --squash`"** → Squash flag does NOT exist. Squash locally FIRST with `git rebase -i`, then create PR.
- ❌ **"Let me try `gh pr list --reviewer @user`"** → This flag doesn't exist. Use `gh api search/issues` with search syntax instead.
- ❌ **"I'll skip verification, just run the command"** → Typos and shell escaping errors silently fail. 10 seconds to verify saves hours debugging.
- ❌ **"The mention will work fine"** → Typos in `@username` post silently; the mention doesn't ping. Verify with `gh pr view` after.
- ❌ **"Double quotes should work in bash"** → On Windows bash, shell escaping is fragile. Use heredoc or single quotes.
- ❌ **"I'll create the PR without a body"** → PR body defaults to empty. Set it with `--body "..."` or `--fill` to edit interactively.

**When you catch yourself about to do ANY of these, stop and re-read the relevant section.**

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `gh pr create` needs a pushed branch | Push first: `git push -u origin branch-name` |
| Assuming `--squash` flag exists on `gh pr create` | Squash locally with `git rebase -i` BEFORE creating PR |
| Using `gh pr list --reviewer` or similar filters | Use `gh api search/issues` with GitHub search syntax instead |
| Typos in `@username` mentions silently fail | Always verify with `gh pr view` after posting—check mentions rendered |
| Shell escaping breaks on Windows bash | Use heredoc or single-quoted strings: `'text'` or `"$(cat <<'EOF'...)"` |
| PR description left empty | PR body defaults to empty; set it explicitly with `--body "..."` or `--fill` |
| Forgetting base branch | Defaults to main; use `--base` to override (e.g., `--base develop`) |
| Guessing at `gh api` endpoint paths | Paths are REST API routes: `/repos/{owner}/{repo}/pulls` not `/pull` or `/pr` |

## Rationalization Traps

When under time pressure, agents rationalize skipping verification. Here's why these don't work:

| Rationalization | Reality |
|---|---|
| "I'll skip verification—the command will just work" | Typos and escaping errors silently fail. Command succeeds; result is wrong. |
| "Mentions will render fine without checking" | Typo in `@alice` → comment posts, but mention doesn't ping. Wasted. |
| "I don't have time to verify" | Verification takes 10 sec. Fixing a silently-failed mention takes 10 min. Math doesn't work. |
| "Shell escaping is fine on Windows" | Windows bash has fragile quoting. Test before running on real PRs. |
| "I'll just try both approaches" | Trying `gh pr list --reviewer` then `gh api search/issues`—but you guessed wrong on both. Stick to documented syntax. |
| "The API endpoint probably works" | REST API endpoint paths are strict: `/repos/{owner}/{repo}/pulls` not `/pull`. One character wrong = API error. |

**The pattern:** All these rationalize skipping validation steps. When you catch yourself thinking one of these, STOP and validate instead.

## Quick Reference: Windows Bash Escaping

On Windows bash (MinGW/MSYS2), some quotes behave differently. When in doubt:

```bash
# ✅ Safe: Single quotes or heredoc
gh pr comment 42 --body 'text with "quotes" inside'

# ✅ Safe: Heredoc for multi-line
gh pr comment 42 --body "$(cat <<'EOF'
multi-line
text here
EOF
)"

# ⚠️ Risky: Escaped double quotes (platform-dependent)
gh pr comment 42 --body "text with \"escaped quotes\""
```

Test before running on real PRs:
```bash
gh pr view 42 --json body
```

## Real-World Impact

- **Automation:** Bulk operations (close stale PRs, label issues) via scripts
- **CI/CD:** GitHub Actions can use `gh` to post results back to PRs
- **Faster reviews:** Check PR status, request reviews, merge—all without opening browser

## See Also

- `gh --help` — command reference (always up-to-date)
- `gh auth login` — set up token-based authentication
- GitHub API docs: https://docs.github.com/en/rest
