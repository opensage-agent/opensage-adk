"""Unit tests for search_agent dynamic tool behavior."""

from __future__ import annotations

import types as _types

import pytest

from opensage.toolbox.general import dynamic_subagent as dyn


class _DummyAgent:
    def __init__(self, name: str, sub_agents=None):
        self.name = name
        self.sub_agents = sub_agents or []


class _DummyInvocationContext:
    def __init__(self, agent):
        self.agent = agent


class _DummyToolContext:
    def __init__(self, agent):
        self._invocation_context = _DummyInvocationContext(agent)


class _AgentMetadata:
    def __init__(self, agent_id: str, name: str, description: str, config=None):
        self.id = agent_id
        self.name = name
        self.description = description
        self.config = config or {}


class _DummyAgentManager:
    def __init__(self, metas):
        self._metas = metas
        self.loaded = False

    def _load_persisted_agents_on_demand(self, caller_tools, caller_agent):
        self.loaded = True

    def list_agents(self):
        return self._metas

    def get_agent(self, agent_id):
        return None


class _DummySession:
    def __init__(self, metas):
        self.agents = _DummyAgentManager(metas)


@pytest.mark.asyncio
async def test_search_agent_sorts_name_match_before_description_match(monkeypatch):
    metas = [
        _AgentMetadata(
            agent_id="a1",
            name="calc_agent",
            description="Does calculations",
            config={"tool_names": ["t1"], "model": "m1"},
        ),
        _AgentMetadata(
            agent_id="a2",
            name="helper",
            description="A calculator for math",
            config={"tool_names": ["t2"], "model": "m2"},
        ),
    ]
    session = _DummySession(metas)
    tool_context = _DummyToolContext(agent=_DummyAgent(name="caller"))

    monkeypatch.setattr(dyn, "get_opensage_session_id_from_context", lambda tc: "sid")
    monkeypatch.setattr(dyn, "get_opensage_session", lambda sid: session)
    monkeypatch.setattr(dyn, "extract_tools_from_agent", lambda agent: {})

    res = await dyn.search_agent("calc", tool_context=tool_context, limit=10)
    assert res["success"] is True
    assert res["total_matches"] == 2
    assert res["matches"][0]["name"] == "calc_agent"
    assert res["matches"][0]["score"] > res["matches"][1]["score"]


@pytest.mark.asyncio
async def test_search_agent_match_all_requires_all_keywords(monkeypatch):
    metas = [
        _AgentMetadata(
            agent_id="a1",
            name="calc_agent",
            description="Does calculations",
            config={"tool_names": ["t1"], "model": "m1"},
        ),
    ]
    session = _DummySession(metas)
    tool_context = _DummyToolContext(agent=_DummyAgent(name="caller"))

    monkeypatch.setattr(dyn, "get_opensage_session_id_from_context", lambda tc: "sid")
    monkeypatch.setattr(dyn, "get_opensage_session", lambda sid: session)
    monkeypatch.setattr(dyn, "extract_tools_from_agent", lambda agent: {})

    res = await dyn.search_agent(
        ["calc", "missing"], tool_context=tool_context, match_all=True
    )
    assert res["success"] is True
    assert res["total_matches"] == 0
    assert res["matches"] == []


@pytest.mark.asyncio
async def test_search_agent_includes_adk_subagents(monkeypatch):
    metas = []
    session = _DummySession(metas)

    sub_agent = _types.SimpleNamespace(
        name="file_reader", description="Reads files from disk", tools=[]
    )
    caller = _DummyAgent(name="caller", sub_agents=[sub_agent])
    tool_context = _DummyToolContext(agent=caller)

    monkeypatch.setattr(dyn, "get_opensage_session_id_from_context", lambda tc: "sid")
    monkeypatch.setattr(dyn, "get_opensage_session", lambda sid: session)
    monkeypatch.setattr(dyn, "extract_tools_from_agent", lambda agent: {})

    res = await dyn.search_agent("read", tool_context=tool_context)
    assert res["success"] is True
    assert res["total_matches"] == 1
    assert res["matches"][0]["type"] == "adk_subagent"
    assert res["matches"][0]["name"] == "file_reader"
