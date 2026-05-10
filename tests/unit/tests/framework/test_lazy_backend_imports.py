"""Tests for lazy sandbox backend imports in factory.py."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock


@contextmanager
def _stub_optional_deps():
    """Temporarily stub optional deps needed while importing the factory."""
    module_names = (
        "nitrobox",
        "opensandbox",
        "tree_sitter_language_pack",
        "neomodel",
    )
    original_modules = {
        name: sys.modules.get(name) for name in module_names if name in sys.modules
    }
    inserted_modules = []
    try:
        for name in module_names:
            if name not in sys.modules:
                sys.modules[name] = MagicMock()
                inserted_modules.append(name)
        yield
    finally:
        for name in inserted_modules:
            sys.modules.pop(name, None)
        for name, module in original_modules.items():
            sys.modules[name] = module


with _stub_optional_deps():
    from opensage.sandbox.factory import (
        _BACKENDS,
        _LOADED,
        get_backend_class,
    )


def test_registry_contains_all_backends():
    expected = {
        "native",
        "k8s",
        "remotedocker",
        "opensandbox",
        "agentdocker-lite",
        "local",
    }
    assert set(_BACKENDS.keys()) == expected


def test_registry_entries_are_two_tuples():
    for name, entry in _BACKENDS.items():
        assert len(entry) == 2, f"{name}: expected (module, class), got {entry}"


def test_local_backend_always_loaded():
    assert "local" in _LOADED
    cls = get_backend_class("local")
    assert cls.__name__ == "LocalSandbox"


def test_unknown_backend_raises_valueerror():
    import pytest

    with pytest.raises(ValueError, match="Unsupported backend type"):
        get_backend_class("nonexistent")


def test_native_backend_resolves():
    cls = get_backend_class("native")
    assert cls.__name__ == "NativeDockerSandbox"


def test_backend_cached_after_first_resolve():
    cls1 = get_backend_class("native")
    cls2 = get_backend_class("native")
    assert cls1 is cls2
    assert "native" in _LOADED
