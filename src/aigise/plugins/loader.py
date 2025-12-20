from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterable
from pathlib import Path
from typing import List, Type

from google.adk.plugins.base_plugin import BasePlugin

_PLUGIN_DIR = Path(__file__).resolve().parent
_PLUGIN_PACKAGE = __package__


def _load_plugin_class(name: str) -> Type[BasePlugin]:
    """Load a plugin class based on its file name."""
    module_name = f"{_PLUGIN_PACKAGE}.{name}"
    module_path = _PLUGIN_DIR / f"{name}.py"
    if not module_path.exists():
        raise ValueError(
            f'Unknown plugin "{name}". File '
            f'"{module_path.name}" does not exist in {_PLUGIN_DIR}.'
        )
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ValueError(
            f'Failed to import plugin "{name}" from "{module_name}".'
        ) from exc

    candidates = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BasePlugin) and obj is not BasePlugin
    ]
    if not candidates:
        raise ValueError(f'No BasePlugin subclass found in module "{module_name}".')
    if len(candidates) > 1:
        raise ValueError(
            f'Multiple plugin classes found in "{module_name}". '
            "Please keep exactly one BasePlugin subclass per file."
        )
    return candidates[0]


def load_plugins(enabled: Iterable[str] | None) -> List[BasePlugin]:
    """Instantiate plugins in the order provided by `enabled`."""
    plugins: List[BasePlugin] = []
    if not enabled:
        return plugins

    for name in enabled:
        plugin_class = _load_plugin_class(name)
        plugins.append(plugin_class())
    return plugins
