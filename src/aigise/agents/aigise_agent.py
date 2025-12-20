import logging
from typing import List, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from pydantic import Field

from aigise.features.tool_combo import ToolCombo

logger = logging.getLogger(__name__)


class AigiseAgent(LlmAgent):
    tool_combos: Optional[List[ToolCombo]] = Field(default=None)

    def __init__(
        self,
        *args,
        tools: Optional[List] = None,
        tool_combos: Optional[List[ToolCombo]] = None,
        **kwargs,
    ):
        tools = list(tools) if tools else []

        sub_agents = kwargs.get("sub_agents", [])
        for combo in tool_combos or []:
            if combo.return_history:
                sub_agents.append(combo.sequential_agent)
            else:
                if combo.agent_tool not in tools:
                    tools.append(combo.agent_tool)

        kwargs["sub_agents"] = sub_agents
        kwargs["tools"] = tools

        # Initialize the parent class first
        super().__init__(*args, **kwargs)
