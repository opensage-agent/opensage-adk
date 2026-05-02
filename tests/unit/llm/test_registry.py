"""Tests for LlmRegistry: toml/py loading, validation, conflict raising."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from opensage.config.config_dataclass import (
    ModelEntry,
    ModelRegistryConfig,
    OpenSageConfig,
)
from opensage.llm import LlmRegistry


def _make_config(
    *,
    available_models: list[ModelEntry] | None = None,
    models_python_file: str | None = None,
) -> OpenSageConfig:
    """Hand-build an OpenSageConfig with only the model section populated."""
    cfg = OpenSageConfig()
    cfg.model = ModelRegistryConfig(
        available_models=available_models or [],
        models_python_file=models_python_file,
    )
    return cfg


# ---------------------------------------------------------------------------
# Empty / disabled
# ---------------------------------------------------------------------------


def test_no_sources_yields_empty_registry():
    cfg = _make_config()
    reg = LlmRegistry.from_config(cfg)
    assert len(reg) == 0
    assert reg.list_names() == []
    assert "anything" not in reg


def test_missing_model_section_yields_empty_registry():
    cfg = OpenSageConfig()
    cfg.model = None  # type: ignore[assignment]
    reg = LlmRegistry.from_config(cfg)
    assert len(reg) == 0


# ---------------------------------------------------------------------------
# TOML source
# ---------------------------------------------------------------------------


def test_toml_source_builds_litellm_entries(monkeypatch):
    monkeypatch.setenv("MY_OPENAI_KEY", "sk-test")
    monkeypatch.setenv("MY_ANTHROPIC_KEY", "sk-anthropic")
    cfg = _make_config(
        available_models=[
            ModelEntry(
                model="openai/gpt-5.4",
                api_key_env="MY_OPENAI_KEY",
                base_url="http://localhost:8088/v1",
            ),
            ModelEntry(
                model="anthropic/claude-opus-4-7",
                api_key_env="MY_ANTHROPIC_KEY",
            ),
        ]
    )
    reg = LlmRegistry.from_config(cfg)
    assert reg.list_names() == [
        "anthropic/claude-opus-4-7",
        "openai/gpt-5.4",
    ]
    inst = reg.get("openai/gpt-5.4")
    assert inst.model == "openai/gpt-5.4"


def test_toml_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("OPENSAGE_TEST_NEVER_SET", raising=False)
    cfg = _make_config(
        available_models=[
            ModelEntry(model="openai/foo", api_key_env="OPENSAGE_TEST_NEVER_SET"),
        ]
    )
    with pytest.raises(ValueError, match="OPENSAGE_TEST_NEVER_SET"):
        LlmRegistry.from_config(cfg)


def test_toml_empty_model_field_raises():
    cfg = _make_config(available_models=[ModelEntry(model="", api_key_env="X")])
    with pytest.raises(ValueError, match="'model' is required"):
        LlmRegistry.from_config(cfg)


def test_toml_empty_api_key_env_raises():
    cfg = _make_config(
        available_models=[ModelEntry(model="openai/foo", api_key_env="")]
    )
    with pytest.raises(ValueError, match="'api_key_env' is required"):
        LlmRegistry.from_config(cfg)


def test_toml_duplicate_model_raises(monkeypatch):
    monkeypatch.setenv("KEY1", "v")
    cfg = _make_config(
        available_models=[
            ModelEntry(model="openai/dup", api_key_env="KEY1"),
            ModelEntry(model="openai/dup", api_key_env="KEY1"),
        ]
    )
    with pytest.raises(ValueError, match="duplicate model"):
        LlmRegistry.from_config(cfg)


# ---------------------------------------------------------------------------
# Python file source
# ---------------------------------------------------------------------------


def _write_py_file(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "models.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_py_source_loads_models(tmp_path, monkeypatch):
    monkeypatch.setenv("PYFILE_KEY", "sk-test")
    py_path = _write_py_file(
        tmp_path,
        """
        import os
        from google.adk.models.lite_llm import LiteLlm
        models = [
            LiteLlm(model="openai/gpt-5.4-mini", api_key=os.environ["PYFILE_KEY"]),
            LiteLlm(model="anthropic/claude-haiku-4-5", api_key="anyhing"),
        ]
        """,
    )
    cfg = _make_config(models_python_file=str(py_path))
    reg = LlmRegistry.from_config(cfg)
    assert reg.list_names() == [
        "anthropic/claude-haiku-4-5",
        "openai/gpt-5.4-mini",
    ]


def test_py_relative_path_uses_agent_dir(tmp_path, monkeypatch):
    py_path = _write_py_file(
        tmp_path,
        """
        from google.adk.models.lite_llm import LiteLlm
        models = [LiteLlm(model="openai/test-rel", api_key="k")]
        """,
    )
    cfg = _make_config(models_python_file="models.py")
    reg = LlmRegistry.from_config(cfg, agent_dir=str(tmp_path))
    assert "openai/test-rel" in reg


def test_py_relative_path_without_agent_dir_raises(tmp_path):
    cfg = _make_config(models_python_file="models.py")
    with pytest.raises(ValueError, match="agent_dir"):
        LlmRegistry.from_config(cfg, agent_dir=None)


def test_py_path_not_found_raises(tmp_path):
    cfg = _make_config(models_python_file=str(tmp_path / "nope.py"))
    with pytest.raises(FileNotFoundError):
        LlmRegistry.from_config(cfg)


def test_py_missing_models_attr_raises(tmp_path):
    py_path = _write_py_file(tmp_path, "# empty file, no models var")
    cfg = _make_config(models_python_file=str(py_path))
    with pytest.raises(ValueError, match="must define a top-level ``models``"):
        LlmRegistry.from_config(cfg)


def test_py_models_not_list_raises(tmp_path):
    py_path = _write_py_file(tmp_path, "models = 'not a list'")
    cfg = _make_config(models_python_file=str(py_path))
    with pytest.raises(ValueError, match="must be a list"):
        LlmRegistry.from_config(cfg)


def test_py_non_basellm_element_raises(tmp_path):
    py_path = _write_py_file(tmp_path, "models = [42]")
    cfg = _make_config(models_python_file=str(py_path))
    with pytest.raises(ValueError, match="must be a BaseLlm instance"):
        LlmRegistry.from_config(cfg)


def test_py_duplicate_model_attr_raises(tmp_path):
    py_path = _write_py_file(
        tmp_path,
        """
        from google.adk.models.lite_llm import LiteLlm
        models = [
            LiteLlm(model="openai/dup", api_key="k"),
            LiteLlm(model="openai/dup", api_key="k"),
        ]
        """,
    )
    cfg = _make_config(models_python_file=str(py_path))
    with pytest.raises(ValueError, match="duplicate model name"):
        LlmRegistry.from_config(cfg)


# ---------------------------------------------------------------------------
# Mutual exclusion
# ---------------------------------------------------------------------------


def test_both_sources_configured_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("KEY", "v")
    py_path = _write_py_file(
        tmp_path,
        """
        from google.adk.models.lite_llm import LiteLlm
        models = [LiteLlm(model="openai/x", api_key="k")]
        """,
    )
    cfg = _make_config(
        available_models=[ModelEntry(model="openai/y", api_key_env="KEY")],
        models_python_file=str(py_path),
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        LlmRegistry.from_config(cfg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def test_get_unknown_raises(monkeypatch):
    monkeypatch.setenv("KEY", "v")
    cfg = _make_config(
        available_models=[ModelEntry(model="openai/known", api_key_env="KEY")]
    )
    reg = LlmRegistry.from_config(cfg)
    with pytest.raises(KeyError, match="not in registry"):
        reg.get("openai/unknown")


def test_contains_returns_false_for_non_string():
    reg = LlmRegistry({})
    assert (123 in reg) is False
    assert (None in reg) is False  # type: ignore[operator]


def test_get_returns_same_instance_each_call(monkeypatch):
    """Sharing semantics: same Python object every time, no copy."""
    monkeypatch.setenv("KEY", "v")
    cfg = _make_config(
        available_models=[ModelEntry(model="openai/share", api_key_env="KEY")]
    )
    reg = LlmRegistry.from_config(cfg)
    a = reg.get("openai/share")
    b = reg.get("openai/share")
    assert a is b
