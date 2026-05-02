"""Core types for the agent orchestration system."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Framework state keys that are re-derived on every spawn (not inherited even when
# use_parent_history=True). These identify the environment and the instance's
# memory directory.
FRAMEWORK_STATE_KEYS: frozenset[str] = frozenset(
    {
        "opensage_session_id",
        "_mem_agent_dir",
        "_host_instance_dir",
    }
)


class AgentInstanceState(Enum):
    """Lifecycle state of an AgentInstance."""

    RUNNING = "running"  # invocation in progress
    SLEEPING = "sleeping"  # no active invocation; may be loaded or on disk


@dataclass
class Message:
    """A peer message in an Inbox (file-backed JSONL).

    ``kind`` values actually in use:

    - ``"text"``  — any peer send via ``send_message``; informational
    - ``"result"`` — auto-generated reply when an async sub-agent call finishes
                    (final text posted back to caller's inbox)
    - ``"error"`` — auto-generated reply when an async sub-agent call raises;
                    ``error`` field carries ``"<ExcType>: <msg>"``

    ``from_agent_name`` / ``from_agent_description`` are resolved at send time
    by ``AgentManager.send_message`` so the receiver's LLM can identify the
    sender without a sid lookup.
    """

    from_sid: str
    to_sid: str
    content: str
    kind: str = "text"
    ts: float = field(default_factory=time.time)
    metadata: dict[str, Any] | None = None
    from_agent_name: str = ""
    from_agent_description: str = ""
    # Set when ``kind == "error"`` to carry a structured failure description
    # ("<ExcType>: <msg>") distinct from any partial output that lives in
    # ``content``. None for non-error kinds.
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.ts,
            "from_sid": self.from_sid,
            "from_agent_name": self.from_agent_name,
            "from_agent_description": self.from_agent_description,
            "to_sid": self.to_sid,
            "content": self.content,
            "kind": self.kind,
        }
        if self.metadata is not None:
            d["metadata"] = self.metadata
        if self.error is not None:
            d["error"] = self.error
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Message:
        return cls(
            from_sid=d["from_sid"],
            to_sid=d["to_sid"],
            content=d["content"],
            kind=d.get("kind", "text"),
            ts=d.get("ts", 0.0),
            metadata=d.get("metadata"),
            from_agent_name=d.get("from_agent_name", ""),
            from_agent_description=d.get("from_agent_description", ""),
            error=d.get("error"),
        )
