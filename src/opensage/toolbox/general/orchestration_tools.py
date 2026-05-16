"""LLM-facing tools for agent orchestration: spawn, continue, send, list, etc.

All tools address instances by ``session_id: str`` (UUID). LLMs receive a
session_id when calling ``call_subagent`` / ``create_subagent`` and use it for
subsequent ``continue_agent_instance`` / ``send_message`` / ``wait_for_subagent``.

Internal helpers also provide ``rebuild_agent_from_definition`` used by
AgentManager.ensure_loaded when reconstructing a dynamic agent from disk.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.tool_context import ToolContext

from opensage.agents.opensage_agent import OpenSageAgent, OpenSageMCPToolset
from opensage.orchestration.persistence import save_agent_definition
from opensage.toolbox.general.agent_tools import complain
from opensage.toolbox.general.bash_tools_interface import (
    get_background_task_output,
    list_background_tasks,
    run_terminal_command,
    wait_for_background,
)
from opensage.utils.agent_utils import (
    extract_tools_from_agent,
    sanitize_agent_name,
)

if TYPE_CHECKING:
    from opensage.orchestration.manager import AgentManager
    from opensage.session.opensage_session import OpenSageSession

logger = logging.getLogger("opensage." + __name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clone_mcp_toolset(toolset: OpenSageMCPToolset) -> OpenSageMCPToolset:
    """Create a fresh MCP toolset with its own session/connection.

    Each clone gets an independent MCPSessionManager, so subagents using
    stateful MCP servers (GDB, IDA, Ghidra) don't interfere with each other.
    """
    return OpenSageMCPToolset(
        name=toolset.name,
        connection_params=toolset._connection_params,
        tool_name_prefix=toolset.name,
    )


def _get_opensage_session(tool_context: ToolContext) -> "OpenSageSession":
    inv = getattr(tool_context, "_invocation_context", tool_context)
    session_service = inv.session_service
    opensage_session = getattr(session_service, "opensage_session", None)
    if opensage_session is None:
        raise RuntimeError(
            "session_service has no opensage_session back-reference; "
            "OpenSageSession may not have been initialized."
        )
    return opensage_session


def _get_manager(tool_context: ToolContext) -> "AgentManager":
    return _get_opensage_session(tool_context).agent_manager


def _get_caller_sid(tool_context: ToolContext) -> str:
    return tool_context._invocation_context.session.id


# ---------------------------------------------------------------------------
# Tool: call_subagent (spawn new instance from a named agent, then invoke)
# ---------------------------------------------------------------------------


async def call_subagent(
    agent_name: str,
    request: str,
    tool_context: ToolContext,
    mode: str = "sync",
    use_parent_history: bool = False,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Spawn a fresh instance of ``agent_name`` and invoke it with ``request``.

    Args:
        agent_name: Name of a registered agent (static subagent or dynamic).
        request: Initial user message to kick off the invocation.
        mode: ``"sync"`` blocks until the invocation finishes and returns the
            final response text. ``"async"`` returns immediately; when the
            subagent finishes, its result is posted to the caller's inbox and
            the caller is automatically woken up if it has ended its turn.
        use_parent_history: If True, the sub-agent inherits the parent's full
            conversation history (all prior tool calls, their results, user
            messages, and agent responses), so it can reason about context
            the parent has already explored without re-description.
            If False (default), the sub-agent starts with a blank context
            and only sees the ``request`` message — suitable for
            self-contained tasks that don't need the parent's exploration
            history, saving context window budget.
        model_name: Override the subagent template's model for THIS spawned
            instance only. Must be a name returned by ``get_available_models``
            (i.e. present in the session's LlmRegistry). If omitted, the
            instance uses the agent template's declared model.

    Returns:
        ``{"success": True, "session_id": ..., "result"|"status": ...}``
    """
    try:
        manager = _get_manager(tool_context)
        caller_sid = _get_caller_sid(tool_context)

        if model_name is not None:
            opensage_session = _get_opensage_session(tool_context)
            if model_name not in opensage_session.llms:
                return {
                    "success": False,
                    "error": (
                        f"model_name {model_name!r} is not registered. "
                        f"Available: {opensage_session.llms.list_names()}"
                    ),
                }

        new_sid = await manager.spawn(
            agent_name,
            parent_session_id=caller_sid,
            use_parent_history=use_parent_history,
            model_override=model_name,
        )
        instance = manager.ensure_loaded(new_sid)
        return await manager._invoke_instance(instance, request, mode, caller_sid)
    except KeyError as e:
        return {"success": False, "error": f"unknown_agent: {e}"}
    except Exception as e:
        logger.exception("call_subagent failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: continue_agent_instance (invoke an existing instance)
# ---------------------------------------------------------------------------


async def continue_agent_instance(
    session_id: str,
    request: str,
    tool_context: ToolContext,
    mode: str = "sync",
) -> Dict[str, Any]:
    """Continue an existing sub-agent instance by sending it a follow-up message.

    Use this instead of ``call_subagent`` when you already have a running or
    previously-spawned instance and want to give it additional instructions,
    ask follow-up questions, or tell it to keep working. The sub-agent retains
    its full conversation history and state from prior invocations.

    Args:
        session_id: The session_id returned by a prior ``call_subagent`` or
            ``create_subagent`` call.
        request: The follow-up message to send to the sub-agent.
        mode: ``"sync"`` blocks until done. ``"async"`` returns immediately;
            the result is posted to the caller's inbox and the caller is
            automatically woken up if it has ended its turn.

    If the instance is currently RUNNING, returns ``{"success": False,
    "error": "busy", ...}`` — the caller decides to retry / spawn new / give up.
    """
    try:
        manager = _get_manager(tool_context)
        caller_sid = _get_caller_sid(tool_context)
        instance = manager.ensure_loaded(session_id)
        return await manager._invoke_instance(instance, request, mode, caller_sid)
    except KeyError as e:
        return {"success": False, "error": f"unknown_session: {e}"}
    except Exception as e:
        logger.exception("continue_agent_instance failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: send_message (peer messaging, fire-and-forget)
# ---------------------------------------------------------------------------


async def send_message(
    to_session_id: str,
    content: str,
    tool_context: ToolContext,
    kind: str = "text",
) -> Dict[str, Any]:
    """Push a message to another instance's inbox.

    ``to_session_id="*"`` broadcasts to all known instances (except the caller).
    Fire-and-forget: does not wait for a response. The recipient picks up the
    message at its next tool boundary (if RUNNING) or on its next wake-up (if
    SLEEPING).

    """
    try:
        manager = _get_manager(tool_context)
        from_sid = _get_caller_sid(tool_context)
        await manager.send_message(
            from_sid=from_sid, to_sid=to_session_id, content=content, kind=kind
        )
        return {"success": True, "to_session_id": to_session_id}
    except KeyError as e:
        return {"success": False, "error": f"unknown_session: {e}"}
    except Exception as e:
        logger.exception("send_message failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: wait_for_subagent
# ---------------------------------------------------------------------------


async def wait_for_subagent(
    session_id: str,
    tool_context: ToolContext,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Block until the target instance's current invocation completes.

    Returns ``{"success": True, "state": "sleeping"}`` on completion, or
    ``{"success": False, "error": "timeout"}`` on timeout.
    """
    try:
        manager = _get_manager(tool_context)
        await manager.wait_for(session_id, timeout=timeout)
        inst = manager.get_instance(session_id)
        state = inst.state.value if inst is not None else "unknown"
        return {"success": True, "session_id": session_id, "state": state}
    except asyncio.TimeoutError:
        return {"success": False, "error": "timeout", "session_id": session_id}
    except Exception as e:
        logger.exception("wait_for_subagent failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: list_subagents (list registered agents and loaded instances)
# ---------------------------------------------------------------------------


async def list_subagents(tool_context: ToolContext) -> Dict[str, Any]:
    """List all registered agent definitions and all known instances.

    Returns each instance's session_id, agent_name, and current state.
    Terminated instances are excluded.
    """
    try:
        manager = _get_manager(tool_context)
        agents = [
            {
                "name": name,
                "description": getattr(agent, "description", None),
            }
            for name, agent in manager.list_agents().items()
        ]
        from opensage.orchestration.types import AgentInstanceState

        _hidden = (AgentInstanceState.TERMINATING, AgentInstanceState.TERMINATED)
        instances = [
            {
                "session_id": inst.session_id,
                "agent_name": inst.agent_name,
                "state": inst.state.value,
            }
            for inst in manager.list_instances()
            if inst.state not in _hidden
        ]
        return {"success": True, "agents": agents, "instances": instances}
    except Exception as e:
        logger.exception("list_subagents failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: terminate_subagent_forever
# ---------------------------------------------------------------------------


async def terminate_subagent_forever(
    session_id: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Permanently terminate a subagent.

    If the subagent is idle it is terminated immediately. If it is still
    running, it is marked for termination and will transition to terminated
    as soon as its current invocation finishes.

    Once terminated the subagent is invisible to peers (hidden from
    list_subagents, cannot receive send_message) but remains visible in
    the UI topology.

    Args:
        session_id: The session_id of the subagent to terminate.
    """
    try:
        manager = _get_manager(tool_context)
        new_state = await manager.terminate_instance(session_id)
        return {"success": True, "session_id": session_id, "state": new_state}
    except KeyError as e:
        return {"success": False, "error": f"not_found: {e}"}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("terminate_subagent_forever failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: get_available_models (list LlmRegistry entries)
# ---------------------------------------------------------------------------


async def get_available_models(tool_context: ToolContext) -> Dict[str, Any]:
    """List model names that subagents are allowed to use.

    The list comes from the OpenSageSession's ``LlmRegistry``, which is populated
    from ``config.model.available_models`` (TOML) or
    ``config.model.models_python_file`` (Python file). If the user did not
    configure a model section, the registry is automatically seeded with the
    root agent's model when the agent tree is registered, so this tool always
    returns at least one usable model.

    Returns:
        ``{"success": True, "models": [list of model names]}``
    """
    try:
        opensage_session = _get_opensage_session(tool_context)
        return {"success": True, "models": opensage_session.llms.list_names()}
    except Exception as e:
        logger.exception("get_available_models failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: create_subagent (register a new dynamic agent)
# ---------------------------------------------------------------------------


_BASELINE_TOOLS_BY_NAME: Dict[str, Any] = {}


def _get_baseline_tools() -> Dict[str, Any]:
    if not _BASELINE_TOOLS_BY_NAME:
        _BASELINE_TOOLS_BY_NAME.update(
            {
                "run_terminal_command": run_terminal_command,
                "list_background_tasks": list_background_tasks,
                "get_background_task_output": get_background_task_output,
                "wait_for_background": wait_for_background,
                "complain": complain,
                "create_subagent": create_subagent,
                "call_subagent": call_subagent,
                "continue_agent_instance": continue_agent_instance,
                "send_message": send_message,
                "wait_for_subagent": wait_for_subagent,
                "list_subagents": list_subagents,
                "get_available_models": get_available_models,
            }
        )
    return _BASELINE_TOOLS_BY_NAME


async def create_subagent(
    agent_name: str,
    instruction: str,
    model_name: str,
    tool_context: ToolContext,
    tools_list: Optional[List[str]] = None,
    enabled_skills: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build and register a new OpenSageAgent in the current environment.

    Args:
        agent_name: Custom name for the agent. Will be canonical-sanitized.
        instruction: System prompt for the agent.
        model_name: Required model identifier — must be a name returned by
            ``get_available_models``.
        tools_list: List of Python tool names to assign. Defaults to ALL
            tools available to the caller. Only specify this if you want to
            intentionally restrict the sub-agent's toolset. Baseline tools
            (run_terminal_command, orchestration tools, etc.) are always
            injected automatically.
        enabled_skills: Bash-tools selection (None / ["all"] / list of paths).
        description: Optional description.

    The created agent is added to AgentManager's agent registry (same namespace
    as statically declared ``subagents=[...]``) and its definition is persisted
    to disk for cross-restart recovery.

    To actually run it, follow this with ``call_subagent(agent_name, request)``.
    """
    try:
        manager = _get_manager(tool_context)
        opensage_session = _get_opensage_session(tool_context)

        if model_name not in opensage_session.llms:
            return {
                "success": False,
                "error": (
                    f"model_name {model_name!r} is not registered. "
                    f"Available: {opensage_session.llms.list_names()}"
                ),
            }

        requested_name = agent_name
        agent_name = sanitize_agent_name(requested_name)
        renamed = agent_name != requested_name

        current_agent = tool_context._invocation_context.agent
        available_tools = extract_tools_from_agent(current_agent)

        if tools_list is None:
            tools_list = [n for n in available_tools if n not in _get_baseline_tools()]

        # Build prefix -> toolset mapping for MCP-style prefixed tool names.
        prefix_to_toolset_name: Dict[str, str] = {}
        for tname, tobj in available_tools.items():
            if isinstance(tobj, BaseToolset):
                prefix = getattr(tobj, "tool_name_prefix", None)
                if isinstance(prefix, str) and prefix.strip():
                    prefix_to_toolset_name[prefix] = tname

        tools_to_add: list[Any] = []
        added_ids: set[int] = set()

        # Always inject baseline tools
        baseline = _get_baseline_tools()
        for t in baseline.values():
            if id(t) not in added_ids:
                tools_to_add.append(t)
                added_ids.add(id(t))

        tool_names_final: list[str] = list(baseline.keys())
        injected_toolsets: set[str] = set()
        invalid: list[str] = []

        for requested in tools_list:
            if requested in baseline:
                continue
            if requested in tool_names_final:
                continue
            if requested in available_tools:
                obj = available_tools[requested]
                if isinstance(obj, OpenSageMCPToolset):
                    obj = _clone_mcp_toolset(obj)
                    injected_toolsets.add(requested)
                elif isinstance(obj, BaseToolset):
                    injected_toolsets.add(requested)
                tools_to_add.append(obj)
                tool_names_final.append(requested)
                continue
            # Prefix match for MCP toolsets
            matched = None
            for prefix, tsname in prefix_to_toolset_name.items():
                if requested.startswith(f"{prefix}_"):
                    matched = tsname
                    break
            if matched is not None and matched not in injected_toolsets:
                obj = available_tools[matched]
                if isinstance(obj, OpenSageMCPToolset):
                    obj = _clone_mcp_toolset(obj)
                tools_to_add.append(obj)
                injected_toolsets.add(matched)
                if matched not in tool_names_final:
                    tool_names_final.append(matched)
                continue
            invalid.append(requested)

        if invalid:
            return {
                "success": False,
                "error": (
                    f"Invalid tool names: {invalid}. "
                    f"Available: {list(available_tools.keys())}"
                ),
            }

        # Resolve model from the session's LlmRegistry. Validation above already
        # rejected unknown names, so this lookup must succeed.
        resolved_model = opensage_session.llms.get(model_name)

        # Clamp enabled_skills to the caller's (root's) enabled_skills so
        # subagents never list skills that aren't present in the sandbox.
        caller_skills = getattr(current_agent, "_enabled_skills", None)
        if enabled_skills is not None and caller_skills is not None:
            if isinstance(caller_skills, list) and caller_skills != ["all"]:
                caller_set = set(caller_skills)
                if enabled_skills == ["all"] or enabled_skills == "all":
                    enabled_skills = list(caller_set)
                elif isinstance(enabled_skills, list):
                    enabled_skills = [s for s in enabled_skills if s in caller_set]

        # Skills guardrail in instruction
        enabled_repr = "None" if enabled_skills is None else repr(enabled_skills)
        full_instruction = instruction + (
            "\n\n[Tooling policy]\n"
            f"Bash tools availability is controlled by enabled_skills={enabled_repr}. "
            "Use only tools available under this selection; if something is missing, "
            "report the limitation and ask the caller to recreate with updated settings.\n"
        )

        agent = OpenSageAgent(
            name=agent_name,
            instruction=full_instruction,
            model=resolved_model,
            tools=tools_to_add,
            enabled_skills=enabled_skills,
            description=description or f"Dynamically created agent {agent_name!r}.",
        )

        manager.register_agent(agent_name, agent)

        # Persist the definition so we can rebuild on resume.
        save_agent_definition(
            opensage_session.opensage_session_id,
            agent_name,
            {
                "name": agent_name,
                "instruction": full_instruction,
                "model": model_name,  # store the string, not the BaseLlm
                "tool_names": tool_names_final,
                "enabled_skills": enabled_skills,
                "description": agent.description,
            },
        )

        result = {
            "success": True,
            "agent_name": agent_name,
            "tools": tool_names_final,
            "description": agent.description,
        }
        if renamed:
            result["requested_agent_name"] = requested_name
            result["name_was_sanitized"] = True
            result["notice"] = (
                f"NOTE: the requested agent name {requested_name!r} contained "
                f"characters that are not valid in an agent name, so it was "
                f"sanitized to {agent_name!r}. Use {agent_name!r} in all "
                f"subsequent tool calls (call_subagent, continue_agent_instance, "
                f"send_message)."
            )
        return result
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("create_subagent failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Rebuild helper (used by AgentManager.ensure_loaded for dynamic agents)
# ---------------------------------------------------------------------------


async def rebuild_agent_from_definition(
    definition: Dict[str, Any],
    opensage_session: "OpenSageSession",
) -> OpenSageAgent:
    """Reconstruct an OpenSageAgent from a persisted definition.json.

    Resolves non-baseline tool names against the manager's startup tool
    snapshot (built in ``register_agent_tree`` from the live agent tree).
    Names that aren't in baseline or snapshot are logged and dropped.
    """
    agent_name = definition["name"]
    instruction = definition["instruction"]
    model_name = definition.get("model")
    tool_names = definition.get("tool_names") or []
    enabled_skills = definition.get("enabled_skills")
    description = definition.get("description")

    if not model_name:
        raise ValueError(
            f"Cannot rebuild agent {agent_name!r}: definition.json has no 'model' field"
        )
    if model_name not in opensage_session.llms:
        raise ValueError(
            f"Cannot rebuild agent {agent_name!r}: model {model_name!r} is not "
            f"in the LlmRegistry. Known: {opensage_session.llms.list_names()}"
        )
    resolved_model = opensage_session.llms.get(model_name)

    snapshot = getattr(opensage_session.agent_manager, "_tool_snapshot", {}) or {}

    # Baseline tools are unconditionally added. Non-baseline names are looked
    # up in the startup tool snapshot (built by AgentManager.register_agent_tree).
    # Names absent from both are logged and dropped.
    baseline = _get_baseline_tools()
    tools_to_add: list[Any] = list(baseline.values())
    dropped: list[str] = []
    for tname in tool_names:
        if tname in baseline:
            continue
        snap_obj = snapshot.get(tname)
        if snap_obj is not None:
            if isinstance(snap_obj, OpenSageMCPToolset):
                snap_obj = _clone_mcp_toolset(snap_obj)
            tools_to_add.append(snap_obj)
        else:
            dropped.append(tname)
    if dropped:
        logger.warning(
            "rebuild_agent_from_definition: dropping tools %s for agent %r "
            "(not in startup tool snapshot — likely the live agent tree no "
            "longer registers them)",
            dropped,
            agent_name,
        )

    return OpenSageAgent(
        name=agent_name,
        instruction=instruction,
        model=resolved_model,
        tools=tools_to_add,
        enabled_skills=enabled_skills,
        description=description or f"Restored agent {agent_name!r}.",
    )
