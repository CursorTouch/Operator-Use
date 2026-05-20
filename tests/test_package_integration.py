"""Integration tests using the real test_package fixture.

Tests the full pipeline:
  install_local → load_packages_from_settings → discover_and_load_extensions
  including manifest reading, extension loading, config injection, and skill discovery.
"""
import pytest
from pathlib import Path

from program.package.manifest import read_manifest
from program.package.installer import install_local, install_package
from program.package.loader import load_packages_from_settings
from program.extension.loader import discover_and_load_extensions
from program.skill.loader import load_skills
from program.skill.types import LoadSkillsOptions

FIXTURE_PKG = Path(__file__).parent / "fixtures" / "test_package"


# ── Manifest ──────────────────────────────────────────────────────────────────

class TestFixtureManifest:
    def test_reads_name_and_author(self):
        m = read_manifest(FIXTURE_PKG)
        assert m.name == "test-package"
        assert m.author == "jeomon"
        assert m.version == "0.1.0"

    def test_extensions_and_skills_dirs_declared(self):
        m = read_manifest(FIXTURE_PKG)
        assert "extensions" in m.extensions
        assert "skills" in m.skills

    def test_extension_dirs_resolve_to_real_paths(self):
        from program.package.types import InstalledPackage
        m = read_manifest(FIXTURE_PKG)
        pkg = InstalledPackage(source=str(FIXTURE_PKG), install_path=FIXTURE_PKG, manifest=m)
        ext_dirs = pkg.extension_dirs()
        assert all(d.is_dir() for d in ext_dirs)

    def test_skill_paths_resolve_to_real_dirs(self):
        from program.package.types import InstalledPackage
        m = read_manifest(FIXTURE_PKG)
        pkg = InstalledPackage(source=str(FIXTURE_PKG), install_path=FIXTURE_PKG, manifest=m)
        skill_paths = pkg.skill_paths()
        assert all(Path(p).is_dir() for p in skill_paths)


# ── Install ───────────────────────────────────────────────────────────────────

class TestInstallFixturePackage:
    def test_install_local_succeeds(self):
        result = install_local(str(FIXTURE_PKG))
        assert result.success
        assert result.error is None

    def test_installed_package_has_correct_manifest(self):
        result = install_local(str(FIXTURE_PKG))
        assert result.package.manifest.name == "test-package"
        assert result.package.manifest.author == "jeomon"

    def test_installed_package_path_is_correct(self):
        result = install_local(str(FIXTURE_PKG))
        assert result.package.install_path == FIXTURE_PKG

    def test_install_package_dispatch_works_for_local(self, tmp_path):
        result = install_package(str(FIXTURE_PKG), tmp_path / "packages")
        assert result.success
        assert result.package.manifest.name == "test-package"


# ── Load packages ─────────────────────────────────────────────────────────────

class TestLoadFixturePackage:
    def test_package_is_discovered(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        assert len(loaded.packages) == 1

    def test_extension_dirs_returned(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        assert len(loaded.extension_dirs) == 1
        assert loaded.extension_dirs[0].name == "extensions"

    def test_skill_paths_returned(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        assert len(loaded.skill_paths) == 1
        assert Path(loaded.skill_paths[0]).name == "skills"

    def test_prompt_dirs_empty_when_no_prompts_dir(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        assert loaded.prompt_dirs == []


# ── Extension loading from package ───────────────────────────────────────────

class TestExtensionsLoadFromPackage:
    @pytest.mark.asyncio
    async def test_extensions_are_loaded(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        result = await discover_and_load_extensions(loaded.extension_dirs)
        assert len(result.extensions) == 2
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_greeter_tool_registered(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        result = await discover_and_load_extensions(loaded.extension_dirs)
        all_tools = {}
        for ext in result.extensions:
            all_tools.update(ext.tools)
        assert "greet" in all_tools

    @pytest.mark.asyncio
    async def test_config_reader_tool_registered(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        result = await discover_and_load_extensions(loaded.extension_dirs)
        all_tools = {}
        for ext in result.extensions:
            all_tools.update(ext.tools)
        assert "get_config" in all_tools

    @pytest.mark.asyncio
    async def test_greeter_handler_registered(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        result = await discover_and_load_extensions(loaded.extension_dirs)
        greeter_ext = next(e for e in result.extensions if "greeter" in e.path)
        assert "session_start" in greeter_ext.handlers

    @pytest.mark.asyncio
    async def test_config_injected_into_extension(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        ext_dirs = loaded.extension_dirs
        result = await discover_and_load_extensions(
            ext_dirs,
            entry_configs={"config_reader": {"threshold": 42, "mode": "strict"}},
        )
        config_ext = next(e for e in result.extensions if "config_reader" in e.path)
        assert config_ext.config == {"threshold": 42, "mode": "strict"}

    @pytest.mark.asyncio
    async def test_disabled_extension_is_skipped(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        result = await discover_and_load_extensions(
            loaded.extension_dirs,
            disabled_stems={"greeter"},
        )
        names = [Path(e.path).stem for e in result.extensions]
        assert "greeter" not in names
        assert "config_reader" in names

    @pytest.mark.asyncio
    async def test_greet_tool_executes_correctly(self, tmp_path):
        from program.tool.types import ToolInvocation
        from program.bus.service import EventBus

        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        result = await discover_and_load_extensions(loaded.extension_dirs, bus=EventBus())

        all_tools = {}
        for ext in result.extensions:
            all_tools.update(ext.tools)

        greet_def = all_tools["greet"].definition
        invocation = ToolInvocation(id="t1", name="greet", params={"name": "World"})
        tool_result = await greet_def.execute(
            greet_def.parameters(name="World"), invocation, ctx=None
        )
        assert "Hello, World!" in str(tool_result.content)


# ── Skills loading from package ───────────────────────────────────────────────

class TestSkillsLoadFromPackage:
    def test_greet_skill_loaded(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        skills_result = load_skills(LoadSkillsOptions(
            cwd=tmp_path,
            skill_paths=loaded.skill_paths,
            include_defaults=False,
        ))
        assert len(skills_result.skills) == 1
        skill = skills_result.skills[0]
        assert skill.name == "greet-skill"

    def test_skill_has_description(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")
        skills_result = load_skills(LoadSkillsOptions(
            cwd=tmp_path,
            skill_paths=loaded.skill_paths,
            include_defaults=False,
        ))
        skill = skills_result.skills[0]
        assert skill.description


# ── Full pipeline: install → load → extensions + skills ──────────────────────

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_install_then_load_extensions_and_skills(self, tmp_path):
        # Step 1: install
        install_result = install_local(str(FIXTURE_PKG))
        assert install_result.success

        # Step 2: load packages from settings
        source = str(FIXTURE_PKG)
        loaded = load_packages_from_settings([source], tmp_path / "packages")

        # Step 3: load extensions
        ext_result = await discover_and_load_extensions(loaded.extension_dirs)
        assert len(ext_result.extensions) == 2
        assert ext_result.errors == []

        # Step 4: load skills
        skills_result = load_skills(LoadSkillsOptions(
            cwd=tmp_path,
            skill_paths=loaded.skill_paths,
            include_defaults=False,
        ))
        assert len(skills_result.skills) == 1

        # Step 5: verify tools
        all_tools = {}
        for ext in ext_result.extensions:
            all_tools.update(ext.tools)
        assert "greet" in all_tools
        assert "get_config" in all_tools

    @pytest.mark.asyncio
    async def test_config_flows_from_settings_to_extension(self, tmp_path):
        loaded = load_packages_from_settings([str(FIXTURE_PKG)], tmp_path / "packages")

        ext_result = await discover_and_load_extensions(
            loaded.extension_dirs,
            entry_configs={"config_reader": {"api_key": "secret", "retries": 3}},
        )

        config_ext = next(e for e in ext_result.extensions if "config_reader" in e.path)
        assert config_ext.config["api_key"] == "secret"
        assert config_ext.config["retries"] == 3
