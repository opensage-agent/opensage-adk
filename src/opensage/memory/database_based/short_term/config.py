from __future__ import annotations

from typing import Any


def is_database_short_term_enabled_from_config(config: Any) -> bool:
    """Return whether database short-term memory is enabled in config."""
    memory_config = getattr(config, "memory", None)
    database_config = getattr(memory_config, "database", None)
    short_term_config = getattr(database_config, "short_term", None)
    return bool(getattr(short_term_config, "enabled", False))


def is_database_short_term_enabled_from_session_id(opensage_session_id: str) -> bool:
    """Return whether database short-term memory is enabled for a session."""
    from opensage.session import get_opensage_session

    opensage_session = get_opensage_session(opensage_session_id)
    return is_database_short_term_enabled_from_config(
        getattr(opensage_session, "config", None)
    )


def is_database_short_term_enabled_from_context(context: Any) -> bool:
    """Return whether database short-term memory is enabled for a context."""
    from opensage.utils.agent_utils import get_opensage_session_id_from_context

    opensage_session_id = get_opensage_session_id_from_context(context)
    return is_database_short_term_enabled_from_session_id(opensage_session_id)
