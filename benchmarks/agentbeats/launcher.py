"""
OpenSage AgentBeats Launcher

Implements the AgentBeats Launcher protocol so that agentbeats.org can reset
the agent between battles.

Protocol (from src/agentbeats/agent_launcher.py in the AgentBeats repo):
  - Launcher listens on POST /reset
  - Payload: {"backend_url": str, "signal": "reset", "agent_id": str,
               "extra_args": dict | None}
  - On reset: kill the current agent process, start a fresh one, then poll
    /.well-known/agent.json until the agent is up and notify the backend via
    PUT /agents/{agent_id} {"ready": true}.
  - Launcher also serves GET /status for health checks.

Usage
-----
    python launcher.py [--launcher_host HOST] [--launcher_port PORT] \\
                       [--agent_host HOST] [--agent_port PORT] \\
                       [--model MODEL] [--config CONFIG_TOML] \\
                       [--session_id SESSION_ID]

The launcher will immediately start the agent process on boot and keep it alive
across battle resets.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] [%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Path to run_agent.py in the same directory as this file.
_AGENT_SCRIPT = Path(__file__).parent / "run_agent.py"
_AGENT_KILL_TIMEOUT = 8  # seconds to wait before SIGKILL
_READINESS_MAX_ATTEMPTS = 30  # × 2 s = 60 s timeout
_READINESS_INTERVAL = 2  # seconds between readiness polls


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="OpenSage AgentBeats Launcher", version="1.0.0")


class _SignalPayload(BaseModel):
    backend_url: str
    signal: str
    agent_id: str
    extra_args: Optional[dict] = None


class _LauncherState:
    """Mutable state shared across requests."""

    def __init__(self) -> None:
        self.agent_proc: Optional[subprocess.Popen] = None
        self.agent_cmd: list[str] = []
        self.agent_host: str = "0.0.0.0"
        self.agent_port: int = 8001
        self._lock = asyncio.Lock()
        # Allowlist of trusted backend URLs (set from CLI args in main()).
        self.allowed_backend_url: str = ""

    # ------------------------------------------------------------------
    # Process control
    # ------------------------------------------------------------------

    def _start(self, session_id: Optional[str] = None) -> subprocess.Popen:
        """Start the agent process, optionally overriding the session_id arg."""
        cmd = list(self.agent_cmd)
        if session_id is not None:
            # Replace the --session_id value in the command list.
            try:
                idx = cmd.index("--session_id")
                cmd[idx + 1] = session_id
            except ValueError:
                cmd += ["--session_id", session_id]
        logger.info("Starting agent: %s", " ".join(cmd))
        return subprocess.Popen(cmd)

    def _terminate(self) -> None:
        proc = self.agent_proc
        if proc is None or proc.poll() is not None:
            return
        logger.info("Terminating agent process (PID %s) …", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=_AGENT_KILL_TIMEOUT)
            logger.info("Agent process terminated cleanly.")
        except subprocess.TimeoutExpired:
            logger.warning("Agent did not terminate in time – sending SIGKILL.")
            proc.kill()
            proc.wait()

    # ------------------------------------------------------------------
    # Readiness polling
    # ------------------------------------------------------------------

    async def _wait_for_agent_and_notify(self, backend_url: str, agent_id: str) -> None:
        """Poll /.well-known/agent.json until the agent is up, then notify backend."""
        agent_url = f"http://{self.agent_host}:{self.agent_port}"
        card_url = f"{agent_url}/.well-known/agent.json"

        async with httpx.AsyncClient(timeout=5) as client:
            for attempt in range(1, _READINESS_MAX_ATTEMPTS + 1):
                await asyncio.sleep(_READINESS_INTERVAL)
                try:
                    resp = await client.get(card_url)
                    if resp.status_code == 200:
                        logger.info(
                            "Agent is ready after %d attempt(s). Notifying backend …",
                            attempt,
                        )
                        try:
                            await client.put(
                                f"{backend_url}/agents/{agent_id}",
                                json={"ready": True},
                            )
                            logger.info(
                                "Backend notified: agent %s is ready.", agent_id
                            )
                        except Exception as exc:
                            logger.warning("Failed to notify backend: %s", exc)
                        return
                except Exception:
                    pass  # Agent not ready yet

        logger.warning(
            "Agent %s did not become ready within %d seconds.",
            agent_id,
            _READINESS_MAX_ATTEMPTS * _READINESS_INTERVAL,
        )

    # ------------------------------------------------------------------
    # Reset handler
    # ------------------------------------------------------------------

    async def handle_reset(self, payload: _SignalPayload) -> dict:
        if payload.signal != "reset":
            raise ValueError(f"Unsupported signal: {payload.signal!r}")

        # --- SSRF mitigation -------------------------------------------------
        # Validate backend_url against the trusted URL supplied at startup.
        if self.allowed_backend_url and payload.backend_url != self.allowed_backend_url:
            raise ValueError(
                f"backend_url {payload.backend_url!r} is not in the allowlist. "
                f"Expected {self.allowed_backend_url!r}."
            )
        # URL-encode agent_id to prevent path traversal (e.g. '../../admin').
        safe_agent_id = urllib.parse.quote(payload.agent_id, safe="")
        # ---------------------------------------------------------------------

        # Generate a fresh session_id so each battle starts with a clean slate.
        fresh_session_id = f"agentbeats_{uuid4().hex}"

        loop = asyncio.get_event_loop()
        async with self._lock:
            # Run blocking _terminate() in a thread to avoid stalling the loop.
            await loop.run_in_executor(None, self._terminate)
            self.agent_proc = self._start(session_id=fresh_session_id)
            pid = self.agent_proc.pid

        logger.info(
            "Agent restarted (PID %s, session %s). Waiting for readiness …",
            pid,
            fresh_session_id,
        )
        asyncio.create_task(
            self._wait_for_agent_and_notify(payload.backend_url, safe_agent_id)
        )
        return {"status": "restarting", "pid": pid}


# Module-level state (initialised in main()).
_state = _LauncherState()


@app.post("/reset")
async def reset(payload: _SignalPayload) -> dict:
    """Receive reset signal from AgentBeats backend."""
    return await _state.handle_reset(payload)


@app.get("/status")
async def status() -> dict:
    """Health check endpoint."""
    proc = _state.agent_proc
    if proc is not None and proc.poll() is None:
        return {"status": "server up, agent running", "pid": proc.pid}
    return {"status": "server up, no agent running"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentBeats Launcher for OpenSage Red Agent.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--launcher_host", default="0.0.0.0")
    parser.add_argument("--launcher_port", type=int, default=8000)
    parser.add_argument("--agent_host", default="0.0.0.0")
    parser.add_argument("--agent_port", type=int, default=8001)
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENSAGE_MODEL", "litellm_proxy/sage-gpt-5"),
    )
    parser.add_argument("--config", default=None, help="OpenSage TOML config path.")
    parser.add_argument("--session_id", default="agentbeats_red_agent")
    parser.add_argument(
        "--backend_url",
        default="",
        help=(
            "Trusted AgentBeats backend URL (e.g. https://backend.agentbeats.org). "
            "When set, /reset will reject any backend_url that does not match this value."
        ),
    )
    parser.add_argument(
        "--public_host",
        default=None,
        help=(
            "Public hostname/IP for the agent card URL. "
            "Defaults to --agent_host. Set when binding to 0.0.0.0."
        ),
    )
    args = parser.parse_args()

    # Build the command used to (re-)start the agent process.
    agent_cmd = [
        sys.executable,
        str(_AGENT_SCRIPT),
        "--agent_host",
        args.agent_host,
        "--agent_port",
        str(args.agent_port),
        "--model",
        args.model,
        "--session_id",
        args.session_id,
    ]
    if args.config:
        agent_cmd += ["--config", args.config]
    if args.public_host:
        agent_cmd += ["--public_host", args.public_host]

    _state.agent_cmd = agent_cmd
    _state.agent_host = args.agent_host
    _state.agent_port = args.agent_port
    _state.allowed_backend_url = args.backend_url

    logger.info("=== OpenSage AgentBeats Launcher ===")
    logger.info("Launcher : http://%s:%s/", args.launcher_host, args.launcher_port)
    logger.info("Agent    : http://%s:%s/", args.agent_host, args.agent_port)

    # Start the agent immediately so it is ready before the first battle.
    _state.agent_proc = _state._start()
    logger.info("Agent started (PID %s).", _state.agent_proc.pid)

    # Start the launcher HTTP server (blocking).
    uvicorn.run(app, host=args.launcher_host, port=args.launcher_port)


if __name__ == "__main__":
    main()
