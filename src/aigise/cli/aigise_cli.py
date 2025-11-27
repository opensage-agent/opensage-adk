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
from aigise.features.aigise_in_memory_session_service import (
    AigiseInMemorySessionService,
)
from aigise.session import get_aigise_session
from aigise.toolbox.decorators import collect_sandbox_dependencies

logger = logging.getLogger(__name__)


@click.group(context_settings={"max_content_width": 240})
def main():
    """AIgiSE CLI tools."""
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
    """Prepare AIgiSE environment: create session and initialize sandboxes.

    Returns:
      The created AIgiSE session_id (used to bind agent state).
    """
    import uuid

    session_id = str(uuid.uuid4())
    logger.info(f"Initializing AIgiSE session: {session_id}")

    # 1) Create session from config
    aigise_session = get_aigise_session(
        aigise_session_id=session_id, config_path=config_path
    )

    # 1.5) Collect sandbox dependencies from the specified agent, and prune config
    try:
        mk_agent = _load_mk_agent_from_dir(agent_dir)
        dummy_agent = mk_agent(aigise_session_id=session_id)
        sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
        if (
            aigise_session.config.sandbox
            and aigise_session.config.sandbox.sandboxes
            and sandbox_dependencies
        ):
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
    aigise_session.sandboxes.initialize_shared_volumes()

    # 3) Launch sandboxes (create containers)
    await aigise_session.sandboxes.launch_all_sandboxes()

    # 4) Initialize sandboxes (tools ready)
    await aigise_session.sandboxes.initialize_all_sandboxes(continue_on_error=True)

    logger.info(f"AIgiSE environment is ready for session: {session_id}")
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
    help="Path to AIgiSE TOML config.",
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
    """Starts an AIgiSE-flavored Web UI: prepare environment then serve agents."""
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

    # 1) Prepare environment (create AIgiSE session and initialize sandboxes)
    session_id = asyncio.run(
        _prepare_environment_async(config_path=config_path, agent_dir=agent_dir)
    )
    click.secho(f"AIgiSE session prepared: {session_id}", fg="green")

    # 2) Load the agent and bind to the prepared session (no reload/auto-discovery)
    mk_agent = _load_mk_agent_from_dir(agent_dir)
    root_agent = mk_agent(aigise_session_id=session_id)

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
    )
    # Pre-create the session using the server's inferred app_name to avoid mismatch
    asyncio.run(
        session_service.create_session(
            app_name=web_server.app_name,
            user_id="user",
            state={},
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
        f"Serving AIgiSE Web at http://{host}:{port} (session: {session_id})",
        fg="green",
    )
    server = uvicorn.Server(config)
    server.run()
