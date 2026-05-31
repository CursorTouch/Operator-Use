from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

from operator_use.package.manifest import read_manifest
from operator_use.package.types import InstalledPackage, LoadedPackages


def _parse_git_source(source: str) -> tuple[str, Optional[str]]:
    spec = source.removeprefix("git:")
    ref: Optional[str] = None
    last_segment = spec.rsplit("/", 1)[-1]
    if "@" in last_segment:
        spec, ref = spec.rsplit("@", 1)
    if not spec.startswith(("https://", "http://", "ssh://", "git://")):
        spec = "https://" + spec
    return spec, ref


def _slug_from_url(url: str) -> str:
    slug = re.sub(r"^https?://|^ssh://git@|^ssh://|^git://", "", url)
    return re.sub(r"\.git$", "", slug)


def _safe_pypi_name(source: str) -> str:
    name = re.split(r"[=<>~!;\[\s]", source.removeprefix("pypi:").strip(), maxsplit=1)[0]
    name = re.sub(r"[-_.]+", "-", name.lower())
    return re.sub(r"[^a-z0-9-]", "", name).strip("-")


def resolve_install_path(source: str, packages_dir: Path, cwd: Path | None = None) -> Path:
    """Resolve a source spec to its local path on disk."""
    if source.startswith("pypi:"):
        return packages_dir / "pypi" / _safe_pypi_name(source)
    if source.startswith(("git:", "https://", "http://", "ssh://")):
        url, _ = _parse_git_source(source)
        return packages_dir / "git" / _slug_from_url(url)
    path = Path(source)
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    return path.resolve()


def load_packages_from_settings(
    package_sources: list[str],
    packages_dir: Path,
    cwd: Path | None = None,
) -> LoadedPackages:
    """
    Resolve each source spec from settings.packages to its install path,
    read its manifest, and collect all extension/skill/prompt dirs.
    Missing or not-yet-installed packages are silently skipped.
    """
    result = LoadedPackages()

    for source in package_sources:
        install_path = resolve_install_path(source, packages_dir, cwd)
        if not install_path.exists():
            continue

        # PyPI packages install their deps alongside their resources; put the
        # target dir on sys.path so extension code can import those deps.
        if source.startswith("pypi:"):
            target = str(install_path)
            if target not in sys.path:
                sys.path.insert(0, target)

        manifest = read_manifest(install_path)
        pkg = InstalledPackage(source=source, install_path=install_path, manifest=manifest)
        result.packages.append(pkg)

        for d in pkg.extension_dirs():
            if d.is_dir():
                result.extension_dirs.append(d)

        for p in pkg.skill_paths():
            if Path(p).is_dir():
                result.skill_paths.append(p)

        for d in pkg.prompt_dirs():
            if d.is_dir():
                result.prompt_dirs.append(d)

        for d in pkg.command_dirs():
            if d.is_dir():
                result.command_dirs.append(d)

        for d in pkg.subagent_dirs():
            if d.is_dir():
                result.subagent_dirs.append(d)

    return result
