"""Unit tests for bash tools staging and filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from opensage.utils.bash_tools_staging import (
    build_bash_tools_staging_dir,
    compute_bash_tools_top_roots,
)


class _DummyAgent:
    def __init__(
        self,
        *,
        enabled_skills=None,
        tools=None,
        sub_agents=None,
        steps=None,
    ):
        self._enabled_skills = enabled_skills
        self.tools = tools or []
        self.sub_agents = sub_agents or []
        self.steps = steps or []


class _DummyTool:
    def __init__(self, agent):
        self.agent = agent


class _DummyStep:
    def __init__(self, agent):
        self.agent = agent


def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_compute_bash_tools_top_roots_merges_agent_tree() -> None:
    # root -> tool.agent -> step.agent -> sub_agents
    tool_agent = _DummyAgent(enabled_skills=["retrieval/grep"])
    step_agent = _DummyAgent(enabled_skills=["static_analysis/get-caller"])
    sub_agent = _DummyAgent(enabled_skills=["fuzz/run-fuzzing-campaign"])

    root = _DummyAgent(
        enabled_skills=None,
        tools=[_DummyTool(tool_agent)],
        steps=[_DummyStep(step_agent)],
        sub_agents=[sub_agent],
    )

    roots = compute_bash_tools_top_roots(root)
    assert roots == {"retrieval", "static_analysis", "fuzz"}


def test_compute_bash_tools_top_roots_all_forces_copy_all() -> None:
    root = _DummyAgent(enabled_skills=["fuzz/run-fuzzing-campaign"])
    sub = _DummyAgent(enabled_skills="all")
    root.sub_agents.append(sub)

    assert compute_bash_tools_top_roots(root) is None


def test_build_staging_dir_filters_builtin_and_plugin(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    plugin = tmp_path / "plugin"

    _touch(builtin / "fuzz" / "SKILL.md", "builtin fuzz")
    _touch(builtin / "retrieval" / "SKILL.md", "builtin retrieval")
    _touch(plugin / "static_analysis" / "SKILL.md", "plugin static_analysis")

    roots_to_copy = {"fuzz", "static_analysis"}
    with build_bash_tools_staging_dir(
        roots_to_copy=roots_to_copy, builtin_root=builtin, plugin_root=plugin
    ) as staging:
        assert (staging / "fuzz" / "SKILL.md").read_text(
            encoding="utf-8"
        ) == "builtin fuzz"
        assert (staging / "static_analysis" / "SKILL.md").read_text(
            encoding="utf-8"
        ) == "plugin static_analysis"
        assert not (staging / "retrieval").exists()


def test_build_staging_dir_conflict_refuses_overwrite(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    plugin = tmp_path / "plugin"

    _touch(builtin / "fuzz" / "SKILL.md", "builtin fuzz")
    _touch(plugin / "fuzz" / "SKILL.md", "plugin fuzz")

    with pytest.raises(RuntimeError, match="conflict detected"):
        with build_bash_tools_staging_dir(
            roots_to_copy={"fuzz"}, builtin_root=builtin, plugin_root=plugin
        ):
            pass
