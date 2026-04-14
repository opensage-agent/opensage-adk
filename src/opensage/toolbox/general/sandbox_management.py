"""Sandbox management tools — create, execute, stop, and list sandboxes.

These tools give the agent dynamic control over sandbox lifecycle,
similar to VulClaw's spawn/exec/stop pattern but integrated with
OpenSage's session-scoped sandbox manager.
"""

from __future__ import annotations

import logging

from google.adk.tools import ToolContext

from opensage.utils.agent_utils import get_opensage_session_from_context

logger = logging.getLogger(__name__)

# Output truncation limit (bytes)
_MAX_OUTPUT = 50 * 1024


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + f"\n... [truncated, {len(text)} bytes total]"
    return text


async def create_sandbox(
    sandbox_type: str,
    image: str,
    tool_context: ToolContext,
    working_dir: str = "/workspace",
    network: bool = False,
    privileged: bool = False,
    volumes: str = "",
    environment: str = "",
) -> dict:
    """Create and start a new sandbox (container) in the current session.

    Use this when you need an isolated environment beyond the default "main"
    sandbox — for example, a second container running a database, a target
    service, or a different OS image.

    Args:
        sandbox_type: Unique name for this sandbox (e.g. 'target', 'db', 'attacker').
            Must not collide with existing sandbox names in this session.
        image: Docker/OCI image to use (e.g. 'ubuntu:24.04', 'postgres:16').
        working_dir: Working directory inside the container.
        network: Enable network access (bridge mode). Needed for client/server setups.
        privileged: Run container in privileged mode. Use only when necessary.
        volumes: Comma-separated volume mounts (e.g. '/host/path:/container/path:ro').
        environment: Comma-separated env vars (e.g. 'KEY1=val1,KEY2=val2').
    Returns:
        dict with status and sandbox info.
    """
    session = get_opensage_session_from_context(tool_context)
    manager = session.sandboxes

    # Check for name collision
    if sandbox_type in manager.list_sandboxes():
        return {
            "success": False,
            "error": f"Sandbox '{sandbox_type}' already exists. Choose a different name or stop it first.",
            "active_sandboxes": list(manager.list_sandboxes().keys()),
        }

    try:
        from opensage.config.config_dataclass import ContainerConfig

        # Build container config
        vol_list = (
            [v.strip() for v in volumes.split(",") if v.strip()] if volumes else []
        )
        env_dict = {}
        if environment:
            for pair in environment.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    env_dict[k.strip()] = v.strip()

        container_config = ContainerConfig(
            image=image,
            working_dir=working_dir,
            network="bridge" if network else None,
            privileged=privileged,
            volumes=vol_list,
            environment=env_dict,
        )

        # Inject the session's existing shared-volume mounts into the new
        # sandbox config. The native backend explicitly ignores the volume_id
        # parameters to launch_all_sandboxes() — it expects the mounts to
        # already be present in ContainerConfig.volumes.
        if manager._scripts_volume_id:
            container_config.volumes.append(
                f"{manager._scripts_volume_id}:/sandbox_scripts:ro"
            )
        if manager._shared_volume_id:
            container_config.volumes.append(f"{manager._shared_volume_id}:/shared:rw")
        if manager._tools_volume_id:
            container_config.volumes.append(
                f"{manager._tools_volume_id}:/bash_tools:rw"
            )

        # Register config in session so the manager can find it
        if hasattr(session.config, "sandbox") and session.config.sandbox is not None:
            session.config.sandbox.add_or_update_sandbox(sandbox_type, container_config)

        from opensage.sandbox.factory import get_backend_class

        backend_type = getattr(session.config.sandbox, "backend", "native")
        backend_class = get_backend_class(backend_type, session.config)

        # Launch the new sandbox. The volume_id kwargs are passed for
        # signature parity with the manager but are ignored by the native
        # backend (mounts already injected into container_config.volumes
        # above).
        launch_kwargs: dict = {
            "session_id": manager.opensage_session_id,
            "sandbox_configs": {sandbox_type: container_config},
        }
        if manager._shared_volume_id:
            launch_kwargs["shared_volume_id"] = manager._shared_volume_id
        if manager._scripts_volume_id:
            launch_kwargs["scripts_volume_id"] = manager._scripts_volume_id
        if manager._tools_volume_id:
            launch_kwargs["tools_volume_id"] = manager._tools_volume_id

        sandbox_instances = await backend_class.launch_all_sandboxes(**launch_kwargs)

        if sandbox_type not in sandbox_instances:
            return {
                "success": False,
                "error": f"Backend failed to create sandbox '{sandbox_type}'",
            }

        sandbox_instance = sandbox_instances[sandbox_type]
        manager._sandboxes[sandbox_type] = sandbox_instance

        # Initialize using the full sandbox map so initializers that reference
        # peer sandboxes (e.g. asserting 'main' is present) keep working.
        await backend_class.initialize_all_sandboxes(  # type: ignore[attr-defined]
            manager._sandboxes, continue_on_error=True
        )

        # Run skill dependency installers for parity with attach_sandbox /
        # OpenSageSandboxManager.initialize_all_sandboxes.
        if manager.enabled_skills is not None:
            try:
                from opensage.sandbox.skill_deps import prepare_skill_deps

                await prepare_skill_deps(sandbox_instance, manager.enabled_skills)
            except Exception as skill_exc:
                logger.warning(
                    "Skill deps prep failed for sandbox '%s': %s",
                    sandbox_type,
                    skill_exc,
                )

        logger.info(
            "Created sandbox '%s' (image=%s) for session %s",
            sandbox_type,
            image,
            manager.opensage_session_id,
        )

        return {
            "success": True,
            "sandbox_type": sandbox_type,
            "image": image,
            "working_dir": working_dir,
            "network": network,
            "active_sandboxes": list(manager.list_sandboxes().keys()),
        }

    except Exception as e:
        logger.exception("Failed to create sandbox '%s': %s", sandbox_type, e)
        return {"success": False, "error": str(e)}


async def exec_in_sandbox(
    sandbox_type: str,
    command: str,
    tool_context: ToolContext,
    timeout: int = 120,
) -> str:
    """Execute a command inside a sandbox.

    Works with any sandbox in the current session — both pre-configured ones
    (like 'main') and dynamically created ones (via create_sandbox).

    Args:
        sandbox_type: Name of the sandbox to execute in (e.g. 'main', 'target').
        command: Shell command to execute.
        timeout: Command timeout in seconds.
    Returns:
        Command output (stdout + stderr). Truncated at 50KB.
    """
    session = get_opensage_session_from_context(tool_context)
    manager = session.sandboxes
    sandboxes = manager.list_sandboxes()

    if sandbox_type not in sandboxes:
        return (
            f"Error: sandbox '{sandbox_type}' not found. "
            f"Active sandboxes: {list(sandboxes.keys())}"
        )

    try:
        sandbox = manager.get_sandbox(sandbox_type)
        output, exit_code = sandbox.run_command_in_container(command, timeout=timeout)
        result = output or ""
        if exit_code != 0:
            result += f"\n[exit_code: {exit_code}]"
        return _truncate(result) or "(no output)"
    except Exception as e:
        logger.exception("exec_in_sandbox('%s') failed: %s", sandbox_type, e)
        return f"Error: {e}"


def stop_sandbox(
    sandbox_type: str,
    tool_context: ToolContext,
) -> dict:
    """Stop and remove a sandbox from the current session.

    This permanently destroys the container. Any unsaved data inside is lost.
    Cannot stop the 'main' sandbox — it is managed by the session lifecycle.

    Args:
        sandbox_type: Name of the sandbox to stop.
    Returns:
        dict with status.
    """
    if sandbox_type == "main":
        return {
            "success": False,
            "error": "Cannot stop the 'main' sandbox. It is managed by the session lifecycle.",
        }

    session = get_opensage_session_from_context(tool_context)
    manager = session.sandboxes

    if sandbox_type not in manager.list_sandboxes():
        return {
            "success": False,
            "error": f"Sandbox '{sandbox_type}' not found.",
            "active_sandboxes": list(manager.list_sandboxes().keys()),
        }

    try:
        removed = manager.remove_sandbox(sandbox_type)
        if removed:
            logger.info(
                "Stopped sandbox '%s' for session %s",
                sandbox_type,
                manager.opensage_session_id,
            )
            return {
                "success": True,
                "stopped": sandbox_type,
                "active_sandboxes": list(manager.list_sandboxes().keys()),
            }
        return {"success": False, "error": f"Failed to remove sandbox '{sandbox_type}'"}
    except Exception as e:
        logger.exception("stop_sandbox('%s') failed: %s", sandbox_type, e)
        return {"success": False, "error": str(e)}


def list_active_sandboxes(
    tool_context: ToolContext,
) -> dict:
    """List all active sandboxes in the current session.

    Returns:
        dict with sandbox names, their states, and basic info.
    """
    session = get_opensage_session_from_context(tool_context)
    manager = session.sandboxes
    sandboxes = manager.list_sandboxes()

    info = {}
    for name, sandbox in sandboxes.items():
        entry: dict = {
            "state": sandbox.state.value if hasattr(sandbox, "state") else "unknown",
        }
        # Real sandboxes store the config on `container_config_obj`
        # (see BaseSandbox.__init__). Fall back to `container_config` for
        # tests/mocks that may use the simpler attribute name.
        cfg = getattr(sandbox, "container_config_obj", None) or getattr(
            sandbox, "container_config", None
        )
        if cfg is not None:
            image = getattr(cfg, "image", None)
            working_dir = getattr(cfg, "working_dir", None)
            if image:
                entry["image"] = image
            if working_dir:
                entry["working_dir"] = working_dir
        info[name] = entry

    return {
        "count": len(info),
        "sandboxes": info,
    }
