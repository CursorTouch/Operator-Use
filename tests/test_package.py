"""Tests for the package system: manifest, installer, loader."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from program.package.manifest import read_manifest, MANIFEST_FILE
from program.package.types import PackageManifest, InstalledPackage, LoadedPackages
from program.package.installer import (
    install_local, install_git, install_package, remove_package,
    _parse_git_source, _slug_from_url,
)
from program.package.loader import load_packages_from_settings, resolve_install_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_package(tmp_path: Path, name: str = "my-pkg", manifest: dict | None = None,
                 with_extensions: bool = True, with_skills: bool = True,
                 with_commands: bool = False, with_subagents: bool = False) -> Path:
    pkg_dir = tmp_path / name
    pkg_dir.mkdir()
    if manifest is not None:
        (pkg_dir / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    if with_extensions:
        (pkg_dir / "extensions").mkdir()
    if with_skills:
        (pkg_dir / "skills").mkdir()
    if with_commands:
        (pkg_dir / "commands").mkdir()
    if with_subagents:
        (pkg_dir / "subagents").mkdir()
    return pkg_dir


def make_subagent_profile_file(directory: Path, name: str = "researcher") -> Path:
    """Write a minimal SUBAGENT.md into directory/<name>/."""
    profile_dir = directory / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "SUBAGENT.md").write_text(
        f"---\nname: {name}\ndescription: A test agent\n---\nYou are a test agent.",
        encoding="utf-8",
    )
    return profile_dir


# ── read_manifest ─────────────────────────────────────────────────────────────

class TestReadManifest:
    def test_reads_operator_json(self, tmp_path):
        pkg = make_package(tmp_path, manifest={
            "name": "my-pkg", "author": "jeomon", "version": "1.0.0",
            "extensions": ["./extensions"], "skills": ["./skills"],
        })
        m = read_manifest(pkg)
        assert m.name == "my-pkg"
        assert m.author == "jeomon"
        assert m.version == "1.0.0"
        assert m.extensions == ["./extensions"]
        assert m.skills == ["./skills"]

    def test_falls_back_to_convention_when_no_manifest(self, tmp_path):
        pkg = make_package(tmp_path)
        m = read_manifest(pkg)
        assert m.name == pkg.name
        assert "extensions" in m.extensions[0]
        assert "skills" in m.skills[0]

    def test_uses_dir_name_as_name_when_missing_from_manifest(self, tmp_path):
        pkg = make_package(tmp_path, name="cool-pkg", manifest={})
        m = read_manifest(pkg)
        assert m.name == "cool-pkg"

    def test_tolerates_malformed_json(self, tmp_path):
        pkg = tmp_path / "bad-pkg"
        pkg.mkdir()
        (pkg / MANIFEST_FILE).write_text("{not valid json", encoding="utf-8")
        m = read_manifest(pkg)
        assert m.name == "bad-pkg"

    def test_optional_fields_are_none_when_absent(self, tmp_path):
        pkg = make_package(tmp_path, manifest={"name": "pkg"})
        m = read_manifest(pkg)
        assert m.author is None
        assert m.version is None
        assert m.description is None

    def test_custom_extension_and_skill_dirs(self, tmp_path):
        pkg = make_package(tmp_path, manifest={
            "name": "pkg",
            "extensions": ["./src/extensions", "./extra"],
            "skills": ["./docs/skills"],
        })
        m = read_manifest(pkg)
        assert "./src/extensions" in m.extensions
        assert "./extra" in m.extensions
        assert "./docs/skills" in m.skills


# ── InstalledPackage helpers ──────────────────────────────────────────────────

class TestInstalledPackage:
    def test_extension_dirs_resolved_from_install_path(self, tmp_path):
        pkg_dir = make_package(tmp_path)
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        dirs = pkg.extension_dirs()
        assert len(dirs) == 1
        assert dirs[0] == pkg_dir / "extensions"

    def test_skill_paths_are_strings(self, tmp_path):
        pkg_dir = make_package(tmp_path)
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        paths = pkg.skill_paths()
        assert all(isinstance(p, str) for p in paths)

    def test_multiple_extension_dirs(self, tmp_path):
        pkg_dir = make_package(tmp_path, manifest={
            "name": "pkg",
            "extensions": ["./ext1", "./ext2"],
            "skills": [],
        })
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        assert len(pkg.extension_dirs()) == 2


# ── _parse_git_source / _slug_from_url ───────────────────────────────────────

class TestGitHelpers:
    def test_parse_git_shorthand(self):
        url, ref = _parse_git_source("git:github.com/user/repo")
        assert url == "https://github.com/user/repo"
        assert ref is None

    def test_parse_git_with_ref(self):
        url, ref = _parse_git_source("git:github.com/user/repo@v1.0.0")
        assert url == "https://github.com/user/repo"
        assert ref == "v1.0.0"

    def test_parse_https_url_passthrough(self):
        url, ref = _parse_git_source("https://github.com/user/repo")
        assert url == "https://github.com/user/repo"
        assert ref is None

    def test_parse_https_url_with_ref(self):
        url, ref = _parse_git_source("https://github.com/user/repo@main")
        assert url == "https://github.com/user/repo"
        assert ref == "main"

    def test_slug_strips_protocol(self):
        assert _slug_from_url("https://github.com/user/repo") == "github.com/user/repo"

    def test_slug_strips_git_suffix(self):
        assert _slug_from_url("https://github.com/user/repo.git") == "github.com/user/repo"

    def test_slug_strips_ssh(self):
        slug = _slug_from_url("ssh://git@github.com/user/repo")
        assert "github.com/user/repo" in slug


# ── install_local ─────────────────────────────────────────────────────────────

class TestInstallLocal:
    def test_installs_existing_path(self, tmp_path):
        pkg_dir = make_package(tmp_path, manifest={"name": "local-pkg"})
        result = install_local(str(pkg_dir))
        assert result.success
        assert result.package.manifest.name == "local-pkg"
        assert result.package.install_path == pkg_dir

    def test_error_for_missing_path(self, tmp_path):
        result = install_local(str(tmp_path / "does_not_exist"))
        assert not result.success
        assert result.error is not None

    def test_resolves_relative_path(self, tmp_path):
        pkg_dir = make_package(tmp_path, name="rel-pkg", manifest={"name": "rel-pkg"})
        result = install_local("rel-pkg", cwd=tmp_path)
        assert result.success
        assert result.package.install_path == pkg_dir

    def test_source_preserved_on_package(self, tmp_path):
        pkg_dir = make_package(tmp_path, manifest={"name": "pkg"})
        result = install_local(str(pkg_dir))
        assert result.package.source == str(pkg_dir)


# ── install_git (mocked) ──────────────────────────────────────────────────────

class TestInstallGit:
    def _make_fake_pkg(self, tmp_path: Path, slug: str) -> Path:
        """Simulate a cloned package directory."""
        install_path = tmp_path / "packages" / "git" / slug
        install_path.mkdir(parents=True)
        (install_path / MANIFEST_FILE).write_text(
            json.dumps({"name": "git-pkg", "author": "jeomon"}), encoding="utf-8"
        )
        (install_path / "extensions").mkdir()
        return install_path

    def test_clones_new_package(self, tmp_path):
        packages_dir = tmp_path / "packages"
        slug = "github.com/user/repo"

        def fake_run(cmd, cwd=None, capture_output=False, text=False):
            if "clone" in cmd:
                self._make_fake_pkg(tmp_path, slug)
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("program.package.installer.subprocess.run", side_effect=fake_run):
            result = install_git("git:github.com/user/repo", packages_dir)

        assert result.success
        assert result.package.manifest.name == "git-pkg"

    def test_returns_error_on_clone_failure(self, tmp_path):
        packages_dir = tmp_path / "packages"

        def fake_run(cmd, cwd=None, capture_output=False, text=False):
            m = MagicMock()
            m.returncode = 1
            m.stderr = "repository not found"
            m.stdout = ""
            return m

        with patch("program.package.installer.subprocess.run", side_effect=fake_run):
            result = install_git("git:github.com/user/bad-repo", packages_dir)

        assert not result.success
        assert "repository not found" in result.error

    def test_pulls_existing_package(self, tmp_path):
        packages_dir = tmp_path / "packages"
        slug = "github.com/user/repo"
        self._make_fake_pkg(tmp_path, slug)

        calls = []

        def fake_run(cmd, cwd=None, capture_output=False, text=False):
            calls.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("program.package.installer.subprocess.run", side_effect=fake_run):
            result = install_git("git:github.com/user/repo", packages_dir)

        assert result.success
        assert any("pull" in cmd for cmd in calls)
        assert not any("clone" in cmd for cmd in calls)

    def test_pinned_ref_skips_pull(self, tmp_path):
        packages_dir = tmp_path / "packages"
        slug = "github.com/user/repo"
        self._make_fake_pkg(tmp_path, slug)

        calls = []

        def fake_run(cmd, cwd=None, capture_output=False, text=False):
            calls.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("program.package.installer.subprocess.run", side_effect=fake_run):
            result = install_git("git:github.com/user/repo@v1.0.0", packages_dir)

        assert result.success
        assert not any("pull" in cmd for cmd in calls)


# ── install_package dispatch ──────────────────────────────────────────────────

class TestInstallPackageDispatch:
    def test_dispatches_local_for_absolute_path(self, tmp_path):
        pkg = make_package(tmp_path, manifest={"name": "pkg"})
        result = install_package(str(pkg), tmp_path / "packages")
        assert result.success

    def test_dispatches_local_for_relative_path(self, tmp_path):
        pkg = make_package(tmp_path, name="rel", manifest={"name": "rel"})
        result = install_package("rel", tmp_path / "packages", cwd=tmp_path)
        assert result.success

    def test_dispatches_git_for_git_prefix(self, tmp_path):
        packages_dir = tmp_path / "packages"
        slug = "github.com/user/repo"

        def fake_run(cmd, cwd=None, capture_output=False, text=False):
            if "clone" in cmd:
                install_path = packages_dir / "git" / slug
                install_path.mkdir(parents=True)
                (install_path / MANIFEST_FILE).write_text(json.dumps({"name": "p"}), encoding="utf-8")
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("program.package.installer.subprocess.run", side_effect=fake_run):
            result = install_package("git:github.com/user/repo", packages_dir)
        assert result.success

    def test_dispatches_git_for_https_url(self, tmp_path):
        packages_dir = tmp_path / "packages"
        slug = "github.com/user/https-repo"

        def fake_run(cmd, cwd=None, capture_output=False, text=False):
            if "clone" in cmd:
                install_path = packages_dir / "git" / slug
                install_path.mkdir(parents=True)
                (install_path / MANIFEST_FILE).write_text(json.dumps({"name": "p"}), encoding="utf-8")
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("program.package.installer.subprocess.run", side_effect=fake_run):
            result = install_package("https://github.com/user/https-repo", packages_dir)
        assert result.success


# ── remove_package ────────────────────────────────────────────────────────────

class TestRemovePackage:
    def test_removes_git_package_dir(self, tmp_path):
        packages_dir = tmp_path / "packages"
        slug = "github.com/user/repo"
        install_path = packages_dir / "git" / slug
        install_path.mkdir(parents=True)
        (install_path / "some_file.py").write_text("x")

        ok, err = remove_package("git:github.com/user/repo", packages_dir)
        assert ok
        assert not install_path.exists()

    def test_remove_nonexistent_is_ok(self, tmp_path):
        ok, err = remove_package("git:github.com/user/ghost", tmp_path / "packages")
        assert ok
        assert err == ""

    def test_local_package_remove_is_noop(self, tmp_path):
        pkg = make_package(tmp_path, manifest={"name": "local"})
        ok, err = remove_package(str(pkg), tmp_path / "packages")
        assert ok
        assert pkg.exists()  # not deleted — local packages are just dereferenced


# ── load_packages_from_settings ───────────────────────────────────────────────

class TestLoadPackagesFromSettings:
    def test_loads_package_with_existing_dirs(self, tmp_path):
        pkg = make_package(tmp_path, manifest={"name": "pkg", "extensions": ["extensions"], "skills": ["skills"]})
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)

        assert len(loaded.packages) == 1
        assert len(loaded.extension_dirs) == 1
        assert len(loaded.skill_paths) == 1

    def test_skips_missing_packages(self, tmp_path):
        packages_dir = tmp_path / "packages"
        loaded = load_packages_from_settings(
            [str(tmp_path / "ghost")], packages_dir
        )
        assert loaded.packages == []
        assert loaded.extension_dirs == []

    def test_only_includes_dirs_that_exist(self, tmp_path):
        pkg = make_package(tmp_path, manifest={
            "name": "pkg",
            "extensions": ["extensions", "extra"],  # "extra" dir doesn't exist
            "skills": ["skills"],
        })
        packages_dir = tmp_path / "packages"
        loaded = load_packages_from_settings([str(pkg)], packages_dir)

        assert len(loaded.extension_dirs) == 1  # only "extensions" exists

    def test_loads_multiple_packages(self, tmp_path):
        pkg1 = make_package(tmp_path, name="pkg1", manifest={"name": "pkg1"})
        pkg2 = make_package(tmp_path, name="pkg2", manifest={"name": "pkg2"})
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg1), str(pkg2)], packages_dir)

        assert len(loaded.packages) == 2
        assert len(loaded.extension_dirs) == 2

    def test_empty_sources_returns_empty_result(self, tmp_path):
        loaded = load_packages_from_settings([], tmp_path / "packages")
        assert loaded.packages == []
        assert loaded.extension_dirs == []
        assert loaded.skill_paths == []

    def test_resolves_git_source_to_packages_dir(self, tmp_path):
        packages_dir = tmp_path / "packages"
        install_path = packages_dir / "git" / "github.com" / "user" / "repo"
        install_path.mkdir(parents=True)
        (install_path / MANIFEST_FILE).write_text(json.dumps({"name": "git-pkg"}), encoding="utf-8")
        (install_path / "extensions").mkdir()

        loaded = load_packages_from_settings(
            ["git:github.com/user/repo"], packages_dir
        )

        assert len(loaded.packages) == 1
        assert loaded.packages[0].manifest.name == "git-pkg"

    def test_convention_dirs_used_when_no_manifest(self, tmp_path):
        pkg = make_package(tmp_path)  # no manifest, has extensions/ and skills/
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)

        assert len(loaded.extension_dirs) == 1
        assert len(loaded.skill_paths) == 1


# ── resolve_install_path ──────────────────────────────────────────────────────

class TestResolveInstallPath:
    def test_git_resolves_to_packages_git_dir(self, tmp_path):
        packages_dir = tmp_path / "packages"
        path = resolve_install_path("git:github.com/user/repo", packages_dir)
        assert path == packages_dir / "git" / "github.com" / "user" / "repo"

    def test_https_resolves_to_packages_git_dir(self, tmp_path):
        packages_dir = tmp_path / "packages"
        path = resolve_install_path("https://github.com/user/repo", packages_dir)
        assert path == packages_dir / "git" / "github.com" / "user" / "repo"

    def test_local_absolute_resolves_to_itself(self, tmp_path):
        packages_dir = tmp_path / "packages"
        path = resolve_install_path(str(tmp_path), packages_dir)
        assert path == tmp_path

    def test_local_relative_resolves_against_cwd(self, tmp_path):
        packages_dir = tmp_path / "packages"
        path = resolve_install_path("my-pkg", packages_dir, cwd=tmp_path)
        assert path == tmp_path / "my-pkg"


# ── PackageManifest commands / subagents ──────────────────────────────────────

class TestPackageManifestCommandsAndSubagents:
    def test_default_commands_convention(self, tmp_path):
        pkg = make_package(tmp_path)
        m = read_manifest(pkg)
        assert m.commands == ["commands"]

    def test_default_subagents_convention(self, tmp_path):
        pkg = make_package(tmp_path)
        m = read_manifest(pkg)
        assert m.subagents == ["subagents"]

    def test_custom_commands_dir_from_manifest(self, tmp_path):
        pkg = make_package(tmp_path, manifest={
            "name": "pkg",
            "commands": ["./my_commands", "./extra_cmds"],
        })
        m = read_manifest(pkg)
        assert m.commands == ["./my_commands", "./extra_cmds"]

    def test_custom_subagents_dir_from_manifest(self, tmp_path):
        pkg = make_package(tmp_path, manifest={
            "name": "pkg",
            "subagents": ["./agents"],
        })
        m = read_manifest(pkg)
        assert m.subagents == ["./agents"]

    def test_commands_falls_back_to_convention_when_no_manifest(self, tmp_path):
        pkg = make_package(tmp_path)  # no operator.json
        m = read_manifest(pkg)
        assert "commands" in m.commands[0]

    def test_subagents_falls_back_to_convention_when_no_manifest(self, tmp_path):
        pkg = make_package(tmp_path)
        m = read_manifest(pkg)
        assert "subagents" in m.subagents[0]


# ── InstalledPackage command_dirs / subagent_dirs ─────────────────────────────

class TestInstalledPackageCommandsAndSubagents:
    def test_command_dirs_resolved_from_install_path(self, tmp_path):
        pkg_dir = make_package(tmp_path, with_commands=True)
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        dirs = pkg.command_dirs()
        assert len(dirs) == 1
        assert dirs[0] == pkg_dir / "commands"

    def test_subagent_dirs_resolved_from_install_path(self, tmp_path):
        pkg_dir = make_package(tmp_path, with_subagents=True)
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        dirs = pkg.subagent_dirs()
        assert len(dirs) == 1
        assert dirs[0] == pkg_dir / "subagents"

    def test_multiple_command_dirs_from_manifest(self, tmp_path):
        pkg_dir = make_package(tmp_path, manifest={
            "name": "pkg",
            "commands": ["./cmds1", "./cmds2"],
        })
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        assert len(pkg.command_dirs()) == 2

    def test_command_dirs_are_path_objects(self, tmp_path):
        pkg_dir = make_package(tmp_path, with_commands=True)
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        assert all(isinstance(d, Path) for d in pkg.command_dirs())

    def test_subagent_dirs_are_path_objects(self, tmp_path):
        pkg_dir = make_package(tmp_path, with_subagents=True)
        manifest = read_manifest(pkg_dir)
        pkg = InstalledPackage(source=str(pkg_dir), install_path=pkg_dir, manifest=manifest)
        assert all(isinstance(d, Path) for d in pkg.subagent_dirs())


# ── LoadedPackages command_dirs / subagent_dirs ───────────────────────────────

class TestLoadedPackagesCommandsAndSubagents:
    def test_loaded_packages_has_command_dirs_field(self):
        lp = LoadedPackages()
        assert hasattr(lp, 'command_dirs')
        assert lp.command_dirs == []

    def test_loaded_packages_has_subagent_dirs_field(self):
        lp = LoadedPackages()
        assert hasattr(lp, 'subagent_dirs')
        assert lp.subagent_dirs == []

    def test_load_packages_collects_command_dirs(self, tmp_path):
        pkg = make_package(tmp_path, manifest={"name": "pkg"}, with_commands=True)
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)

        assert len(loaded.command_dirs) == 1
        assert loaded.command_dirs[0] == pkg / "commands"

    def test_load_packages_collects_subagent_dirs(self, tmp_path):
        pkg = make_package(tmp_path, manifest={"name": "pkg"}, with_subagents=True)
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)

        assert len(loaded.subagent_dirs) == 1
        assert loaded.subagent_dirs[0] == pkg / "subagents"

    def test_command_and_subagent_dirs_empty_when_dirs_absent(self, tmp_path):
        # Package exists but has no commands/ or subagents/ dirs
        pkg = make_package(tmp_path, manifest={"name": "pkg"})
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)

        assert loaded.command_dirs == []
        assert loaded.subagent_dirs == []

    def test_multiple_packages_accumulate_command_dirs(self, tmp_path):
        pkg1 = make_package(tmp_path, name="p1", manifest={"name": "p1"}, with_commands=True)
        pkg2 = make_package(tmp_path, name="p2", manifest={"name": "p2"}, with_commands=True)
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg1), str(pkg2)], packages_dir)

        assert len(loaded.command_dirs) == 2

    def test_subagent_profiles_loadable_from_package_subagents_dir(self, tmp_path):
        """End-to-end: SUBAGENT.md files in a package's subagents/ dir are discoverable."""
        from program.subagent.profile import load_profiles

        pkg = make_package(tmp_path, manifest={"name": "pkg"}, with_subagents=True)
        make_subagent_profile_file(pkg / "subagents", name="coder")
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)
        assert len(loaded.subagent_dirs) == 1

        result = load_profiles(loaded.subagent_dirs)
        assert len(result.profiles) == 1
        assert result.profiles[0].name == "coder"

    def test_slash_commands_loadable_from_package_commands_dir(self, tmp_path):
        """End-to-end: .py files in a package's commands/ dir are discoverable."""
        from program.commands.loader import load_commands as load_cmds
        from program.commands.types import SlashCommandInfo

        pkg = make_package(tmp_path, manifest={"name": "pkg"}, with_commands=True)
        cmd_file = pkg / "commands" / "greet.py"
        cmd_file.write_text(
            "from program.commands.types import SlashCommandInfo\n"
            "async def handle(reg, args): pass\n"
            "command = SlashCommandInfo(name='greet', description='Say hi', handler=handle)\n",
            encoding="utf-8",
        )
        packages_dir = tmp_path / "packages"

        loaded = load_packages_from_settings([str(pkg)], packages_dir)
        assert len(loaded.command_dirs) == 1

        result = load_cmds(loaded.command_dirs)
        assert len(result.commands) == 1
        assert result.commands[0].name == "greet"
