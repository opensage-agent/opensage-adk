"""Resolution + content loading for AutoInsertPromptFileConfig."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from opensage.config.config_dataclass import (
    AutoInsertPromptFileConfig,
    OpenSageConfig,
)
from opensage.utils.auto_insert_prompt import (
    DEFAULT_AUTO_INSERT_PROMPT_PATH,
    load_auto_insert_prompt_content,
    resolve_auto_insert_prompt_path,
)


def _make_session(*, agent_dir: str | None = None, abs_path=None, rel_path=None):
    cfg = OpenSageConfig()
    cfg.auto_insert_prompt_file = AutoInsertPromptFileConfig(
        absolute_path=abs_path, agent_relative_path=rel_path
    )
    return SimpleNamespace(config=cfg, agent_dir=agent_dir)


def test_default_path_when_neither_set():
    session = _make_session()
    assert resolve_auto_insert_prompt_path(session) == DEFAULT_AUTO_INSERT_PROMPT_PATH


def test_absolute_path_used_as_is(tmp_path: Path):
    f = tmp_path / "custom.md"
    f.write_text("hello", encoding="utf-8")
    session = _make_session(abs_path=str(f))
    assert resolve_auto_insert_prompt_path(session) == Path(str(f))


def test_relative_path_joined_to_agent_dir(tmp_path: Path):
    f = tmp_path / "custom.md"
    f.write_text("hello", encoding="utf-8")
    session = _make_session(agent_dir=str(tmp_path), rel_path="custom.md")
    assert resolve_auto_insert_prompt_path(session) == tmp_path / "custom.md"


def test_relative_without_agent_dir_raises():
    session = _make_session(rel_path="custom.md")
    with pytest.raises(ValueError, match="no agent_dir"):
        resolve_auto_insert_prompt_path(session)


def test_both_paths_set_raises():
    session = _make_session(abs_path="/x", rel_path="y")
    with pytest.raises(ValueError, match="not both"):
        resolve_auto_insert_prompt_path(session)


def test_load_returns_content_for_default_template():
    session = _make_session()
    content = load_auto_insert_prompt_content(session)
    assert content is not None
    # Must be the long-term memory policy template.
    assert "long-term memory" in content.lower() or "long_term" in content.lower()


def test_load_missing_file_returns_none(tmp_path: Path):
    session = _make_session(abs_path=str(tmp_path / "missing.md"))
    assert load_auto_insert_prompt_content(session) is None


def test_load_returns_user_content(tmp_path: Path):
    f = tmp_path / "custom.md"
    f.write_text("policy: always reboot\n", encoding="utf-8")
    session = _make_session(abs_path=str(f))
    content = load_auto_insert_prompt_content(session)
    assert content == "policy: always reboot\n"
