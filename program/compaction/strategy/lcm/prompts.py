LEAF_SUMMARY_SYSTEM_PROMPT = (
    "You are a context archival assistant. Summarize the provided conversation turns "
    "into a compact, information-dense record. Preserve decisions, file paths, function "
    "names, error messages, and any unresolved questions. Do NOT continue the conversation."
)

LEAF_SUMMARY_PROMPT = """Summarize these conversation turns into a compact archival record.

Use this format:

## What Happened
- [Key actions taken, in order]

## Decisions Made
- **[Decision]**: [Brief rationale]

## Files Touched
- [Exact file paths mentioned or modified]

## Unresolved / Carry Forward
- [Anything left open or that the next context window needs to know]

Be dense. Preserve exact identifiers, paths, and error text."""

CONDENSE_PROMPT = """These are summaries from earlier conversation segments. Condense them into a single higher-level summary.

Preserve all unresolved items and key decisions. Drop redundant narrative. Keep exact file paths, function names, and error messages.

Use this format:

## Arc
[One paragraph: what the overall session is trying to accomplish]

## Key Decisions
- **[Decision]**: [Rationale]

## Files Touched
- [Consolidated list of exact paths]

## Open Items
- [Anything still unresolved across all source summaries]"""

ACTIVE_CONTEXT_PREAMBLE = (
    "[ARCHIVED CONTEXT — searchable via `lcm_grep`]\n"
    "Earlier turns were compacted into the archive below. "
    "Use `lcm_grep` to search for specific details or `lcm_expand` to drill into a summary node. "
    "Do NOT re-derive information already in the archive — retrieve it instead.\n\n"
)
