"""Decorators for declaring sandbox dependencies in AIgiSE tools and toolsets.

This module provides a unified decorator system for declaring which sandboxes
are required by tools and toolsets. This enables static dependency analysis
before actually creating any sandboxes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import traceback
from functools import wraps
from pathlib import Path
from typing import Callable, List, Optional, Set, TypeVar, Union

from aigise.utils.project_info import SRC_PATH

logger = logging.getLogger(__name__)

from google.adk.agents.base_agent import BaseAgent
from google.adk.tools.agent_tool import AgentTool

F = TypeVar("F", bound=Callable)
logger = logging.getLogger(__name__)


def requires_sandbox(*sandbox_types: str) -> Callable[[F], F]:
    """Universal decorator for declaring sandbox dependencies.

    This decorator works for both:
    - Tool functions: Only marks the function with metadata
    - Toolset factories: Marks function AND injects metadata into returned instance

    The decorator is purely declarative - it does not create or fetch sandboxes.
    It only adds `__sandbox_requirements__` metadata for static analysis via
    `collect_sandbox_dependencies()`.

    Args:
        *sandbox_types: Variable number of sandbox type names that the tool
            or toolset depends on (e.g., "main", "gdb_mcp", "neo4j").

    Returns:
        A decorator function that adds __sandbox_requirements__ metadata.
    """

    def decorator(func: F) -> F:
        # Store as tuple for immutability
        func.__sandbox_requirements__ = tuple(sandbox_types)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            # If result is an object (like MCPToolset), inject metadata
            if result is not None and hasattr(result, "__dict__"):
                result.__sandbox_requirements__ = tuple(sandbox_types)
            return result

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            # If result is an object (like MCPToolset), inject metadata
            if result is not None and hasattr(result, "__dict__"):
                result.__sandbox_requirements__ = tuple(sandbox_types)
            return result

        # Determine if the function is async and return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            async_wrapper.__sandbox_requirements__ = tuple(sandbox_types)
            return async_wrapper
        else:
            sync_wrapper.__sandbox_requirements__ = tuple(sandbox_types)
            return sync_wrapper

    return decorator


def safe_tool_execution(func: F) -> F:
    """Decorator to wrap tool functions with error handling.

    Catches all exceptions and returns a formatted error message with backtrace.
    Works for both sync and async functions.

    Returns:
        dict with "error" key containing failure message and backtrace
    """
    from types import FunctionType

    # Preserve original function's __globals__ for type hint resolution.
    # When ADK calls typing.get_type_hints() on the wrapper, it uses the wrapper's
    # __globals__ to resolve type annotations. By using the original function's
    # __globals__, we ensure all types (like ToolContext, Dict, etc.) are accessible.
    original_globals = func.__globals__

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(
                "Tool %s raised: %s", getattr(func, "__name__", func), e, exc_info=True
            )
            error_msg = f"Failed: {type(e).__name__}: {str(e)}\n\nBacktrace:\n{traceback.format_exc()}"
            return {"error": error_msg, "success": False}

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(
                "Tool %s raised: %s", getattr(func, "__name__", func), e, exc_info=True
            )
            error_msg = f"Failed: {type(e).__name__}: {str(e)}\n\nBacktrace:\n{traceback.format_exc()}"
            return {"error": error_msg, "success": False}

    # Recreate wrapper functions with a merged globals dict:
    # - Start from the decorators module globals (so logger/traceback/etc exist at runtime)
    # - Add missing names from the original function globals (so type hints like
    #   ToolContext can be resolved by typing.get_type_hints()).
    async_globals = dict(async_wrapper.__globals__)
    for key, value in original_globals.items():
        async_globals.setdefault(key, value)
    sync_globals = dict(sync_wrapper.__globals__)
    for key, value in original_globals.items():
        sync_globals.setdefault(key, value)

    async_wrapper = FunctionType(
        async_wrapper.__code__,
        async_globals,
        async_wrapper.__name__,
        async_wrapper.__defaults__,
        async_wrapper.__closure__,
    )
    sync_wrapper = FunctionType(
        sync_wrapper.__code__,
        sync_globals,
        sync_wrapper.__name__,
        sync_wrapper.__defaults__,
        sync_wrapper.__closure__,
    )

    # Preserve metadata from @wraps
    for attr in (
        "__module__",
        "__name__",
        "__qualname__",
        "__doc__",
        "__annotations__",
    ):
        if hasattr(func, attr):
            setattr(async_wrapper, attr, getattr(func, attr))
            setattr(sync_wrapper, attr, getattr(func, attr))

    # Preserve signature behavior for ADK tool schema generation.
    async_wrapper.__wrapped__ = func
    sync_wrapper.__wrapped__ = func
    try:
        import inspect

        sig = inspect.signature(func)
        async_wrapper.__signature__ = sig
        sync_wrapper.__signature__ = sig
    except (TypeError, ValueError):
        pass

    # Return appropriate wrapper based on function type
    if asyncio.iscoroutinefunction(func):
        async_wrapper.__sandbox_requirements__ = getattr(
            func, "__sandbox_requirements__", ()
        )
        return async_wrapper
    else:
        sync_wrapper.__sandbox_requirements__ = getattr(
            func, "__sandbox_requirements__", ()
        )
        return sync_wrapper


def collect_sandbox_dependencies(agent) -> set[str]:
    """Collect all sandbox dependencies from an agent and its tools.

    This function performs static analysis on an agent's tools to determine
    which sandboxes are required. It checks for `__sandbox_requirements__`
    metadata on:
    - Direct tool functions (decorated with @requires_sandbox)
    - Toolset instances (including MCP toolsets, returned by get_toolset() functions decorated with @requires_sandbox)
    - AgentTools (agents wrapped as tools, recursively)
    - Sub-agents (recursively)

    Args:
        agent: An agent instance (LlmAgent, SequentialAgent, AigiseAgent, etc.)

    Returns:
        A set of sandbox type names required by the agent and all its sub-agents and mcp toolsets.
    """
    dependencies = set()

    # Check agent's tools
    if hasattr(agent, "tools") and agent.tools:
        for tool in agent.tools:
            # 1. Check if tool is an AgentTool (agent wrapped as tool)
            if isinstance(tool, AgentTool):
                # Recursively collect dependencies from the nested agent
                dependencies.update(collect_sandbox_dependencies(tool.agent))

            # 2. Check if tool has nested agent (for complex tools)
            elif hasattr(tool, "agent") and isinstance(tool.agent, BaseAgent):
                # Recursively collect dependencies from the nested agent
                dependencies.update(collect_sandbox_dependencies(tool.agent))

            # 3. Check if it has the metadata attribute (regular tool or toolset)
            if hasattr(tool, "__sandbox_requirements__"):
                deps = tool.__sandbox_requirements__
                if isinstance(deps, (tuple, list, set)):
                    dependencies.update(deps)
                elif isinstance(deps, str):
                    dependencies.add(deps)

    # Recursively check sub-agents
    if hasattr(agent, "sub_agents") and agent.sub_agents:
        for sub_agent in agent.sub_agents:
            dependencies.update(collect_sandbox_dependencies(sub_agent))

    # Check workflow agent steps
    if hasattr(agent, "steps") and agent.steps:
        for step in agent.steps:
            if hasattr(step, "agent"):
                dependencies.update(collect_sandbox_dependencies(step.agent))

    # Collect dependencies from dynamic bash skills (SKILL.md)
    # Get enabled_skills from agent if it's an AigiseAgent
    enabled_skills = None
    if hasattr(agent, "_enabled_skills"):
        enabled_skills = agent._enabled_skills

    skill_deps = _collect_dynamic_skill_dependencies(enabled_skills=enabled_skills)
    logger.info(
        "Collecting dynamic skill dependencies: %s",
        skill_deps,
    )
    dependencies.update(skill_deps)

    dependencies.add("main")
    from aigise.features import is_neo4j_logging_enabled

    if is_neo4j_logging_enabled():
        dependencies.add("neo4j")

    return dependencies


def _collect_dynamic_skill_dependencies(
    enabled_skills: Optional[Union[List[str], str]] = None,
) -> set[str]:
    """Scan all available bash skills for sandbox requirements defined in SKILL.md.

    This manually scans the search paths (mirroring ToolLoader defaults) and parses
    the '## Requires Sandbox' section from SKILL.md files.

    Args:
        enabled_skills: Optional filter to only collect dependencies from enabled tools.
                       - None: Collect from no tools (returns empty set)
                       - "all": Collect from all tools
                       - List[str]: Only collect from specified tools
    """

    dependencies = set()

    # Determine filter set based on enabled_skills (mirroring ToolLoader logic)
    filter_skills: Optional[Set[str]] = None
    if enabled_skills == "all":
        filter_skills = None  # No filtering, collect all
    elif enabled_skills is None:
        # None means no tools enabled, return empty set
        return set()
    elif isinstance(enabled_skills, list):
        filter_skills = set(enabled_skills)  # Filter by allowlist
    else:
        filter_skills = None  # Unknown type, collect all

    search_paths = [
        SRC_PATH / "aigise/bash_tools",
        Path.home() / ".local/plugins/aigise/tools",
    ]

    def parse_skill_md(file_path: Path) -> set[str]:
        deps = set()
        try:
            content = file_path.read_text(encoding="utf-8")
            # Regex to find "## Requires Sandbox" section and capture content until next "##" or EOF
            match = re.search(
                r"^## Requires Sandbox\s*\n(.*?)(?=\n## |\Z)",
                content,
                re.MULTILINE | re.DOTALL,
            )
            if match:
                section_content = match.group(1)
                # Parse comma-separated values, ignoring empty lines
                for line in section_content.splitlines():
                    line = line.strip()
                    if line:
                        for part in line.split(","):
                            clean_part = part.strip()
                            if clean_part:
                                deps.add(clean_part)
        except Exception as e:
            logger.warning(
                f"Failed to parse SKILL.md sandbox requirements at {file_path}: {e}"
            )
        logger.info("Collected dependencies: %s from %s", deps, file_path)
        return deps

    processed_tools = set()

    for search_path in search_paths:
        if not search_path.exists():
            continue

        # Logic mirrors ToolLoader: check root items
        for item in search_path.iterdir():
            if not item.is_dir():
                continue

            # Check if item is a tool (has SKILL.md inside)
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                tool_name = item.name
                # Apply filter if enabled_skills is specified
                if filter_skills is not None and tool_name not in filter_skills:
                    continue
                if tool_name not in processed_tools:
                    dependencies.update(parse_skill_md(skill_md))
                    processed_tools.add(tool_name)
            else:
                # Treat as category/sandbox directory, check children
                for subitem in item.iterdir():
                    if subitem.is_dir() and (subitem / "SKILL.md").exists():
                        tool_name = f"{item.name}/{subitem.name}"
                        # Apply filter if enabled_skills is specified
                        if filter_skills is not None and tool_name not in filter_skills:
                            continue
                        if tool_name not in processed_tools:
                            dependencies.update(parse_skill_md(subitem / "SKILL.md"))
                            processed_tools.add(tool_name)
    return dependencies
