# Session

`SessionManager` owns all persistent state for one agent session: the JSONL file on disk, the in-memory entry index, the current leaf pointer, and the tree structure that makes branching and forking possible.

## Storage format

Sessions are stored as JSONL files — one JSON object per line. The first line is always a `SessionHeader`. All subsequent lines are `SessionEntry` objects, each with an `id`, `parent_id`, and `timestamp`.

```
{"type":"session_header","id":"<uuid>","timestamp":...,"cwd":"/my/project"}
{"type":"message","id":"a1","parent_id":null,"timestamp":...,"message":{...}}
{"type":"message","id":"a2","parent_id":"a1","timestamp":...,"message":{...}}
{"type":"compaction","id":"a3","parent_id":"a2","summary":"...","first_kept_entry_id":"a2"}
{"type":"message","id":"a4","parent_id":"a3","timestamp":...,"message":{...}}
{"type":"leaf","id":"a5","parent_id":"a4","timestamp":...,"target_id":"a2"}
```

`parent_id` forms a linked list from leaf to root. Following `parent_id` links from any entry back to the root gives you the branch — the sequence of entries the LLM will see on that path.

## Lazy persistence

Session files are not written on creation. The file is first written when the session accumulates at least one `AssistantMessage`. Until that point `flushed = False` and nothing is on disk.

Once `flushed = True`, new entries are appended one at a time with `open("a")`. This keeps persistence O(1) per entry.

If a new session is created or entries are rewound before any assistant message arrives, no file is written and no cleanup is needed.

## In-memory index

`_build_index()` walks all entries and maintains:

- `by_id: dict[str, SessionEntry]` — fast entry lookup
- `leaf_id: str | None` — the current tip of the active branch
- `labels_by_id: dict[str, str]` — human labels attached to entries

`leaf_id` is updated on every `_append_entry()` call. `LeafEntry` records are special: they update `leaf_id` to `entry.target_id` (a navigation pointer, not the leaf entry itself).

## Entry types

| Type | Purpose |
|---|---|
| `SessionHeader` | First entry in every file; holds session ID and cwd |
| `MessageEntry` | One `AgentMessage` (user, assistant, or tool-result) |
| `CompactionEntry` | Marks where a compaction happened; carries the summary and `first_kept_entry_id` |
| `BranchEntry` | A branch-navigation point with a summary of what was on the old branch |
| `LeafEntry` | A durable navigation pointer: `target_id` becomes the new leaf |
| `LabelEntry` | Attaches or removes a human label on any entry |
| `ThinkingLevelChangeEntry` | Records a thinking-level switch for context reconstruction |
| `ModelChangeEntry` | Records a model switch |
| `SessionInfoEntry` | Stores an arbitrary session name |
| `CustomInfoEntry` | Extension-writable metadata |
| `CustomMessageEntry` | Extension-writable message displayed in the UI |

## Context reconstruction

`build_session_context()` walks `get_branch()` (root-to-leaf path) and builds the message list the LLM will receive:

1. Scan all entries for the most recent `ThinkingLevelChangeEntry` and `ModelChangeEntry` — these set the `thinking_level` and `model_id` for the next turn.
2. Find the most recent `CompactionEntry` on the path.
3. If no compaction:
   - Append all `MessageEntry` and `BranchEntry` messages in path order.
4. If a compaction exists:
   - Prepend a `CompactionSummaryMessage` (the compaction summary as a synthetic user message).
   - Include entries between `first_kept_entry_id` and the compaction entry (the retained pre-compaction tail).
   - Include all entries after the compaction entry.

This gives the LLM: a summary of everything before the cut point, the retained tail of the last incomplete turn (if the compaction split mid-turn), and all messages after the compaction.

## Tree navigation

The session is a tree, not a linear list. Every entry has a `parent_id`, enabling arbitrary branching from any point in history.

### branch(from_id)

Move the leaf pointer backward (or sideways) without discarding entries:

```python
def branch(self, from_id: str):
    leaf_entry = LeafEntry(parent_id=self.leaf_id, target_id=from_id)
    self.entries.append(leaf_entry)
    self.leaf_id = from_id
```

The `LeafEntry` is persisted so the navigation survives a restart. The original chain of entries is untouched — walking the tree still shows them. Only `leaf_id` changes.

### branch_with_summary(summary, from_id)

Creates a `BranchEntry` at `from_id`, attaching a summary of what was on the previous branch. Used when the Runtime forks the session during tree navigation with a branch-summary pass.

### create_branched_session(leaf_id)

Creates a new JSONL file containing only the entries on the path from root to `leaf_id`. The new session gets its own `session_id` and a `parent_session` back-link. This is the mechanism behind the Runtime's `fork_session()`.

### get_branch(from_id)

Returns the root-to-leaf path for any given entry ID by following `parent_id` links backward and reversing:

```python
def get_branch(self, from_id=None) -> list[SessionEntry]:
    path = []
    cursor = from_id or self.leaf_id
    while cursor:
        entry = self.by_id.get(cursor)
        if not entry: break
        path.append(entry)
        cursor = entry.parent_id
    path.reverse()
    return path
```

### get_tree()

Returns the full tree as a list of `SessionTreeNode` objects (each with children). Used by UIs to display the branching history.

## Factory methods

| Method | When to use |
|---|---|
| `SessionManager.create(cwd)` | Start fresh in a new session file |
| `SessionManager.open(path)` | Load an existing session file |
| `SessionManager.continue_recent(cwd)` | Resume the most recently modified session in `session_dir` |
| `SessionManager.in_memory(cwd)` | No persistence — testing and ephemeral use |
| `SessionManager.fork_from(source, target_cwd)` | Copy a session file to a new location with a new ID |

## Session directory layout

All sessions are stored flat in a single directory:

```
~/.operator/agent/sessions/
  2024-01-15T10-30-00-000000_<uuid>.jsonl
  2024-01-15T11-00-00-000000_<uuid>.jsonl
  ...
```

`get_default_session_dir()` returns this directory (no `cwd` parameter — sessions from all projects share the same folder). `list_sessions_from_dir()` scans it and returns `SessionInfo` objects sorted by last modification time. The `cwd` recorded in each `SessionHeader` is how per-project filtering is done at list/load time.

## Related documents

- [agent.md](./agent.md) — How Agent persists messages and rewinds on retry
- [compaction.md](./compaction.md) — How `CompactionEntry` interacts with `build_session_context`
- [hooks.md](./hooks.md) — `session_before_compact`, `session_compact`, `session_before_fork`, `session_tree` events
