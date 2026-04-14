"""Patch tool: apply unified diffs to files using difflib for fuzzy matching."""

import difflib
import re

from operator_use.utils.helper import resolve, is_binary_file
from operator_use.tools.service import Tool, ToolResult
from pydantic import BaseModel, Field


def _parse_unified_diff(patch_content: str) -> list[tuple[int, int, list[str]]]:
    """
    Parse unified diff content into hunks.
    Returns list of (old_start, old_count, hunk_lines) where hunk_lines are the diff lines.
    """
    hunks: list[tuple[int, int, list[str]]] = []
    lines = patch_content.splitlines()
    i = 0

    # Skip header until we find a hunk
    while i < len(lines):
        line = lines[i]
        if line.startswith("@@"):
            break
        i += 1

    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue

        # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            i += 1
            continue

        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        # new_start, new_count unused for application
        i += 1

        hunk_lines: list[str] = []
        while i < len(lines):
            hunk_line = lines[i]
            if hunk_line.startswith("@@"):
                break
            if hunk_line.startswith((" ", "-", "+")):
                hunk_lines.append(hunk_line)
            i += 1

        hunks.append((old_start, old_count, hunk_lines))

    return hunks


def _extract_old_context(hunk_lines: list[str]) -> list[str]:
    """Extract the 'old' lines (context and removals) from a hunk for matching."""
    return [
        line[1:]  # strip leading ' ' or '-'
        for line in hunk_lines
        if line.startswith((" ", "-"))
    ]


def _apply_hunk(
    old_lines: list[str], hunk_lines: list[str], old_start: int, old_count: int
) -> list[str]:
    """
    Apply a single hunk to old_lines. Returns the new lines.
    old_start is 1-based, old_count is number of context/removal lines.
    """
    # Convert to 0-based index
    start_idx = old_start - 1
    end_idx = start_idx + old_count

    # Build the replacement from the hunk (context and additions only)
    replacement: list[str] = []
    for line in hunk_lines:
        content = line[1:] + "\n"  # splitlines() strips newlines
        if line.startswith(" "):
            replacement.append(content)
        elif line.startswith("-"):
            pass  # removal
        elif line.startswith("+"):
            replacement.append(content)

    return old_lines[:start_idx] + replacement + old_lines[end_idx:]


def _find_hunk_position(
    lines: list[str], hunk_lines: list[str], old_start: int, old_count: int
) -> int | None:
    """
    Find the best position to apply the hunk. Uses difflib.SequenceMatcher for fuzzy matching.
    Returns 0-based start index or None if no suitable match.
    """
    old_context = _extract_old_context(hunk_lines)
    if not old_context:
        # Pure addition hunk - use old_start
        return max(0, old_start - 1)

    # Try exact match first
    start_idx = old_start - 1
    end_idx = start_idx + len(old_context)
    if end_idx <= len(lines):
        candidate = [line.rstrip("\n\r") for line in lines[start_idx:end_idx]]
        if candidate == old_context:
            return start_idx

    # Fuzzy match using SequenceMatcher
    text = "\n".join(line.rstrip("\n\r") for line in lines)
    pattern = "\n".join(old_context)
    matcher = difflib.SequenceMatcher(None, text, pattern)
    match = matcher.find_longest_match(0, len(text), 0, len(pattern))

    if match.size > 0:
        # Find which line this corresponds to
        before_match = text[: match.a]
        line_num = before_match.count("\n")
        return line_num

    return None


def apply_patch_to_text(original: str, patch_content: str) -> tuple[str, list[str]]:
    """
    Apply a unified diff to original text.
    Returns (patched_text, list of error/warning messages).
    """
    work_lines = original.splitlines(keepends=True)
    if not work_lines:
        work_lines = [""]

    hunks = _parse_unified_diff(patch_content)
    errors: list[str] = []

    for old_start, old_count, hunk_lines in hunks:
        pos = _find_hunk_position(work_lines, hunk_lines, old_start, old_count)
        if pos is None:
            errors.append(
                f"Hunk @@ -{old_start},{old_count} could not be applied (no matching context)"
            )
            continue

        old_context = _extract_old_context(hunk_lines)
        work_lines = _apply_hunk(work_lines, hunk_lines, pos + 1, len(old_context))

    result = "".join(work_lines)
    if result and not result.endswith("\n") and original.endswith("\n"):
        result += "\n"
    return result, errors


def create_unified_diff(
    old_lines: list[str], new_lines: list[str], fromfile: str = "a", tofile: str = "b"
) -> str:
    """Generate unified diff using difflib. Use the result with patch_file to apply changes."""
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )


class PatchFile(BaseModel):
    path: str = Field(..., description="Absolute path or path relative to the codebase root.")
    patch: str = Field(
        ...,
        description="Unified diff in standard format (--- a/file, +++ b/file, @@ hunks). Generate with 'diff -u old new' or difflib.unified_diff(). Context lines (unchanged lines around edits) are required for fuzzy matching to work.",
    )


@Tool(
    name="patch_file",
    description="Apply a unified diff to a file. Best for large or complex edits where edit_file would be unwieldy — e.g. multi-hunk changes across a big file. Uses fuzzy matching so it still works even if line numbers have shifted slightly. Prefer edit_file for simple single-location changes.",
    model=PatchFile,
)
async def patch_file(path: str, patch: str, **kwargs) -> ToolResult:
    resolved_path = resolve(base="./workspace", path=path)

    if not resolved_path.exists():
        return ToolResult.error_result(f"File not found: {resolved_path}")

    if not resolved_path.is_file():
        return ToolResult.error_result(f"Path is not a file: {resolved_path}")

    if is_binary_file(resolved_path):
        return ToolResult.error_result(f"Cannot patch binary file: {resolved_path}")

    try:
        original = resolved_path.read_text(encoding="utf-8")
    except (OSError, IOError) as e:
        return ToolResult.error_result(f"Failed to read file: {resolved_path}. {e}")

    try:
        patched, errors = apply_patch_to_text(original, patch)
    except Exception as e:
        return ToolResult.error_result(f"Failed to apply patch: {e}")

    if errors:
        return ToolResult.error_result("Patch application failed:\n" + "\n".join(errors))

    try:
        resolved_path.write_text(patched, encoding="utf-8")
    except (OSError, IOError) as e:
        return ToolResult.error_result(f"Failed to write file: {resolved_path}. {e}")

    return ToolResult.success_result(f"Applied patch to {resolved_path}")
