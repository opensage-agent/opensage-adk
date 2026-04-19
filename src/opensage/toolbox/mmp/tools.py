"""OpenSage Python wrappers around the `mmp` CLI.

Note on installation: these tools only *invoke* `mmp` inside the main sandbox;
they do not install it. The `mmp` binary is provisioned by the bash skill at
`src/opensage/bash_tools/mmp` via its `deps/install.sh`, which only runs when
`"mmp"` is in `OpenSageAgent(enabled_skills=...)` (see
`opensage.sandbox.skill_deps.prepare_skill_deps`). If you wire these tools in
without enabling the skill, calls will fail with `mmp: command not found`
unless `mmp` is preinstalled in the main sandbox image.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from typing import Any

from google.adk.tools.tool_context import ToolContext

from opensage.utils.agent_utils import get_sandbox_from_context

_DEFAULT_MMP_TIMEOUT_SECONDS = 60


def _parse_output(stdout: str) -> Any:
    stripped = stdout.strip()
    if not stripped:
        return ""
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stdout
    return stdout


def _parse_table(stdout: str) -> list[dict[str, str]]:
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if re.fullmatch(r"[\-\s]+", line.strip()) and idx > 0:
            headers = re.split(r"\s{2,}", lines[idx - 1].strip())
            rows: list[dict[str, str]] = []
            for row_line in lines[idx + 1 :]:
                values = re.split(
                    r"\s{2,}", row_line.strip(), maxsplit=len(headers) - 1
                )
                if len(values) != len(headers):
                    continue
                rows.append(dict(zip(headers, values, strict=True)))
            return rows
    return []


def _parse_transport_label(label: str) -> dict[str, Any]:
    match = re.match(r"^\[(?P<transport>[^\]]+)\]\s*(?P<target>.*)$", label.strip())
    if not match:
        return {
            "transport": label.strip() or None,
            "url": None,
            "command": None,
            "parse_error": None,
            "raw_target": None,
        }

    transport = match.group("transport").strip()
    target = match.group("target").strip()
    if transport == "stdio":
        try:
            command = shlex.split(target) if target else []
            parse_error = None
        except ValueError as exc:
            command = []
            parse_error = str(exc)
        return {
            "transport": transport,
            "url": None,
            "command": command,
            "parse_error": parse_error,
            "raw_target": target or None,
        }
    return {
        "transport": transport,
        "url": target or None,
        "command": None,
        "parse_error": None,
        "raw_target": None,
    }


def _servers_from_ls_output(stdout: str) -> list[dict[str, Any]]:
    servers = []
    for row in _parse_table(stdout):
        transport_info = _parse_transport_label(row.get("TRANSPORT", ""))
        server = {
            "name": row.get("SERVER", ""),
            "transport": transport_info["transport"],
            "url": transport_info["url"],
            "command": transport_info["command"],
        }
        if transport_info["raw_target"] is not None:
            server["raw_target"] = transport_info["raw_target"]
        if transport_info["parse_error"] is not None:
            server["parse_error"] = transport_info["parse_error"]
        servers.append(server)
    return servers


async def _run_mmp_in_sandbox(
    command: str,
    tool_context: ToolContext,
    timeout: int = _DEFAULT_MMP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    sandbox = get_sandbox_from_context(tool_context, "main")
    output, exit_code = await sandbox.arun_command_in_container(
        f"mmp {command}", timeout=timeout
    )
    return {
        "success": exit_code == 0,
        "exit_code": exit_code,
        "stdout": output,
        "stderr": "" if exit_code == 0 else output,
        "output": _parse_output(output),
    }


async def _read_toml_from_sandbox(
    path: str, tool_context: ToolContext
) -> dict[str, Any]:
    sandbox = get_sandbox_from_context(tool_context, "main")
    content = await sandbox.aextract_file_from_container(path)
    return tomllib.loads(content)


def _read_servers_from_toml(raw: dict[str, Any]) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    for name, server in sorted(raw.get("servers", {}).items()):
        servers.append(
            {
                "name": name,
                "transport": server.get("transport"),
                "url": server.get("url"),
                "command": server.get("command"),
            }
        )
    return servers


async def mmp_load_config(
    config_path: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Load an MMP gateway configuration file inside the main sandbox.

    Use this tool before other MMP tools when the sandbox has not loaded the
    target gateway config yet. Call it with a config path that is valid inside
    the sandbox.

    Args:
        config_path: Path to the MMP gateway TOML file inside the main sandbox.
        tool_context: OpenSage tool context used to locate the sandbox.

    Returns:
        dict[str, Any]: Structured result with a human-readable `message`,
        loaded `servers`. On failure, returns `success=False`
        with both `message` and `error`.
    """
    cmd = f"load {shlex.quote(config_path)}"
    result = await _run_mmp_in_sandbox(cmd, tool_context)
    if not result["success"]:
        error = str(result["stdout"]).strip() or "mmp load failed"
        return {
            "success": False,
            "message": f"Failed to load MMP config from {config_path}: {error}",
            "error": error,
        }

    try:
        raw = await _read_toml_from_sandbox(config_path, tool_context)
        servers = _read_servers_from_toml(raw)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        error = str(exc).strip() or "failed to read or parse TOML config"
        return {
            "success": False,
            "message": (
                f"Failed to read or parse loaded MMP config from {config_path}: {error}"
            ),
            "error": error,
        }

    server_names = (
        ", ".join(server["name"] for server in servers) if servers else "no servers"
    )
    return {
        "success": True,
        "message": (
            f"Loaded MMP config from {config_path}. "
            f"Discovered {len(servers)} server(s): {server_names}."
        ),
        "config_path": config_path,
        "servers": servers,
    }


async def mmp_list_servers(
    *,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """List servers currently visible to the sandbox MMP CLI.

    Use this tool after loading a config when you need to discover which MMP
    server names are available before making a call.

    Args:
        tool_context: OpenSage tool context used to locate the sandbox.

    Returns:
        dict[str, Any]: Structured result with `success`, `servers`.
        On failure, returns `success=False` with an `error` message.
    """
    result = await _run_mmp_in_sandbox("ls", tool_context)
    if not result["success"]:
        error = str(result["stdout"]).strip() or "mmp ls failed"
        return {
            "success": False,
            "message": f"Failed to list MMP servers: {error}",
            "error": error,
        }
    servers = _servers_from_ls_output(str(result["stdout"]))
    server_names = (
        ", ".join(server["name"] for server in servers) if servers else "none"
    )
    return {
        "success": True,
        "message": f"Found {len(servers)} MMP server(s): {server_names}.",
        "servers": servers,
    }


async def mmp_call(
    *,
    method: str,
    tool_context: ToolContext,
    server_name: str | None = None,
    session_id: str | None = None,
    params: Any | None = None,
) -> dict[str, Any]:
    """Call an MCP method through the sandbox MMP CLI.

    Use this tool when you need to invoke an MCP method on a configured MMP
    server or continue an existing MMP session. Provide exactly one of
    `server_name` or `session_id`.

    Args:
        method: MCP method name to invoke, for example `tools/list`.
        tool_context: OpenSage tool context used to locate the sandbox.
        server_name: MMP server name to call for a new or resumed session.
        session_id: Existing MMP session id to continue.
        params: Optional JSON-serializable parameters passed with `--arg`.

    Returns:
        dict[str, Any]: Structured result with `success`, the returned
        `session_id`, target `server`, and method `result`. On failure, returns
        `success=False` with an `error` message.
    """
    if bool(server_name) == bool(session_id):
        return {
            "success": False,
            "message": "Provide exactly one of server_name or session_id.",
            "error": "invalid mmp call",
        }

    command_parts: list[str] = ["call"]
    if params is not None:
        command_parts.extend(["--arg", json.dumps(params)])
    if session_id is not None:
        command_parts.extend(["--id", session_id, method])
    elif server_name is not None:
        command_parts.extend([server_name, method])
    else:
        return {
            "success": False,
            "message": "neither server_name nor session_id provided.",
            "error": "Unexpected error",
        }

    command = " ".join(shlex.quote(part) for part in command_parts)
    result = await _run_mmp_in_sandbox(command=command, tool_context=tool_context)
    if not result["success"]:
        error = str(result["stdout"]).strip() or "mmp call failed"
        return {
            "success": False,
            "message": f"Failed to call MMP method {method}: {error}",
            "error": error,
        }

    payload = result["output"]
    if not isinstance(payload, dict):
        return {
            "success": False,
            "message": "MMP call returned a non-JSON response.",
            "error": "Unexpected non-JSON response from mmp call",
        }
    resolved_server = payload.get("server") or server_name or "unknown"
    resolved_session_id = payload.get("id")
    return {
        "success": True,
        "message": (
            f"Called MMP method {method} on server {resolved_server}"
            + (f" using session {resolved_session_id}." if resolved_session_id else ".")
        ),
        "session_id": resolved_session_id,
        "server": resolved_server,
        "result": payload.get("result"),
    }


async def mmp_list_sessions(
    *,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """List active MMP sessions known to the sandbox CLI.

    Use this tool when you need to inspect existing MMP sessions before
    resuming one with `mmp_call` or deleting one with `mmp_remove_session`.

    Args:
        tool_context: OpenSage tool context used to locate the sandbox.

    Returns:
        dict[str, Any]: Structured result with `success`, `sessions`.
        On failure, returns `success=False` with an `error` message.
    """
    result = await _run_mmp_in_sandbox(command="ps", tool_context=tool_context)
    if not result["success"]:
        error = str(result["stdout"]).strip() or "mmp ps failed"
        return {
            "success": False,
            "message": f"Failed to list MMP sessions: {error}",
            "error": error,
        }

    sessions = []
    for row in _parse_table(str(result["stdout"])):
        pid = row.get("PID", "")
        sessions.append(
            {
                "id": row.get("ID", ""),
                "server": row.get("SERVER", ""),
                "pid": int(pid) if pid.isdigit() else pid,
                "created_at": row.get("CREATED AT", ""),
                "alive": row.get("STATUS", "") == "running",
            }
        )
    return {
        "success": True,
        "sessions": sessions,
    }


async def mmp_remove_session(
    *,
    tool_context: ToolContext,
    session_id: str | None = None,
    remove_all: bool = False,
) -> dict[str, Any]:
    """Remove one MMP session or all MMP sessions in the sandbox.

    Use this tool to clean up stale MMP sessions after inspection or after a
    completed workflow. Pass `session_id` to remove one session, or
    `remove_all=True` to remove all sessions.

    Args:
        tool_context: OpenSage tool context used to locate the sandbox.
        session_id: Specific MMP session id to remove.
        remove_all: Whether to remove all MMP sessions.

    Returns:
        dict[str, Any]: Structured result with `success` and a `removed` list.
        On failure, returns `success=False` with an `error` message.
    """
    if remove_all:
        command = "rm --all"
    elif session_id:
        command = f"rm --id {shlex.quote(session_id)}"
    else:
        error = "session_id is required unless remove_all=True."
        return {
            "success": False,
            "message": f"Failed to remove MMP session(s): {error}",
            "error": error,
        }

    result = await _run_mmp_in_sandbox(command=command, tool_context=tool_context)
    if not result["success"]:
        error = str(result["stdout"]).strip() or "mmp rm failed"
        return {
            "success": False,
            "message": f"Failed to remove MMP session(s): {error}",
            "error": error,
        }

    stdout = str(result["stdout"])
    if "No sessions removed." in stdout:
        return {
            "success": True,
            "message": "No MMP sessions were removed.",
            "removed": [],
        }

    removed = []
    for row in _parse_table(stdout):
        pid = row.get("PID", "")
        removed.append(
            {
                "id": row.get("ID", ""),
                "server": row.get("SERVER", ""),
                "pid": int(pid) if pid.isdigit() else pid,
                "removed": True,
            }
        )
    return {
        "success": True,
        "message": f"Removed {len(removed)} MMP session(s).",
        "removed": removed,
    }
