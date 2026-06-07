"""LlmRegistry: the single authority for LLM-facing models in an OpenSageSession.

The registry is the **only** place LLM-driven tools (``create_subagent``,
``call_subagent(model_name=...)``, ``get_available_models``) look up models from.
User-written static agents (in ``mk_agent``) may construct ``BaseLlm`` instances
directly without going through the registry — that path is unconstrained.

Two mutually-exclusive sources for registry entries (configured under ``[model]``):

1. **TOML declarative**: ``[[model.available_models]]`` blocks with ``model`` /
   ``api_key_env`` / ``base_url``. Each entry is built into a ``LiteLlm`` at load
   time. ``api_key_env`` is the *name* of an environment variable; secrets never
   go into TOML directly.

2. **Python file**: ``models_python_file`` points at a ``.py`` file (relative to
   ``agent_dir``) that defines ``models: list[BaseLlm]``. Useful when you need
   constructor kwargs that don't express well in TOML
   (``cache_control_injection_points``, custom subclasses, etc.).

Configuring both sources at once raises. Duplicate keys (across or within
sources) raise. Missing ``api_key_env`` env var raises. The registry is loaded
eagerly during ``OpenSageSession.__init__`` so configuration errors surface at
session start, not at the first tool call.

The key for each entry is the ``BaseLlm.model`` field (i.e. the LiteLlm provider
id). No aliasing — one provider id can only have one configuration.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm

if TYPE_CHECKING:
    from opensage.config.config_dataclass import (
        ModelEntry,
        ModelRegistryConfig,
        OpenSageConfig,
    )
    from opensage.llm.budget import BudgetManager

logger = logging.getLogger("opensage." + __name__)


class LlmRegistry:
    """Read-only registry of named ``BaseLlm`` instances available in this session."""

    def __init__(
        self,
        models: dict[str, BaseLlm],
        *,
        budget_manager: Optional["BudgetManager"] = None,
    ) -> None:
        # Stored by ``BaseLlm.model`` (the provider id). Instances are SHARED:
        # ``get(name)`` returns the same Python object every time. Multiple
        # AgentInstances using the same model share state — see module docstring.
        self._budget_manager = budget_manager
        self._models: dict[str, BaseLlm] = {}
        for name, model in dict(models).items():
            self._models[name] = self._maybe_wrap(model)

    # ---- public API ----

    def list_names(self) -> list[str]:
        """Return all registered model names, sorted."""
        return sorted(self._models.keys())

    def get(self, name: str) -> BaseLlm:
        """Return the ``BaseLlm`` for ``name``. Raises ``KeyError`` if absent."""
        if name not in self._models:
            raise KeyError(
                f"Model {name!r} not in registry. Known: {self.list_names()}"
            )
        return self._models[name]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._models

    def __len__(self) -> int:
        return len(self._models)

    def ensure_root_fallback(self, root_model: BaseLlm) -> None:
        """If the registry is empty, register ``root_model`` as the sole entry.

        Called by ``AgentManager.register_agent_tree`` so that, when the user
        configured no ``[model]`` section, the registry isn't empty — the root
        agent's model becomes the only available model for LLM-driven subagents.

        Idempotent: does nothing once the registry has at least one entry.
        Key is the ``BaseLlm.model`` attribute (matching the rest of the
        registry's naming convention).
        """
        if self._models:
            return
        if not isinstance(root_model, BaseLlm):
            raise TypeError(
                f"ensure_root_fallback expects a BaseLlm, got {type(root_model).__name__}"
            )
        name = getattr(root_model, "model", None)
        if not isinstance(name, str) or not name:
            raise ValueError(
                "Cannot use root agent's model as registry fallback: "
                "model has no usable .model attribute"
            )
        self._models[name] = self._maybe_wrap(root_model)

    def _maybe_wrap(self, model: BaseLlm) -> BaseLlm:
        if self._budget_manager is None:
            return model
        from opensage.llm.budget import wrap_llm_with_budget

        return wrap_llm_with_budget(model, self._budget_manager)

    # ---- construction ----

    @classmethod
    def from_config(
        cls,
        config: "OpenSageConfig",
        *,
        agent_dir: Optional[str] = None,
        budget_manager: Optional["BudgetManager"] = None,
    ) -> "LlmRegistry":
        """Build a registry from ``config.model``.

        Args:
            config: the loaded ``OpenSageConfig`` (already TOML-parsed).
            agent_dir: directory the agent was loaded from. ``models_python_file``
                in the TOML is resolved relative to this if it is not already an
                absolute path. May be None if the config does not reference a
                python file.

        Raises:
            ValueError: configuration is invalid (both sources configured,
                duplicate names, missing required fields, missing env var).
            FileNotFoundError: ``models_python_file`` does not exist.
            ImportError: ``models_python_file`` failed to import.
        """
        model_cfg = getattr(config, "model", None)
        if model_cfg is None:
            return cls({}, budget_manager=budget_manager)

        toml_entries = list(model_cfg.available_models or [])
        py_path = (model_cfg.models_python_file or "").strip() or None

        if toml_entries and py_path:
            raise ValueError(
                "config.model.available_models and config.model.models_python_file "
                "are mutually exclusive — configure exactly one."
            )

        if py_path:
            return cls(
                _load_from_python_file(py_path, agent_dir=agent_dir),
                budget_manager=budget_manager,
            )
        if toml_entries:
            return cls(
                _load_from_toml_entries(toml_entries),
                budget_manager=budget_manager,
            )
        return cls({}, budget_manager=budget_manager)


# ---- helpers ----


def _load_from_toml_entries(entries: list["ModelEntry"]) -> dict[str, BaseLlm]:
    """Build LiteLlm instances from declarative TOML entries.

    Each entry must have a non-empty ``model`` and ``api_key_env``. The named
    env var must exist and be non-empty. Duplicate ``model`` values across the
    list raise.
    """
    out: dict[str, BaseLlm] = {}
    for i, entry in enumerate(entries):
        model_id = (entry.model or "").strip()
        if not model_id:
            raise ValueError(
                f"config.model.available_models[{i}]: 'model' is required and must be non-empty"
            )
        if model_id in out:
            raise ValueError(
                f"config.model.available_models[{i}]: duplicate model {model_id!r}"
            )
        env_name = (entry.api_key_env or "").strip()
        if not env_name:
            raise ValueError(
                f"config.model.available_models[{i}] ({model_id!r}): "
                f"'api_key_env' is required (the *name* of an env var, not the secret)"
            )
        api_key = os.environ.get(env_name)
        if not api_key:
            raise ValueError(
                f"config.model.available_models[{i}] ({model_id!r}): "
                f"environment variable {env_name!r} (api_key_env) is unset or empty"
            )

        kwargs: dict[str, object] = {"model": model_id, "api_key": api_key}
        base_url = (entry.base_url or "").strip() if entry.base_url else None
        if base_url:
            kwargs["base_url"] = base_url

        out[model_id] = LiteLlm(**kwargs)
    return out


def _load_from_python_file(
    py_path: str, *, agent_dir: Optional[str]
) -> dict[str, BaseLlm]:
    """Import a user-supplied python file and validate it exports ``models: list[BaseLlm]``.

    Resolution: absolute paths used as-is; relative paths require ``agent_dir``
    and are resolved against it.
    """
    p = Path(py_path)
    if not p.is_absolute():
        if not agent_dir:
            raise ValueError(
                f"config.model.models_python_file is relative ({py_path!r}) but "
                f"agent_dir was not supplied to LlmRegistry.from_config; either "
                f"use an absolute path or pass agent_dir."
            )
        p = (Path(agent_dir) / p).resolve()
    else:
        p = p.resolve()

    if not p.is_file():
        raise FileNotFoundError(f"config.model.models_python_file not found: {p}")

    spec = importlib.util.spec_from_file_location("opensage_user_models", p)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {p}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ImportError(f"Failed to import {p}: {e}") from e

    raw_models = getattr(module, "models", None)
    if raw_models is None:
        raise ValueError(
            f"{p}: module must define a top-level ``models`` list (got nothing)"
        )
    if not isinstance(raw_models, list):
        raise ValueError(
            f"{p}: ``models`` must be a list, got {type(raw_models).__name__}"
        )

    out: dict[str, BaseLlm] = {}
    for i, m in enumerate(raw_models):
        if not isinstance(m, BaseLlm):
            raise ValueError(
                f"{p}: models[{i}] must be a BaseLlm instance, got {type(m).__name__}"
            )
        name = getattr(m, "model", None)
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{p}: models[{i}] must have a non-empty .model attribute "
                f"(used as the registry key); got {name!r}"
            )
        if name in out:
            raise ValueError(f"{p}: duplicate model name {name!r} at models[{i}]")
        out[name] = m
    return out
