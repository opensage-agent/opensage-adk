"""Evaluation dispatchers — pluggable execution backends.

Each dispatcher implements :class:`BaseDispatcher` and handles *how* to distribute
evaluation samples (native threads, Ray cluster, …).  The Evaluation
class only cares about *what* to run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opensage.evaluation.dispatchers.base import BaseDispatcher


def get_dispatcher(dispatcher_type: str, **kwargs) -> BaseDispatcher:
    """Create a dispatcher by type.

        Args:
            dispatcher_type (str): ``"native"`` or ``"ray"``.
            **kwargs: Forwarded to the dispatcher constructor.

    Raises:
      ValueError: Raised when this operation fails."""
    if dispatcher_type == "native":
        from opensage.evaluation.dispatchers.native import NativeDispatcher

        return NativeDispatcher(**kwargs)
    elif dispatcher_type == "ray":
        from opensage.evaluation.dispatchers.ray import RayDispatcher

        return RayDispatcher(**kwargs)
    else:
        raise ValueError(
            f"Unknown dispatcher_type: {dispatcher_type!r}. Must be 'native' or 'ray'."
        )
