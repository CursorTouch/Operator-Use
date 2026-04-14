import json
from operator_use.utils.helper import resolve, is_binary_file, ensure_directory
from operator_use.tools.service import Tool, ToolResult, MAX_TOOL_OUTPUT_LENGTH
from operator_use.config.paths import get_named_workspace_dir
from pydantic import BaseModel, Field, field_validator
from pathlib import Path


def _get_workspace(**kwargs) -> Path:
    return kwargs.get("_workspace") or get_named_workspace_dir("operator")


class ReadFile(BaseModel):
    path: str = Field(
        ...,
        description="Absolute path or path relative to the codebase root. Use list_dir first if you're unsure where the file is.",
    )
    start_line: int | None = Field(
        default=None,
        description="1-based line to start reading from (inclusive). Use with end_line to read a specific section of a large file.",
        examples=[1],
    )
    end_line: int | None = Field(
        default=None,
        description="1-based line to stop reading at (inclusive). Use with start_line to avoid reading the whole file when you only need a section.",
        examples=[10],
    )


@Tool(
    name="read_file",
    description="Read a text file and return its contents with line numbers (format: N | content). Use start_line/end_line to read a slice of a large file. Cannot read binary files — use terminal for those.",
    model=ReadFile,
)
async def read_file(
    path: str, start_line: int | None = None, end_line: int | None = None, **kwargs
) -> ToolResult:
    workspace = _get_workspace(**kwargs)
    resolved_path = resolve(base=workspace, path=path)
    if not resolved_path.exists():
        return ToolResult.error_result(f"File not found: {resolved_path}")

    if not resolved_path.is_file():
        return ToolResult.error_result(f"Path is not a file: {resolved_path}")

    if is_binary_file(resolved_path):
        return ToolResult.error_result(
            f"Cannot read binary file: {resolved_path}. Use the terminal tool to execute binary files."
        )

    with open(resolved_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    total_lines = len(lines)
    start_idx = 0 if start_line is None else max(0, start_line - 1)
    end_idx = total_lines if end_line is None else min(total_lines, end_line)
    selected_lines = lines[start_idx:end_idx]

    numbered_lines = [
        f"{i + 1} | {line.rstrip('\n\r')}" for i, line in enumerate(selected_lines, start=start_idx)
    ]
    content = "\n".join(numbered_lines)

    if len(content) > MAX_TOOL_OUTPUT_LENGTH:
        content = content[:MAX_TOOL_OUTPUT_LENGTH] + "..."
    return ToolResult.success_result(content)


class WriteFile(BaseModel):
    path: str = Field(
        ...,
        description="Absolute path or path relative to the codebase root. Parent directories are created automatically.",
    )
    content: str = Field(
        ...,
        description="Full content to write. This replaces the entire file — use edit_file to change only part of an existing file.",
    )
    overwrite: bool = Field(
        default=True,
        description="Set to False to prevent accidentally overwriting an existing file. Raises an error if the file already exists.",
    )
    empty: bool = Field(
        default=False,
        description="Set to True only when intentionally writing an empty file (e.g. a placeholder). Prevents accidental empty writes by default.",
    )


@Tool(
    name="write_file",
    description="Create a new file or fully overwrite an existing one. Use for new files or complete rewrites. To change only part of a file, use edit_file instead. Parent directories are created automatically.",
    model=WriteFile,
)
async def write_file(
    path: str, content: str, overwrite: bool = True, empty: bool = False, **kwargs
) -> ToolResult:
    workspace = _get_workspace(**kwargs)
    resolved_path = resolve(base=workspace, path=path)
    file_exists = resolved_path.exists()
    if file_exists and not overwrite:
        return ToolResult.error_result(f"File exists and overwrite=False: {resolved_path}")
    if not content.strip() and not empty:
        return ToolResult.error_result(
            "Content is empty. Set empty=True to allow writing empty files."
        )
    try:
        ensure_directory(resolved_path.parent)
        resolved_path.write_text(content, encoding="utf-8")
    except (OSError, IOError) as e:
        return ToolResult.error_result(f"Failed to write file: {resolved_path}. {e}")
    return ToolResult.success_result(
        f"{'Overwrote' if file_exists else 'Created'} file: {resolved_path}"
    )


class Edit(BaseModel):
    old_content: str = Field(
        ...,
        description="Exact text to find. Must match character-for-character including whitespace and indentation.",
    )
    new_content: str = Field(
        ..., description="The replacement text. Set to empty string to delete the chunk."
    )


class EditFile(BaseModel):
    path: str = Field(..., description="Absolute path or path relative to the codebase root.")
    edits: list[Edit] = Field(
        ...,
        description="One or more edits to apply in order. Each entry finds old_content and replaces it with new_content. Applied sequentially — one read, one write.",
    )

    @field_validator("edits", mode="before")
    @classmethod
    def coerce_edits(cls, v):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"edits must be a list or a JSON-encoded list, got invalid JSON: {e}"
                )
        return v


@Tool(
    name="edit_file",
    description="Edit a file by replacing exact chunks of text. Pass one or more {old_content, new_content} pairs — applied in order on a single read/write. Set new_content to empty string to delete a chunk. Always read the file first to get the exact text. Use write_file for full rewrites.",
    model=EditFile,
)
async def edit_file(path: str, edits: list[dict], **kwargs) -> ToolResult:
    # Coerce edits if the LLM passed a JSON-encoded string instead of a list
    if isinstance(edits, str):
        try:
            edits = json.loads(edits)
        except json.JSONDecodeError as e:
            return ToolResult.error_result(f"edits must be a list, got invalid JSON string: {e}")
    workspace = _get_workspace(**kwargs)
    resolved_path = resolve(base=workspace, path=path)
    if not resolved_path.exists():
        return ToolResult.error_result(f"File not found: {resolved_path}")
    if not resolved_path.is_file():
        return ToolResult.error_result(f"Path is not a file: {resolved_path}")
    if is_binary_file(resolved_path):
        return ToolResult.error_result(f"Cannot edit binary file: {resolved_path}")
    try:
        content = resolved_path.read_text(encoding="utf-8")
    except (OSError, IOError) as e:
        return ToolResult.error_result(f"Failed to read file: {resolved_path}. {e}")

    for i, entry in enumerate(edits):
        old = entry.get("old_content") if isinstance(entry, dict) else entry.old_content
        new = entry.get("new_content") if isinstance(entry, dict) else entry.new_content
        if not old:
            return ToolResult.error_result(f"Edit #{i + 1}: old_content is required.")
        if old not in content:
            return ToolResult.error_result(
                f"Edit #{i + 1}: old_content not found in {resolved_path}. Read the file with read_file first to get the exact text, then retry."
            )
        count = content.count(old)
        if count > 1:
            return ToolResult.error_result(
                f"Edit #{i + 1}: old_content matches {count} locations. Expand old_content to include more surrounding lines so it uniquely identifies the target."
            )
        content = content.replace(old, new, 1)

    try:
        resolved_path.write_text(content, encoding="utf-8")
    except (OSError, IOError) as e:
        return ToolResult.error_result(f"Failed to write file: {resolved_path}. {e}")

    return ToolResult.success_result(f"Applied {len(edits)} edit(s) to {resolved_path}.")


class ListDir(BaseModel):
    path: str = Field(
        default=".",
        description="Absolute path or path relative to the workspace root. Omit to list the workspace root.",
    )


@Tool(
    name="list_dir",
    description="List files and subdirectories inside a directory. Directories are shown first, then files, both sorted alphabetically. Use this to explore the filesystem before reading or editing files.",
    model=ListDir,
)
async def list_dir(path: str = ".", **kwargs) -> ToolResult:
    workspace = _get_workspace(**kwargs)
    resolved_path = resolve(base=workspace, path=path)

    if not resolved_path.exists():
        return ToolResult.error_result(f"Directory not found: {resolved_path}")

    if not resolved_path.is_dir():
        return ToolResult.error_result(f"Path is not a directory: {resolved_path}")

    try:
        items = sorted(resolved_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except Exception as e:
        return ToolResult.error_result(f"Failed to list directory: {resolved_path}. {e}")

    if not items:
        return ToolResult.success_result(f"Directory is empty: {resolved_path}")

    lines = []
    for item in items:
        if item.is_dir():
            lines.append(f"📁 {item.name}/")
        else:
            lines.append(f"📄 {item.name}")

    output = "\n".join(lines)

    return ToolResult.success_result(output)
