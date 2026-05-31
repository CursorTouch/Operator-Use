from __future__ import annotations

import re
import shutil
import subprocess
import sys
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


def _normalize_pypi_name(name: str) -> str:
    """Normalize a PyPI project name to a safe on-disk dir name (PEP 503-ish)."""
    name = re.sub(r"[-_.]+", "-", name.strip().lower())
    return re.sub(r"[^a-z0-9-]", "", name).strip("-")


def _parse_pypi_source(source: str) -> tuple[str, str]:
    """Parse 'pypi:my-tools==1.2.3' → (pip_spec, safe_name).

    The pip spec is passed verbatim to the installer; safe_name is the project
    name with version/marker specifiers stripped, used only as the dir name."""
    spec = source.removeprefix("pypi:").strip()
    name = re.split(r"[=<>~!;\[\s]", spec, maxsplit=1)[0]
    return spec, _normalize_pypi_name(name)


def _pypi_install_path(safe_name: str, packages_dir: Path) -> Optional[Path]:
    """Resolve the on-disk install path for a PyPI package, ensuring it stays
    inside packages_dir/pypi. Returns None for an empty or escaping name."""
    base = (packages_dir / "pypi").resolve()
    if not safe_name:
        return None
    candidate = (base / safe_name).resolve()
    if candidate != base and base not in candidate.parents:
        return None
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


def install_pypi(source: str, packages_dir: Path) -> InstallResult:
    pip_spec, safe_name = _parse_pypi_source(source)
    install_path = _pypi_install_path(safe_name, packages_dir)
    if install_path is None:
        return InstallResult(package=None, error=f"Invalid package source path: {source!r}")

    # Clean install: drop any prior contents so removed deps don't linger.
    shutil.rmtree(install_path, ignore_errors=True)
    install_path.mkdir(parents=True, exist_ok=True)

    if shutil.which("uv"):
        cmd = ["uv", "pip", "install", "--target", str(install_path), pip_spec]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--target", str(install_path), pip_spec]

    code, err = _run(cmd)
    if code != 0:
        return InstallResult(package=None, error=f"pip install failed: {err}")

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
    if source.startswith("pypi:"):
        return install_pypi(source, packages_dir)
    if source.startswith(("git:", "https://", "http://", "ssh://")):
        return install_git(source, packages_dir)
    return install_local(source, cwd)


def remove_package(source: str, packages_dir: Path) -> tuple[bool, str]:
    """Remove an installed git or pypi package from disk. Local packages are just dereferenced."""
    if source.startswith("pypi:"):
        _, safe_name = _parse_pypi_source(source)
        install_path = _pypi_install_path(safe_name, packages_dir)
    elif source.startswith(("git:", "https://", "http://", "ssh://")):
        url, _ = _parse_git_source(source)
        install_path = _git_install_path(url, packages_dir)
    else:
        return True, ""  # local — nothing to delete from disk

    if install_path is None:
        return False, f"Invalid package source path: {source!r}"

    if not install_path.exists():
        return True, ""

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
