"""Centralized sandbox path configuration.

All in-sandbox paths (mem_root, shared, bash_tools, sandbox_scripts, src)
should be accessed through this module rather than hardcoded.

Call ``configure()`` once at startup with the SandboxPathsConfig from the
user's config. All getters return defaults (Docker paths) if never configured.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Defaults:
    mem_root: str = "/mem"
    shared: str = "/shared"
    bash_tools: str = "/bash_tools"
    sandbox_scripts: str = "/sandbox_scripts"
    src: str = "/src"


_defaults = _Defaults()

_mem_root: str = _defaults.mem_root
_shared: str = _defaults.shared
_bash_tools: str = _defaults.bash_tools
_sandbox_scripts: str = _defaults.sandbox_scripts
_src: str = _defaults.src


def configure(
    *,
    mem_root: str | None = None,
    shared: str | None = None,
    bash_tools: str | None = None,
    sandbox_scripts: str | None = None,
    src: str | None = None,
) -> None:
    """Set sandbox paths from config. Call once at startup."""
    global _mem_root, _shared, _bash_tools, _sandbox_scripts, _src
    if mem_root is not None:
        _mem_root = mem_root
    if shared is not None:
        _shared = shared
    if bash_tools is not None:
        _bash_tools = bash_tools
    if sandbox_scripts is not None:
        _sandbox_scripts = sandbox_scripts
    if src is not None:
        _src = src


def configure_from_config(config) -> None:
    """Configure paths from an OpenSageConfig instance."""
    sandbox_cfg = getattr(config, "sandbox", None)
    if not sandbox_cfg:
        return
    paths = getattr(sandbox_cfg, "paths", None)
    if not paths:
        return
    configure(
        mem_root=getattr(paths, "mem_root", None),
        shared=getattr(paths, "shared", None),
        bash_tools=getattr(paths, "bash_tools", None),
        sandbox_scripts=getattr(paths, "sandbox_scripts", None),
        src=getattr(paths, "src", None),
    )


def get_mem_root() -> str:
    return _mem_root


def get_shared() -> str:
    return _shared


def get_bash_tools() -> str:
    return _bash_tools


def get_sandbox_scripts() -> str:
    return _sandbox_scripts


def get_src() -> str:
    return _src
