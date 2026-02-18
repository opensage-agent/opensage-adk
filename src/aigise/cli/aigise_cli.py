from __future__ import annotations

import asyncio
import logging
import os
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

from aigise.cli.aigise_web_app import AigiseWebServer
from aigise.cli.dependency_check import (
    verify_codeql,
    verify_docker,
    verify_kubectl,
)
from aigise.features.aigise_in_memory_session_service import (
    AigiseInMemorySessionService,
)
from aigise.plugins import load_plugins
from aigise.session import get_aigise_session
from aigise.toolbox.decorators import collect_sandbox_dependencies
from aigise.utils.bash_tools_staging import compute_bash_tools_top_roots

logger = logging.getLogger(__name__)


@click.group(context_settings={"max_content_width": 240})
def main():
    """OpenSage CLI tools."""
    pass


def _load_mk_agent_from_dir(agent_dir: str):
    """Load mk_agent callable from an agent folder."""
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
            "Please define mk_agent(aigise_session_id: str, ...) -> Agent"
        )
    return mk_agent


async def _prepare_environment_async(config_path: str, agent_dir: str) -> str:
    """Prepare OpenSage environment: create session and initialize sandboxes.

    Returns:
      The created OpenSage session_id (used to bind agent state).
    """
    import uuid

    session_id = str(uuid.uuid4())
    logger.info(f"Initializing OpenSage session: {session_id}")

    # 1) Create session from config
    aigise_session = get_aigise_session(
        aigise_session_id=session_id, config_path=config_path
    )

    # 1.5) Collect sandbox dependencies from the specified agent, and prune config
    tools_top_roots = None
    try:
        mk_agent = _load_mk_agent_from_dir(agent_dir)
        dummy_agent = mk_agent(aigise_session_id=session_id)
        sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
        tools_top_roots = compute_bash_tools_top_roots(dummy_agent)
        if aigise_session.config.sandbox and aigise_session.config.sandbox.sandboxes:
            configured_sandboxes = set(aigise_session.config.sandbox.sandboxes.keys())

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
                for s_type in list(aigise_session.config.sandbox.sandboxes.keys())
                if s_type not in sandbox_dependencies
            ]
            for s_type in sandboxes_to_remove:
                del aigise_session.config.sandbox.sandboxes[s_type]
                logger.warning(
                    "Removed unused sandbox '%s' from config (not in agent dependencies: %s)",
                    s_type,
                    sandbox_dependencies,
                )
    except Exception as e:
        logger.warning("Sandbox dependency pruning skipped due to error: %s", e)

    # 2) Initialize shared volumes
    aigise_session.sandboxes.initialize_shared_volumes(
        tools_top_roots=tools_top_roots,
        enabled_skills=getattr(dummy_agent, "_enabled_skills", None),
    )

    # 3) Launch sandboxes (create containers)
    await aigise_session.sandboxes.launch_all_sandboxes()

    # 4) Initialize sandboxes (tools ready)
    await aigise_session.sandboxes.initialize_all_sandboxes(continue_on_error=True)

    logger.info(f"OpenSage environment is ready for session: {session_id}")
    return session_id


def _verify_agent_module(agent_dir: str) -> None:
    """Best-effort precheck to load agent module early.

    This surfaces import errors before starting the server.
    """
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
    type=click.Path(exists=True, dir_okay=False, file_okay=True, resolve_path=True),
    required=True,
    help="Path to OpenSage TOML config.",
)
@click.option(
    "--agent",
    "agent_dir",
    type=click.Path(exists=True, dir_okay=True, file_okay=False, resolve_path=True),
    required=True,
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
def cli_web(
    config_path: str,
    agent_dir: str,
    host: str,
    port: int,
    reload: bool,
    log_level: str,
    neo4j_logging: bool,
):
    """Starts an OpenSage-flavored Web UI: prepare environment then serve agents."""
    # Normalize logging
    logging.basicConfig(level=getattr(logging, log_level.upper()))

    # Optionally enable Neo4j logging (monkey patches BaseAgent/AgentTool)
    if neo4j_logging:
        try:
            from aigise.features.agent_history_tracker import enable_neo4j_logging

            enable_neo4j_logging()
            logger.info("Neo4j logging enabled.")
        except Exception as e:
            logger.error("Failed to enable Neo4j logging: %s", e)

    # 1) Prepare environment (create session and initialize sandboxes)
    session_id = asyncio.run(
        _prepare_environment_async(config_path=config_path, agent_dir=agent_dir)
    )
    click.secho(f"OpenSage session prepared: {session_id}", fg="green")

    # 2) Load the agent and bind to the prepared session (no reload/auto-discovery)
    mk_agent = _load_mk_agent_from_dir(agent_dir)
    root_agent = mk_agent(aigise_session_id=session_id)
    aigise_session = get_aigise_session(session_id)
    enabled_plugins = []
    if aigise_session and getattr(aigise_session, "config", None):
        enabled_plugins = (
            getattr(getattr(aigise_session.config, "plugins", None), "enabled", [])
            or []
        )
    plugins = load_plugins(enabled_plugins)

    # 3) Build services (use AigiseInMemorySessionService and pre-create the ADK session)
    # Infer app name as the parent folder of the agent directory.
    # Example: /.../examples/agents/debuger_agent -> app_name = "agents"
    app_name = os.path.basename(os.path.dirname(agent_dir.rstrip(os.sep)))
    session_service = AigiseInMemorySessionService()

    artifact_service = InMemoryArtifactService()
    memory_service = InMemoryMemoryService()
    credential_service = InMemoryCredentialService()
    # Eval managers (local) to retain parity with ADK Dev UI features
    agents_dir_parent = os.path.dirname(agent_dir) or "."
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir_parent)
    eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=agents_dir_parent)

    # 4) Create our single-agent web server (rich endpoints, no agent reload)
    web_server = AigiseWebServer(
        app_name=app_name,
        root_agent=root_agent,
        fixed_session_id=session_id,
        session_service=session_service,
        artifact_service=artifact_service,
        memory_service=memory_service,
        credential_service=credential_service,
        eval_sets_manager=eval_sets_manager,
        eval_set_results_manager=eval_set_results_manager,
        logo_text=None,
        logo_image_url=None,
        url_prefix=None,
        plugins=plugins,
    )
    # Pre-create the session using the server's inferred app_name to avoid mismatch
    asyncio.run(
        session_service.create_session(
            app_name=web_server.app_name,
            user_id="user",
            state={"aigise_session_id": session_id},
            session_id=session_id,
        )
    )
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
