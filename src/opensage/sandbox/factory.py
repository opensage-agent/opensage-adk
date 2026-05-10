"""Sandbox factory for creating typed sandbox instances."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Optional, Type

from opensage.sandbox.initializers.base import SandboxInitializer

from .base_sandbox import BaseSandbox
from .local_sandbox import LocalSandbox

logger = logging.getLogger(__name__)

# Backend modules are imported lazily in get_backend_class() so that
# ``import opensage`` works without optional deps like nitrobox or opensandbox.
# Each entry: backend_name → (module_name, class_name).
_BACKENDS = {
    "native":           ("native_docker_sandbox",       "NativeDockerSandbox"),
    "k8s":              ("k8s_sandbox",                 "K8sSandbox"),
    "remotedocker":     ("remote_docker_sandbox",       "RemoteDockerSandbox"),
    "opensandbox":      ("opensandbox_sandbox",         "OpenSandboxSandbox"),
    "agentdocker-lite": ("agentdocker_lite_sandbox",    "AgentDockerLiteSandbox"),
    "local":            ("local_sandbox",               "LocalSandbox"),
}  # fmt: skip

_LOADED: dict[str, type[BaseSandbox]] = {"local": LocalSandbox}

# Backward-compatible dict — populated lazily by get_backend_class().
SANDBOX_BACKENDS: dict[str, type[BaseSandbox]] = {"local": LocalSandbox}

_BUILTIN_DIR = Path(__file__).resolve().parent / "initializers"
_BUILTIN_PACKAGE = "opensage.sandbox.initializers"
_USER_DIR = Path.home() / ".local" / "opensage" / "initializers"


def _load_initializer_from_file(
    name: str, py_path: Path, *, is_builtin: bool = False
) -> Type[SandboxInitializer] | None:
    """Load a SandboxInitializer subclass from a .py file.

    Returns None if the file contains no SandboxInitializer subclass.

    Raises:
        ValueError: If multiple SandboxInitializer subclasses are found.
    """
    if is_builtin:
        # Built-in: import by module name to avoid double-loading
        module = importlib.import_module(f"{_BUILTIN_PACKAGE}.{name}")
    else:
        # User: load from filesystem path
        spec = importlib.util.spec_from_file_location(name, py_path)
        if spec is None or spec.loader is None:
            logger.warning('Cannot load initializer from "%s".', py_path)
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    candidates = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, SandboxInitializer) and obj is not SandboxInitializer
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError(
            f'Multiple SandboxInitializer subclasses found in "{py_path}". '
            "Please keep exactly one per file."
        )
    return candidates[0]


def _scan_dir(
    directory: Path, *, is_builtin: bool = False
) -> dict[str, Type[SandboxInitializer]]:
    """Scan a directory for .py files containing SandboxInitializer subclasses."""
    found: dict[str, Type[SandboxInitializer]] = {}
    if not directory.is_dir():
        return found
    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_") or py_file.stem == "base":
            continue
        try:
            cls = _load_initializer_from_file(
                py_file.stem, py_file, is_builtin=is_builtin
            )
            if cls is not None:
                found[py_file.stem] = cls
        except Exception:
            logger.exception("Failed to load sandbox initializer from %s", py_file)
    return found


def _discover_initializers() -> dict[str, Type[SandboxInitializer]]:
    """Discover sandbox initializers by scanning directories.

    Scan order (later entries override earlier ones):
    1. Built-in: src/opensage/sandbox/initializers/*.py
    2. User-defined: ~/.local/opensage/initializers/*.py
    """
    registry = _scan_dir(_BUILTIN_DIR, is_builtin=True)

    user_initializers = _scan_dir(_USER_DIR)
    for name, cls in user_initializers.items():
        logger.info("Loaded user sandbox initializer: %s -> %s", name, cls.__name__)
    registry.update(user_initializers)

    return registry


SANDBOX_INITIALIZERS = _discover_initializers()


def create_sandbox_class(
    backend_class: Type[BaseSandbox], initializer_class: Type
) -> Type[BaseSandbox]:
    """
    Create a sandbox class by combining a backend with a initializer.

    Args:
        backend_class (Type[BaseSandbox]): The backend sandbox class (e.g., NativeDockerSandbox)
        initializer_class (Type): Initializer class to add functionality
    Returns:
        Type[BaseSandbox]: A new class that combines the backend and initializer
    """

    # Create a dynamic class that combines backend + initializer
    class CombinedSandbox(initializer_class, backend_class):
        """Dynamically created sandbox class with initializer functionality."""

        def __init__(
            self,
            container_config,
            session_id=None,
            backend_type=None,
            sandbox_type=None,
        ):
            # Initialize the backend
            backend_class.__init__(
                self, container_config, session_id, backend_type, sandbox_type
            )

    # Set a meaningful name for the combined class
    CombinedSandbox.__name__ = (
        f"{backend_class.__name__}With{initializer_class.__name__}"
    )
    CombinedSandbox.__qualname__ = CombinedSandbox.__name__

    return CombinedSandbox


def get_backend_class(backend_type: str, config=None) -> Type[BaseSandbox]:
    """
    Get the backend class for a given backend type.

    The backend module is imported lazily on first use so that
    ``import opensage`` works without optional dependencies like
    nitrobox or opensandbox.

    Args:
      backend_type (str): The type of backend needed (e.g., 'native', 'k8s')
      config: Optional config to inject into backend (for remotedocker)
    Returns:
      Type[BaseSandbox]: The backend class

    Raises:
      ValueError: If backend type is not supported
      ImportError: If the backend's dependencies are not installed
    """
    # Resolve from cache or lazy-import
    backend_class = _LOADED.get(backend_type)
    if backend_class is None:
        if backend_type not in _BACKENDS:
            raise ValueError(
                f"Unsupported backend type: {backend_type!r}. "
                f"Known: {sorted(_BACKENDS.keys())}"
            )
        module_name, class_name = _BACKENDS[backend_type]
        try:
            mod = importlib.import_module(f".{module_name}", package="opensage.sandbox")
        except ImportError as e:
            raise ImportError(
                f"Sandbox backend {backend_type!r} is unavailable: {e}"
            ) from e
        backend_class = getattr(mod, class_name)
        _LOADED[backend_type] = backend_class
        SANDBOX_BACKENDS[backend_type] = backend_class

    # Inject config for remote docker backend
    if (
        backend_type in {"remotedocker", "opensandbox"}
        and config
        and hasattr(backend_class, "set_config")
    ):
        backend_class.set_config(config)

    return backend_class


def get_initializer_class(sandbox_type: str) -> Type:
    """
    Get the initializer class for a given sandbox type.

    Args:
        sandbox_type (str): The type of sandbox functionality needed
    Returns:
        Type: The initializer class, or the base SandboxInitializer if not found
    """
    return SANDBOX_INITIALIZERS.get(sandbox_type, SandboxInitializer)
