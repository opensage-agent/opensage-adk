from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
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
from opensage.memory.file_based.short_term import build_root_session_state
from opensage.plugins import load_plugins
from opensage.session import cleanup_opensage_session, get_opensage_session
from opensage.toolbox.sandbox_requirements import collect_sandbox_dependencies
from opensage.utils.agent_utils import sanitize_agent_name
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
        opensage_session_id=session_id,
        config_path=config_path,
        agent_dir=agent_dir,
    )

    # 1.5) Collect sandbox dependencies from the specified agent, and prune config
    tools_top_roots = None
    mk_agent = _load_mk_agent_from_dir(agent_dir)
    dummy_agent = mk_agent(opensage_session_id=session_id)

    try:
        sandbox_dependencies = collect_sandbox_dependencies(
            dummy_agent, config=opensage_session.config
        )
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


def _is_saved_session_dir(path: Path) -> bool:
    """Return whether a directory is a resumable saved session snapshot."""
    return path.is_dir() and (path / "metadata.json").exists()


def _persist_session_snapshot(
    *,
    opensage_session,
    agent_dir: str,
    app_name: str,
    user_id: str,
    root_agent_name: str | None,
) -> Path:
    from opensage.orchestration.persistence import persist_session_snapshot

    return persist_session_snapshot(
        opensage_session=opensage_session,
        agent_dir=agent_dir,
        app_name=app_name,
        user_id=user_id,
        root_agent_name=root_agent_name,
    )


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
    root_agent,
) -> tuple[str, str]:
    """Load persisted ADK session object into the in-memory session service."""
    from google.adk.sessions.session import Session

    if not snapshot_path.exists():
        raise click.ClickException(f"Session snapshot file not found: {snapshot_path}")

    from opensage.orchestration.persistence import sanitize_adk_session

    persisted = Session.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    persisted = sanitize_adk_session(persisted, copy=False)
    # Force requested session id as source of truth.
    persisted.id = session_id
    persisted.app_name = target_app_name
    persisted.user_id = target_user_id
    persisted_state = persisted.state if isinstance(persisted.state, dict) else {}
    persisted_state["opensage_session_id"] = session_id
    expected_root_state = build_root_session_state(
        opensage_session_id=session_id,
        session_id=session_id,
        agent_name=getattr(root_agent, "name", "agent"),
    )
    expected_mem_dir = expected_root_state.get("_mem_agent_dir")
    persisted_mem_dir = persisted_state.get("_mem_agent_dir")
    if expected_mem_dir is not None and persisted_mem_dir != expected_mem_dir:
        raise click.ClickException(
            "Cannot resume file-memory session: persisted root session state is "
            f"missing the expected `{expected_mem_dir}` directory."
        )
    if expected_mem_dir is None:
        persisted_state.pop("_mem_agent_dir", None)
    persisted.state = persisted_state
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
    agent_dir = metadata.get("agent_dir", "")
    opensage_session = get_opensage_session(
        opensage_session_id=session_id,
        config_path=resume_config_path,
        agent_dir=agent_dir or None,
    )
    await _attach_sandboxes_from_snapshot_async(
        opensage_session=opensage_session,
        snapshot_metadata=metadata,
    )
    logger.info("Resumed OpenSage environment for session: %s", session_id)
    return session_id, metadata, agent_dir


def _resolve_latest_saved_session_dir() -> Path:
    """Return the most recently saved session directory from local store."""
    if not _SESSION_STORE_ROOT.exists():
        raise click.ClickException(
            f"No saved sessions found under {_SESSION_STORE_ROOT}."
        )

    session_dirs = [
        p for p in _SESSION_STORE_ROOT.iterdir() if _is_saved_session_dir(p)
    ]
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
        if not _is_saved_session_dir(resolved):
            raise click.ClickException(
                f"Saved session metadata not found in {resolved}: "
                f"{resolved / 'metadata.json'}"
            )
        return resolved

    if not _SESSION_STORE_ROOT.exists():
        raise click.ClickException(
            f"No saved sessions found under {_SESSION_STORE_ROOT}."
        )

    # Suffix match: find dirs whose name ends with the given string
    suffix_matches = [
        p
        for p in _SESSION_STORE_ROOT.iterdir()
        if _is_saved_session_dir(p) and p.name.endswith(resume_from)
    ]

    if len(suffix_matches) == 1:
        return suffix_matches[0]

    if len(suffix_matches) > 1:
        names = ", ".join(sorted(p.name for p in suffix_matches))
        raise click.ClickException(
            f"Multiple saved sessions match '{resume_from}': {names}"
        )

    # Exact directory name (no suffix match found)
    exact_match = (_SESSION_STORE_ROOT / resume_from).resolve()
    if exact_match.exists():
        if not exact_match.is_dir():
            raise click.ClickException(
                f"Saved session path must be a directory: {exact_match}"
            )
        if not _is_saved_session_dir(exact_match):
            raise click.ClickException(
                f"Saved session metadata not found in {exact_match}: "
                f"{exact_match / 'metadata.json'}"
            )
        return exact_match

    raise click.ClickException(
        "Saved session not found. Pass a session id or an absolute path "
        f"under {_SESSION_STORE_ROOT}: {resume_from}"
    )


def _is_reviewable_session_dir(path: Path) -> bool:
    """A directory is reviewable if it has instances/ with at least one traj.json."""
    inst = path / "instances"
    if not inst.is_dir():
        return False
    return any(inst.rglob("traj.json"))


def _resolve_review_session_dir(session_path: Optional[str]) -> Path:
    """Resolve a session directory for review (no metadata.json required)."""
    if not session_path:
        if not _SESSION_STORE_ROOT.exists():
            raise click.ClickException(
                f"No sessions found under {_SESSION_STORE_ROOT}."
            )
        dirs = [
            p
            for p in _SESSION_STORE_ROOT.iterdir()
            if p.is_dir() and _is_reviewable_session_dir(p)
        ]
        if not dirs:
            raise click.ClickException(
                f"No reviewable sessions found under {_SESSION_STORE_ROOT}."
            )
        return max(dirs, key=lambda p: p.stat().st_mtime)

    candidate = Path(session_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not resolved.exists():
            raise click.ClickException(f"Session directory not found: {resolved}")
        if not resolved.is_dir():
            raise click.ClickException(f"Session path must be a directory: {resolved}")
        return resolved

    if not _SESSION_STORE_ROOT.exists():
        raise click.ClickException(f"No sessions found under {_SESSION_STORE_ROOT}.")
    exact_match = (_SESSION_STORE_ROOT / session_path).resolve()
    if exact_match.exists() and exact_match.is_dir():
        return exact_match

    suffix_matches = [
        p
        for p in _SESSION_STORE_ROOT.iterdir()
        if p.is_dir() and p.name.endswith(session_path)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if len(suffix_matches) > 1:
        raise click.ClickException(
            f"Ambiguous session suffix {session_path!r}: "
            + ", ".join(p.name for p in suffix_matches)
        )

    raise click.ClickException(
        f"Session not found: {session_path} (looked under {_SESSION_STORE_ROOT})"
    )


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
    "--log_level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
    help="Logging level for the server.",
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
    log_level: str,
    auto_cleanup: bool,
    resume: bool,
    resume_from: Optional[str],
):
    """Starts an OpenSage-flavored Web UI: prepare environment then serve agents."""
    session_id: str | None = None
    opensage_session = None
    session_service: OpenSageInMemorySessionService | None = None
    web_server: OpenSageWebServer | None = None
    session_user_id = "user"
    resume_requested = resume or bool(resume_from)

    def _cli_signal_handler(signum, _frame):
        logger.warning(
            "Received signal %s, interrupting OpenSage web server.",
            signum,
        )
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    try:
        # Normalize logging
        logging.basicConfig(level=getattr(logging, log_level.upper()))
        if not resume_requested and not agent_dir:
            raise click.ClickException("Missing required option '--agent'.")
        if not resume_requested:
            config_path = _resolve_config_path(config_path, agent_dir)

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

        # 3) Use the ADK services owned by OpenSageSession (one set per environment).
        # Infer app name as the parent folder of the agent directory.
        # Example: /.../agent_library/agents/debuger_agent -> app_name = "agents"
        raw_app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
        app_name = sanitize_agent_name(raw_app_name)
        session_service = opensage_session.session_service
        artifact_service = opensage_session.artifact_service
        memory_service = opensage_session.memory_service
        credential_service = opensage_session.credential_service

        # 3b) Register root agent and every OpenSage subagent under it with the
        # AgentManager, so that call_subagent / continue_agent_instance /
        # send_message tools can resolve names in this environment. The
        # dispatcher is started inside the uvicorn lifespan (see _lifespan
        # below) so it runs on the same event loop that will serve requests.
        opensage_session.agent_manager.set_app_name(app_name)
        opensage_session.agent_manager.set_base_plugins(plugins)
        opensage_session.agent_manager.register_agent_tree(root_agent)

        # Eval managers (local) to retain parity with ADK Dev UI features
        agents_dir_parent = os.path.dirname(agent_dir) or "."
        eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir_parent)
        eval_set_results_manager = LocalEvalSetResultsManager(
            agents_dir=agents_dir_parent
        )

        # 4) Load fake-user function from config (if configured)
        fake_user_fn = None
        fake_user_cfg = getattr(opensage_session.config, "fake_user", None)
        if fake_user_cfg and getattr(fake_user_cfg, "python_file", None):
            from opensage.orchestration.fake_user import load_fake_user_from_file

            fake_user_fn = load_fake_user_from_file(
                fake_user_cfg.python_file,
                agent_dir=agent_dir,
            )
            logger.info("Loaded fake_user from %s", fake_user_cfg.python_file)

        # 5) Create our single-agent web server (rich endpoints, no agent reload)
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
            fake_user_fn=fake_user_fn,
        )
        # Pre-create or restore the ADK session using fixed session id.
        # Both paths go through agent_manager.spawn — for resume, the legacy
        # snapshot is first injected into the session_service and spawn adopts
        # the existing session; otherwise spawn creates a fresh one.
        if resume_metadata:
            snapshot_path = (
                resume_store_dir or _session_store_dir(session_id)
            ) / "adk_session.json"
            _, restored_user_id = asyncio.run(
                _load_adk_session_into_service_async(
                    session_service=session_service,
                    snapshot_path=snapshot_path,
                    session_id=session_id,
                    target_app_name=web_server.app_name,
                    target_user_id="user",
                    root_agent=root_agent,
                )
            )
            session_user_id = restored_user_id
        else:
            session_user_id = "user"

        asyncio.run(
            opensage_session.agent_manager.spawn(
                agent_name=root_agent.name,
                session_id=session_id,
                parent_session_id=None,
            )
        )

        # Lifespan owns the dispatcher task. Anything that creates persistent
        # event-loop state (Tasks/Queues) must run here so it lives on
        # uvicorn's loop — which is also the loop that serves requests and
        # therefore the loop where call_subagent / send_message-triggered
        # invocations execute. spawn() does NOT belong here: it does internal
        # awaits but creates only disk + dict state, no loop-bound resources.
        @contextlib.asynccontextmanager
        async def _lifespan(app):
            started = False
            try:
                await opensage_session.agent_manager.start()
                started = True
                yield
            finally:
                if started:
                    await opensage_session.agent_manager.shutdown()

        app = web_server.get_fast_api_app(
            allow_origins=None, enable_dev_ui=True, lifespan=_lifespan
        )

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level.lower(),
        )
        click.secho(
            f"Serving OpenSage Web at http://{host}:{port} (session: {session_id})",
            fg="green",
        )
        server = uvicorn.Server(config)
        server.capture_signals = lambda: contextlib.nullcontext()
        signal.signal(signal.SIGINT, _cli_signal_handler)
        signal.signal(signal.SIGTERM, _cli_signal_handler)
        server.run()
    finally:
        if session_id is not None:
            exiting_with_exception = sys.exc_info()[0] is not None
            if auto_cleanup:
                try:
                    cleanup_opensage_session(session_id)
                except Exception:
                    logger.exception(
                        "Failed to clean up OpenSage session during web shutdown: %s",
                        session_id,
                    )
            elif web_server is not None and opensage_session is not None:
                # Block SIGINT/SIGTERM during persist so a second ctrl+c
                # cannot interrupt the snapshot write.
                prev_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
                prev_sigterm = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    store_dir = _persist_session_snapshot(
                        opensage_session=opensage_session,
                        agent_dir=agent_dir,
                        app_name=web_server.app_name,
                        user_id=session_user_id,
                        root_agent_name=getattr(root_agent, "name", "agent"),
                    )
                    click.secho(
                        f"Session snapshot saved to {store_dir}",
                        fg="yellow",
                    )
                except Exception:
                    if not exiting_with_exception:
                        raise
                    logger.exception(
                        "Failed to persist OpenSage web session snapshot during shutdown: %s",
                        session_id,
                    )
                finally:
                    signal.signal(signal.SIGINT, prev_sigint)
                    signal.signal(signal.SIGTERM, prev_sigterm)
            else:
                logger.warning(
                    "Skipping session snapshot for %s because web session state was not fully initialized.",
                    session_id,
                )


@main.command("run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, dir_okay=False, file_okay=True, resolve_path=True),
    required=False,
    default=None,
    help="Path to OpenSage TOML config.",
)
@click.option(
    "--agent",
    "agent_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False, resolve_path=True),
    required=False,
    help="Path to the agent folder (must contain agent files).",
)
@click.option(
    "--log_level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
    help="Logging level.",
)
@click.option(
    "--auto_cleanup",
    type=bool,
    default=False,
    show_default=True,
    help="Whether to cleanup sandboxes on process exit.",
)
@click.option(
    "--resume",
    is_flag=True,
    default=False,
    help="Resume from the most recently saved session.",
)
@click.option(
    "--resume-from",
    "resume_from",
    type=str,
    default=None,
    help="Resume from a specific saved session.",
)
@click.option(
    "--prompt",
    "prompt",
    type=str,
    default=None,
    help="Send a single prompt, run to completion, then exit (non-interactive).",
)
def cli_run(
    config_path: Optional[str],
    agent_dir: str,
    log_level: str,
    auto_cleanup: bool,
    resume: bool,
    resume_from: Optional[str],
    prompt: Optional[str],
):
    """Run an agent interactively in the terminal (no web UI)."""
    from google.genai import types

    session_id: str | None = None
    opensage_session = None
    root_agent = None
    session_user_id = "user"
    resume_requested = resume or bool(resume_from)

    def _cli_signal_handler(signum, _frame):
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    try:
        logging.basicConfig(level=getattr(logging, log_level.upper()))
        if not resume_requested and not agent_dir:
            raise click.ClickException("Missing required option '--agent'.")
        if not resume_requested:
            config_path = _resolve_config_path(config_path, agent_dir)

        resume_metadata = None
        resume_store_dir: Path | None = None
        if resume_requested:
            resume_store_dir = _resolve_saved_session_dir(resume_from)
            resume_session_id = resume_store_dir.name
            click.secho(f"Resuming from session: {resume_session_id}", fg="cyan")
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
                        "CLI --agent (%s) differs from resumed agent_dir (%s); using resumed.",
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

        raw_app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
        app_name = sanitize_agent_name(raw_app_name)
        session_service = opensage_session.session_service

        opensage_session.agent_manager.set_app_name(app_name)
        opensage_session.agent_manager.set_base_plugins(plugins)
        opensage_session.agent_manager.register_agent_tree(root_agent)

        fake_user_fn = None
        fake_user_cfg = getattr(opensage_session.config, "fake_user", None)
        if fake_user_cfg and getattr(fake_user_cfg, "python_file", None):
            from opensage.orchestration.fake_user import load_fake_user_from_file

            fake_user_fn = load_fake_user_from_file(
                fake_user_cfg.python_file,
                agent_dir=agent_dir,
            )
            logger.info("Loaded fake_user from %s", fake_user_cfg.python_file)

        if resume_metadata:
            snapshot_path = (
                resume_store_dir or _session_store_dir(session_id)
            ) / "adk_session.json"
            _, restored_user_id = asyncio.run(
                _load_adk_session_into_service_async(
                    session_service=session_service,
                    snapshot_path=snapshot_path,
                    session_id=session_id,
                    target_app_name=app_name,
                    target_user_id="user",
                    root_agent=root_agent,
                )
            )
            session_user_id = restored_user_id
        else:
            session_user_id = "user"

        asyncio.run(
            opensage_session.agent_manager.spawn(
                agent_name=root_agent.name,
                session_id=session_id,
                parent_session_id=None,
            )
        )

        def _print_event(event):
            if not event.content or not event.content.parts:
                return
            text = "".join(part.text or "" for part in event.content.parts)
            fn_calls = [
                part.function_call for part in event.content.parts if part.function_call
            ]
            if text:
                click.echo(click.style(f"[{event.author}]: ", fg="cyan") + text)
            for fc in fn_calls:
                click.echo(
                    click.style(f"[{event.author}] ", fg="yellow")
                    + f"→ {fc.name}({dict(fc.args) if fc.args else ''})"
                )

        async def _run_turn_and_print(manager, msg):
            async for event in manager.run_turn_stream(session_id, msg):
                _print_event(event)

        async def _drain_inbox_loop(manager):
            """Poll inbox while user is typing; run turn when messages arrive."""
            instance = manager.get_instance(session_id)
            while True:
                await asyncio.sleep(0.5)
                if not instance or instance.state.value != "sleeping":
                    continue
                if not await instance.inbox.has_pending():
                    continue
                click.echo(
                    click.style("[system]: ", fg="yellow")
                    + "inbox message received, processing..."
                )
                await _run_turn_and_print(manager, "")
                if fake_user_fn:
                    while True:
                        snap = await session_service.get_session(
                            app_name=app_name,
                            user_id=session_user_id,
                            session_id=session_id,
                        )
                        next_msg = await fake_user_fn(snap)
                        if next_msg is None:
                            break
                        click.echo(
                            click.style("[fake_user]: ", fg="magenta") + next_msg
                        )
                        await _run_turn_and_print(
                            manager,
                            types.Content(
                                role="user",
                                parts=[types.Part(text=next_msg)],
                            ),
                        )

        async def _run_turn_with_fake_user(manager, msg):
            """Run one turn, then loop fake_user if configured."""
            current_msg = msg
            while True:
                await _run_turn_and_print(manager, current_msg)
                if not fake_user_fn:
                    break
                session_snapshot = await session_service.get_session(
                    app_name=app_name,
                    user_id=session_user_id,
                    session_id=session_id,
                )
                next_msg = await fake_user_fn(session_snapshot)
                if next_msg is None:
                    break
                click.echo(click.style("[fake_user]: ", fg="magenta") + next_msg)
                current_msg = types.Content(
                    role="user",
                    parts=[types.Part(text=next_msg)],
                )

        async def _main_loop():
            manager = opensage_session.agent_manager
            manager.mark_externally_managed(session_id)
            await manager.start()
            inbox_task = asyncio.create_task(
                _drain_inbox_loop(manager), name="cli-inbox-poll"
            )
            try:
                if prompt:
                    click.echo(click.style("[user]: ", fg="green") + prompt)
                    await _run_turn_with_fake_user(manager, prompt)
                else:
                    click.secho(
                        f"Running agent {root_agent.name}, type 'exit' to quit.",
                        fg="green",
                    )
                    while True:
                        try:
                            query = await asyncio.get_event_loop().run_in_executor(
                                None, lambda: input("[user]: ")
                            )
                        except EOFError:
                            break
                        if not query or not query.strip():
                            continue
                        if query.strip() == "exit":
                            break
                        await _run_turn_with_fake_user(manager, query)
            finally:
                inbox_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await inbox_task
                manager.unmark_externally_managed(session_id)
                await manager.shutdown()

        signal.signal(signal.SIGINT, _cli_signal_handler)
        signal.signal(signal.SIGTERM, _cli_signal_handler)
        asyncio.run(_main_loop())

    except (KeyboardInterrupt, SystemExit):
        click.secho("\nShutting down...", fg="yellow")
    finally:
        if session_id is not None:
            if auto_cleanup:
                try:
                    cleanup_opensage_session(session_id)
                except Exception:
                    logger.exception("Failed to clean up session: %s", session_id)
            elif opensage_session is not None:
                prev_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
                prev_sigterm = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    store_dir = _persist_session_snapshot(
                        opensage_session=opensage_session,
                        agent_dir=agent_dir,
                        app_name=app_name,
                        user_id=session_user_id,
                        root_agent_name=getattr(root_agent, "name", "agent"),
                    )
                    click.secho(f"Session snapshot saved to {store_dir}", fg="yellow")
                except Exception:
                    logger.exception(
                        "Failed to persist session snapshot: %s", session_id
                    )
                finally:
                    signal.signal(signal.SIGINT, prev_sigint)
                    signal.signal(signal.SIGTERM, prev_sigterm)


@main.command("review")
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
    "--log_level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
    help="Logging level for the server.",
)
@click.argument("session_path", required=False, default=None)
def cli_review(
    host: str,
    port: int,
    log_level: str,
    session_path: Optional[str],
):
    """Read-only session viewer — no LLM, no sandbox, no core services.

    SESSION_PATH is a session directory under ~/.local/opensage/sessions/
    (name, suffix, or absolute path).  If omitted, opens the most recent one.
    """
    logging.basicConfig(level=getattr(logging, log_level.upper()))

    from google.adk.agents.base_agent import BaseAgent as _BaseAgent
    from google.adk.sessions.session import Session

    from opensage.orchestration.persistence import sanitize_adk_session

    store_dir = _resolve_review_session_dir(session_path)

    inst_root = store_dir / "instances"
    if not inst_root.exists():
        raise click.ClickException(
            f"No instances/ directory found in {store_dir}. Nothing to review."
        )

    session_service = OpenSageInMemorySessionService()
    artifact_service = InMemoryArtifactService()
    memory_service = InMemoryMemoryService()
    credential_service = InMemoryCredentialService()

    app_name = "app"
    root_agent_name = "agent"
    root_session_id = None
    loaded = 0
    for traj_path in inst_root.rglob("traj.json"):
        inst_dir = traj_path.parent
        inst_meta_path = inst_dir / "metadata.json"
        if inst_meta_path.exists():
            inst_meta = json.loads(inst_meta_path.read_text(encoding="utf-8"))
            sid = inst_meta.get("session_id", inst_dir.name)
            if inst_meta.get("parent_session_id") is None:
                root_agent_name = inst_meta.get("agent_name", root_agent_name)
                app_name = inst_meta.get("app_name", app_name)
                root_session_id = sid
        else:
            sid = inst_dir.name

        session = Session.model_validate_json(traj_path.read_text(encoding="utf-8"))
        session = sanitize_adk_session(session, copy=False)
        session.id = sid
        session.app_name = app_name
        session.user_id = "user"
        session_service.inject_session(session)
        loaded += 1

    session_id = root_session_id or store_dir.name

    click.secho(f"Review mode — session {session_id}", fg="cyan")
    click.secho("Read-only viewer (no LLM, no sandbox).", fg="yellow")
    click.secho(f"Loaded {loaded} session(s) from instances/.", fg="cyan")

    stub_agent = _BaseAgent(name=root_agent_name)

    web_server = OpenSageWebServer(
        app_name=app_name,
        root_agent=stub_agent,
        fixed_session_id=session_id,
        session_service=session_service,
        artifact_service=artifact_service,
        memory_service=memory_service,
        credential_service=credential_service,
        fake_user_fn=None,
    )

    app = web_server.get_fast_api_app(
        allow_origins=None,
        enable_dev_ui=True,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
    )
    click.secho(
        f"Review UI at http://{host}:{port} (session: {session_id})",
        fg="green",
    )
    server = uvicorn.Server(config)
    server.run()


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
