from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict, List, Tuple

from google.adk.plugins.base_plugin import BasePlugin

# Unified repr for all plugins: <ClassName('plugin_name')>
BasePlugin.__repr__ = lambda self: f"<{type(self).__name__}({self.name!r})>"


class PluginKind(enum.Enum):
    ADK = "adk"
    CC_HOOK = "cc_hook"
    ALL = "all"


logger = logging.getLogger(__name__)

_ADK_PLUGIN_DIR = Path(__file__).resolve().parent / "default" / "adk_plugins"
_CLAUDE_CODE_HOOK_DIR = (
    Path(__file__).resolve().parent / "default" / "claude_code_hooks"
)

# Characters that indicate a regex pattern rather than a literal plugin name.
_REGEX_METACHARACTERS = set(".*+?[](){}|^$\\")


def __getattr__(name: str):
    """Lazy import: ``from opensage.plugins import SomePlugin`` auto-resolves.

    Raises:
      AttributeError: Raised when this operation fails."""
    from .adk_plugin_loader import load_adk_plugin_class

    for py_file in _ADK_PLUGIN_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            cls = load_adk_plugin_class(py_file.stem, py_file)
        except ValueError:
            continue
        if cls.__name__ == name:
            return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _has_regex_metacharacters(s: str) -> bool:
    """Return True if *s* contains any regex metacharacters."""
    return bool(set(s) & _REGEX_METACHARACTERS)


def _get_local_plugin_dir() -> Path:
    """Return user-local plugin directory."""
    return Path.home() / ".local" / "opensage" / "plugins"


def _discover_all_plugins(
    search_dirs: List[Tuple[Path, PluginKind]],
) -> Dict[str, Tuple[PluginKind, Path]]:
    """Scan all *search_dirs* and return ``{name: (kind, path)}`` for every plugin found."""
    discovered: Dict[str, Tuple[PluginKind, Path]] = {}
    for d, kind in search_dirs:
        if not d.is_dir():
            continue
        if kind in (PluginKind.ADK, PluginKind.ALL):
            for py_file in sorted(d.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                discovered[py_file.stem] = (PluginKind.ADK, py_file)
        if kind in (PluginKind.CC_HOOK, PluginKind.ALL):
            for json_file in sorted(d.glob("*.json")):
                if json_file.name.startswith("_"):
                    continue
                discovered[json_file.stem] = (PluginKind.CC_HOOK, json_file)
    return discovered


def load_plugins(
    enabled_plugins: Iterable[str] | None,
    agent_dir: str | Path | None = None,
    adk_plugin_params: Dict[str, Dict[str, Any]] | None = None,
    extra_plugin_dirs: Iterable[str | Path] | None = None,
) -> List[BasePlugin]:
    """Instantiate plugins in the order provided by *enabled_plugins*.

        Each entry in *enabled_plugins* is resolved as follows:

        1. **Literal plugin name** — looked up in the discovered available plugins.
           ``.json`` files are loaded as Claude Code hooks; ``.py`` files as
           ADK plugins.
        2. **Regex pattern** — if literal lookup fails and the
           entry contains regex metacharacters (e.g. ``.*_plugin``), it is
           matched via ``re.fullmatch`` against all discovered plugin names.

        Search order (later entries shadow earlier ones):

        1. Default ADK plugins (``default/adk_plugins/``)
        2. Default Claude Code hooks (``default/claude_code_hooks/``)
        3. User-local defaults: ``~/.local/opensage/plugins/``
        4. Custom directories from *extra_plugin_dirs*
        5. Agent-local ``{agent_dir}/plugins/``

        Each CC hook JSON becomes its own ``ClaudeCodeHookPlugin`` instance,
        so plugin execution order matches the ``enabled`` list exactly.

        Per-ADK-plugin constructor kwargs can be supplied via *adk_plugin_params*,
        keyed by plugin name. CC hooks do not accept constructor kwargs.

    Raises:
      ValueError: Raised when this operation fails."""
    from .adk_plugin_loader import load_adk_plugin_class
    from .claude_code_hook_loader import load_claude_code_hook_plugin

    plugins: List[BasePlugin] = []
    if not enabled_plugins:
        return []

    adk_plugin_params = adk_plugin_params or {}
    # default dirs for plugins
    search_dirs: List[Tuple[Path, PluginKind]] = [
        (_ADK_PLUGIN_DIR, PluginKind.ADK),
        (_CLAUDE_CODE_HOOK_DIR, PluginKind.CC_HOOK),
    ]
    local_plugin_dir = _get_local_plugin_dir()
    if local_plugin_dir.is_dir():
        search_dirs.append((local_plugin_dir, PluginKind.ALL))
    # custom (user) dirs for plugins - override default
    for d in extra_plugin_dirs or []:
        p = Path(d).resolve()
        if p.is_dir():
            search_dirs.append((p, PluginKind.ALL))
    # agent-local plugins - override default and custom
    if agent_dir:
        user_plugin_dir = Path(agent_dir).resolve() / "plugins"
        if user_plugin_dir.is_dir():
            search_dirs.append((user_plugin_dir, PluginKind.ALL))

    # Discover all plugins once upfront
    available_plugins = _discover_all_plugins(search_dirs)

    # Step 1: Resolve plugin names to (name, kind, path)
    resolved_plugins: List[Tuple[str, PluginKind, Path]] = []
    seen_plugins: set[str] = set()

    for each_plugin in enabled_plugins:
        # 1. Literal name lookup
        if each_plugin in available_plugins and each_plugin not in seen_plugins:
            seen_plugins.add(each_plugin)
            kind, path = available_plugins[each_plugin]
            resolved_plugins.append((each_plugin, kind, path))
            continue

        # 2. Regex fallback — auto-detected by metacharacters
        if _has_regex_metacharacters(each_plugin):
            matched_any = False
            for pname, (pkind, ppath) in sorted(available_plugins.items()):
                if re.fullmatch(each_plugin, pname):
                    matched_any = True
                    if pname in seen_plugins:
                        continue
                    seen_plugins.add(pname)
                    resolved_plugins.append((pname, pkind, ppath))
            if not matched_any:
                logger.warning(
                    'Pattern "%s" matched no discovered plugins.', each_plugin
                )
            continue

        # Neither found nor regex
        if each_plugin not in seen_plugins:
            searched = ", ".join(str(d) for d, _ in search_dirs)
            raise ValueError(
                f'Unknown plugin "{each_plugin}". Neither "{each_plugin}.py" nor '
                f'"{each_plugin}.json" found in: {searched}.'
            )

    # Step 2: Load all resolved plugins in order.
    for name, kind, path in resolved_plugins:
        if kind is PluginKind.CC_HOOK:
            plugins.append(load_claude_code_hook_plugin(str(path)))
        else:
            plugin_class = load_adk_plugin_class(name, path)
            plugins.append(plugin_class(**adk_plugin_params.get(name, {})))

    if plugins:
        order = ", ".join(f"[{i}]{p.name}" for i, p in enumerate(plugins, 1))
        logger.warning("Plugin execution order: %s", order)

    return plugins
