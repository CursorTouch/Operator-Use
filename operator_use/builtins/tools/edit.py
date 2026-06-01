import re
import zlib
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult


def _file_hash(content: str) -> str:
    normalized = re.sub(r'[ \t\r]+(?=\n|$)', '', content)
    return format(zlib.crc32(normalized.encode('utf-8')) & 0xFFFF, '04X')


class Edit(BaseModel):
    operation: Literal["replace", "delete", "insert_before", "insert_after", "insert_head", "insert_tail"] = Field(
        ...,
        description=(
            "'replace': replace lines start_line..end_line with new_content. "
            "'delete': remove lines start_line..end_line (no new_content). "
            "'insert_before': insert new_content before start_line. "
            "'insert_after': insert new_content after start_line. "
            "'insert_head': insert new_content at the very start of the file. "
            "'insert_tail': insert new_content at the very end of the file."
        ),
    )
    start_line: int | None = Field(
        default=None,
        description=(
            "1-based original line number. Required for replace, delete, insert_before, "
            "insert_after. Not used for insert_head or insert_tail."
        ),
    )
    end_line: int | None = Field(
        default=None,
        description=(
            "Last line of the range (inclusive). Required for replace and delete when "
            "targeting more than one line. Defaults to start_line when omitted."
        ),
    )
    new_content: str = Field(
        default="",
        description=(
            "Lines to insert or use as replacement. For replace/insert ops, provide the "
            "full replacement text. Not used for delete."
        ),
    )


class EditSchema(BaseModel):
    path: str = Field(..., description="Absolute path or path relative to the current working directory.")
    file_hash: str = Field(
        ...,
        description=(
            "The 4-hex TAG from the '¶PATH#TAG' header in the read output (e.g. 'A1B2'). "
            "Validates that the file has not changed since it was last read. "
            "If the tag is stale, re-read the file before editing."
        ),
    )
    edits: list[Edit] = Field(
        ...,
        description=(
            "One or more edits to apply. All line numbers refer to the ORIGINAL file "
            "as read — they do not shift as other edits in this batch apply. "
            "Edits are applied bottom-up (highest line first) so earlier anchors stay valid."
        ),
    )


class EditTool(Tool):
    def __init__(self):
        super().__init__(
            name="edit_file",
            description=(
                "Edit a file using line numbers from the read tool. Supply the file_hash "
                "('¶PATH#TAG' header) to guard against stale edits. All line numbers in "
                "the batch refer to the ORIGINAL file — they do not shift as hunks apply. "
                "Use replace/delete/insert_before/insert_after/insert_head/insert_tail."
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
        expected_hash = (params.get("file_hash") or '').strip().upper()
        edits_raw = params.get("edits", [])

        if not path_str:
            return ToolResult.error(id=invocation.id, content="Parameter 'path' is required.")
        if not expected_hash:
            return ToolResult.error(id=invocation.id, content="Parameter 'file_hash' is required.")

        resolved_path = Path(path_str).resolve()
        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"File not found: {resolved_path}")

        try:
            raw = resolved_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to read file: {resolved_path}. {e}")

        actual_hash = _file_hash(raw)
        if actual_hash != expected_hash:
            return ToolResult.error(
                id=invocation.id,
                content=(
                    f"File hash mismatch (expected {expected_hash!r}, got {actual_hash!r}). "
                    "Re-read the file to get the current '¶PATH#TAG' before editing."
                ),
            )

        trailing_newline = raw.endswith('\n') or raw.endswith('\r\n')
        eol = '\r\n' if '\r\n' in raw else '\n'
        lines = raw.splitlines()
        total = len(lines)

        # Parse and validate all edits before touching the file.
        parsed: list[tuple] = []
        for i, entry in enumerate(edits_raw):
            if isinstance(entry, dict):
                op = entry.get("operation") or ''
                start = entry.get("start_line")
                end = entry.get("end_line")
                new_content = entry.get("new_content", '')
            else:
                op = entry.operation
                start = entry.start_line
                end = entry.end_line
                new_content = entry.new_content

            if not op:
                return ToolResult.error(id=invocation.id, content=f"Edit #{i + 1}: 'operation' is required.")

            if op in ("insert_head", "insert_tail"):
                parsed.append((op, None, None, new_content))
                continue

            if start is None:
                return ToolResult.error(
                    id=invocation.id,
                    content=f"Edit #{i + 1}: 'start_line' is required for operation '{op}'.",
                )
            end = end if end is not None else start

            if start < 1 or start > total:
                return ToolResult.error(
                    id=invocation.id,
                    content=f"Edit #{i + 1}: start_line {start} is out of range (file has {total} lines).",
                )
            if op in ("replace", "delete") and (end < start or end > total):
                return ToolResult.error(
                    id=invocation.id,
                    content=f"Edit #{i + 1}: end_line {end} is invalid (start={start}, total={total}).",
                )

            parsed.append((op, start, end, new_content))

        # Apply bottom-up so original line numbers for later edits stay valid.
        def _replacement_lines(text: str) -> list[str]:
            return text.splitlines() if text else []

        # Sort anchor edits by start_line descending; head/tail go last (applied first).
        anchor_edits = [(op, s, e, nc) for op, s, e, nc in parsed if op not in ("insert_head", "insert_tail")]
        special_edits = [(op, s, e, nc) for op, s, e, nc in parsed if op in ("insert_head", "insert_tail")]
        anchor_edits.sort(key=lambda x: x[1], reverse=True)

        for op, start, end, new_content in anchor_edits:
            s = start - 1  # 0-based
            e = end - 1    # 0-based inclusive
            replacement = _replacement_lines(new_content)

            if op == "replace":
                lines[s:e + 1] = replacement
            elif op == "delete":
                lines[s:e + 1] = []
            elif op == "insert_before":
                lines[s:s] = replacement
            elif op == "insert_after":
                lines[e + 1:e + 1] = replacement

        for op, _, _, new_content in special_edits:
            replacement = _replacement_lines(new_content)
            if op == "insert_head":
                lines[0:0] = replacement
            elif op == "insert_tail":
                lines.extend(replacement)

        result = eol.join(lines)
        if trailing_newline and result and not result.endswith(('\n', '\r\n')):
            result += eol

        try:
            resolved_path.write_text(result, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to write file: {resolved_path}. {e}")

        new_hash = _file_hash(result)
        return ToolResult.ok(
            id=invocation.id,
            content=f"Applied {len(edits_raw)} edit(s) to {resolved_path}.\n¶{resolved_path}#{new_hash}",
        )


tool = EditTool()
