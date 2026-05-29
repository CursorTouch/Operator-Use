from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from operator_use.package.manifest import read_manifest
from operator_use.package.types import InstalledPackage, InstallResult


def _parse_git_source(source: str) -> tuple[str, Optional[str]]:
    """Parse 'git:github.com/user/repo@ref' → (clone_url, ref | None)."""
    spec = source.removeprefix("git:")
    ref: Optional[str] = None

    # Split off trailing @ref (only after the last path segment)
    last_segment = spec.rsplit("/", 1)[-1]
    if "@" in last_segment:
        spec, ref = spec.rsplit("@", 1)

    if not spec.startswith(("https://", "http://", "ssh://", "git://")):
        spec = "https://" + spec
    return spec, ref


def _slug_from_url(url: str) -> str:
    """Convert a clone URL to a safe relative directory path."""
    slug = re.sub(r"^https?://|^ssh://git@|^ssh://|^git://", "", url)
    slug = re.sub(r"\.git$", "", slug)
    return slug  # e.g. "github.com/user/repo"


def _git_install_path(url: str, packages_dir: Path) -> Optional[Path]:
    """Resolve the on-disk install path for a clone URL, ensuring it stays
    inside packages_dir/git. Returns None if the slug escapes that root
    (e.g. via '..' segments or an absolute path), which would otherwise let a
    crafted source clone to — or rmtree — an arbitrary directory."""
    base = (packages_dir / "git").resolve()
    candidate = (base / _slug_from_url(url)).resolve()
    if candidate != base and base not in candidate.parents:
        return None
    if candidate == base:
        return None  # empty slug — no repo segment
    return candidate


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, (result.stderr or result.stdout).strip()


def install_git(source: str, packages_dir: Path) -> InstallResult:
    url, ref = _parse_git_source(source)
    install_path = _git_install_path(url, packages_dir)
    if install_path is None:
        return InstallResult(package=None, error=f"Invalid package source path: {source!r}")

    if install_path.exists():
        if ref is None:
            code, err = _run(["git", "pull"], install_path)
            if code != 0:
                return InstallResult(package=None, error=f"git pull failed: {err}")
    else:
        install_path.parent.mkdir(parents=True, exist_ok=True)
        code, err = _run(["git", "clone", url, str(install_path)])
        if code != 0:
            return InstallResult(package=None, error=f"git clone failed: {err}")
        if ref:
            _run(["git", "checkout", ref], install_path)

    manifest = read_manifest(install_path)
    return InstallResult(package=InstalledPackage(
        source=source, install_path=install_path, manifest=manifest,
    ))


def install_local(source: str, cwd: Path | None = None) -> InstallResult:
    path = Path(source)
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    path = path.resolve()

    if not path.exists():
        return InstallResult(package=None, error=f"Path does not exist: {path}")

    manifest = read_manifest(path)
    return InstallResult(package=InstalledPackage(
        source=source, install_path=path, manifest=manifest,
    ))


def install_package(source: str, packages_dir: Path, cwd: Path | None = None) -> InstallResult:
    """Install a package from a source spec and return the result."""
    if source.startswith(("git:", "https://", "http://", "ssh://")):
        return install_git(source, packages_dir)
    return install_local(source, cwd)


def remove_package(source: str, packages_dir: Path) -> tuple[bool, str]:
    """Remove an installed git package from disk. Local packages are just dereferenced."""
    if not source.startswith(("git:", "https://", "http://", "ssh://")):
        return True, ""  # local — nothing to delete from disk

    url, _ = _parse_git_source(source)
    install_path = _git_install_path(url, packages_dir)
    if install_path is None:
        return False, f"Invalid package source path: {source!r}"

    if not install_path.exists():
        return True, ""

    import shutil
    try:
        shutil.rmtree(install_path)
        # Clean up empty parent dirs
        for parent in install_path.parents:
            if parent == packages_dir:
                break
            try:
                parent.rmdir()
            except OSError:
                break
        return True, ""
    except Exception as e:
        return False, str(e)
