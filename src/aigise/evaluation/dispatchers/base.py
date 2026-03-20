"""Abstract base class for evaluation dispatchers."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opensage.evaluation.base import Evaluation


class BaseDispatcher(abc.ABC):
    """Interface that every execution backend must implement."""

    @abc.abstractmethod
    def run(self, evaluation: Evaluation) -> None:
        """Execute all samples in *evaluation*'s dataset."""
        ...
