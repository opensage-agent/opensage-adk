import asyncio
import types as _types

import pytest
from google.adk.agents.base_agent import BaseAgent
from google.adk.tools.agent_tool import AgentTool


class _StubAgent(BaseAgent):
    """Minimal BaseAgent implementation for testing AgentTool patch."""

    async def _run_async_impl(self, ctx):
        if False:
            yield  # pragma: no cover
        return

    async def _run_live_impl(self, ctx):
        if False:
            yield  # pragma: no cover
        return


from opensage.features import agent_history_tracker


class _DummyInvCostMgr:
    def __init__(self, used: int):
        self._number_of_llm_calls = used


class _DummyInvocationContext:
    def __init__(self, used: int, limit: int):
        from google.adk.agents.run_config import RunConfig

        self.run_config = RunConfig(max_llm_calls=limit)
        self._invocation_cost_manager = _DummyInvCostMgr(used)
        self.agent = _types.SimpleNamespace(name="parent")
        self.session = _types.SimpleNamespace(id="parent-session")
        self.credential_service = object()


class _DummyToolContext:
    def __init__(self, used: int, limit: int):
        self._invocation_context = _DummyInvocationContext(used, limit)
        self.actions = _types.SimpleNamespace(
            skip_summarization=False, state_delta=None
        )
        self.state = _types.SimpleNamespace(to_dict=lambda: {})


class _DummySession:
    def __init__(self, user_id: str, session_id: str, child_used: int):
        self.user_id = user_id
        self.id = session_id
        self.state = {"_adk": {"llm_calls_used": child_used}}


class _DummySessionService:
    def __init__(self, child_used: int):
        self._child_used = child_used
        self._created = None

    async def create_session(self, *, app_name, user_id, state, session_id=None):
        sid = session_id or "child-session"
        self._created = _DummySession(
            user_id=user_id, session_id=sid, child_used=self._child_used
        )
        return self._created

    async def get_session(self, *, app_name, user_id, session_id):
        return self._created or _DummySession(user_id, session_id, self._child_used)


class _DummyRunner:
    def __init__(self, **kwargs):
        # Tests only need the injected session_service; ignore plugin/app args.
        self.session_service = kwargs["session_service"]
        self._last_run_config = None

    async def close(self):
        return

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        self._last_run_config = run_config
        if False:
            yield None
        return


@pytest.mark.asyncio
async def test_agenttool_runs_with_remaining_quota_and_merges_child_usage(monkeypatch):
    agent_history_tracker.enable_neo4j_logging()

    tool_context = _DummyToolContext(used=7, limit=10)
    child_used = 2

    import opensage.patches.neo4j_logging as patch_mod

    monkeypatch.setattr(
        patch_mod, "Runner", _types.SimpleNamespace(__call__=None), raising=False
    )

    def _runner_ctor(**kwargs):
        kwargs["session_service"] = _DummySessionService(child_used=child_used)
        return _DummyRunner(**kwargs)

    monkeypatch.setattr(patch_mod, "Runner", _runner_ctor, raising=True)

    dummy_agent = _StubAgent(name="child_agent", description="desc")
    tool = AgentTool(agent=dummy_agent, skip_summarization=False)

    res = await tool.run_async(args={"request": "hello"}, tool_context=tool_context)
    assert res == ""

    parent_mgr = tool_context._invocation_context._invocation_cost_manager
    assert parent_mgr._number_of_llm_calls == 9


@pytest.mark.asyncio
async def test_agenttool_passes_zero_remaining_when_parent_exhausted(monkeypatch):
    agent_history_tracker.enable_neo4j_logging()

    tool_context = _DummyToolContext(used=5, limit=5)

    captured = {"run_config": None}

    class _CapturingRunner(_DummyRunner):
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            captured["run_config"] = run_config
            if False:
                yield None
            return

    import opensage.patches.neo4j_logging as patch_mod

    def _runner_ctor(**kwargs):
        kwargs["session_service"] = _DummySessionService(child_used=0)
        return _CapturingRunner(**kwargs)

    monkeypatch.setattr(patch_mod, "Runner", _runner_ctor, raising=True)

    dummy_agent = _StubAgent(name="child_agent", description="desc")
    tool = AgentTool(agent=dummy_agent, skip_summarization=False)

    await tool.run_async(args={"request": "hello"}, tool_context=tool_context)

    from google.adk.agents.run_config import RunConfig

    assert isinstance(captured["run_config"], RunConfig)
    assert captured["run_config"].max_llm_calls == 0
