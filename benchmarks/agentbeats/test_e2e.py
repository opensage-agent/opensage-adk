#!/usr/bin/env python3
"""
E2E test for OpenSage × AgentBeats integration.

Tests:
  1. Agent starts and serves /.well-known/agent.json correctly
  2. Agent card contains required A2A fields (name, url, skills, capabilities)
  3. Agent responds to a streaming A2A message (real LLM call)
  4. Launcher starts, serves GET /status, manages its own agent, and handles POST /reset

Port layout
-----------
  AGENT_PORT (18001)          – standalone agent used by Layer 1 & 2
  LAUNCHER_PORT (18000)       – launcher HTTP control endpoint (Layer 3)
  LAUNCHER_AGENT_PORT (18002) – the agent that the launcher manages (Layer 3)

Keeping Layer 3 on a separate agent port means launcher_proc never competes
with agent_proc for the same port, avoiding a silent false-positive where
the launcher's broken agent is masked by the standalone one.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
AGENT_SCRIPT = PROJECT_ROOT / "benchmarks" / "agentbeats" / "run_agent.py"
LAUNCHER_SCRIPT = PROJECT_ROOT / "benchmarks" / "agentbeats" / "launcher.py"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

# Layer 1 & 2: standalone agent
AGENT_PORT = 18001
AGENT_BASE = f"http://localhost:{AGENT_PORT}"

# Layer 3: launcher + the agent it manages (separate port to avoid conflicts)
LAUNCHER_PORT = 18000
LAUNCHER_AGENT_PORT = 18002
LAUNCHER_BASE = f"http://localhost:{LAUNCHER_PORT}"
LAUNCHER_AGENT_BASE = f"http://localhost:{LAUNCHER_AGENT_PORT}"

STARTUP_TIMEOUT = 30  # seconds
READINESS_INTERVAL = 1  # seconds between readiness polls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _python() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


async def _wait_for_http(url: str, timeout: float = STARTUP_TIMEOUT) -> bool:
    """Poll *url* until HTTP 200 or *timeout* seconds elapse. Returns True if ready."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(READINESS_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_proc():
    """Start run_agent.py as a standalone server on AGENT_PORT (18001).

    Used exclusively by Layer 1 & 2.  The launcher (Layer 3) manages its own
    separate agent instance on LAUNCHER_AGENT_PORT (18002) so the two groups
    never compete for the same port.
    """
    cmd = [
        _python(),
        str(AGENT_SCRIPT),
        "--agent_host",
        "127.0.0.1",
        "--agent_port",
        str(AGENT_PORT),
        "--model",
        "openai/gpt-4o-mini",
        "--session_id",
        "e2e_test",
    ]
    print(f"\n[fixture] Starting standalone agent: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    yield proc
    print("\n[fixture] Killing standalone agent process …")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def launcher_proc():
    """Start launcher.py which manages its own agent on LAUNCHER_AGENT_PORT (18002).

    Deliberately does NOT depend on agent_proc so the two fixture groups use
    different ports and do not interfere with each other.  The launcher itself
    spawns the agent it needs internally.
    """
    cmd = [
        _python(),
        str(LAUNCHER_SCRIPT),
        "--launcher_host",
        "127.0.0.1",
        "--launcher_port",
        str(LAUNCHER_PORT),
        "--agent_host",
        "127.0.0.1",
        "--agent_port",
        str(LAUNCHER_AGENT_PORT),
        "--model",
        "openai/gpt-4o-mini",
        "--session_id",
        "e2e_test_launcher",
    ]
    print(f"\n[fixture] Starting launcher: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    yield proc
    print("\n[fixture] Killing launcher process …")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Tests – Layer 1: Agent card
# ---------------------------------------------------------------------------


class TestLayer1AgentCard:
    """Layer 1: Agent starts and serves a valid A2A agent card."""

    @pytest.fixture(autouse=True)
    def _wait(self, agent_proc):
        ready = asyncio.run(_wait_for_http(f"{AGENT_BASE}/.well-known/agent.json"))
        assert ready, f"Agent did not become ready within {STARTUP_TIMEOUT}s"

    def test_agent_card_http_200(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{AGENT_BASE}/.well-known/agent.json")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    def test_agent_card_is_valid_json(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{AGENT_BASE}/.well-known/agent.json")
        card = r.json()
        assert isinstance(card, dict), "Agent card must be a JSON object"

    def test_agent_card_required_fields(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{AGENT_BASE}/.well-known/agent.json")
        card = r.json()
        for field in ("name", "url", "version", "capabilities", "skills"):
            assert field in card, f"Agent card missing required field: {field!r}"

    def test_agent_card_has_streaming_capability(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{AGENT_BASE}/.well-known/agent.json")
        card = r.json()
        caps = card.get("capabilities", {})
        assert caps.get("streaming") is True, "Agent card must declare streaming=true"

    def test_agent_card_name(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{AGENT_BASE}/.well-known/agent.json")
        card = r.json()
        assert "opensage" in card["name"].lower(), (
            f"Expected 'opensage' in agent name, got: {card['name']!r}"
        )


# ---------------------------------------------------------------------------
# Tests – Layer 2: A2A messaging
# ---------------------------------------------------------------------------


class TestLayer2A2AMessage:
    """Layer 2: Agent responds to a real streaming A2A message."""

    @pytest.fixture(autouse=True)
    def _wait(self, agent_proc):
        ready = asyncio.run(_wait_for_http(f"{AGENT_BASE}/.well-known/agent.json"))
        assert ready

    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="requires OPENAI_API_KEY to make a real LLM call",
    )
    def test_send_streaming_message(self):
        """Send a simple question and collect at least one text chunk."""
        from a2a.client import A2ACardResolver, A2AClient
        from a2a.types import (
            Message,
            MessageSendParams,
            Part,
            Role,
            SendStreamingMessageRequest,
            SendStreamingMessageSuccessResponse,
            TaskArtifactUpdateEvent,
            TaskStatusUpdateEvent,
            TextPart,
        )

        async def _run():
            async with httpx.AsyncClient(timeout=60) as http:
                resolver = A2ACardResolver(httpx_client=http, base_url=AGENT_BASE)
                card = await resolver.get_agent_card(
                    relative_card_path="/.well-known/agent.json"
                )
                assert card is not None, "Could not resolve agent card"

                client = A2AClient(httpx_client=http, agent_card=card)
                params = MessageSendParams(
                    message=Message(
                        role=Role.user,
                        parts=[Part(TextPart(text="Reply with exactly: PONG"))],
                        messageId=uuid4().hex,
                        taskId=None,
                    )
                )
                req = SendStreamingMessageRequest(id=str(uuid4()), params=params)

                chunks = []
                async for chunk in client.send_message_streaming(req):
                    if not isinstance(chunk.root, SendStreamingMessageSuccessResponse):
                        continue
                    event = chunk.root.result
                    if isinstance(event, TaskArtifactUpdateEvent):
                        for p in event.artifact.parts:
                            if isinstance(p.root, TextPart):
                                chunks.append(p.root.text)
                    elif isinstance(event, TaskStatusUpdateEvent):
                        msg = event.status.message
                        if msg:
                            for p in msg.parts:
                                if isinstance(p.root, TextPart):
                                    chunks.append(p.root.text)
                return chunks

        chunks = asyncio.run(_run())
        full_response = "".join(chunks).strip()
        print(f"\n[Layer2] Agent response: {full_response!r}")
        assert len(full_response) > 0, "Agent returned empty response"


# ---------------------------------------------------------------------------
# Tests – Layer 3: Launcher lifecycle
# ---------------------------------------------------------------------------


class TestLayer3Launcher:
    """Layer 3: Launcher /status and /reset endpoints work correctly.

    Uses launcher_proc which manages its own agent on LAUNCHER_AGENT_PORT (18002).
    This is fully independent of the Layer 1/2 standalone agent on AGENT_PORT (18001).
    """

    @pytest.fixture(autouse=True)
    def _wait(self, launcher_proc):
        ready = asyncio.run(_wait_for_http(f"{LAUNCHER_BASE}/status"))
        assert ready, f"Launcher did not become ready within {STARTUP_TIMEOUT}s"

    def test_launcher_status_200(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{LAUNCHER_BASE}/status")
        assert r.status_code == 200

    def test_launcher_status_has_status_field(self):
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{LAUNCHER_BASE}/status")
        body = r.json()
        assert "status" in body, f"Expected 'status' field, got: {body}"

    def test_launcher_agent_starts_automatically(self):
        """Launcher should have started its own agent on LAUNCHER_AGENT_PORT."""
        ready = asyncio.run(
            _wait_for_http(
                f"{LAUNCHER_AGENT_BASE}/.well-known/agent.json",
                timeout=STARTUP_TIMEOUT,
            )
        )
        assert ready, (
            f"Launcher-managed agent did not start within {STARTUP_TIMEOUT}s "
            f"on port {LAUNCHER_AGENT_PORT}"
        )

    def test_launcher_status_shows_agent_running(self):
        """After the launcher's agent is up, /status must report it as running."""
        # Ensure the agent is up first.
        asyncio.run(_wait_for_http(f"{LAUNCHER_AGENT_BASE}/.well-known/agent.json"))
        with httpx.Client(timeout=5) as client:
            body = client.get(f"{LAUNCHER_BASE}/status").json()
        assert "running" in body.get("status", ""), (
            f"Expected 'running' in /status after agent is up, got: {body}"
        )

    def test_launcher_reset(self):
        """POST /reset should restart the launcher's own agent and it should recover.

        We verify two things beyond the HTTP response:
          1. The agent on LAUNCHER_AGENT_PORT comes back up (readiness poll).
          2. The PID reported by /status changes, proving a new process was spawned
             rather than the old one still accidentally serving requests.
        """
        # Wait for the launcher's agent to be up and record its PID.
        asyncio.run(_wait_for_http(f"{LAUNCHER_AGENT_BASE}/.well-known/agent.json"))
        with httpx.Client(timeout=5) as client:
            pid_before = client.get(f"{LAUNCHER_BASE}/status").json().get("pid")

        # Send the reset signal.
        payload = {
            "backend_url": "http://localhost:9000",  # fake backend – no real notification
            "signal": "reset",
            "agent_id": "test_agent_e2e",
        }
        with httpx.Client(timeout=10) as client:
            r = client.post(f"{LAUNCHER_BASE}/reset", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("status") == "restarting", f"Expected 'restarting', got: {body}"

        # The launcher's own agent on LAUNCHER_AGENT_PORT should recover.
        ready = asyncio.run(
            _wait_for_http(f"{LAUNCHER_AGENT_BASE}/.well-known/agent.json", timeout=40)
        )
        assert ready, (
            "Launcher-managed agent did not recover after /reset within 40 seconds"
        )

        # Confirm the PID changed – a new process was actually started.
        with httpx.Client(timeout=5) as client:
            pid_after = client.get(f"{LAUNCHER_BASE}/status").json().get("pid")
        assert pid_before != pid_after, (
            f"Expected a new agent PID after /reset, but PID is still {pid_after}"
        )


# ---------------------------------------------------------------------------
# Entry point (run without pytest for quick manual testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
