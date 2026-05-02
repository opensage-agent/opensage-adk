"""Patch: make AgentTool.run_async raise at runtime.

Fallback defense-in-depth: OpenSageAgent.__init__ already rejects AgentTool in
``tools=[...]``, but this patch ensures that even if an AgentTool slips through
some other path, invoking it explodes loudly instead of silently going through
ADK's built-in sub-agent machinery.

The official path for invoking sub-agents in OpenSage is:
    - Declare with ``OpenSageAgent(subagents=[...])``
    - Invoke via the ``call_subagent`` / ``continue_agent_instance`` tools
"""

from __future__ import annotations

import logging

logger = logging.getLogger("opensage." + __name__)

_patched: bool = False


def apply() -> None:
    global _patched
    if _patched:
        return

    try:
        from google.adk.tools.agent_tool import AgentTool
    except ImportError:
        logger.warning("google-adk AgentTool not importable; skipping ban_agent_tool")
        return

    async def _banned(self, *args, **kwargs):
        agent_name = getattr(getattr(self, "agent", None), "name", "<unknown>")
        raise RuntimeError(
            f"AgentTool is banned in OpenSage (wrapped agent: {agent_name!r}). "
            f"Use OpenSageAgent's subagents=[...] field to declare sub-agents and "
            f"invoke them via call_subagent / continue_agent_instance tools."
        )

    AgentTool.run_async = _banned
    _patched = True
