from __future__ import annotations

import logging
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent

from aigise.extended_features.sec_agent import SecAgent

logger = logging.getLogger("aigise.extended_features." + __name__)


class AgentRegistry:
    """Simplified agent registry that only handles agent creation."""

    def __init__(self):
        """Initialize the registry."""
        pass

    def create_agent(self, agent_type: str, **kwargs) -> BaseAgent:
        """Create an agent of the specified type.

        Args:
            agent_type: Type of agent to create ("llm_agent", "sec_agent", etc.)
            **kwargs: Agent configuration parameters

        Returns:
            Created agent instance

        Raises:
            ValueError: If agent_type is not supported
        """
        # Handle common model string wrapping for all agent types
        if "model" in kwargs and isinstance(kwargs["model"], str):
            from google.adk.models.lite_llm import LiteLlm

            kwargs["model"] = LiteLlm(model=kwargs["model"])

        # Create agent based on type
        if agent_type == "llm_agent":
            return self._create_llm_agent(**kwargs)
        elif agent_type == "sec_agent":
            return self._create_sec_agent(**kwargs)
        else:
            raise ValueError(
                f"Unknown agent type: {agent_type}. Supported types: llm_agent, sec_agent"
            )

    def _create_llm_agent(self, **kwargs) -> LlmAgent:
        """Create an LLM agent."""
        # Validate required parameters
        required_params = ["name", "model"]
        missing = [param for param in required_params if param not in kwargs]
        if missing:
            raise ValueError(f"Missing required parameters for llm_agent: {missing}")

        return LlmAgent(**kwargs)

    def _create_sec_agent(self, **kwargs) -> BaseAgent:
        """Create a SecAgent."""
        # Validate required parameters
        required_params = ["name", "model"]
        missing = [param for param in required_params if param not in kwargs]
        if missing:
            raise ValueError(f"Missing required parameters for sec_agent: {missing}")

        return SecAgent(**kwargs)


# Global registry instance
_global_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry


def create_agent(agent_type: str, **kwargs) -> BaseAgent:
    """Create an agent using the global registry.

    Args:
        agent_type: Type of agent to create
        **kwargs: Agent configuration parameters

    Returns:
        Created agent instance
    """
    return get_agent_registry().create_agent(agent_type, **kwargs)
