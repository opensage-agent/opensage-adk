"""AgentManager: environment-scoped agent lifecycle, messaging, and persistence."""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types

from .inbox import Inbox
from .instance import AgentInstance
from .persistence import (
    inbox_path,
    instance_dir,
    instances_root,
    list_instance_sids,
    load_adk_session,
    load_agent_definition,
    read_metadata,
    save_adk_session,
    touch_inbox,
    write_metadata,
)
from .plugins.inbox_delivery import InboxDeliveryPlugin
from .types import FRAMEWORK_STATE_KEYS, AgentInstanceState, Message

if TYPE_CHECKING:
    from google.adk.events.event import Event
    from google.adk.plugins.base_plugin import BasePlugin

    from opensage.agents.opensage_agent import OpenSageAgent
    from opensage.session.opensage_session import OpenSageSession

logger = logging.getLogger("opensage." + __name__)

# Default user_id used for ADK sessions we create.
DEFAULT_USER_ID = "user"

# Sender id used when a spawn's initial_message has no real caller.
SPAWNER_SENDER_ID = "__spawner__"

# Sender id used when an external driver (CLI web, evaluation) sends the
# initial user message to the root.
USER_SENDER_ID = "__user__"


class AgentManager:
    """Manages agent definitions, instances, messaging, and lifecycle within an
    OpenSageSession (one isolated environment).

    Loop-ownership map (do not violate without reading this).

    The criterion for "must run on a specific loop" is whether a call creates
    persistent loop-bound state (asyncio.Task / Future / long-lived Queue or
    Lock) that future code must consume on the same loop. Using ``await``
    internally is NOT sufficient to make a call loop-pinned.

    - **loop-agnostic** (any loop is fine; safe at module/handler init,
      inside ``asyncio.run``, or inside lifespan startup):
      ``set_app_name``, ``set_base_plugins``, ``register_agent_tree`` (sync
      dict writes); ``spawn`` (awaits get_session/create_session but only
      writes disk + dict, no Task handed back); resume snapshot inject (async
      I/O, no persistent loop state).
    - **loop-pinned** (creates / consumes persistent loop-bound state; future
      consumers MUST run on the same loop):
      ``start`` (creates ``_dispatcher_task`` + binds ``_wake_queue`` to that
      loop; subsequent send_message-triggered invocations run on this loop);
      ``shutdown`` (cancels the dispatcher task, must run on the same loop
      that created it).

    When adding a new init step, ask the criterion question. Do NOT
    generalize from "this is async, so it must go in lifespan startup".
    """

    def __init__(self, opensage_session: "OpenSageSession") -> None:
        self._opensage_session = opensage_session
        # Default app_name derived from opensage_session_id; CLI/eval override
        # this via ``set_app_name`` before spawning root so it matches the
        # app_name used by the external Runner / web server.
        self._app_name: str = f"opensage-{opensage_session.opensage_session_id[:8]}"
        self._base_plugins: list["BasePlugin"] = []

        # agents: name -> OpenSageAgent definition (shared read-only templates)
        self._agents: dict[str, "OpenSageAgent"] = {}

        # Snapshot of all tools reachable from the registered agent tree, keyed
        # by tool name. Built once in ``register_agent_tree``; used by
        # ``rebuild_agent_from_definition`` to restore non-baseline tools when
        # reloading dynamic subagents (e.g. after a process restart). On name
        # collision (same name, different object) we keep the first observed
        # object and warn — first-wins is deterministic by walk order.
        self._tool_snapshot: dict[str, Any] = {}

        # instances: session_id -> AgentInstance (kept for entire lifetime)
        self._instances: dict[str, AgentInstance] = {}

        # wake queue: dispatcher consumer reads from this
        self._wake_queue: asyncio.Queue[str] = asyncio.Queue()
        self._dispatcher_task: asyncio.Task | None = None

    # ==================================================================
    # lifecycle
    # ==================================================================

    async def start(self) -> None:
        """Start the dispatcher background task.

        Also resets every persisted inbox on disk (peer messages do NOT
        survive across process restarts) and eagerly loads any on-disk
        instances that aren't already in memory (e.g. subagents from a
        previous session being resumed).
        """
        self._reset_all_inboxes()
        await self._eager_load_from_disk()
        if self._dispatcher_task is not None:
            return
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="AgentManager.dispatcher"
        )

    async def shutdown(self) -> None:
        """Cancel the dispatcher task and drop in-memory instances.

        Runner internals get released by GC at process exit. Mostly used
        by tests to cleanly tear down a manager between fixtures.
        """
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except (asyncio.CancelledError, Exception):
                pass
        self._dispatcher_task = None
        self._instances.clear()

    def _reset_all_inboxes(self) -> None:
        """Truncate every persisted inbox.jsonl on disk.

        Called from ``start`` so peer messages from a previous process don't
        get re-consumed.
        """
        root = instances_root(self._opensage_session.opensage_session_id)
        if not root.exists():
            return
        for path in root.rglob("inbox.jsonl"):
            try:
                path.write_text("", encoding="utf-8")
            except Exception:
                logger.exception("Failed to truncate inbox at %s", path)

    async def _eager_load_from_disk(self) -> None:
        """Load any on-disk instances not already in ``_instances``.

        This handles the resume case: the CLI spawns only the root agent
        before calling ``start()``, so subagent instances from the previous
        process must be loaded here.
        """
        on_disk = list_instance_sids(self._opensage_session.opensage_session_id)
        for sid in on_disk:
            if sid in self._instances:
                continue
            try:
                await self._load_instance(sid)
                logger.info("Eager-loaded instance %s from disk", sid)
            except Exception:
                logger.exception(
                    "Failed to eager-load instance %s; it will not be "
                    "available in this session",
                    sid,
                )

    # ==================================================================
    # plugins
    # ==================================================================

    def set_base_plugins(self, plugins: list["BasePlugin"]) -> None:
        """Base plugins shared by all instances' Runners (user plugins + defaults)."""
        self._base_plugins = list(plugins)

    def set_app_name(self, app_name: str) -> None:
        """Override the app_name used for ADK session lookups.

        Must be called BEFORE ``spawn`` so all instances share one value.
        CLI uses the agent_dir's parent folder name; evaluation uses the same
        convention; tests rely on the default.
        """
        if not isinstance(app_name, str) or not app_name:
            raise ValueError("app_name must be a non-empty string")
        self._app_name = app_name

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def default_user_id(self) -> str:
        """Default user_id for ADK sessions created by this manager."""
        return DEFAULT_USER_ID

    @property
    def session_service(self):
        """Public accessor for the session service."""
        return self._opensage_session.session_service

    @property
    def opensage_session_id(self) -> str:
        return self._opensage_session.opensage_session_id

    # ==================================================================
    # agent definitions (templates)
    # ==================================================================

    def register_agent(self, name: str, agent: "OpenSageAgent") -> None:
        """Register a named agent definition. Names must be unique per
        OpenSageSession after canonical sanitization — two raw names that
        collapse to the same sanitized form are also rejected."""
        from opensage.utils.agent_utils import sanitize_agent_name

        sanitized = sanitize_agent_name(name)
        if sanitized != name:
            raise ValueError(
                f"Agent name {name!r} is not canonically sanitized. "
                f"Expected {sanitized!r}. Callers must sanitize before register."
            )
        for existing in self._agents:
            if sanitize_agent_name(existing) == sanitized:
                raise ValueError(
                    f"Agent name {name!r} is already registered as {existing!r} "
                    f"(both sanitize to {sanitized!r}). Agent names must be "
                    f"unique after sanitization."
                )
        self._agents[name] = agent

    def register_agent_tree(self, root: "OpenSageAgent") -> None:
        """Recursively register ``root`` and every OpenSageAgent reachable
        through its ``_subagents`` field.

        Any duplicate name encountered while walking the tree — from ``root``
        itself or from any descendant — raises ValueError immediately.

        Side effect: if the OpenSageSession's ``LlmRegistry`` is empty (user
        didn't configure ``[model]``), seed it with ``root.model`` so that
        LLM-driven subagent tools (``call_subagent``/``create_subagent``) have
        at least one usable entry.
        """
        from opensage.utils.agent_utils import extract_tools_from_agent

        visited: set[int] = set()

        def _walk(agent: "OpenSageAgent") -> None:
            if id(agent) in visited:
                return
            visited.add(id(agent))
            self.register_agent(agent.name, agent)

            # Snapshot tools (first-wins on same-name-different-source).
            # OpenSageAgent.__init__ wraps callables via tool_normalization, so
            # the same source function reachable through multiple agents shows
            # up as distinct wrappers. Compare by ``__wrapped__`` (set by the
            # wrapper) when present so we don't false-flag those.
            for tname, tobj in extract_tools_from_agent(agent).items():
                existing = self._tool_snapshot.get(tname)
                if existing is None:
                    self._tool_snapshot[tname] = tobj
                else:
                    existing_src = getattr(existing, "__wrapped__", existing)
                    new_src = getattr(tobj, "__wrapped__", tobj)
                    if existing_src is not new_src:
                        logger.warning(
                            "Tool name %r appears in multiple agents as "
                            "different objects; keeping the first one "
                            "observed. Dynamic subagent rebuild that requests "
                            "this name will get the first-registered object.",
                            tname,
                        )

            for child in getattr(agent, "_subagents", []) or []:
                _walk(child)

        _walk(root)

        # Seed registry with root's model when user didn't configure any.
        root_model = getattr(root, "model", None)
        if root_model is not None:
            try:
                self._opensage_session.llms.ensure_root_fallback(root_model)
            except Exception:
                logger.exception(
                    "Failed to seed LlmRegistry with root model; LLM-driven "
                    "subagent tools may have no available models"
                )

    def get_agent(self, name: str) -> Optional["OpenSageAgent"]:
        return self._agents.get(name)

    def list_agents(self) -> dict[str, "OpenSageAgent"]:
        return dict(self._agents)

    # ==================================================================
    # spawn (create new instance from registered agent)
    # ==================================================================

    async def spawn(
        self,
        agent_name: str,
        *,
        parent_session_id: Optional[str] = None,
        use_parent_history: bool = False,
        session_id: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        """Create a new instance of a registered agent, persist to disk, and
        register it in memory. Does NOT start any invocation. Returns the
        new session_id.

        ``model_override``: name (registry key) of an LLM model to override the
        agent template's own model for this instance. Persisted in metadata.json
        so it survives process restarts.
        """
        agent = self._agents.get(agent_name)
        if agent is None:
            raise KeyError(
                f"Agent {agent_name!r} not registered. "
                f"Known: {list(self._agents.keys())}"
            )

        new_sid = session_id or str(uuid.uuid4())
        if new_sid in self._instances:
            raise ValueError(f"session_id {new_sid} is already in memory")

        # If the session already exists in the shared service (e.g. it was
        # just loaded from a legacy resume snapshot), adopt it instead of
        # creating a new one. Otherwise build fresh state + events.
        session_service = self._opensage_session.session_service
        adk_session = await session_service.get_session(
            app_name=self._app_name,
            user_id=DEFAULT_USER_ID,
            session_id=new_sid,
        )
        if adk_session is None:
            state, events = await self._build_child_state(
                agent=agent,
                new_sid=new_sid,
                parent_session_id=parent_session_id,
                use_parent_history=use_parent_history,
            )
            adk_session = await session_service.create_session(
                app_name=self._app_name,
                user_id=DEFAULT_USER_ID,
                state=state,
                session_id=new_sid,
            )
            for ev in events:
                await session_service.append_event(adk_session, ev)
        else:
            # Adopt path: backfill any missing framework keys on the resumed
            # state so downstream code (patches, scan, orchestration) works.
            from opensage.memory.file_based.short_term.session_files import (
                HOST_INSTANCE_DIR_KEY,
                compute_host_root_instance_dir,
            )

            state = adk_session.state if isinstance(adk_session.state, dict) else {}
            state.setdefault(
                "opensage_session_id",
                self._opensage_session.opensage_session_id,
            )
            if not state.get(HOST_INSTANCE_DIR_KEY):
                state[HOST_INSTANCE_DIR_KEY] = compute_host_root_instance_dir(
                    opensage_session_id=self._opensage_session.opensage_session_id,
                    session_id=new_sid,
                )

        host_dir_str = adk_session.state.get("_host_instance_dir")
        if not isinstance(host_dir_str, str) or not host_dir_str:
            raise RuntimeError(
                "spawn: _host_instance_dir missing from computed state; "
                "this is a framework-key bug."
            )
        d = Path(host_dir_str)
        d.mkdir(parents=True, exist_ok=True)

        # Persist: metadata, empty inbox, and an initial traj.json snapshot.
        # The traj.json acts as the ADK session file (replaces adk_session.json)
        # and is overwritten by the BaseAgent.run_async patch during invocations.
        write_metadata(
            d,
            {
                "session_id": new_sid,
                "agent_name": agent_name,
                "parent_session_id": parent_session_id,
                "app_name": self._app_name,
                "user_id": DEFAULT_USER_ID,
                "model_override": model_override,
            },
        )
        touch_inbox(d)
        save_adk_session(d, adk_session)

        # Build the in-memory AgentInstance immediately so no separate
        # ensure_loaded / _load_instance round-trip is needed.
        try:
            inst_agent = agent.model_copy(deep=False)
        except Exception:
            inst_agent = copy.copy(agent)

        if model_override:
            inst_agent.model = self._opensage_session.llms.get(model_override)

        inbox = Inbox(inbox_path(d))
        runner = self._build_runner(inst_agent, inbox)

        inst = AgentInstance(
            session_id=new_sid,
            agent=inst_agent,
            agent_name=agent_name,
            state=AgentInstanceState.SLEEPING,
            inbox=inbox,
            runner=runner,
            user_id=DEFAULT_USER_ID,
            parent_session_id=parent_session_id,
        )
        self._instances[new_sid] = inst

        return new_sid

    async def _build_child_state(
        self,
        *,
        agent: "OpenSageAgent",
        new_sid: str,
        parent_session_id: Optional[str],
        use_parent_history: bool,
    ) -> tuple[dict[str, Any], list["Event"]]:
        """Compute the initial state dict and events list for a new ADK session.

        Framework keys (opensage_session_id, _mem_agent_dir, _host_instance_dir)
        are always freshly derived. Non-framework state + events are inherited
        from parent only if ``use_parent_history=True``.

        Host side uses pure sid with nested dirs. Sandbox side keeps the
        {agent_name}__{sid} convention.
        """
        from opensage.memory.file_based.short_term.session_files import (
            HOST_INSTANCE_DIR_KEY,
            MEM_AGENT_DIR_KEY,
            compute_host_root_instance_dir,
            compute_root_session_mem_dir,
        )

        osid = self._opensage_session.opensage_session_id
        state: dict[str, Any] = {"opensage_session_id": osid}

        parent_session = None
        if parent_session_id is not None:
            session_service = self._opensage_session.session_service
            parent_session = await session_service.get_session(
                app_name=self._app_name,
                user_id=DEFAULT_USER_ID,
                session_id=parent_session_id,
            )

        if parent_session is not None and parent_session.state:
            parent_mem = parent_session.state.get(MEM_AGENT_DIR_KEY)
            parent_host = parent_session.state.get(HOST_INSTANCE_DIR_KEY)
            # Sandbox side stays nested with {agent_name}__{sid}
            if parent_mem:
                state[MEM_AGENT_DIR_KEY] = f"{parent_mem}/{agent.name}__{new_sid}"
            else:
                state[MEM_AGENT_DIR_KEY] = compute_root_session_mem_dir(
                    agent_name=agent.name, session_id=new_sid
                )
            # Host side uses pure sid, nested directly under parent's dir
            if parent_host:
                state[HOST_INSTANCE_DIR_KEY] = f"{parent_host}/{new_sid}"
            else:
                state[HOST_INSTANCE_DIR_KEY] = compute_host_root_instance_dir(
                    opensage_session_id=osid,
                    session_id=new_sid,
                )
        else:
            state[MEM_AGENT_DIR_KEY] = compute_root_session_mem_dir(
                agent_name=agent.name, session_id=new_sid
            )
            state[HOST_INSTANCE_DIR_KEY] = compute_host_root_instance_dir(
                opensage_session_id=osid,
                session_id=new_sid,
            )

        # Inherit business/plugin state (not framework keys) if requested
        events: list["Event"] = []
        if use_parent_history and parent_session is not None:
            for k, v in (parent_session.state or {}).items():
                if k not in FRAMEWORK_STATE_KEYS:
                    try:
                        state[k] = copy.deepcopy(v)
                    except Exception:
                        logger.warning(
                            "Shallow-copying non-deep-copyable state key %s", k
                        )
                        state[k] = v
            events = copy.deepcopy(parent_session.events or [])

        return state, events

    # ==================================================================
    # instance lookup
    # ==================================================================

    def ensure_loaded(self, session_id: str) -> AgentInstance:
        """Return the in-memory AgentInstance.

        All instances are kept in memory for their entire lifetime (created
        by ``spawn`` or loaded eagerly in ``start``). This is a simple dict
        lookup; raises ``KeyError`` if the sid is unknown.
        """
        inst = self._instances.get(session_id)
        if inst is None:
            raise KeyError(
                f"No in-memory instance for session_id={session_id}. "
                f"Known: {list(self._instances.keys())}"
            )
        return inst

    # ==================================================================
    # disk load (resume only — called by _eager_load_from_disk)
    # ==================================================================

    async def _load_instance(self, session_id: str) -> AgentInstance:
        """Build an AgentInstance from on-disk state (resume path only)."""
        from opensage.memory.file_based.short_term.session_files import (
            find_instance_dir,
        )

        d = find_instance_dir(self._opensage_session.opensage_session_id, session_id)
        if d is None:
            raise KeyError(f"No instance dir for session_id={session_id}")

        metadata = read_metadata(d)
        agent_name = metadata["agent_name"]

        # Reuse the session already in the service (e.g., injected by resume).
        # Only read disk + inject when nothing is there.
        session_service = self._opensage_session.session_service
        adk_session = await session_service.get_session(
            app_name=self._app_name,
            user_id=metadata.get("user_id", DEFAULT_USER_ID),
            session_id=session_id,
        )
        if adk_session is None:
            adk_session = load_adk_session(d)
            session_service.inject_session(adk_session)

        # Get the agent definition (template). If missing, try to restore from
        # the on-disk definition.json (for dynamic agents).
        template = self._agents.get(agent_name)
        if template is None:
            defn = load_agent_definition(
                self._opensage_session.opensage_session_id, agent_name
            )
            if defn is None:
                raise KeyError(
                    f"Agent {agent_name!r} not registered and no saved definition; "
                    f"cannot load instance {session_id}."
                )
            template = await self._rebuild_agent_from_definition(defn)
            self._agents[agent_name] = template

        try:
            agent = template.model_copy(deep=False)
        except Exception:
            agent = copy.copy(template)

        model_override = metadata.get("model_override")
        if model_override:
            try:
                agent.model = self._opensage_session.llms.get(model_override)
            except KeyError as e:
                raise KeyError(
                    f"Cannot load instance {session_id}: model_override "
                    f"{model_override!r} is not in the LlmRegistry. {e}"
                ) from e

        inbox = Inbox(inbox_path(d))
        runner = self._build_runner(agent, inbox)

        inst = AgentInstance(
            session_id=session_id,
            agent=agent,
            agent_name=agent_name,
            state=AgentInstanceState.SLEEPING,
            inbox=inbox,
            runner=runner,
            user_id=metadata.get("user_id", DEFAULT_USER_ID),
            parent_session_id=metadata.get("parent_session_id"),
        )
        self._instances[session_id] = inst
        return inst

    def _build_runner(self, agent: "OpenSageAgent", inbox: Inbox) -> Runner:
        """Construct a Runner for one instance, with InboxDeliveryPlugin added."""
        plugins = list(self._base_plugins) + [InboxDeliveryPlugin(inbox)]
        return Runner(
            app_name=self._app_name,
            agent=agent,
            session_service=self._opensage_session.session_service,
            artifact_service=self._opensage_session.artifact_service,
            memory_service=self._opensage_session.memory_service,
            credential_service=self._opensage_session.credential_service,
            plugins=plugins,
        )

    async def _rebuild_agent_from_definition(
        self, definition: dict[str, Any]
    ) -> "OpenSageAgent":
        """Restore a dynamically-created OpenSageAgent from its saved definition."""
        # Delegate to the orchestration_tools builder (lazy import to avoid cycles).
        from opensage.toolbox.general.orchestration_tools import (
            rebuild_agent_from_definition,
        )

        return await rebuild_agent_from_definition(definition, self._opensage_session)

    # ==================================================================
    # dispatcher (wake on send_message)
    # ==================================================================

    async def _dispatcher_loop(self) -> None:
        """Single-consumer loop: serializes wake decisions."""
        while True:
            try:
                sid = await self._wake_queue.get()
                await self._handle_wake_signal(sid)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Dispatcher iteration failed")

    async def _handle_wake_signal(self, sid: str) -> None:
        try:
            instance = self.ensure_loaded(sid)
        except KeyError:
            logger.warning("Wake for unknown sid %s, ignoring", sid)
            return

        if instance.state != AgentInstanceState.SLEEPING:
            return
        instance.state = AgentInstanceState.RUNNING
        instance._done_event.clear()

        if not await instance.inbox.has_pending():
            instance.state = AgentInstanceState.SLEEPING
            instance._done_event.set()
            return

        async def _run_dispatcher_turn():
            try:
                async for _event in self._run_turn_loop(instance):
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Dispatcher turn for %s raised", sid)

        instance._task = asyncio.create_task(
            _run_dispatcher_turn(),
            name=f"dispatch-{sid[:8]}",
        )

    async def _post_invocation(self, instance: AgentInstance) -> None:
        """After an invocation completes: re-wake if inbox has pending messages."""
        try:
            has_pending = await instance.inbox.has_pending()
        except Exception:
            logger.exception("has_pending failed for %s", instance.session_id)
            has_pending = False

        if has_pending and instance.state == AgentInstanceState.SLEEPING:
            await self._wake_queue.put(instance.session_id)

    def _release(self, instance: AgentInstance) -> None:
        """Reset state to SLEEPING + clear task + signal done. Sync, idempotent.

        Called from invocation finally blocks. Does NOT trigger post_invocation
        — most callers want it (use ``_release_and_post``), but the async-reply
        path delivers a result message between release and post.

        If the instance was marked TERMINATING, transitions to TERMINATED
        instead of SLEEPING so it is permanently shut down.
        """
        if instance.state == AgentInstanceState.TERMINATING:
            instance.state = AgentInstanceState.TERMINATED
        else:
            instance.state = AgentInstanceState.SLEEPING
        instance._task = None
        instance._done_event.set()

    async def _release_and_post(self, instance: AgentInstance) -> None:
        """Release running state then run post_invocation (re-wake if pending)."""
        self._release(instance)
        await self._post_invocation(instance)

    # ==================================================================
    # send_message (peer communication)
    # ==================================================================

    async def send_message(
        self,
        *,
        from_sid: str,
        to_sid: str,
        content: str,
        kind: str = "text",
        metadata: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Send a message. ``to_sid='*'`` broadcasts to all known instances."""
        _terminal = (AgentInstanceState.TERMINATING, AgentInstanceState.TERMINATED)
        if to_sid == "*":
            for sid in self._all_known_sids():
                if sid == from_sid:
                    continue
                inst = self._instances.get(sid)
                if inst and inst.state in _terminal:
                    continue
                await self._send_one(from_sid, sid, content, kind, metadata, error)
        else:
            await self._send_one(from_sid, to_sid, content, kind, metadata, error)

    async def _send_one(
        self,
        from_sid: str,
        to_sid: str,
        content: str,
        kind: str,
        metadata: Optional[dict[str, Any]],
        error: Optional[str] = None,
    ) -> None:
        inst = self._instances.get(to_sid)
        if inst and inst.state in (
            AgentInstanceState.TERMINATING,
            AgentInstanceState.TERMINATED,
        ):
            raise KeyError(f"Instance {to_sid} is {inst.state.value}")
        d = instance_dir(self._opensage_session.opensage_session_id, to_sid)
        if not d.exists():
            raise KeyError(f"No instance dir for to_sid={to_sid}")
        from_name, from_desc = self._resolve_sender_identity(from_sid)
        msg = Message(
            from_sid=from_sid,
            to_sid=to_sid,
            content=content,
            kind=kind,
            metadata=metadata,
            from_agent_name=from_name,
            from_agent_description=from_desc,
            error=error,
        )
        await Inbox.append_to(inbox_path(d), msg)
        await self._wake_queue.put(to_sid)

    def _resolve_sender_identity(self, from_sid: str) -> tuple[str, str]:
        """Resolve ``(agent_name, description)`` for the sender of a message.

        All instances are in memory, so a dict lookup suffices. Returns
        pseudo-names for synthetic sender ids (spawner, user). On miss
        returns two empty strings rather than raising.
        """
        if from_sid == SPAWNER_SENDER_ID:
            return ("__spawner__", "Internal spawn trigger")
        if from_sid == USER_SENDER_ID:
            return ("__user__", "External user driver (CLI / evaluation)")

        inst = self._instances.get(from_sid)
        if inst is not None:
            return (
                inst.agent_name or "",
                getattr(inst.agent, "description", "") or "",
            )
        return ("", "")

    def _all_known_sids(self) -> list[str]:
        """All instance session_ids (all are in memory)."""
        return sorted(self._instances.keys())

    # ==================================================================
    # directed invocation (call_subagent / continue_agent_instance backends)
    # ==================================================================

    BUSY_ERROR = "busy"

    async def _invoke_instance(
        self,
        instance: AgentInstance,
        request: str,
        mode: str,
        caller_sid: str,
        *,
        run_config: Optional[RunConfig] = None,
    ) -> dict[str, Any]:
        """Start one invocation on an already-loaded instance.

        Returns:
            {"success": True, "session_id": ..., "result": str}  # mode=sync
            {"success": True, "session_id": ..., "status": "running"}  # mode=async
            {"success": False, "error": "busy", "session_id": ...}
        """
        # Atomic check+set (no await between check and mutation)
        if instance.state != AgentInstanceState.SLEEPING:
            return {
                "success": False,
                "error": self.BUSY_ERROR,
                "session_id": instance.session_id,
            }
        instance.state = AgentInstanceState.RUNNING
        instance._done_event.clear()

        # Drain inbox so peer messages and request are combined
        try:
            inbox_msgs = await instance.inbox.pop_all()
        except Exception:
            logger.exception("Failed to drain inbox for %s", instance.session_id)
            inbox_msgs = []

        content = _build_user_content(inbox_msgs, request)

        if mode == "sync":
            # _run_invocation_collect returns a dict with success/result and
            # optionally error/exception. Merge in session_id so the tool
            # layer always sees a consistent shape, regardless of success
            # vs failure.
            result = await self._run_invocation_collect(
                instance, content, run_config=run_config
            )
            return {**result, "session_id": instance.session_id}
        else:
            instance._task = asyncio.create_task(
                self._run_invocation_async_reply(
                    instance, content, caller_sid, run_config
                ),
                name=f"inv-async-{instance.session_id[:8]}",
            )
            return {
                "success": True,
                "session_id": instance.session_id,
                "status": "running",
            }

    async def _run_invocation_collect(
        self,
        instance: AgentInstance,
        content: types.Content,
        run_config: Optional[RunConfig],
    ) -> dict[str, Any]:
        """Sync path: run one invocation, collect final text, handle state.

        Returns a dict with at minimum ``{"success": bool, "result": str}``.
        On failure the dict additionally carries ``"error"`` (a short tag)
        and ``"exception"`` (formatted ``type: msg``). Caller is responsible
        for adding ``session_id``.
        """
        final_text = ""
        try:
            async for event in instance.runner.run_async(
                user_id=instance.user_id,
                session_id=instance.session_id,
                new_message=content,
                run_config=run_config or RunConfig(),
            ):
                text = _extract_final_text(event)
                if text is not None:
                    final_text = text
            return {"success": True, "result": final_text}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                "Invocation for %s raised; reporting failure to caller",
                instance.session_id,
            )
            return {
                "success": False,
                "error": "invocation_error",
                "exception": f"{type(e).__name__}: {e}",
                "result": final_text,
            }
        finally:
            await self._release_and_post(instance)

    async def _run_invocation_async_reply(
        self,
        instance: AgentInstance,
        content: types.Content,
        caller_sid: str,
        run_config: Optional[RunConfig],
    ) -> None:
        """Async path: run invocation, post final text back to caller's inbox.

        On runner failure, posts a ``kind="error"`` message carrying the
        exception in ``error`` and any partial output in ``content``. The
        caller's LLM sees the error distinctly from a normal empty reply.
        """
        final_text = ""
        error_str: Optional[str] = None
        try:
            async for event in instance.runner.run_async(
                user_id=instance.user_id,
                session_id=instance.session_id,
                new_message=content,
                run_config=run_config or RunConfig(),
            ):
                text = _extract_final_text(event)
                if text is not None:
                    final_text = text
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Async invocation for %s raised", instance.session_id)
            error_str = f"{type(e).__name__}: {e}"
        finally:
            # Release state before delivering reply: send_message may itself
            # trigger a wake on the caller, and we want this instance to
            # already be SLEEPING by then. _post_invocation runs at the end.
            self._release(instance)

        # Deliver the result (or error) back to the caller's inbox
        try:
            d = instance_dir(self._opensage_session.opensage_session_id, caller_sid)
            if d.exists():
                await self.send_message(
                    from_sid=instance.session_id,
                    to_sid=caller_sid,
                    content=final_text,
                    kind="error" if error_str is not None else "result",
                    error=error_str,
                )
        except Exception:
            logger.exception(
                "Failed to post async result from %s to %s",
                instance.session_id,
                caller_sid,
            )

        await self._post_invocation(instance)

    # ==================================================================
    # external driver entrypoints (evaluation, web_server)
    # ==================================================================

    async def run_turn(
        self,
        session_id: str,
        request: str,
        *,
        mode: str = "sync",
        caller_sid: str = USER_SENDER_ID,
        run_config: Optional[RunConfig] = None,
    ) -> dict[str, Any]:
        """External driver entry: run one turn on the given instance.

        ``mode='sync'`` blocks until the turn completes (returns final text).
        ``mode='async'`` returns immediately; the final text is posted back to
        ``caller_sid``'s inbox when done.
        """
        instance = self.ensure_loaded(session_id)
        return await self._invoke_instance(
            instance, request, mode, caller_sid, run_config=run_config
        )

    async def _run_turn_loop(
        self,
        instance: AgentInstance,
        *,
        request: str | types.Content | None = None,
        run_config: Optional[RunConfig] = None,
        state_delta: Optional[dict[str, Any]] = None,
        invocation_id: Optional[str] = None,
    ) -> AsyncIterator["Event"]:
        """Core loop: run invocations + fake_user. Caller must have already
        set state=RUNNING and cleared _done_event.
        """
        from google.adk.agents.invocation_context import LlmCallsLimitExceededError

        session_id = instance.session_id
        try:
            current_msg: str | types.Content | None = request
            current_state_delta = state_delta
            current_invocation_id = invocation_id
            first_iteration = True

            while True:
                if (
                    instance.max_turns is not None
                    and instance._turns_used >= instance.max_turns
                ):
                    break

                try:
                    inbox_msgs = await instance.inbox.pop_all()
                except Exception:
                    logger.exception("Failed to drain inbox for %s", session_id)
                    inbox_msgs = []

                content = _build_user_content(inbox_msgs, current_msg)

                effective_rc = run_config if first_iteration else None

                try:
                    async for event in instance.runner.run_async(
                        user_id=instance.user_id,
                        session_id=instance.session_id,
                        new_message=content,
                        run_config=effective_rc or RunConfig(),
                        state_delta=current_state_delta,
                        invocation_id=current_invocation_id,
                    ):
                        yield event
                except LlmCallsLimitExceededError:
                    logger.warning(
                        "LLM-call budget exhausted for session %s", session_id
                    )
                    break

                instance._turns_used += 1
                first_iteration = False
                current_state_delta = None
                current_invocation_id = None

                session = await self.session_service.get_session(
                    app_name=self._app_name,
                    user_id=instance.user_id,
                    session_id=session_id,
                )
                used_this_turn = (
                    int(
                        (session.state or {}).get("_adk", {}).get("llm_calls_used", 0)
                        or 0
                    )
                    if session
                    else 0
                )
                instance._llm_calls_used += used_this_turn

                if (
                    instance.max_llm_calls is not None
                    and instance._llm_calls_used >= instance.max_llm_calls
                ):
                    break

                if not instance.fake_user_fn:
                    break

                next_msg = await instance.fake_user_fn(session)
                if next_msg is None:
                    break

                current_msg = types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=next_msg)],
                )
                from google.adk.events.event import Event

                yield Event(author="fake_user", content=current_msg)
        finally:
            await self._release_and_post(instance)

    # ==================================================================
    # queries
    # ==================================================================

    def is_loaded(self, session_id: str) -> bool:
        return session_id in self._instances

    def get_instance(self, session_id: str) -> Optional[AgentInstance]:
        return self._instances.get(session_id)

    def list_instances(self) -> list[AgentInstance]:
        return list(self._instances.values())

    def list_known_sids(self) -> list[str]:
        return self._all_known_sids()

    async def terminate_instance(self, session_id: str) -> str:
        """Mark an instance for permanent termination.

        If SLEEPING → immediately TERMINATED.
        If RUNNING  → TERMINATING; transitions to TERMINATED when the
                       current invocation finishes.

        Returns the new state value.

        Raises:
            KeyError: session_id not found.
            RuntimeError: already TERMINATING or TERMINATED.
        """
        inst = self._instances.get(session_id)
        if inst is None:
            raise KeyError(f"No instance for session_id={session_id}")
        if inst.state in (
            AgentInstanceState.TERMINATING,
            AgentInstanceState.TERMINATED,
        ):
            raise RuntimeError(f"Instance {session_id} is already {inst.state.value}")
        if inst.state == AgentInstanceState.SLEEPING:
            inst.state = AgentInstanceState.TERMINATED
        else:
            inst.state = AgentInstanceState.TERMINATING
        return inst.state.value

    # ==================================================================
    # wait
    # ==================================================================

    async def wait_for(self, session_id: str, timeout: Optional[float] = None) -> None:
        """Wait until the instance is truly idle: SLEEPING, no active task,
        inbox empty.

        Raises:
            KeyError: session_id is not in memory.
            asyncio.TimeoutError: timed out before reaching idle.
        """
        inst = self.ensure_loaded(session_id)

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        while True:
            # Active invocation: wait for it to finish, then re-check.
            if inst.state == AgentInstanceState.RUNNING or inst._task is not None:
                remaining = (
                    None if deadline is None else max(0.0, deadline - loop.time())
                )
                await asyncio.wait_for(inst._done_event.wait(), timeout=remaining)
                continue

            # SLEEPING + no task. Empty inbox means truly idle.
            if not await inst.inbox.has_pending():
                return

            # SLEEPING + pending: dispatcher will transition to RUNNING
            # shortly. Re-signal it and yield briefly.
            self._wake_queue.put_nowait(session_id)
            if deadline is not None and loop.time() >= deadline:
                raise asyncio.TimeoutError()
            await asyncio.sleep(0.01)

    def _has_active_watchers(self) -> bool:
        btm = getattr(self._opensage_session, "bash_tasks", None)
        if btm is None:
            return False
        return any(not t.done() for t in btm._watcher_tasks.values())

    async def _wait_watchers(self) -> None:
        btm = getattr(self._opensage_session, "bash_tasks", None)
        if btm is None:
            return
        active = [t for t in btm._watcher_tasks.values() if not t.done()]
        if active:
            await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)

    async def wait_until_idle(
        self, session_id: str, *, timeout: float | None = None
    ) -> None:
        """Wait until the entire agent tree is stable: all instances SLEEPING,
        no active tasks, all inboxes empty.

        Used by evaluation to wait for the full fake_user loop to complete.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        while True:
            busy = [
                inst
                for inst in self._instances.values()
                if inst.state == AgentInstanceState.RUNNING or inst._task is not None
            ]
            if busy:
                remaining = (
                    None if deadline is None else max(0.0, deadline - loop.time())
                )
                done_events = [inst._done_event.wait() for inst in busy]
                await asyncio.wait_for(asyncio.gather(*done_events), timeout=remaining)
                await asyncio.sleep(0.05)
                continue

            has_pending = False
            for inst in self._instances.values():
                if await inst.inbox.has_pending():
                    has_pending = True
                    break

            if not has_pending:
                if not self._has_active_watchers():
                    return
                remaining = (
                    None if deadline is None else max(0.0, deadline - loop.time())
                )
                try:
                    await asyncio.wait_for(self._wait_watchers(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(
                        f"wait_until_idle timed out for {session_id}"
                    )
                continue

            if deadline is not None and loop.time() >= deadline:
                raise asyncio.TimeoutError(
                    f"wait_until_idle timed out for {session_id}"
                )
            await asyncio.sleep(0.1)

    def get_running_children(self, parent_sid: str) -> list[str]:
        """Return session_ids of children that are currently RUNNING."""
        result: list[str] = []
        for inst in self._instances.values():
            if inst.parent_session_id == parent_sid and (
                inst.state == AgentInstanceState.RUNNING or inst._task is not None
            ):
                result.append(inst.session_id)
        return result

    async def wait_for_children(
        self, parent_sid: str, timeout: float | None = None
    ) -> list[str]:
        """Wait for all running children of *parent_sid* to finish.

        Returns the list of child session_ids that were waited on.
        """
        children = self.get_running_children(parent_sid)
        for cid in children:
            await self.wait_for(cid, timeout=timeout)
        return children

    # ==================================================================
    # task cancellation (cleanup)
    # ==================================================================

    def cancel_all_tasks(self) -> None:
        """Cancel all running instance tasks and the dispatcher (sync)."""
        for inst in self._instances.values():
            if inst._task is not None and not inst._task.done():
                inst._task.cancel()
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()


# ==================================================================
# helpers
# ==================================================================


_PEER_MESSAGE_GUIDANCE = (
    "The block below contains peer-to-peer messages from OTHER AGENTS — they "
    "are NOT new user requests. Treat each entry as informational context. "
    "If a sender did not explicitly ask you to do something, evaluate whether "
    "the new info helps the current user task; if it does not, keep working "
    "on the user task and do not switch context. "
    "kind=text: a peer sharing information or making a request. "
    "kind=result: the final response from a sub-agent you previously called "
    "asynchronously. "
    "kind=error: a sub-agent you called asynchronously CRASHED — its `error` "
    "line carries the exception. Do not treat its content as a normal reply."
)


def _format_messages_block(messages: list[Message]) -> str:
    """Format inbox messages into a single LLM-readable text block.

    Uses the agent_name + description captured at send time (see
    ``AgentManager._resolve_sender_identity``). Returns an empty string if
    there are no messages.
    """
    if not messages:
        return ""
    lines: list[str] = ["[Incoming peer messages]", _PEER_MESSAGE_GUIDANCE, ""]
    for m in messages:
        name = m.from_agent_name or "<unknown>"
        header = f"- from {name} (session_id: {m.from_sid})"
        if m.from_agent_description:
            header += f" — {m.from_agent_description}"
        lines.append(header)
        lines.append(f"  kind: {m.kind}")
        if m.kind == "error" and m.error:
            lines.append(f"  error: {m.error}")
        content_lines = m.content.splitlines() or [""]
        for cl in content_lines:
            lines.append(f"  {cl}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _build_user_content(
    inbox_msgs: list[Message],
    request: Optional[str | types.Content] = None,
) -> types.Content:
    """Build user Content from drained inbox messages + a direct request.

    ``request`` can be a plain string (evaluation / orchestration tools) or
    a ``types.Content`` (web server forwarding the frontend message as-is).
    When ``request`` is None this is a dispatcher-triggered peer invocation.

    Messages from ``__user__`` are treated as direct user text (no peer
    guidance wrapping). All other inbox messages get peer formatting.
    """
    user_msgs = [m for m in inbox_msgs if m.from_sid == USER_SENDER_ID]
    peer_msgs = [m for m in inbox_msgs if m.from_sid != USER_SENDER_ID]
    block = _format_messages_block(peer_msgs)

    text_parts: list[str] = []
    if block:
        text_parts.append(block)
    for m in user_msgs:
        text_parts.append(m.content)
    if isinstance(request, types.Content):
        if not text_parts:
            return request
        prefix_part = types.Part.from_text(text="\n\n".join(text_parts))
        return types.Content(
            role="user", parts=[prefix_part] + list(request.parts or [])
        )
    if request is not None:
        text_parts.append(request)
    text = "\n\n".join(text_parts)
    return types.Content(role="user", parts=[types.Part.from_text(text=text)])


def _extract_final_text(event: Any) -> Optional[str]:
    """Pull out a plausible 'final response' text from an ADK event."""
    try:
        if event.author == "user":
            return None
        content = getattr(event, "content", None)
        if content is None:
            return None
        parts = getattr(content, "parts", None)
        if not parts:
            return None
        # Skip function_call-bearing events
        if event.get_function_calls():
            return None
        texts: list[str] = []
        for part in parts:
            t = getattr(part, "text", None)
            if isinstance(t, str) and t:
                texts.append(t)
        if texts:
            return "\n".join(texts)
    except Exception:
        return None
    return None
