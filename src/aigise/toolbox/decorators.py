"""Decorators for declaring sandbox dependencies in AIgiSE tools and toolsets.

This module provides a unified decorator system for declaring which sandboxes
are required by tools and toolsets. This enables static dependency analysis
before actually creating any sandboxes.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from functools import wraps
from typing import Callable, TypeVar

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
    dependencies.add("main")
    from aigise.features import is_neo4j_logging_enabled

    if is_neo4j_logging_enabled():
        dependencies.add("neo4j")

    return dependencies
