"""AgentInstance dataclass: runtime state of one running agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import AgentInstanceState

if TYPE_CHECKING:
    from google.adk.runners import Runner

    from opensage.agents.opensage_agent import OpenSageAgent

    from .inbox import Inbox


@dataclass
class AgentInstance:
    """Tracks the full runtime state of one agent instance within AgentManager.

    Instances are kept in memory for their entire lifetime — created by
    ``spawn`` or loaded eagerly in ``start`` (resume path).
    """

    session_id: str
    agent: OpenSageAgent  # shallow clone of the template
    agent_name: str  # name in manager.agents registry
    state: AgentInstanceState
    inbox: Inbox
    runner: Runner
    user_id: str

    _task: asyncio.Task | None = None
    _done_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        # _done_event starts set (no invocation in flight).
        self._done_event.set()
