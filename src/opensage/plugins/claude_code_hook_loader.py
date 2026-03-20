"""Claude Code hook loader — bridges CC hook JSON declarations into ADK plugins.

Claude Code hooks are declarative JSON rules that define tool/session-level
callbacks.  This module parses them and bridges into ADK's plugin system via
:class:`ClaudeCodeHookPlugin`.

Semantic Mapping (CC → ADK)
===========================

Uses a cross-callback injection pattern (inspired by ``ImageInjectionPlugin``
in ``parts_from_tool.py``) to achieve exact simulation where possible:
collect context in one callback, inject transiently in
``before_model_callback``, persist in ``on_event_callback``.

.. list-table::
   :header-rows: 1

   * - CC Event
     - ADK Callbacks
     - Bridge Status
   * - ``PreToolUse``
     - ``before_tool_callback`` + ``before_model_callback`` + ``on_event_callback``
     - **Full** — command runs before tool (correct timing); prompt/command
       output is injected into the *next* model call (transient) and persisted
       to session history (future calls).  CC ``additionalContext`` visibility
       is exactly simulated.
   * - ``PostToolUse``
     - ``after_tool_callback``
     - **Full** — prompt text is injected into the tool result dict; command
       output is injected the same way.  CC ``additionalContext`` ≈ ADK result
       mutation.
   * - ``PostToolUseFailure``
     - *(not bridged)*
     - **Not simulated** — ADK's ``on_tool_error_callback`` swallows the
       exception (model sees success, not error).  CC preserves error
       propagation + adds context alongside.  Cannot exactly simulate.
   * - ``UserPromptSubmit``
     - ``on_user_message_callback``
     - **Partial** — prompt text is appended to ``user_message.parts``.
       CC can also *block* the prompt (``decision: "block"``); ADK cannot.
       Command actions are not supported (no sandbox context).
   * - ``SessionStart``
     - ``before_model_callback`` + ``on_event_callback``
     - **Full for prompt** — prompt is injected into the first model call
       (transient) and persisted to session history (future calls).
       Not simulated: ``command`` (no sandbox context at session start),
       ``CLAUDE_ENV_FILE`` (ADK has no concept of env file injection).

CC events with **no ADK equivalent** (not bridgeable at all):

- ``PermissionRequest``, ``Notification``, ``Stop``, ``SubagentStart``,
  ``SubagentStop``, ``TeammateIdle``, ``TaskCompleted``, ``ConfigChange``,
  ``WorktreeCreate``, ``WorktreeRemove``, ``PreCompact``, ``SessionEnd``

Aliases (Gemini CLI):

- ``BeforeTool`` → ``PreToolUse``
- ``AfterTool``  → ``PostToolUse``
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Event
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported events — only real Claude Code hook events that we can bridge
# ---------------------------------------------------------------------------

SUPPORTED_EVENTS = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
        "SessionStart",
    }
)

# Known CC events that cannot be exactly simulated in ADK
_UNBRIDGEABLE_EVENTS: Dict[str, str] = {
    "PostToolUseFailure": (
        "ADK's on_tool_error_callback swallows the exception (model sees "
        "success, not error). CC preserves error propagation + adds context "
        "alongside. Cannot exactly simulate."
    ),
}

# Gemini CLI uses different names for the same semantics
_EVENT_ALIASES: Dict[str, str] = {
    "BeforeTool": "PreToolUse",
    "AfterTool": "PostToolUse",
}

# CC-only hook action fields that have no ADK equivalent.
# Detected at parse time to warn users migrating from Claude Code.
_CC_ONLY_ACTION_FIELDS: Dict[str, str] = {
    "decision": (
        'CC "decision" (deny/block) cannot be bridged — ADK cannot prevent '
        "tool execution or reject user prompts."
    ),
    "permissionDecision": (
        'CC "permissionDecision" (deny) cannot be bridged — ADK cannot '
        "prevent tool execution."
    ),
    "updatedInput": (
        'CC "updatedInput" cannot be bridged — ADK cannot modify tool '
        "arguments from a hook."
    ),
}

_MAX_COMMAND_OUTPUT_LENGTH = 5000
_COMMAND_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PluginAction:
    """A single action: inject prompt text or run a sandbox command."""

    type: str  # "prompt" | "command"
    prompt: Optional[str] = None
    command: Optional[str] = None
    transient: bool = False


@dataclass
class PluginRule:
    """A matcher + list of actions (one entry in a JSON event array)."""

    matcher: str
    actions: List[PluginAction] = field(default_factory=list)


# Event name → list of rules.  Replaces the old PluginConfig dataclass.
PluginConfig = Dict[str, List[PluginRule]]


# ---------------------------------------------------------------------------
# Parsing & matching
# ---------------------------------------------------------------------------


def _name_matches(matcher: str, tool_name: str) -> bool:
    """Case-insensitive name match, supporting pipe-separated alternatives."""
    tool_lower = tool_name.lower()
    return any(part.strip().lower() == tool_lower for part in matcher.split("|"))


def _tool_matches(
    matcher: str,
    tool_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if *tool_name* matches *matcher* (Claude Code matching rules).

    - ``"*"`` or ``""`` — matches everything
    - ``"bash"`` — case-insensitive exact match
    - ``"bash|read_file"`` — pipe-separated alternatives
    - ``"bash(npm test*)"`` — tool name + argument glob pattern
    """
    if not matcher or matcher == "*":
        return True

    # Argument pattern: ToolName(argPattern)
    arg_match = re.match(r"^([^(]+)\((.+)\)$", matcher)
    if arg_match:
        name_part = arg_match.group(1).strip()
        arg_pattern = arg_match.group(2).strip()
        if not _name_matches(name_part, tool_name):
            return False
        if tool_args:
            for value in tool_args.values():
                if isinstance(value, str) and fnmatch.fnmatch(value, arg_pattern):
                    return True
        return False

    return _name_matches(matcher, tool_name)


def _parse_json_sources(
    sources: List[str],
    base_dir: Path,
) -> PluginConfig:
    """Load and parse JSON files into a :data:`PluginConfig` dict."""
    config: PluginConfig = {ev: [] for ev in SUPPORTED_EVENTS}

    for source in sources:
        path = Path(source) if Path(source).is_absolute() else base_dir / source
        if not path.exists():
            logger.warning("Plugin source not found, skipping: %s", path)
            continue

        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            logger.exception("Failed to load plugin file: %s", path)
            continue

        for event_name, rules_data in raw.items():
            # Resolve Gemini CLI aliases (BeforeTool → PreToolUse, etc.)
            event_name = _EVENT_ALIASES.get(event_name, event_name)

            # Known unbridgeable events → warn and skip
            if event_name in _UNBRIDGEABLE_EVENTS:
                logger.warning(
                    "Skipping unbridgeable event %r from %s: %s",
                    event_name,
                    path,
                    _UNBRIDGEABLE_EVENTS[event_name],
                )
                continue

            if event_name not in SUPPORTED_EVENTS:
                logger.debug("Ignoring unsupported event %r from %s", event_name, path)
                continue
            if not isinstance(rules_data, list):
                logger.warning(
                    "Expected list for event %r in %s — skipping", event_name, path
                )
                continue

            config[event_name].extend(_parse_rules(rules_data, str(path)))

        logger.info(
            "Loaded hook file %s (PreToolUse: %d, PostToolUse: %d)",
            path,
            len(config["PreToolUse"]),
            len(config["PostToolUse"]),
        )

    return config


def _parse_rules(rules_data: list, source_label: str) -> List[PluginRule]:
    """Parse raw rule dicts into :class:`PluginRule` objects."""
    parsed: List[PluginRule] = []

    for i, rule_data in enumerate(rules_data):
        if not isinstance(rule_data, dict):
            logger.warning("Rule #%d in %s is not a dict — skipping", i, source_label)
            continue

        matcher = rule_data.get("matcher", "*")
        hooks_data = rule_data.get("hooks") or rule_data.get("actions") or []

        # Shorthand: { "matcher": "bash", "type": "prompt", "prompt": "..." }
        if "type" in rule_data and not hooks_data:
            hooks_data = [rule_data]

        actions: List[PluginAction] = []
        for h in hooks_data:
            # Warn on CC-only fields that cannot be bridged to ADK
            for cc_field, cc_msg in _CC_ONLY_ACTION_FIELDS.items():
                if cc_field in h:
                    logger.warning(
                        "[ClaudeCodeHook] %r in %s rule #%d: %s",
                        cc_field,
                        source_label,
                        i,
                        cc_msg,
                    )

            action_type = h.get("type")
            if action_type not in ("prompt", "command"):
                logger.warning(
                    "Unknown action type %r in %s — skipping", action_type, source_label
                )
                continue
            actions.append(
                PluginAction(
                    type=action_type,
                    prompt=h.get("prompt"),
                    command=h.get("command"),
                    transient=h.get("transient", False),
                )
            )

        if actions:
            parsed.append(PluginRule(matcher=str(matcher), actions=actions))

    return parsed


# ---------------------------------------------------------------------------
# ADK Plugin bridge
# ---------------------------------------------------------------------------


class ClaudeCodeHookPlugin(BasePlugin):
    """ADK plugin that bridges Claude Code hook declarations.

    Loads Claude Code format JSON files and executes them as ADK callbacks.
    See module docstring for detailed semantic mapping between CC and ADK.

    Uses a cross-callback injection pattern (from ``ImageInjectionPlugin``)
    for ``PreToolUse`` and ``SessionStart``:

    1. Collect context in ``before_tool_callback`` (or first
       ``before_model_callback`` for SessionStart)
    2. Inject transiently into ``llm_request.contents`` in
       ``before_model_callback`` (visible to current model call)
    3. Persist to session history in ``on_event_callback`` (visible to
       future model calls)

    **Fully simulated:**

    - ``PostToolUse`` — prompt/command output injected into tool result
    - ``PreToolUse`` — command runs before tool; prompt injected into next
      model call and persisted to history
    - ``SessionStart`` — prompt injected into first model call and persisted

    **Partially simulated:**

    - ``UserPromptSubmit`` — prompt appended to user message; ``block`` not
      supported

    **Not simulated (removed):**

    - ``PostToolUseFailure`` — ADK swallows exception; cannot preserve CC's
      "error propagates + context alongside" semantics
    """

    def __init__(
        self,
        sources: List[str] | None = None,
        base_dir: str | None = None,
        name: str = "claude_code_hook",
        **kwargs,
    ) -> None:
        super().__init__(name=name, **kwargs)
        base = Path(base_dir) if base_dir else Path.cwd()
        self._config = _parse_json_sources(sources or [], base)

        # Cross-callback state for PreToolUse injection
        self._pending_pre_tool_context: list[str] = []
        self._pending_pre_tool_persist: list[str] = []

        # Cross-callback state for SessionStart injection
        self._session_start_fired: bool = False
        self._pending_session_persist: list[str] = []

        logger.info(
            "ClaudeCodeHookPlugin(%s): %d PreToolUse rules, %d PostToolUse rules",
            self.name,
            len(self._config.get("PreToolUse", [])),
            len(self._config.get("PostToolUse", [])),
        )

    @property
    def config(self) -> PluginConfig:
        """Access the parsed config (for testing)."""
        return self._config

    # -- ADK tool callbacks -------------------------------------------------

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
    ) -> Optional[dict]:
        """``PreToolUse`` — fires before tool execution.

        Command actions run here (correct timing: before tool executes).
        Prompt text and command output are stored in pending lists for
        injection by ``before_model_callback`` (transient) and
        ``on_event_callback`` (persistent).

        **Not simulated:** ``deny`` (CC's ``permissionDecision``),
        ``updatedInput`` (CC can modify tool args) — JSON hook format does
        not expose these.
        """
        for action in self._get_actions("PreToolUse", tool.name, tool_args):
            if action.type == "prompt" and action.prompt:
                self._pending_pre_tool_context.append(action.prompt)
                self._pending_pre_tool_persist.append(action.prompt)
                logger.debug(
                    "[ClaudeCodeHook] PreToolUse prompt queued for model injection: %.80s",
                    action.prompt,
                )
            elif action.type == "command":
                output = await self._run_command(
                    action, tool_context, event="PreToolUse"
                )
                if output:
                    self._pending_pre_tool_context.append(output)
                    self._pending_pre_tool_persist.append(output)
                    logger.debug(
                        "[ClaudeCodeHook] PreToolUse command output queued: %.80s",
                        output,
                    )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ) -> Optional[Dict[str, Any]]:
        """``PostToolUse`` — fires after successful tool execution.

        **Full bridge**: prompt text and command output are injected into the
        tool result dict via ``result["output"]``.  The model sees the
        injected text alongside the original tool output.

        CC equivalent: ``additionalContext`` / ``decision: "block"`` with
        ``reason`` — both add text that Claude sees after the tool runs.
        """
        for action in self._get_actions("PostToolUse", tool.name, tool_args):
            text = await self._execute_action(
                action, event="PostToolUse", tool_context=tool_context
            )
            if text:
                self._inject_into_result(result, text, action)
        return None

    # -- ADK model callbacks (cross-callback injection) ---------------------

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        """Inject pending context into the current model request (transient).

        Handles two sources:

        1. **SessionStart** — on the first model call, collect all
           SessionStart prompt actions and inject them.
        2. **PreToolUse** — any prompts/command output accumulated by
           ``before_tool_callback`` since the last model call.

        Injection is transient (``llm_request.contents`` is not persisted).
        Persistence happens in ``on_event_callback``.
        """
        injected: list[str] = []

        # SessionStart — first model call only
        if not self._session_start_fired:
            self._session_start_fired = True
            for action in self._get_actions("SessionStart"):
                if action.type == "prompt" and action.prompt:
                    injected.append(action.prompt)
                    self._pending_session_persist.append(action.prompt)
                    logger.debug(
                        "[ClaudeCodeHook] SessionStart prompt injected: %.80s",
                        action.prompt,
                    )

        # PreToolUse — accumulated pending context
        if self._pending_pre_tool_context:
            injected.extend(self._pending_pre_tool_context)
            self._pending_pre_tool_context.clear()

        if injected and llm_request.contents:
            llm_request.contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text="\n\n".join(injected))],
                )
            )

        return None

    # -- ADK event callback (persistence) -----------------------------------

    async def on_event_callback(
        self,
        *,
        invocation_context: InvocationContext,
        event: Event,
    ) -> Optional[Event]:
        """Persist pending context to session history.

        Any context that was injected transiently by ``before_model_callback``
        also needs to be persisted so that future model calls (after context
        window shifts) can still see it.
        """
        parts_to_persist: list[str] = []

        if self._pending_session_persist:
            parts_to_persist.extend(self._pending_session_persist)
            self._pending_session_persist.clear()

        if self._pending_pre_tool_persist:
            parts_to_persist.extend(self._pending_pre_tool_persist)
            self._pending_pre_tool_persist.clear()

        if parts_to_persist:
            persist_event = Event(
                invocation_id=invocation_context.invocation_id,
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part(text="\n\n".join(parts_to_persist))],
                ),
            )
            await invocation_context.session_service.append_event(
                session=invocation_context.session, event=persist_event
            )

        return None

    # -- ADK session callbacks ----------------------------------------------

    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> Optional[types.Content]:
        """``UserPromptSubmit`` — fires when user submits a prompt.

        **Partial bridge**: prompt text is appended to ``user_message.parts``
        as additional context.

        **Not bridgeable from CC:**

        - ``decision: "block"`` — CC can reject a prompt entirely; ADK's
          ``on_user_message_callback`` can only modify or replace the message.
        - ``command`` actions — no sandbox context available at this stage.
        """
        actions = self._get_actions("UserPromptSubmit")
        for action in actions:
            text = await self._execute_action(action, event="UserPromptSubmit")
            if text and action.type == "prompt":
                user_message.parts.append(types.Part(text=f"\n[Plugin] {text}"))
        return None

    # -- internals ----------------------------------------------------------

    def _get_actions(
        self,
        event: str,
        tool_name: str | None = None,
        tool_args: dict | None = None,
    ) -> List[PluginAction]:
        """Return matching actions for an event (+ optional tool)."""
        actions: List[PluginAction] = []
        for rule in self._config.get(event, []):
            if tool_name is not None:
                if _tool_matches(rule.matcher, tool_name, tool_args):
                    actions.extend(rule.actions)
            else:
                # Non-tool events: all rules match (matcher is informational)
                actions.extend(rule.actions)
        return actions

    async def _execute_action(
        self,
        action: PluginAction,
        *,
        event: str,
        tool_context: ToolContext | None = None,
    ) -> Optional[str]:
        """Execute a single action, returning the text to inject (or None).

        *tool_context* is required for ``command`` actions (sandbox access).
        When absent, command actions are skipped with a warning.
        """
        if action.type == "prompt":
            logger.debug(
                "[ClaudeCodeHook] %s prompt: %s", event, (action.prompt or "")[:80]
            )
            return action.prompt

        if action.type == "command":
            if tool_context is None:
                logger.warning(
                    "[ClaudeCodeHook] command actions not supported for %s events "
                    "(no sandbox context available)",
                    event,
                )
                return None
            return await self._run_command(action, tool_context, event=event)

        return None

    async def _run_command(
        self, action: PluginAction, tool_context: ToolContext, *, event: str
    ) -> Optional[str]:
        try:
            from opensage.utils.agent_utils import get_sandbox_from_context

            sandbox = get_sandbox_from_context(tool_context, "main")
        except Exception as e:
            logger.warning("[ClaudeCodeHook] Could not get sandbox: %s", e)
            return None

        if not action.command:
            return None

        logger.debug("[ClaudeCodeHook] %s command: %s", event, action.command[:80])

        try:
            output, exit_code = sandbox.run_command_in_container(
                action.command, timeout=_COMMAND_TIMEOUT
            )
        except Exception as e:
            logger.warning("[ClaudeCodeHook] Command failed: %s", e)
            return None

        if exit_code != 0:
            return None

        if output and len(output) > _MAX_COMMAND_OUTPUT_LENGTH:
            output = output[:_MAX_COMMAND_OUTPUT_LENGTH] + "\n... (truncated)"

        return output if output and output.strip() else None

    @staticmethod
    def _inject_into_result(result: dict, text: str, action: PluginAction) -> None:
        if action.transient:
            logger.warning(
                "[ClaudeCodeHook] transient=true not yet supported, using persistent injection"
            )

        existing = result.get("output", "")
        separator = "\n\n" if existing else ""
        result["output"] = f"{existing}{separator}[Plugin] {text}"
        logger.info("[ClaudeCodeHook] Injected plugin text (%d chars)", len(text))


def load_claude_code_hook_plugin(source: str) -> ClaudeCodeHookPlugin:
    """Load a :class:`ClaudeCodeHookPlugin` from a single JSON source file.

    The plugin name is derived from the filename (e.g. ``careful_edit.json``
    → ``"careful_edit"``).
    """
    return ClaudeCodeHookPlugin(sources=[source], name=Path(source).stem)
