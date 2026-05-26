from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PackageManifest:
    name: str
    version: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    extensions: list[str] = field(default_factory=lambda: ["extensions"])
    skills: list[str] = field(default_factory=lambda: ["skills"])
    prompts: list[str] = field(default_factory=lambda: ["prompts"])
    commands: list[str] = field(default_factory=lambda: ["commands"])
    subagents: list[str] = field(default_factory=lambda: ["subagents"])


@dataclass
class InstalledPackage:
    source: str          # original spec: "git:github.com/user/repo", "/path/to/pkg"
    install_path: Path
    manifest: PackageManifest

    def extension_dirs(self) -> list[Path]:
        return [self.install_path / d for d in self.manifest.extensions]

    def skill_paths(self) -> list[str]:
        return [str(self.install_path / d) for d in self.manifest.skills]

    def prompt_dirs(self) -> list[Path]:
        return [self.install_path / d for d in self.manifest.prompts]

    def command_dirs(self) -> list[Path]:
        return [self.install_path / d for d in self.manifest.commands]

    def subagent_dirs(self) -> list[Path]:
        return [self.install_path / d for d in self.manifest.subagents]


@dataclass
class LoadedPackages:
    packages: list[InstalledPackage] = field(default_factory=list)
    extension_dirs: list[Path] = field(default_factory=list)
    skill_paths: list[str] = field(default_factory=list)
    prompt_dirs: list[Path] = field(default_factory=list)
    command_dirs: list[Path] = field(default_factory=list)
    subagent_dirs: list[Path] = field(default_factory=list)


@dataclass
class InstallResult:
    package: Optional[InstalledPackage]
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.package is not None
