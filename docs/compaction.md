# Compaction

Compaction keeps the context window under budget by summarizing old conversation history into a compact text block. When the context exceeds the reserve threshold, a separate LLM call summarizes everything before a cut point, and a `CompactionEntry` is appended to the session. On the next turn, `build_session_context()` presents the summary plus the retained tail instead of the full history.

## When compaction runs

Compaction is checked at the end of every successful `invoke()` call in Agent:

```python
if self._compact_requested or self._compaction.should_compact(
    self._context_tokens, self._context_window
):
    await self._run_compaction(...)
```

`should_compact()` triggers when:

```python
context_tokens > context_window - reserve_tokens
```

The default `reserve_tokens` gives the next turn room to work without immediately hitting the limit again.

Extensions may call `ctx.compact()` mid-turn to set `_compact_requested = True`. The actual compaction always waits until after the turn completes.

`run_compaction()` is also a public API for manual compaction. It requires `phase == "idle"` and fails immediately if called while a turn is running.

## Preparation

`Compaction.prepare(path_entries)` analyzes the session branch and returns a `CompactionPreparation` — everything needed for the LLM summarization call. Returns `None` if compaction is not possible (e.g., the last entry is already a compaction, or no valid cut point exists).

Steps:

1. Find the previous compaction entry index (`find_prev_compaction_index`).
2. Determine the boundary start — where the region to be summarized begins.
3. Estimate total context tokens (`estimate_context_tokens`).
4. Find the cut point (`find_cut_point`) — the entry at which `retain_recent_tokens` kicks in. Everything before the cut is summarized; everything from the cut to the leaf is retained.
5. Check whether the cut falls inside a turn (split-turn case).
6. Collect messages to summarize and, if split-turn, the turn-prefix messages.
7. Build a file-operations diff from previous compaction forward (tracks which files were read or modified for the compaction summary footer).

The `CompactionPreparation` carries:

```python
@dataclass
class CompactionPreparation:
    retained_from_id: str              # first entry the LLM still sees in full
    messages_to_summarize: list[...]   # messages that become the summary
    turn_prefix_messages: list[...]    # split-turn prefix (if any)
    is_split_turn: bool
    tokens_before: int
    previous_summary: str | None       # from the last compaction entry
    file_ops: dict                     # file read/write tracking
    settings: CompactionSettings
```

## Summarization

`Compaction.compact(preparation, custom_instructions)` calls the LLM to produce the summary.

**Normal case**: One LLM call with the full message history to summarize. The prompt combines the previous summary (if any) with the new messages using `UPDATE_SUMMARIZATION_PROMPT` or `SUMMARIZATION_PROMPT`.

**Split-turn case**: The cut point falls inside an incomplete assistant turn (the LLM was mid-response when the context limit hit). Two concurrent LLM calls run:

1. Summarize the history before the split turn (or skip if empty).
2. Summarize just the turn-prefix messages to produce a "Turn Context" block.

The results are concatenated with a `---` divider.

After summarization, a file-operations footer is appended:
- Files read during the compacted period.
- Files modified (created, updated, deleted) during the compacted period.

The final `CompactionResult`:

```python
@dataclass
class CompactionResult:
    summary: str               # the LLM-generated summary + file footer
    retained_from_id: str      # entry ID where the retained tail begins
    tokens_before: int         # context size before compaction
    details: CompactionDetails # read_files, modified_files lists
```

## Session integration

After `compact()` returns, Agent calls:

```python
self._session_manager.append_compaction(
    summary=compaction_result.summary,
    first_kept_entry_id=compaction_result.retained_from_id,
    tokens_before=compaction_result.tokens_before,
    details=compaction_result.details,
)
self._refresh_context_tokens_from_session()
```

`refresh_context_tokens_from_session()` re-estimates the token count from the session's reconstructed message list rather than trusting the historical `input_tokens` from the last assistant message. Pre-compaction assistant messages still carry their original (larger) provider usage — using those would give a stale, inflated token count.

## Extension hooks

Extensions can intercept compaction via `session_before_compact`:

```python
@hooks.on('session_before_compact')
async def on_before_compact(event, ctx):
    return SessionBeforeCompactResult(cancel=True)         # skip compaction
    # or:
    return SessionBeforeCompactResult(compaction=my_result) # supply custom summary
```

If any handler returns `cancel=True`, compaction is skipped and Agent returns `False` from `_run_compaction()`. If any handler supplies a `CompactionResult`, the LLM call is skipped and that result is used directly.

After compaction, `session_compact` fires with the newly appended `CompactionEntry`.

## Settings

```python
@dataclass
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 8192       # headroom to keep for the next turn
    keep_recent_tokens: int = 16384  # tokens to retain uncompressed after the cut
```

Settings are read live via a provider callback so changes to `settings.json` take effect on the next compaction check without restarting.

## Context reconstruction after compaction

See [session.md](./session.md) — specifically the `build_session_context()` section — for how the LLM receives the compacted context on subsequent turns.

## Related documents

- [agent.md](./agent.md) — Compaction scheduling inside `invoke()`
- [session.md](./session.md) — How `CompactionEntry` affects context reconstruction
- [hooks.md](./hooks.md) — `session_before_compact` and `session_compact` events
