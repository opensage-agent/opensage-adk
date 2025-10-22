from typing import Any, List, Union

from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext


def delegate_to_parent(tool_context: ToolContext) -> str:
    """Delegate control back to the parent agent.

    This tool allows the current agent to transfer control back to its parent agent.
    Use this when you have completed your task and want the parent agent to continue.

    """
    current_agent = tool_context._invocation_context.agent
    parent_agent = current_agent.parent_agent

    if parent_agent is None:
        return "No parent agent to delegate to."

    if (
        hasattr(parent_agent, "__class__")
        and parent_agent.__class__.__name__ == "SequentialAgent"
    ):
        sequential_parent = parent_agent.parent_agent
        if sequential_parent is None:
            return "SequentialAgent has no parent agent to delegate to."
        tool_context.actions.transfer_to_agent = sequential_parent.name
        return f"Delegating control back to SequentialAgent's parent: {sequential_parent.name}"
    else:
        tool_context.actions.transfer_to_agent = parent_agent.name
        return f"Delegating control back to parent agent: {parent_agent.name}"


class ToolCombo:
    def __init__(
        self,
        name: str,
        tool_sequences: List[Any],
        description: str = "",
        model: Union[str, BaseLlm] = "",
        return_history: bool = True,
    ):
        self.name = name
        self.tool_sequences = tool_sequences
        self.description = description
        self.model = model
        self.return_history = return_history

        sub_agents = []
        for idx, tool in enumerate(self.tool_sequences):
            sub_agent = self._wrap_tool_as_agent(tool, idx, len(self.tool_sequences))
            sub_agents.append(sub_agent)

        if sub_agents and self.return_history:
            last_agent = sub_agents[-1]
            if isinstance(last_agent, LlmAgent):
                delegate_tool = FunctionTool(func=delegate_to_parent)
                if delegate_tool not in last_agent.tools:
                    last_agent.tools.append(delegate_tool)
                current_instruction = last_agent.instruction
                if isinstance(current_instruction, str):
                    last_agent.instruction = (
                        current_instruction
                        + f"\n\nWhen you have completed your task, call the {delegate_to_parent.__name__} function to transfer control back to the parent agent."
                    )

        if self.return_history:
            self.sequential_agent = SequentialAgent(
                name=self.name, sub_agents=sub_agents, description=self.description
            )
            self.agent_tool = None
        else:
            self.sequential_agent = SequentialAgent(
                name=self.name, sub_agents=sub_agents, description=self.description
            )
            self.agent_tool = AgentTool(agent=self.sequential_agent)

    def _wrap_tool_as_agent(self, tool: Any, idx: int, total_tools: int) -> LlmAgent:
        """Wrap a tool as an LlmAgent for use in the SequentialAgent.

        Args:
            tool: The tool to wrap (can be BaseTool, LlmAgent, or callable)
            idx: Index of the tool in the sequence
            total_tools: Total number of tools in the sequence

        Returns:
            LlmAgent: The wrapped agent
        """
        # If it's already an agent, return as is
        if isinstance(tool, LlmAgent):
            return tool

        # Validate that tool is a valid tool type
        if not isinstance(tool, BaseTool) and not callable(tool):
            raise ValueError(
                f"Tool at index {idx} must be a BaseTool, LlmAgent, or callable function"
            )

        is_last = idx == total_tools - 1

        # Get tool name for better instruction
        tool_name = self._get_tool_name(idx)

        # Build sequence overview
        sequence_overview = self._build_sequence_overview()

        # Build detailed instruction
        instruction = f"""You are executing step {idx + 1} of {total_tools} in the '{self.name}' ToolCombo sequence.

            FULL SEQUENCE OVERVIEW:
            {sequence_overview}

            YOUR TASK:
            - You are responsible for step {idx + 1}: '{tool_name}'
            - Execute ONLY your assigned tool/task
            - Save your result clearly and concisely
            - Your result will be passed to the next step in the sequence

            IMPORTANT:
            - Do NOT attempt to execute other tools in the sequence
            - Focus only on your specific task
            - Provide a clear result that the next step can use"""

        if is_last and self.return_history:
            instruction += f"\n\nSince you are the FINAL step in the sequence, after completing your task, you will have access to a function to transfer control back to the parent agent."
        elif not is_last:
            instruction += f"\n\nYour result will be passed to the next step: '{self._get_tool_name(idx + 1)}'"

        # Otherwise, wrap the tool as a minimal LlmAgent with the specified model
        return LlmAgent(
            name=f"{self.name}_step_{idx}",
            model=self.model,
            tools=[tool],
            instruction=instruction,
            output_key=f"{self.name}_step_{idx}_result",
        )

    def _get_tool_name(self, idx: int) -> str:
        """Get the name of a tool at the given index."""
        if idx >= len(self.tool_sequences):
            return "unknown"
        tool = self.tool_sequences[idx]
        if hasattr(tool, "name"):
            return tool.name
        elif hasattr(tool, "__name__"):
            return tool.__name__
        else:
            return f"tool_{idx}"

    def _build_sequence_overview(self) -> str:
        """Build a string overview of the entire ToolCombo sequence."""
        overview_lines = []
        for idx, tool in enumerate(self.tool_sequences):
            tool_name = self._get_tool_name(idx)
            overview_lines.append(f"  Step {idx + 1}: {tool_name}")
        return "\n".join(overview_lines)
