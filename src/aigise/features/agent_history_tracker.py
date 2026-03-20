from __future__ import annotations

from opensage.patches.neo4j_logging import (
    disable as _neo4j_disable,
)

# Import functions directly to avoid any package attribute masking issues.
from opensage.patches.neo4j_logging import (  # type: ignore
    enable as _neo4j_enable,
)
from opensage.patches.neo4j_logging import (
    is_enabled as _neo4j_is_enabled,
)


def enable_neo4j_logging():
    _neo4j_enable()


def disable_neo4j_logging():
    _neo4j_disable()


def is_neo4j_logging_enabled() -> bool:
    return _neo4j_is_enabled()
