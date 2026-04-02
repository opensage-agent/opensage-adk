from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import click
import uvicorn

# ADK services we still reuse (not the packaged server)
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import (
    InMemoryCredentialService,
)
from google.adk.evaluation.local_eval_set_results_manager import (
    LocalEvalSetResultsManager,
)
from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

from opensage.cli.dependency_check import (
    verify_codeql,
    verify_docker,
    verify_kubectl,
)
from opensage.cli.opensage_web_app import OpenSageWebServer
from opensage.features.opensage_in_memory_session_service import (
    OpenSageInMemorySessionService,
)
from opensage.plugins import load_plugins
from opensage.session import get_opensage_session
from opensage.toolbox.sandbox_requirements import collect_sandbox_dependencies
from opensage.utils.bash_tools_staging import compute_bash_tools_top_roots

logger = logging.getLogger(__name__)
_SESSION_STORE_ROOT = Path.home() / ".local" / "opensage" / "sessions"


@click.group(context_settings={"max_content_width": 240})
def main():
    """OpenSage CLI tools."""
    pass


def _resolve_config_path(config_path: Optional[str], agent_dir: str) -> str:
    """Resolve the OpenSage TOML config path.

        Precedence:
        - If user specified --config, use it.
        - Otherwise, default to <agent_dir>/config.toml if it exists.

    Raises:
      ClickException: Raised when this operation fails."""
    if config_path:
        resolved = Path(config_path).expanduser().resolve()
        if not resolved.exists():
            raise click.ClickException(f"Config file not found: {resolved}")
        if resolved.is_dir():
            raise click.ClickException(
                f"Config path must be a file, got directory: {resolved}"
            )
        return str(resolved)

    agent_path = Path(agent_dir).resolve()
    candidate = agent_path / "config.toml"
    if candidate.exists() and candidate.is_file():
        return str(candidate.resolve())

    raise click.ClickException(
        "Missing required option '--config'. "
        f"Either pass --config PATH, or create {candidate}."
    )


def _load_mk_agent_from_dir(agent_dir: str):
    """Load mk_agent callable from an agent folder.

    Raises:
      ClickException: Raised when this operation fails."""
    agent_path = Path(agent_dir).resolve()
    if not agent_path.exists() or not agent_path.is_dir():
        raise click.ClickException(f"Invalid agent directory: {agent_dir}")

    agent_file = agent_path / "agent.py"
    if not agent_file.exists():
        raise click.ClickException(f"agent.py not found in {agent_dir}")

    import importlib
    import sys

    parent_dir = str(agent_path.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    module_name = f"{agent_path.name}.agent"
    try:
        agent_module = importlib.import_module(module_name)
    except Exception as e:
        raise click.ClickException(
            f"Failed to import agent module '{module_name}': {e}"
        ) from e

    mk_agent = getattr(agent_module, "mk_agent", None)
    if not callable(mk_agent):
        raise click.ClickException(
            f"`mk_agent` not found in {agent_file}. "
            "Please define mk_agent(opensage_session_id: str, ...) -> Agent"
        )
    return mk_agent


async def _prepare_environment_async(config_path: str, agent_dir: str) -> str:
    """Prepare OpenSage environment: create session and initialize sandboxes.

    Returns:
      str: The created OpenSage session_id (used to bind agent state).
    """
    import uuid

    session_id = str(uuid.uuid4())
    logger.info(f"Initializing OpenSage session: {session_id}")

    # 1) Create session from config
    opensage_session = get_opensage_session(
        opensage_session_id=session_id, config_path=config_path
    )

    # 1.5) Collect sandbox dependencies from the specified agent, and prune config
    tools_top_roots = None
    try:
        mk_agent = _load_mk_agent_from_dir(agent_dir)
        dummy_agent = mk_agent(opensage_session_id=session_id)
        sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
        tools_top_roots = compute_bash_tools_top_roots(dummy_agent)
        if (
            opensage_session.config.sandbox
            and opensage_session.config.sandbox.sandboxes
        ):
            configured_sandboxes = set(opensage_session.config.sandbox.sandboxes.keys())

            missing_in_config = sorted(
                sb for sb in sandbox_dependencies if sb not in configured_sandboxes
            )
            if missing_in_config:
                sandbox_dependencies = set(sandbox_dependencies) - set(
                    missing_in_config
                )
                logger.warning(
                    "Removed sandbox dependencies not present in config: %s. "
                    "Configured sandboxes: %s",
                    missing_in_config,
                    sorted(configured_sandboxes),
                )

            sandboxes_to_remove = [
                s_type
                for s_type in list(opensage_session.config.sandbox.sandboxes.keys())
                if s_type not in sandbox_dependencies
            ]
            for s_type in sandboxes_to_remove:
                del opensage_session.config.sandbox.sandboxes[s_type]
                logger.warning(
                    "Removed unused sandbox '%s' from config (not in agent dependencies: %s)",
                    s_type,
                    sandbox_dependencies,
                )
    except Exception as e:
        logger.warning("Sandbox dependency pruning skipped due to error: %s", e)

    # 2) Initialize shared volumes
    opensage_session.sandboxes.initialize_shared_volumes(
        tools_top_roots=tools_top_roots,
        enabled_skills=getattr(dummy_agent, "_enabled_skills", None),
    )

    # 3) Launch sandboxes (create containers)
    await opensage_session.sandboxes.launch_all_sandboxes()

    # 4) Initialize sandboxes (tools ready)
    await opensage_session.sandboxes.initialize_all_sandboxes(continue_on_error=True)

    logger.info(f"OpenSage environment is ready for session: {session_id}")
    return session_id


def _session_store_dir(session_id: str) -> Path:
    return _SESSION_STORE_ROOT / session_id


def _sanitize_identifier(name: str) -> str:
    """Sanitize a name into a valid Python identifier (letters, digits, underscores)."""
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "").strip())
    return sanitized.strip("_") or "agent"


def _session_store_dir_for_agent(*, session_id: str, agent_name: str) -> Path:
    """Return canonical session store dir: <agent_name>_<session_id>."""
    return _SESSION_STORE_ROOT / f"{_sanitize_identifier(agent_name)}_{session_id}"


def _collect_sandbox_runtime_metadata(opensage_session) -> dict:
    """Collect attachable runtime metadata for current sandboxes."""
    backend = (
        getattr(getattr(opensage_session, "config", None), "sandbox", None)
        and opensage_session.config.sandbox.backend
    ) or "native"
    sandboxes = {}
    for sandbox_type, sandbox in opensage_session.sandboxes.list_sandboxes().items():
        entry = {"backend": backend}
        container_id = getattr(sandbox, "container_id", None)
        pod_name = getattr(sandbox, "pod_name", None)
        container_name = getattr(sandbox, "container_name", None)
        if container_id:
            entry["container_id"] = container_id
        if pod_name:
            entry["pod_name"] = pod_name
        if container_name:
            entry["container_name"] = container_name
        sandboxes[sandbox_type] = entry
    return {"backend": backend, "sandboxes": sandboxes}


async def _persist_web_session_snapshot_async(
    *,
    session_id: str,
    app_name: str,
    user_id: str,
    agent_dir: str,
    session_service: OpenSageInMemorySessionService,
    opensage_session,
) -> Path:
    """Persist ADK session + sandbox runtime metadata to local disk."""
    agent_name = Path(agent_dir).resolve().name
    store_dir = _session_store_dir_for_agent(
        session_id=session_id, agent_name=agent_name
    )
    store_dir.mkdir(parents=True, exist_ok=True)

    adk_session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if adk_session is None:
        raise click.ClickException(
            f"Cannot persist session: ADK session not found ({session_id})"
        )

    persisted_snapshot = _sanitize_adk_session_for_persistence(
        adk_session, copy_before_mutating=True
    )
    session_snapshot_path = store_dir / "adk_session.json"
    session_snapshot_path.write_text(
        persisted_snapshot.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )

    # Persist the fully resolved runtime config used by this session.
    resolved_config_path = store_dir / "resolved_config.toml"
    opensage_session.config.save_to_toml(str(resolved_config_path))

    metadata = {
        "session_id": session_id,
        "agent_name": agent_name,
        "agent_dir": str(Path(agent_dir).expanduser().resolve()),
        "app_name": app_name,
        "user_id": user_id,
        "saved_at_unix": int(time.time()),
        "resolved_config_file": resolved_config_path.name,
        "runtime": _collect_sandbox_runtime_metadata(opensage_session),
    }
    metadata_path = store_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    logger.info("Persisted OpenSage web session snapshot to %s", store_dir)
    return store_dir


async def _attach_sandboxes_from_snapshot_async(
    *,
    opensage_session,
    snapshot_metadata: dict,
) -> None:
    """Attach current OpenSage session to previously running sandboxes."""
    runtime = snapshot_metadata.get("runtime", {})
    sandbox_map = runtime.get("sandboxes", {})
    if not sandbox_map:
        logger.warning("No sandbox runtime metadata found for resume.")
        return

    for sandbox_type, entry in sandbox_map.items():
        await opensage_session.sandboxes.attach_sandbox(
            sandbox_type=sandbox_type,
            container_id=entry.get("container_id"),
            pod_name=entry.get("pod_name"),
            container_name=entry.get("container_name"),
        )


async def _load_adk_session_into_service_async(
    *,
    session_service: OpenSageInMemorySessionService,
    snapshot_path: Path,
    session_id: str,
    target_app_name: str,
    target_user_id: str,
) -> tuple[str, str]:
    """Load persisted ADK session object into the in-memory session service."""
    from google.adk.sessions.session import Session

    if not snapshot_path.exists():
        raise click.ClickException(f"Session snapshot file not found: {snapshot_path}")

    persisted = Session.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    persisted = _sanitize_adk_session_for_persistence(
        persisted, copy_before_mutating=False
    )
    # Force requested session id as source of truth.
    persisted.id = session_id
    persisted.app_name = target_app_name
    persisted.user_id = target_user_id
    app_name = target_app_name
    user_id = target_user_id

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        state=persisted.state,
        session_id=session_id,
    )
    session_service.sessions.setdefault(app_name, {}).setdefault(user_id, {})[
        session_id
    ] = persisted
    return app_name, user_id


async def _resume_environment_async(
    *,
    resume_dir: Path,
    config_path: str,
) -> tuple[str, dict, str]:
    """Restore an OpenSage session by re-attaching to existing sandboxes."""
    store_dir = resume_dir
    metadata_path = store_dir / "metadata.json"
    if not metadata_path.exists():
        raise click.ClickException(
            f"Resume metadata not found in {store_dir}: {metadata_path}"
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    session_id = metadata.get("session_id") or store_dir.name
    resolved_config_file = (
        metadata.get("resolved_config_file") or "resolved_config.toml"
    )
    resolved_config_path = store_dir / resolved_config_file
    resume_config_path = (
        str(resolved_config_path) if resolved_config_path.exists() else config_path
    )
    if resolved_config_path.exists():
        logger.info("Resuming with resolved config snapshot: %s", resolved_config_path)
    elif resume_config_path:
        logger.warning(
            "Resolved config snapshot missing in %s; fallback to CLI config: %s",
            store_dir,
            config_path,
        )
    else:
        raise click.ClickException(
            "Resolved config snapshot missing in saved session and no --config provided. "
            "Please pass --config PATH for this legacy snapshot."
        )
    opensage_session = get_opensage_session(
        opensage_session_id=session_id, config_path=resume_config_path
    )
    await _attach_sandboxes_from_snapshot_async(
        opensage_session=opensage_session,
        snapshot_metadata=metadata,
    )
    logger.info("Resumed OpenSage environment for session: %s", session_id)
    agent_dir = metadata.get("agent_dir", "")
    return session_id, metadata, agent_dir


def _resolve_latest_saved_session_dir() -> Path:
    """Return the most recently saved session directory from local store."""
    if not _SESSION_STORE_ROOT.exists():
        raise click.ClickException(
            f"No saved sessions found under {_SESSION_STORE_ROOT}."
        )

    session_dirs = [p for p in _SESSION_STORE_ROOT.iterdir() if p.is_dir()]
    if not session_dirs:
        raise click.ClickException(
            f"No saved sessions found under {_SESSION_STORE_ROOT}."
        )

    return max(session_dirs, key=lambda p: p.stat().st_mtime)


def _resolve_saved_session_dir(resume_from: Optional[str]) -> Path:
    """Resolve a saved session directory by latest, name, suffix, or path."""
    if not resume_from:
        return _resolve_latest_saved_session_dir()

    candidate = Path(resume_from).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not resolved.exists():
            raise click.ClickException(f"Saved session directory not found: {resolved}")
        if not resolved.is_dir():
            raise click.ClickException(
                f"Saved session path must be a directory: {resolved}"
            )
        return resolved

    if not _SESSION_STORE_ROOT.exists():
        raise click.ClickException(
            f"No saved sessions found under {_SESSION_STORE_ROOT}."
        )

    exact_match = (_SESSION_STORE_ROOT / resume_from).resolve()
    if exact_match.exists():
        if not exact_match.is_dir():
            raise click.ClickException(
                f"Saved session path must be a directory: {exact_match}"
            )
        return exact_match

    session_dirs = [p for p in _SESSION_STORE_ROOT.iterdir() if p.is_dir()]
    suffix_matches = sorted(
        [p for p in session_dirs if p.name.endswith(f"_{resume_from}")]
    )
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if len(suffix_matches) > 1:
        raise click.ClickException(
            "Multiple saved sessions match "
            f"'{resume_from}': {', '.join(p.name for p in suffix_matches)}"
        )

    raise click.ClickException(
        "Saved session not found. Pass a saved session directory name, "
        f"a bare session id suffix, or an absolute path under {_SESSION_STORE_ROOT}: "
        f"{resume_from}"
    )


def _drop_unmatched_function_call_events_for_resume(events):
    """Drop unresolved function_call-only events from a persisted session."""
    matched_response_ids: set[str] = set()
    for event in events:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_response = getattr(part, "function_response", None)
            if function_response and getattr(function_response, "id", None):
                matched_response_ids.add(function_response.id)

    sanitized_events = []
    for event in events:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        if not parts:
            sanitized_events.append(event)
            continue

        kept_parts = []
        removed_any = False
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if (
                function_call
                and getattr(function_call, "id", None)
                and function_call.id not in matched_response_ids
            ):
                removed_any = True
                continue
            kept_parts.append(part)

        if not removed_any:
            sanitized_events.append(event)
            continue
        if not kept_parts:
            continue

        sanitized_event = event.model_copy(deep=True)
        sanitized_event.content.parts = kept_parts
        sanitized_events.append(sanitized_event)

    return sanitized_events


def _sanitize_adk_session_for_persistence(session, *, copy_before_mutating: bool):
    """Trim unresolved function_call events from an ADK session."""
    target = session.model_copy(deep=True) if copy_before_mutating else session
    target.events = _drop_unmatched_function_call_events_for_resume(target.events or [])
    if target.events:
        last_event_ts = getattr(target.events[-1], "timestamp", None)
        if last_event_ts is not None:
            target.last_update_time = last_event_ts
    return target


def _verify_agent_module(agent_dir: str) -> None:
    """Best-effort precheck to load agent module early.

        This surfaces import errors before starting the server.

    Raises:
      ClickException: Raised when this operation fails."""
    agent_path = Path(agent_dir).resolve()
    if not agent_path.exists() or not agent_path.is_dir():
        raise click.ClickException(f"Invalid agent directory: {agent_dir}")

    agent_file = agent_path / "agent.py"
    if not agent_file.exists():
        # Not fatal for ADK loader if other agents exist; still warn loudly.
        click.secho(
            f"WARNING: agent.py not found in {agent_dir}. "
            "ADK web will still attempt to discover agents.",
            fg="yellow",
        )
        return

    # Try an import similar to Evaluation._load_mk_agent, but don't require mk_agent.
    import importlib
    import sys

    parent_dir = str(agent_path.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    module_name = f"{agent_path.name}.agent"
    try:
        importlib.import_module(module_name)
    except Exception as e:
        raise click.ClickException(
            f"Failed to import agent module '{module_name}': {e}"
        ) from e


@main.command("web")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, dir_okay=False, file_okay=True, resolve_path=True),
    required=False,
    default=None,
    help=(
        "Path to OpenSage TOML config. If omitted, defaults to "
        "<agent_dir>/config.toml when present."
    ),
)
@click.option(
    "--agent",
    "agent_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False, resolve_path=True),
    required=False,
    help="Path to the agent folder (must contain agent files).",
)
@click.option(
    "--host",
    type=str,
    default="127.0.0.1",
    show_default=True,
    help="Binding host for the server.",
)
@click.option(
    "--port",
    type=int,
    default=8000,
    show_default=True,
    help="Port for the server.",
)
@click.option(
    "--reload/--no-reload",
    default=True,
    show_default=True,
    help="Whether to enable auto reload.",
)
@click.option(
    "--log_level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
    help="Logging level for the server.",
)
@click.option(
    "--neo4j_logging/--no-neo4j_logging",
    default=False,
    show_default=True,
    help="Enable Neo4j event logging via monkey patches.",
)
@click.option(
    "--auto_cleanup",
    type=bool,
    default=False,
    show_default=True,
    help=(
        "Whether to cleanup sandboxes on process exit. "
        "When false, session snapshots are saved to "
        "~/.local/opensage/sessions/<agent_name>_<session_id>."
    ),
)
@click.option(
    "--resume",
    is_flag=True,
    default=False,
    help=(
        "Resume from the most recently saved session under ~/.local/opensage/sessions."
    ),
)
@click.option(
    "--resume-from",
    "resume_from",
    type=str,
    default=None,
    help=(
        "Resume from a specific saved session. Accepts a saved session directory "
        "name, a bare session id suffix, or an absolute path to a saved session "
        "directory. Implies --resume."
    ),
)
def cli_web(
    config_path: Optional[str],
    agent_dir: str,
    host: str,
    port: int,
    reload: bool,
    log_level: str,
    neo4j_logging: bool,
    auto_cleanup: bool,
    resume: bool,
    resume_from: Optional[str],
):
    """Starts an OpenSage-flavored Web UI: prepare environment then serve agents."""
    resume_requested = resume or bool(resume_from)
    # Normalize logging
    logging.basicConfig(level=getattr(logging, log_level.upper()))
    if not resume_requested and not agent_dir:
        raise click.ClickException("Missing required option '--agent'.")
    if not resume_requested:
        config_path = _resolve_config_path(config_path, agent_dir)

    # Optionally enable Neo4j logging (monkey patches BaseAgent/AgentTool)
    if neo4j_logging:
        try:
            from opensage.features.agent_history_tracker import enable_neo4j_logging

            enable_neo4j_logging()
            logger.info("Neo4j logging enabled.")
        except Exception as e:
            logger.error("Failed to enable Neo4j logging: %s", e)

    # 1) Prepare environment (fresh) or resume environment (attach existing)
    resume_metadata = None
    resume_store_dir: Path | None = None
    if resume_requested:
        resume_store_dir = _resolve_saved_session_dir(resume_from)
        resume_session_id = resume_store_dir.name
        resume_label = (
            f"saved session: {resume_session_id}"
            if resume_from
            else f"latest saved session: {resume_session_id}"
        )
        click.secho(f"Resuming from {resume_label}", fg="cyan")
        session_id, resume_metadata, resumed_agent_dir = asyncio.run(
            _resume_environment_async(
                resume_dir=resume_store_dir, config_path=config_path or ""
            )
        )
        if resumed_agent_dir:
            if (
                agent_dir
                and Path(agent_dir).resolve() != Path(resumed_agent_dir).resolve()
            ):
                logger.warning(
                    "CLI --agent (%s) differs from resumed agent_dir (%s); using resumed agent_dir.",
                    agent_dir,
                    resumed_agent_dir,
                )
            agent_dir = resumed_agent_dir
        elif not agent_dir:
            raise click.ClickException(
                "Resume metadata does not contain agent_dir; please pass --agent."
            )
    else:
        session_id = asyncio.run(
            _prepare_environment_async(config_path=config_path, agent_dir=agent_dir)
        )
    click.secho(f"OpenSage session prepared: {session_id}", fg="green")
    opensage_session = get_opensage_session(session_id)
    opensage_session.config.auto_cleanup = auto_cleanup

    # 2) Load the agent and bind to the prepared session (no reload/auto-discovery)
    mk_agent = _load_mk_agent_from_dir(agent_dir)
    root_agent = mk_agent(opensage_session_id=session_id)
    enabled_plugins = []
    if opensage_session and getattr(opensage_session, "config", None):
        plugins_cfg = getattr(opensage_session.config, "plugins", None)
        enabled_plugins = getattr(plugins_cfg, "enabled", []) or []
        extra_plugin_dirs = getattr(plugins_cfg, "extra_plugin_dirs", []) or []
    plugins = load_plugins(
        enabled_plugins, agent_dir=agent_dir, extra_plugin_dirs=extra_plugin_dirs
    )

    # 3) Build services (use OpenSageInMemorySessionService and pre-create the ADK session)
    # Infer app name as the parent folder of the agent directory.
    # Example: /.../examples/agents/debuger_agent -> app_name = "agents"
    raw_app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
    app_name = _sanitize_identifier(raw_app_name)
    session_service = OpenSageInMemorySessionService()

    artifact_service = InMemoryArtifactService()
    memory_service = InMemoryMemoryService()
    credential_service = InMemoryCredentialService()
    # Eval managers (local) to retain parity with ADK Dev UI features
    agents_dir_parent = os.path.dirname(agent_dir) or "."
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir_parent)
    eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=agents_dir_parent)

    # 4) Create our single-agent web server (rich endpoints, no agent reload)
    web_server = OpenSageWebServer(
        app_name=app_name,
        root_agent=root_agent,
        fixed_session_id=session_id,
        session_service=session_service,
        artifact_service=artifact_service,
        memory_service=memory_service,
        credential_service=credential_service,
        eval_sets_manager=eval_sets_manager,
        eval_set_results_manager=eval_set_results_manager,
        url_prefix=None,
        plugins=plugins,
    )
    # Pre-create or restore the ADK session using fixed session id.
    if resume_metadata:
        snapshot_path = (
            resume_store_dir or _session_store_dir(session_id)
        ) / "adk_session.json"
        restored_app_name, restored_user_id = asyncio.run(
            _load_adk_session_into_service_async(
                session_service=session_service,
                snapshot_path=snapshot_path,
                session_id=session_id,
                target_app_name=web_server.app_name,
                target_user_id="user",
            )
        )
        session_user_id = restored_user_id
    else:
        asyncio.run(
            session_service.create_session(
                app_name=web_server.app_name,
                user_id="user",
                state={"opensage_session_id": session_id},
                session_id=session_id,
            )
        )
        session_user_id = "user"
    app = web_server.get_fast_api_app(allow_origins=None, enable_dev_ui=True)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level.lower(),
    )
    click.secho(
        f"Serving OpenSage Web at http://{host}:{port} (session: {session_id})",
        fg="green",
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        if not auto_cleanup:
            store_dir = asyncio.run(
                _persist_web_session_snapshot_async(
                    session_id=session_id,
                    app_name=web_server.app_name,
                    user_id=session_user_id,
                    agent_dir=agent_dir,
                    session_service=session_service,
                    opensage_session=opensage_session,
                )
            )
            click.secho(
                f"Session snapshot saved to {store_dir}",
                fg="yellow",
            )


@main.command("dependency-check")
def cli_dependency_check():
    """Check OpenSage external dependencies.

    Checks for manually installed dependencies:
    - CodeQL: Required for CodeQL static analysis features
    - Docker: Required for native Docker sandbox backend
    - kubectl: Required for Kubernetes sandbox backend

    All dependencies are optional unless you plan to use the corresponding features.
    """
    click.secho("Checking OpenSage dependencies...\n", fg="cyan", bold=True)

    results = [
        verify_codeql(),
        verify_docker(),
        verify_kubectl(),
    ]

    success_count = sum(1 for r in results if r.success)
    total_count = len(results)

    # Display results
    for result in results:
        click.echo(f"Checking {result.name}...")
        if result.success:
            click.secho(f"  ✓ {result.message}", fg="green")
        else:
            # Use warning for optional dependencies, error for required
            if result.required:
                click.secho(f"  ✗ {result.message}", fg="red", bold=True)
            else:
                click.secho(f"  ⚠ {result.message}", fg="yellow")
                if result.optional_reason:
                    click.secho(
                        f"    Note: {result.optional_reason}", fg="yellow", dim=True
                    )
        click.echo()

    # Summary
    click.secho("=" * 60, fg="cyan")
    if success_count == total_count:
        click.secho(
            f"✓ All dependencies available ({success_count}/{total_count})",
            fg="green",
            bold=True,
        )
    else:
        click.secho(
            f"⚠ Some dependencies missing ({success_count}/{total_count} available)",
            fg="yellow",
            bold=True,
        )
        click.secho(
            "\nNote: Missing dependencies are optional unless you plan to use "
            "the corresponding features.",
            fg="yellow",
        )
    click.secho("=" * 60, fg="cyan")
