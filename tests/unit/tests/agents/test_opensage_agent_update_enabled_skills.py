"""Unit tests for OpenSageAgent.update_enabled_skills branching behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def test_update_enabled_skills_all_uses_recursive(monkeypatch) -> None:
    import opensage.agents.opensage_agent as mod

    calls: list[str] = []

    class FakeToolLoader:
        def __init__(self, enabled_skills: Any = None):
            self._enabled_skills = enabled_skills

        def load_tools(self):
            calls.append("load_tools")
            return [{"path": "x", "description": "x"}]

        @staticmethod
        def generate_system_prompt_part(
            tools_metadata, sandbox_name=None, remote_root="/bash_tools"
        ):
            # Non-empty prompt so update_enabled_skills proceeds through the main branch.
            return ("- path: /bash_tools/x\n  description: x\n", set())

        @staticmethod
        def generate_sandbox_structure_description(
            required_sandboxes, *, enable_memory_management=False
        ):
            return ""

    monkeypatch.setattr(mod, "ToolLoader", FakeToolLoader)

    dummy = SimpleNamespace(
        _enable_memory_management=False,
        name="test-agent",
        instruction="hello\n\nHere are the available bash tools you can use:\nOLD",
    )

    mod.OpenSageAgent.update_enabled_skills(dummy, "all")

    assert calls == ["load_tools"]


def test_update_enabled_skills_list_uses_load_tools(monkeypatch) -> None:
    import opensage.agents.opensage_agent as mod

    calls: list[str] = []

    class FakeToolLoader:
        def __init__(self, enabled_skills: Any = None):
            self._enabled_skills = enabled_skills

        def load_tools(self):
            calls.append("load_tools")
            return [{"path": "x", "description": "x"}]

        @staticmethod
        def generate_system_prompt_part(
            tools_metadata, sandbox_name=None, remote_root="/bash_tools"
        ):
            return ("- path: /bash_tools/x\n  description: x\n", set())

        @staticmethod
        def generate_sandbox_structure_description(
            required_sandboxes, *, enable_memory_management=False
        ):
            return ""

    monkeypatch.setattr(mod, "ToolLoader", FakeToolLoader)

    dummy = SimpleNamespace(
        _enable_memory_management=False,
        name="test-agent",
        instruction="hello\n\nHere are the available bash tools you can use:\nOLD",
    )

    mod.OpenSageAgent.update_enabled_skills(dummy, ["neo4j"])

    assert calls == ["load_tools"]
