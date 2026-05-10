"""Tests for opensage.orchestration.fake_user."""

from __future__ import annotations

# Avoid importing opensage top-level (pulls pandas, docker, etc.)
# Import the module directly.
import importlib
import importlib.util
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_spec = importlib.util.spec_from_file_location(
    "opensage.orchestration.fake_user",
    "src/opensage/orchestration/fake_user.py",
)
_mod = importlib.util.module_from_spec(_spec)
# Stub the relative import of .manager
_manager_mock = MagicMock()
_manager_mock._extract_final_text = MagicMock(return_value=None)
_manager_mock.DEFAULT_USER_ID = "user"
sys.modules["opensage.orchestration.fake_user"] = _mod
sys.modules.setdefault("opensage", MagicMock())
sys.modules.setdefault("opensage.orchestration", MagicMock())
sys.modules.setdefault("opensage.orchestration.manager", _manager_mock)
_spec.loader.exec_module(_mod)

from opensage.orchestration.fake_user import (  # noqa: E402
    FakeUserRunResult,
    run_with_fake_user,
)

# ── Helpers ──────────────────────────────────────────────────────


def _make_session(state: dict | None = None, events: list | None = None):
    """Build a mock ADK Session."""
    session = MagicMock()
    session.state = state or {}
    session.events = events or []
    return session


def _make_manager(session_service, app_name="test-app"):
    """Build a minimal AgentManager stub with the properties fake_user needs."""
    mgr = MagicMock()
    mgr.session_service = session_service
    mgr.app_name = app_name
    mgr.default_user_id = "user"
    return mgr


def _make_event(invocation_id="inv-1", text="hello", author="agent"):
    """Build a mock Event."""
    ev = MagicMock()
    ev.invocation_id = invocation_id
    ev.author = author
    ev.model_dump_json = MagicMock(return_value='{"mock": true}')
    return ev


# ── Tests for run_with_fake_user ─────────────────────────────────


class TestRunWithFakeUser:
    """Tests for the multi-turn loop driver."""

    @pytest.mark.asyncio
    async def test_multi_turn_stops_after_3_turns(self):
        """Fake user sends follow-ups for 2 extra turns, then stops."""
        turn_count = 0

        session_svc = AsyncMock()
        session_svc.get_session = AsyncMock(
            return_value=_make_session(state={"_adk": {"llm_calls_used": 5}})
        )
        mgr = _make_manager(session_svc)

        async def _mock_stream(*args, **kwargs):
            nonlocal turn_count
            turn_count += 1
            yield _make_event(invocation_id=f"inv-{turn_count}")

        mgr.run_turn_stream = _mock_stream

        call_count = 0

        async def _three_turns(session):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return None
            return "continue"

        result = await run_with_fake_user(
            agent_manager=mgr,
            session_id="sid-1",
            first_message="start",
            fake_user=_three_turns,
        )

        assert turn_count == 3
        assert result.termination_reason == "fake_user_stop"

    @pytest.mark.asyncio
    async def test_max_turns_guard(self):
        """max_turns stops the loop even if fake_user wants to continue."""
        session_svc = AsyncMock()
        session_svc.get_session = AsyncMock(return_value=_make_session(state={}))
        mgr = _make_manager(session_svc)

        async def _mock_stream(*args, **kwargs):
            yield _make_event()

        mgr.run_turn_stream = _mock_stream

        async def _always_continue(session):
            return "more"

        result = await run_with_fake_user(
            agent_manager=mgr,
            session_id="sid-1",
            first_message="start",
            fake_user=_always_continue,
            max_turns=5,
        )

        assert result.termination_reason == "max_turns"

    @pytest.mark.asyncio
    async def test_budget_exhausted(self):
        """Loop stops when LLM budget runs out."""
        session_svc = AsyncMock()
        # Each turn uses 60 calls
        session_svc.get_session = AsyncMock(
            return_value=_make_session(state={"_adk": {"llm_calls_used": 60}})
        )
        mgr = _make_manager(session_svc)

        async def _mock_stream(*args, **kwargs):
            yield _make_event()

        mgr.run_turn_stream = _mock_stream

        async def _always_continue(session):
            return "more"

        result = await run_with_fake_user(
            agent_manager=mgr,
            session_id="sid-1",
            first_message="start",
            fake_user=_always_continue,
            max_llm_calls=100,
        )

        # First turn: remaining = 100 - 60 = 40
        # Second turn: remaining = 40 - 60 = 0 (clamped)
        # Third turn: budget guard triggers (remaining <= 0)
        assert result.termination_reason == "budget_exhausted"
        assert result.llm_calls_used == 100

    @pytest.mark.asyncio
    async def test_max_llm_calls_zero_means_unlimited(self):
        """max_llm_calls=0 is treated as unlimited (backward compat)."""
        session_svc = AsyncMock()
        session_svc.get_session = AsyncMock(return_value=_make_session(state={}))
        mgr = _make_manager(session_svc)

        turn_count = 0

        async def _mock_stream(*args, **kwargs):
            nonlocal turn_count
            turn_count += 1
            yield _make_event()

        mgr.run_turn_stream = _mock_stream

        async def _two_turns(session):
            if turn_count >= 2:
                return None
            return "go"

        result = await run_with_fake_user(
            agent_manager=mgr,
            session_id="sid-1",
            first_message="start",
            fake_user=_two_turns,
            max_llm_calls=0,  # Should be treated as unlimited
        )

        assert turn_count == 2
        assert result.termination_reason == "fake_user_stop"

    @pytest.mark.asyncio
    async def test_llm_limit_exceeded_error(self):
        """LlmCallsLimitExceededError is caught gracefully."""
        from google.adk.agents.invocation_context import LlmCallsLimitExceededError

        session_svc = AsyncMock()
        session_svc.get_session = AsyncMock(return_value=_make_session(state={}))
        mgr = _make_manager(session_svc)

        async def _mock_stream(*args, **kwargs):
            yield _make_event()
            raise LlmCallsLimitExceededError("budget exceeded")

        mgr.run_turn_stream = _mock_stream

        async def _always_continue(session):
            return "more"

        result = await run_with_fake_user(
            agent_manager=mgr,
            session_id="sid-1",
            first_message="start",
            fake_user=_always_continue,
            max_llm_calls=100,
        )

        assert result.termination_reason == "budget_exhausted"
