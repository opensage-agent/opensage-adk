"""Sandbox requirement annotations and dependency collection.

This module is part of the public OpenSage toolbox API. Tool authors can use
`@requires_sandbox(...)` to declare which sandboxes a tool (or toolset factory)
depends on.
"""

from __future__ import annotations

import asyncio
import logging
import re
from functools import wraps
from pathlib import Path
from typing import Callable, List, Optional, Set, TypeVar, Union

from google.adk.agents.base_agent import BaseAgent
from google.adk.tools.agent_tool import AgentTool

from opensage.utils.project_info import SRC_PATH

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


def requires_sandbox(*sandbox_types: str) -> Callable[[F], F]:
    """Decorator for declaring sandbox dependencies.

    Works for:
    - Tool functions: Marks the function with metadata.
    - Toolset factories: Marks the factory AND injects metadata into the returned
      instance.

    The decorator is purely declarative. It does not create or fetch sandboxes.
    """

    def decorator(func: F) -> F:
        func.__sandbox_requirements__ = tuple(sandbox_types)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if result is not None and hasattr(result, "__dict__"):
                result.__sandbox_requirements__ = tuple(sandbox_types)
            return result

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            if result is not None and hasattr(result, "__dict__"):
                result.__sandbox_requirements__ = tuple(sandbox_types)
            return result

        if asyncio.iscoroutinefunction(func):
            async_wrapper.__sandbox_requirements__ = tuple(sandbox_types)
            return async_wrapper
        sync_wrapper.__sandbox_requirements__ = tuple(sandbox_types)
        return sync_wrapper

    return decorator


def collect_sandbox_dependencies(agent) -> set[str]:
    """Collect sandbox dependencies from an agent and its tools."""
    dependencies = set()

    if hasattr(agent, "tools") and agent.tools:
        for tool in agent.tools:
            if isinstance(tool, AgentTool):
                dependencies.update(collect_sandbox_dependencies(tool.agent))
            elif hasattr(tool, "agent") and isinstance(tool.agent, BaseAgent):
                dependencies.update(collect_sandbox_dependencies(tool.agent))

            if hasattr(tool, "__sandbox_requirements__"):
                deps = tool.__sandbox_requirements__
                if isinstance(deps, (tuple, list, set)):
                    dependencies.update(deps)
                elif isinstance(deps, str):
                    dependencies.add(deps)

    if hasattr(agent, "sub_agents") and agent.sub_agents:
        for sub_agent in agent.sub_agents:
            dependencies.update(collect_sandbox_dependencies(sub_agent))

    if hasattr(agent, "steps") and agent.steps:
        for step in agent.steps:
            if hasattr(step, "agent"):
                dependencies.update(collect_sandbox_dependencies(step.agent))

    enabled_skills = getattr(agent, "_enabled_skills", None)
    skill_deps = _collect_dynamic_skill_dependencies(enabled_skills=enabled_skills)
    logger.info("Collecting dynamic skill dependencies: %s", skill_deps)
    dependencies.update(skill_deps)

    dependencies.add("main")
    from opensage.features import is_neo4j_logging_enabled

    if is_neo4j_logging_enabled():
        dependencies.add("neo4j")

    return dependencies


def _collect_dynamic_skill_dependencies(
    enabled_skills: Optional[Union[List[str], str]] = None,
) -> set[str]:
    """Scan available bash skills for sandbox requirements defined in SKILL.md."""
    dependencies = set()

    filter_skills: Optional[Set[str]] = None
    if enabled_skills == "all" or enabled_skills == ["all"]:
        filter_skills = None
    elif enabled_skills is None:
        return set()
    elif isinstance(enabled_skills, list):
        filter_skills = set(enabled_skills)
    else:
        filter_skills = None

    search_paths = [
        SRC_PATH / "bash_tools",
        Path.home() / ".local/opensage/bash_tools",
    ]

    def parse_skill_md(file_path: Path) -> set[str]:
        deps = set()
        try:
            content = file_path.read_text(encoding="utf-8")
            match = re.search(
                r"^## Requires Sandbox\s*\n(.*?)(?=\n## |\Z)",
                content,
                re.MULTILINE | re.DOTALL,
            )
            if match:
                section_content = match.group(1)
                for line in section_content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    for part in line.split(","):
                        clean_part = part.strip()
                        if clean_part:
                            deps.add(clean_part)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to parse SKILL.md sandbox requirements at %s: %s",
                file_path,
                exc,
            )
        logger.info("Collected dependencies: %s from %s", deps, file_path)
        return deps

    processed_tools = set()
    for search_path in search_paths:
        if not search_path.exists():
            continue

        for item in search_path.iterdir():
            if not item.is_dir():
                continue
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                tool_name = item.name
                if filter_skills is not None and tool_name not in filter_skills:
                    continue
                if tool_name not in processed_tools:
                    dependencies.update(parse_skill_md(skill_md))
                    processed_tools.add(tool_name)
            else:
                for subitem in item.iterdir():
                    if subitem.is_dir() and (subitem / "SKILL.md").exists():
                        tool_name = f"{item.name}/{subitem.name}"
                        if filter_skills is not None and tool_name not in filter_skills:
                            continue
                        if tool_name not in processed_tools:
                            dependencies.update(parse_skill_md(subitem / "SKILL.md"))
                            processed_tools.add(tool_name)

    return dependencies
