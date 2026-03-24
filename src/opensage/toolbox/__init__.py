"""OpenSage toolbox package.

This package contains reusable tool implementations grouped by capability
area (general utilities, static analysis, retrieval, debugging, etc.).
"""

from __future__ import annotations

# Public toolbox helpers for tool authors.
from .sandbox_requirements import collect_sandbox_dependencies, requires_sandbox
from .tool_normalization import (
    make_tool_safe_dict,
    make_toollike_safe_dict,
    make_toollikes_safe_dict,
)
