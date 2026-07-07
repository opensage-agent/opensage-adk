"""Resolve and read the user-configurable ``auto_insert_prompt_file``.

This file's content is appended to every agent's instruction at invocation
time by the ``BaseAgent.run_async`` patch. The default content describes the
long-term memory layout and policy.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from opensage.utils.project_info import SRC_PATH

if TYPE_CHECKING:
    from opensage.session.opensage_session import OpenSageSession

logger = logging.getLogger(__name__)

DEFAULT_AUTO_INSERT_PROMPT_PATH: Path = (
    SRC_PATH / "templates" / "auto_insert_prompts" / "default.md"
)


def resolve_auto_insert_prompt_path(opensage_session: "OpenSageSession") -> Path:
    """Resolve the path to the auto-insert prompt file.

    Priority:
      1. ``absolute_path`` — used as-is.
      2. ``agent_relative_path`` — joined to ``opensage_session.agent_dir``
         (the directory containing this agent's ``agent.py``/``config.toml``).
      3. Built-in default template under
         ``src/opensage/templates/auto_insert_prompts/default.md``.

    Configuring both 1 and 2 raises ``ValueError``.
    """
    cfg = getattr(opensage_session.config, "auto_insert_prompt_file", None)
    abs_path = getattr(cfg, "absolute_path", None) if cfg else None
    rel_path = getattr(cfg, "agent_relative_path", None) if cfg else None

    if abs_path and rel_path:
        raise ValueError(
            "auto_insert_prompt_file: configure either absolute_path or "
            "agent_relative_path, not both."
        )

    if abs_path:
        return Path(abs_path)

    if rel_path:
        agent_dir = getattr(opensage_session, "agent_dir", None)
        if not agent_dir:
            raise ValueError(
                "auto_insert_prompt_file.agent_relative_path is set but "
                "opensage_session has no agent_dir to resolve it against."
            )
        return Path(agent_dir) / rel_path

    return DEFAULT_AUTO_INSERT_PROMPT_PATH


def load_auto_insert_prompt_content(
    opensage_session: "OpenSageSession",
) -> Optional[str]:
    """Resolve the file path and return its content, or None if unreadable.

    Missing file logs a warning and returns None (no injection). Empty file
    returns "" (still no useful injection — caller should treat empty as
    "skip").
    """
    try:
        path = resolve_auto_insert_prompt_path(opensage_session)
    except Exception:
        logger.exception("Failed to resolve auto_insert_prompt_file path")
        return None

    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "auto_insert_prompt_file does not exist: %s — nothing will be "
            "appended to agent instructions",
            path,
        )
        return None
    except Exception:
        logger.exception("Failed to read auto_insert_prompt_file at %s", path)
        return None
