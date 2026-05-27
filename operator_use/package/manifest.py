from __future__ import annotations

import json
from pathlib import Path

from operator_use.package.types import PackageManifest

MANIFEST_FILE = "operator.json"
_CONVENTION_EXTENSIONS = ["extensions"]
_CONVENTION_SKILLS = ["skills"]
_CONVENTION_PROMPTS = ["prompts"]
_CONVENTION_COMMANDS = ["commands"]
_CONVENTION_SUBAGENTS = ["subagents"]


def read_manifest(package_dir: Path) -> PackageManifest:
    """Read operator.json from package_dir, or return a convention-based manifest."""
    manifest_path = package_dir / MANIFEST_FILE
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return PackageManifest(
            name=data.get("name", package_dir.name),
            version=data.get("version"),
            author=data.get("author"),
            description=data.get("description"),
            extensions=data.get("extensions", _CONVENTION_EXTENSIONS),
            skills=data.get("skills", _CONVENTION_SKILLS),
            prompts=data.get("prompts", _CONVENTION_PROMPTS),
            commands=data.get("commands", _CONVENTION_COMMANDS),
            subagents=data.get("subagents", _CONVENTION_SUBAGENTS),
        )
    return PackageManifest(
        name=package_dir.name,
        extensions=_CONVENTION_EXTENSIONS,
        skills=_CONVENTION_SKILLS,
        prompts=_CONVENTION_PROMPTS,
        commands=_CONVENTION_COMMANDS,
        subagents=_CONVENTION_SUBAGENTS,
    )
