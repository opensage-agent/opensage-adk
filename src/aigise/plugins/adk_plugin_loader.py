"""ADK plugin loader — dynamically imports BasePlugin subclasses from .py files."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Type

from google.adk.plugins.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parent / "default" / "adk_plugins"
_DEFAULT_PACKAGE = f"{__package__}.default.adk_plugins"


def load_adk_plugin_class(name: str, py_path: Path) -> Type[BasePlugin]:
    """Load a plugin class from an ADK plugin written in Python.

        If the file is inside the default plugins package it is imported by
        module name; otherwise it is loaded from the filesystem path directly.

    Raises:
      ValueError: Raised when this operation fails."""
    if py_path.parent == _DEFAULT_DIR:
        module_name = f"{_DEFAULT_PACKAGE}.{name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ValueError(
                f'Failed to import plugin "{name}" from "{module_name}".'
            ) from exc
    else:
        # User plugin — load from path
        spec = importlib.util.spec_from_file_location(name, py_path)
        if spec is None or spec.loader is None:  # pragma: no cover
            raise ValueError(f'Cannot load plugin from "{py_path}".')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    candidates = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BasePlugin) and obj is not BasePlugin
    ]
    if not candidates:
        raise ValueError(f'No BasePlugin subclass found in "{py_path}".')
    if len(candidates) > 1:
        raise ValueError(
            f'Multiple plugin classes found in "{py_path}". '
            "Please keep exactly one BasePlugin subclass per file."
        )
    return candidates[0]
