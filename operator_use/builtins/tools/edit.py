import re
import zlib
from pathlib import Path
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult


def _line_hash(line: str, line_num: int) -> str:
    stripped = line.rstrip('\n\r').strip()
    if not any(c.isalnum() for c in stripped):
        return format(line_num % 256, '02x')
    return format(zlib.crc32(stripped.encode('utf-8')) % 256, '02x')


def _parse_anchor(anchor: str) -> tuple[tuple[int, str], tuple[int, str] | None]:
    """Parse 'LINE:HASH' or 'LINE:HASH-LINE:HASH'. Returns (start, end|None)."""
    parts = anchor.split('-', 1)
    def parse_one(s: str) -> tuple[int, str]:
        m = re.match(r'^(\d+):([0-9a-f]{2})$', s.strip())
        if not m:
            raise ValueError(f"Invalid anchor format {s!r} — expected LINE:HASH (e.g. '5:a3').")
        return int(m.group(1)), m.group(2)
    start = parse_one(parts[0])
    end = parse_one(parts[1]) if len(parts) > 1 else None
    return start, end


class Edit(BaseModel):
    anchor: str = Field(
        ...,
        description=(
            "Line anchor(s) from read output. Single line: '5:a3'. "
            "Range: '5:a3-8:f1'. The hash validates that the file hasn't changed."
        ),
    )
    new_content: str = Field(
        default="",
        description="Replacement text for the anchored line(s). Empty string deletes them.",
    )
    insert_after: bool = Field(
        default=False,
        description="If true, insert new_content after the anchor instead of replacing it.",
    )


class EditSchema(BaseModel):
    path: str = Field(..., description="Absolute path or path relative to the current working directory.")
    edits: list[Edit] = Field(
        ...,
        description=(
            "One or more edits to apply in order. Each edit references line anchors "
            "from the read tool output (LINE:HASH format)."
        ),
    )


class EditTool(Tool):
    def __init__(self):
        super().__init__(
            name="edit_file",
            description=(
                "Edit a file using line anchors from the read tool. Each edit targets a "
                "line or range by its LINE:HASH anchor (e.g. '5:a3' or '5:a3-8:f1'). "
                "The hash verifies the file hasn't changed since it was read — mismatches "
                "are rejected. Set insert_after=true to insert without replacing."
            ),
            schema=EditSchema,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Parallel,
        )

    def get_display_name(self, args: dict) -> str:
        path = args.get('path', '') or ''
        name = path.rsplit('/', 1)[-1] if path else ''
        return f"Editing: {name}" if name else "Editing file"

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params
        path_str = params.get("path")
        edits_raw = params.get("edits", [])

        if not path_str:
            return ToolResult.error(id=invocation.id, content="Parameter 'path' is required.")

        resolved_path = Path(path_str).resolve()
        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"File not found: {resolved_path}")

        try:
            content = resolved_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to read file: {resolved_path}. {e}")

        lines = content.splitlines(keepends=True)

        # Normalise trailing newline: if file ended with \n, last element is ''. Track it.
        trailing_newline = content.endswith('\n') or content.endswith('\r\n')

        # Parse all edits first so we fail before touching the file.
        parsed: list[tuple[int, int, str, bool]] = []  # (start_idx, end_idx, new_content, insert_after)
        for i, entry in enumerate(edits_raw):
            if isinstance(entry, dict):
                anchor = entry.get("anchor") or ''
                new_content = entry.get("new_content", '')
                insert_after = entry.get("insert_after", False)
            else:
                anchor = entry.anchor
                new_content = entry.new_content
                insert_after = entry.insert_after

            if not anchor:
                return ToolResult.error(id=invocation.id, content=f"Edit #{i + 1}: 'anchor' is required.")

            try:
                (start_line, start_hash), end_pair = _parse_anchor(anchor)
            except ValueError as e:
                return ToolResult.error(id=invocation.id, content=f"Edit #{i + 1}: {e}")

            end_line = end_pair[0] if end_pair else start_line
            end_hash = end_pair[1] if end_pair else start_hash

            # Validate 1-based line numbers are in range.
            if start_line < 1 or start_line > len(lines):
                return ToolResult.error(
                    id=invocation.id,
                    content=f"Edit #{i + 1}: line {start_line} is out of range (file has {len(lines)} lines).",
                )
            if end_line < start_line or end_line > len(lines):
                return ToolResult.error(
                    id=invocation.id,
                    content=f"Edit #{i + 1}: end line {end_line} is out of range or before start.",
                )

            # Validate hashes.
            actual_start_hash = _line_hash(lines[start_line - 1], start_line)
            if actual_start_hash != start_hash:
                return ToolResult.error(
                    id=invocation.id,
                    content=(
                        f"Edit #{i + 1}: hash mismatch at line {start_line} "
                        f"(expected {start_hash!r}, got {actual_start_hash!r}). "
                        "Re-read the file before editing."
                    ),
                )
            if end_pair:
                actual_end_hash = _line_hash(lines[end_line - 1], end_line)
                if actual_end_hash != end_hash:
                    return ToolResult.error(
                        id=invocation.id,
                        content=(
                            f"Edit #{i + 1}: hash mismatch at end line {end_line} "
                            f"(expected {end_hash!r}, got {actual_end_hash!r}). "
                            "Re-read the file before editing."
                        ),
                    )

            parsed.append((start_line - 1, end_line - 1, new_content, insert_after))

        # Apply edits in reverse order so earlier line indices stay valid.
        for start_idx, end_idx, new_content, insert_after in reversed(parsed):
            replacement_lines = []
            if new_content:
                # Preserve the file's line ending style.
                eol = '\r\n' if lines and '\r\n' in lines[start_idx] else '\n'
                for ln in new_content.splitlines():
                    replacement_lines.append(ln + eol)

            if insert_after:
                # Keep the anchor line, insert after it.
                lines[end_idx + 1:end_idx + 1] = replacement_lines
            else:
                lines[start_idx:end_idx + 1] = replacement_lines

        result = ''.join(lines)
        if trailing_newline and result and not result.endswith(('\n', '\r\n')):
            result += '\n'

        try:
            resolved_path.write_text(result, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to write file: {resolved_path}. {e}")

        return ToolResult.ok(id=invocation.id, content=f"Applied {len(edits_raw)} edit(s) to {resolved_path}.")


tool = EditTool()
