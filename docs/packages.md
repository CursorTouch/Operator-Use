# Packages

Packages bundle extensions, skills, and prompts so they can be shared and installed from git or local paths. Installing a package makes its resources available to the agent automatically on the next startup or reload.

## Package structure

A package is a directory with an `operator.json` manifest and one or more resource subdirectories:

```
my-package/
  operator.json
  extensions/
    my_tool.py
    another_ext.py
  skills/
    greet/
      SKILL.md
  prompts/
    custom.md
  commands/
    deploy.py
  subagents/
    researcher/
      SUBAGENT.md
```

**`operator.json`** — the package manifest:

```json
{
  "name": "my-package",
  "version": "1.0.0",
  "author": "jeomon",
  "description": "A collection of useful extensions and skills.",
  "extensions": ["extensions"],
  "skills": ["skills"],
  "prompts": ["prompts"],
  "commands": ["commands"],
  "subagents": ["subagents"]
}
```

All fields except `name` are optional. If no `operator.json` is present, the package falls back to convention directories for all resource types.

`PackageManifest` fields:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `name` | `str` | directory name | Display name |
| `version` | `str` | — | Informational version string |
| `author` | `str` | — | Package author |
| `description` | `str` | — | Short description |
| `extensions` | `list[str]` | `["extensions"]` | Dirs containing extension `.py` files |
| `skills` | `list[str]` | `["skills"]` | Dirs containing `SKILL.md` skill directories |
| `prompts` | `list[str]` | `["prompts"]` | Dirs containing `.md` prompt templates |
| `commands` | `list[str]` | `["commands"]` | Dirs containing slash command `.py` files |
| `subagents` | `list[str]` | `["subagents"]` | Dirs containing `SUBAGENT.md` profile directories |

## Installing packages

Packages are installed via `install_package()` and tracked in `settings.packages`.

### Git packages

```python
from program.package.installer import install_package
from program.settings.paths import get_packages_dir

result = install_package("git:github.com/user/my-package", get_packages_dir())
# or with a pinned ref:
result = install_package("git:github.com/user/my-package@v1.0.0", get_packages_dir())
# or with a full https URL:
result = install_package("https://github.com/user/my-package", get_packages_dir())
```

Git packages are cloned into `~/.program/agent/packages/git/<host>/<path>/`. Subsequent installs of an unpinned package run `git pull`. Pinned packages (with a `@ref`) are never pulled.

### Local packages

```python
result = install_package("/absolute/path/to/my-package", get_packages_dir())
result = install_package("./relative/path", get_packages_dir(), cwd=project_root)
```

Local packages are referenced in place — nothing is copied.

### InstallResult

```python
@dataclass
class InstallResult:
    package: InstalledPackage | None
    error: str | None

result.success      # True if package is not None
result.package      # the InstalledPackage
result.error        # error message on failure
```

### Removing packages

```python
from program.package.installer import remove_package

ok, err = remove_package("git:github.com/user/my-package", get_packages_dir())
```

For git packages, the cloned directory is deleted from disk. For local packages, `remove_package` is a no-op (nothing was copied).

After removing, update `settings.packages` to deregister the source:

```python
sm.set_packages([s for s in sm.get_packages() if s != source])
```

## Package discovery at startup

`load_packages_from_settings()` resolves each source spec in `settings.packages` to its install path, reads its manifest, and returns the collected resource directories:

```python
from program.package.loader import load_packages_from_settings
from program.settings.paths import get_packages_dir

loaded = load_packages_from_settings(
    package_sources=settings_manager.get_packages(),
    packages_dir=get_packages_dir(),
    cwd=project_root,
)

loaded.packages        # list[InstalledPackage]
loaded.extension_dirs  # list[Path] — extension dirs from all packages
loaded.skill_paths     # list[str]  — skill dirs from all packages
loaded.prompt_dirs     # list[Path] — prompt dirs from all packages
loaded.command_dirs    # list[Path] — slash command dirs from all packages
loaded.subagent_dirs   # list[Path] — subagent profile dirs from all packages
```

Missing packages (not yet installed or removed from disk) are silently skipped.

## How packages wire into the resource loader

`RuntimeContext.create()` passes `settings_manager.get_packages()` and `get_packages_dir()` into `ResourceLoaderOptions`:

```python
loader_opts = ResourceLoaderOptions(
    ...
    package_sources=settings_manager.get_packages(),
    packages_dir=get_packages_dir(),
)
```

`ResourceLoader` incorporates all package resource types on each `reload()`:

| Package dir | ResourceLoader method | Effect |
|---|---|---|
| `extensions/` | `_reload_extensions()` | Appended to extension scan dirs |
| `skills/` | `_reload_skills()` | Appended to skill paths |
| `prompts/` | `_reload_context_files()` | Available as prompt templates |
| `commands/` | `_reload_commands()` | Appended to slash command scan dirs |
| `subagents/` | `_reload_subagent_profiles()` | Appended to subagent profile scan dirs |

Package resources load automatically alongside built-in and user resources. Missing packages (not yet installed or removed from disk) are silently skipped.

### Slash commands from packages

Drop `.py` files exporting `command = SlashCommandInfo(...)` (or a `commands` list) into the package's `commands/` directory. They are discovered by `load_commands()` and registered into `CommandRegistry` alongside built-in commands.

```python
# my-package/commands/deploy.py
from program.commands.types import SlashCommandInfo

async def handle(registry, args):
    print("deploying...")

command = SlashCommandInfo(name="deploy", description="Deploy the project", handler=handle)
```

### Subagent profiles from packages

Drop `SUBAGENT.md` files into subdirectories of the package's `subagents/` directory. The format is identical to built-in profiles:

```
my-package/subagents/researcher/SUBAGENT.md
```

```markdown
---
name: researcher
description: Deep research agent with web access.
tools: web_search, web_fetch, read
---

You are a focused research agent...
```

Profiles are discovered by `load_profiles()` and made available to the `subagent` tool alongside built-in profiles.

### Custom providers from packages

Packages register custom inference and memory providers through extension files that call `api.register_provider()`, `api.register_llm_api()`, `api.register_memory_provider()`, or `api.register_memory_api()`. See [extensions.md — Provider registration](./extensions.md#provider-registration) for the full pattern.

## Source spec formats

| Format | Example | What happens |
|---|---|---|
| `git:<host>/<path>` | `git:github.com/user/repo` | Cloned to packages/git/... |
| `git:<host>/<path>@<ref>` | `git:github.com/user/repo@v1` | Cloned + ref checkout, never pulled |
| `https://<url>` | `https://github.com/user/repo` | Treated as git |
| `https://<url>@<ref>` | `https://github.com/user/repo@main` | Treated as git with ref |
| `/absolute/path` | `/home/user/my-pkg` | Local reference |
| `./relative/path` | `./packages/my-pkg` | Local reference, resolved against `cwd` |

## Install path layout

```
~/.program/agent/packages/
  git/
    github.com/
      user/
        my-package/        ← cloned here
          operator.json
          extensions/
          skills/
```

Local packages are not copied — they are referenced from wherever they live on disk.

## Types reference

```python
@dataclass
class PackageManifest:
    name: str
    version: Optional[str]
    author: Optional[str]
    description: Optional[str]
    extensions: list[str]   # default: ["extensions"]
    skills: list[str]       # default: ["skills"]
    prompts: list[str]      # default: ["prompts"]
    commands: list[str]     # default: ["commands"]
    subagents: list[str]    # default: ["subagents"]

@dataclass
class InstalledPackage:
    source: str           # original spec
    install_path: Path    # resolved path on disk
    manifest: PackageManifest

    def extension_dirs(self) -> list[Path]: ...
    def skill_paths(self) -> list[str]: ...
    def prompt_dirs(self) -> list[Path]: ...
    def command_dirs(self) -> list[Path]: ...
    def subagent_dirs(self) -> list[Path]: ...

@dataclass
class LoadedPackages:
    packages: list[InstalledPackage]
    extension_dirs: list[Path]
    skill_paths: list[str]
    prompt_dirs: list[Path]
    command_dirs: list[Path]
    subagent_dirs: list[Path]
```

## Settings manager API

```python
sm.get_packages() -> list[str]        # registered source specs
sm.set_packages(sources: list[str])   # persist updated list
```

## Related documents

- [extensions.md](./extensions.md) — How extension files in packages are loaded and configured
- [skill.md](./skill.md) — How skill directories in packages are discovered
