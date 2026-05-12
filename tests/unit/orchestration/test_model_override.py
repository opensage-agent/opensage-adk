"""Tests for spawn(model_override=...) persistence + ensure_loaded application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.adk.models.lite_llm import LiteLlm

from opensage.llm.registry import LlmRegistry
from opensage.orchestration.manager import AgentManager
from opensage.orchestration.persistence import read_metadata


@dataclass
class _StubOpenSageSession:
    opensage_session_id: str
    session_service: object
    llms: LlmRegistry
    artifact_service: object = None
    memory_service: object = None
    credential_service: object = None
    config: object = field(default_factory=lambda: MagicMock())


def _make_manager(tmp_path, monkeypatch, *, registry: LlmRegistry):
    """Build an AgentManager wired to a stub OpenSageSession."""
    from opensage.memory.file_based.short_term import session_files as sf

    monkeypatch.setattr(sf, "HOST_SESSION_ROOT", tmp_path, raising=True)

    svc = AsyncMock()
    svc.get_session = AsyncMock(return_value=None)
    svc.create_session = AsyncMock()
    svc.append_event = AsyncMock()
    op = _StubOpenSageSession(
        opensage_session_id="osid-test",
        session_service=svc,
        llms=registry,
    )
    return AgentManager(op)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_spawn_persists_model_override(tmp_path, monkeypatch):
    """spawn(model_override="X") writes ``model_override: "X"`` to metadata.json."""
    override_model = LiteLlm(model="openai/override-model", api_key="k")
    reg = LlmRegistry({"openai/override-model": override_model})
    mgr = _make_manager(tmp_path, monkeypatch, registry=reg)

    # Stub Runner so spawn can build an in-memory instance.
    monkeypatch.setattr(
        "opensage.orchestration.manager.Runner",
        lambda *a, **kw: MagicMock(),
        raising=True,
    )

    fake_agent = MagicMock()
    fake_agent.name = "worker"
    fake_agent.model = LiteLlm(model="openai/test-model", api_key="k")
    mgr._agents["worker"] = fake_agent

    fake_session = MagicMock()
    fake_session.state = {
        "opensage_session_id": "osid-test",
        "_mem_agent_dir": "/mem/short_term/worker__sid-1",
        "_host_instance_dir": str(tmp_path / "osid-test" / "instances" / "sid-1"),
    }
    fake_session.model_dump_json = MagicMock(return_value="{}")
    mgr._opensage_session.session_service.create_session = AsyncMock(
        return_value=fake_session
    )

    sid = await mgr.spawn(
        "worker",
        session_id="sid-1",
        model_override="openai/override-model",
    )
    assert sid == "sid-1"

    instance_dir = tmp_path / "osid-test" / "instances" / "sid-1"
    meta = read_metadata(instance_dir)
    assert meta["model_override"] == "openai/override-model"

    # Verify the in-memory instance was also created
    assert sid in mgr._instances


@pytest.mark.asyncio
async def test_spawn_no_model_override_persists_none(tmp_path, monkeypatch):
    """Without model_override, metadata stores None."""
    reg = LlmRegistry({})
    mgr = _make_manager(tmp_path, monkeypatch, registry=reg)

    monkeypatch.setattr(
        "opensage.orchestration.manager.Runner",
        lambda *a, **kw: MagicMock(),
        raising=True,
    )

    fake_agent = MagicMock()
    fake_agent.name = "worker"
    fake_agent.model = LiteLlm(model="openai/test", api_key="k")
    mgr._agents["worker"] = fake_agent

    fake_session = MagicMock()
    fake_session.state = {
        "opensage_session_id": "osid-test",
        "_mem_agent_dir": "/mem/short_term/worker__sid-2",
        "_host_instance_dir": str(tmp_path / "osid-test" / "instances" / "sid-2"),
    }
    fake_session.model_dump_json = MagicMock(return_value="{}")
    mgr._opensage_session.session_service.create_session = AsyncMock(
        return_value=fake_session
    )

    await mgr.spawn("worker", session_id="sid-2")

    meta = read_metadata(tmp_path / "osid-test" / "instances" / "sid-2")
    assert meta["model_override"] is None


def test_ensure_root_fallback_seeds_empty_registry():
    """If registry is empty, ensure_root_fallback registers root.model under .model attr."""
    reg = LlmRegistry({})
    root_model = LiteLlm(model="anthropic/claude-x", api_key="k")
    reg.ensure_root_fallback(root_model)
    assert reg.list_names() == ["anthropic/claude-x"]
    # Same Python object — no clone
    assert reg.get("anthropic/claude-x") is root_model


def test_ensure_root_fallback_idempotent_when_non_empty():
    """If registry already has entries, ensure_root_fallback is a no-op."""
    existing = LiteLlm(model="openai/configured", api_key="k")
    reg = LlmRegistry({"openai/configured": existing})

    other = LiteLlm(model="anthropic/root", api_key="k")
    reg.ensure_root_fallback(other)

    # Did NOT add root model — registry left as-is
    assert reg.list_names() == ["openai/configured"]
    assert reg.get("openai/configured") is existing


def test_ensure_root_fallback_rejects_non_basellm():
    reg = LlmRegistry({})
    with pytest.raises(TypeError, match="BaseLlm"):
        reg.ensure_root_fallback("not a model")  # type: ignore[arg-type]


def test_ensure_root_fallback_rejects_empty_model_attr():
    """A BaseLlm whose .model attribute is empty is not a usable registry key."""
    bad = MagicMock(spec=LiteLlm)
    bad.model = ""
    reg = LlmRegistry({})
    with pytest.raises(ValueError, match="no usable .model"):
        reg.ensure_root_fallback(bad)
