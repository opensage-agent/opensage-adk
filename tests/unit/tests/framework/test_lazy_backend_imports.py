"""Tests for lazy sandbox backend imports in factory.py."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

# Stub heavy optional deps unavailable in this test environment.
for _mod in ("nitrobox", "opensandbox", "tree_sitter_language_pack",
             "neomodel"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from opensage.sandbox.factory import (
    _BACKENDS,
    _LOADED,
    get_backend_class,
)


def test_registry_contains_all_backends():
    expected = {"native", "k8s", "remotedocker", "opensandbox",
                "agentdocker-lite", "local"}
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
