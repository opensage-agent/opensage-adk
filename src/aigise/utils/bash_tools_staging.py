"""Helpers for staging bash tools into sandboxes.

This module supports:
- Collecting `enabled_skills` recursively from an agent tree (root agent, tools
  including AgentTool-like wrappers, sub_agents, workflow steps).
- Converting enabled_skills into a set of top-level bash_tools roots.
- Building a temporary staging directory that merges built-in bash_tools and
  plugin bash tools, with strict conflict detection (refuse to overwrite).
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterator, Optional, Set, Union

from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

# Built-in and plugin bash tools roots (host paths).
BUILTIN_BASH_TOOLS_ROOT = Path(PROJECT_PATH) / "src" / "opensage" / "bash_tools"
PLUGIN_BASH_TOOLS_ROOT = Path.home() / ".local" / "opensage" / "bash_tools"


EnabledSkills = Optional[Union[list[str], str]]


def _normalize_enabled_skills_to_top_roots(
    enabled_skills: EnabledSkills,
) -> Optional[Set[str]]:
    """Normalize enabled_skills into a set of top-level roots.

    Returns:
        - None: means "copy all" (e.g. enabled_skills == "all" or unknown type).
        - set(): means "copy none" (enabled_skills is None or empty list).
        - set of top-level directory names under bash_tools.
    """
    if enabled_skills is None:
        return set()
    if enabled_skills == "all" or enabled_skills == ["all"]:
        return None
    if isinstance(enabled_skills, list):
        roots: Set[str] = set()
        for entry in enabled_skills:
            if not isinstance(entry, str):
                return None
            entry = entry.strip()
            if not entry or entry.startswith("/"):
                # Invalid entry for our purposes; safest fallback is copy-all.
                return None
            # Disallow traversal.
            normalized = f"/{entry}/"
            if entry in (".", "..") or "/../" in normalized or "/./" in normalized:
                return None
            roots.add(entry.split("/", 1)[0])
        return roots
    # Unknown type -> copy all to avoid surprising breakage.
    return None


def collect_enabled_skills_values(root_agent: Any) -> list[EnabledSkills]:
    """Collect enabled_skills values from an agent graph (best-effort).

    This is intentionally duck-typed to support both ADK agents and lightweight
    stubs. We avoid relying on inheritance checks and instead traverse common
    structural attributes.
    """
    values: list[EnabledSkills] = []
    visited: set[int] = set()

    def _visit(agent: Any) -> None:
        if agent is None:
            return
        obj_id = id(agent)
        if obj_id in visited:
            return
        visited.add(obj_id)

        if hasattr(agent, "_enabled_skills"):
            values.append(getattr(agent, "_enabled_skills"))

        # Tools: may include AgentTool(agent=...), or other wrappers exposing .agent.
        tools = getattr(agent, "tools", None)
        if tools:
            for tool in tools:
                nested = getattr(tool, "agent", None)
                if nested is not None:
                    _visit(nested)

        # Sub-agents (ADK style)
        subs = getattr(agent, "sub_agents", None)
        if subs:
            for sub_agent in subs:
                _visit(sub_agent)

        # Workflow steps (some agents store agents under step.agent)
        steps = getattr(agent, "steps", None)
        if steps:
            for step in steps:
                nested = getattr(step, "agent", None)
                if nested is not None:
                    _visit(nested)

    _visit(root_agent)
    return values


def compute_bash_tools_top_roots(root_agent: Any) -> Optional[Set[str]]:
    """Compute the top-level bash_tools roots needed for an agent tree.

    Returns:
        - None: copy all tools (any node has enabled_skills == "all" or unknown).
        - set(): copy none (all enabled_skills are None/empty).
        - set[str]: top-level roots to copy (e.g. {"fuzz", "retrieval"}).
    """
    merged: Set[str] = set()
    for enabled_skills in collect_enabled_skills_values(root_agent):
        roots = _normalize_enabled_skills_to_top_roots(enabled_skills)
        if roots is None:
            return None
        merged.update(roots)
    return merged


def _list_top_level_dirs(root: Path) -> Set[str]:
    if not root.exists() or not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir()}


def _copy_roots_into_staging(
    *,
    source_root: Path,
    target_root: Path,
    roots_to_copy: Optional[Set[str]],
    source_label: str,
) -> None:
    """Copy selected top-level roots from source_root into target_root.

    Raises:
        RuntimeError on any conflict (target already exists).
    """
    if not source_root.exists():
        return

    selected = (
        _list_top_level_dirs(source_root) if roots_to_copy is None else roots_to_copy
    )

    for root_name in sorted(selected):
        if not root_name:
            continue
        src = (source_root / root_name).resolve()
        if not src.exists() or not src.is_dir():
            continue
        dst = target_root / root_name
        if dst.exists():
            raise RuntimeError(
                "Bash tools conflict detected while staging: '%s' already exists in "
                "staging, cannot copy from %s (%s). Refusing to overwrite."
                % (root_name, source_label, src)
            )
        shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=False)


@contextlib.contextmanager
def build_bash_tools_staging_dir(
    *,
    roots_to_copy: Optional[Set[str]],
    builtin_root: Path = BUILTIN_BASH_TOOLS_ROOT,
    plugin_root: Path = PLUGIN_BASH_TOOLS_ROOT,
) -> Iterator[Path]:
    """Build a temporary staging directory for bash tools with conflict detection.

    The staging directory is constructed as:
      staging/
        <top-level-root>/...

    It merges:
      - built-in tools from builtin_root
      - plugin tools from plugin_root

    Any path conflict causes an exception and refuses to overwrite.
    """
    with tempfile.TemporaryDirectory(prefix="opensage-bash-tools-staging-") as tmp:
        staging = Path(tmp)
        staging.mkdir(parents=True, exist_ok=True)

        # Copy built-in first, then plugins, so conflicts are deterministic.
        _copy_roots_into_staging(
            source_root=builtin_root,
            target_root=staging,
            roots_to_copy=roots_to_copy,
            source_label="built-in",
        )
        _copy_roots_into_staging(
            source_root=plugin_root,
            target_root=staging,
            roots_to_copy=roots_to_copy,
            source_label="plugin",
        )

        # Helpful debug log.
        try:
            staged = sorted(p.name for p in staging.iterdir() if p.is_dir())
            logger.info("Prepared bash tools staging dir: roots=%s", staged)
        except Exception:
            pass

        yield staging
