from __future__ import annotations

import importlib.util
import inspect
import traceback
from pathlib import Path

from program.skill.types import SourceInfo
from program.bus.service import EventBus
from program.extension.types import (
    Extension, ExtensionAPI, ExtensionError,
    ExtensionFactory, LoadExtensionsResult,
)


async def load_extension_from_file(path: Path, bus: EventBus | None = None, config: dict | None = None) -> tuple[Extension | None, list[ExtensionError]]:
    """Load a single Python extension file and call its factory."""
    errors: list[ExtensionError] = []
    str_path = str(path)

    try:
        spec = importlib.util.spec_from_file_location(f"_ext_{path.stem}", path)
        if spec is None or spec.loader is None:
            errors.append(ExtensionError(
                extension_path=str_path,
                event='load',
                error=f'Cannot create module spec for {path}',
            ))
            return None, errors

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)   # type: ignore[union-attr]

        factory: ExtensionFactory | None = getattr(module, 'extension', None)
        if factory is None or not callable(factory):
            errors.append(ExtensionError(
                extension_path=str_path,
                event='load',
                error='Extension file must export a callable named "extension"',
            ))
            return None, errors

        source_info = SourceInfo(path=str_path, source='local')
        ext = Extension(path=str_path, source_info=source_info, config=config or {})
        api = ExtensionAPI(ext, bus or EventBus())

        result = factory(api)
        if inspect.isawaitable(result):
            await result

        return ext, errors

    except Exception:
        errors.append(ExtensionError(
            extension_path=str_path,
            event='load',
            error=traceback.format_exc().strip().splitlines()[-1],
            stack=traceback.format_exc(),
        ))
        return None, errors


async def discover_and_load_extensions(
    dirs: list[Path],
    bus: EventBus | None = None,
    disabled_stems: set[str] | None = None,
    entry_configs: dict[str, dict] | None = None,
) -> LoadExtensionsResult:
    """Discover all *.py extension files in the given directories and load them."""
    shared_bus = bus or EventBus()
    extensions: list[Extension] = []
    all_errors: list[ExtensionError] = []

    for directory in dirs:
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob('*.py')):
            if file.name.startswith('_'):
                continue
            if disabled_stems and file.stem in disabled_stems:
                continue
            config = (entry_configs or {}).get(file.stem, {})
            ext, errors = await load_extension_from_file(file, shared_bus, config)
            if ext:
                extensions.append(ext)
            all_errors.extend(errors)

    return LoadExtensionsResult(extensions=extensions, errors=all_errors)
