import os

from google.adk import Agent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features import enable_neo4j_logging
from opensage.session import get_opensage_session
from opensage.toolbox.general.agent_tools import (
    agent_ensemble,
    flag_unjustified_claims,
    get_available_agents_for_ensemble,
    get_available_models,
)


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers.

    Args:
        a: The first number to add.
        b: The second number to add.

    Returns:
        The sum of a and b.
    """
    return a + b


calculation_agent = LlmAgent(
    model=LiteLlm(model="openai/o4-mini"),
    name="calculation_agent",
    instruction="""
    You are a helpful math assistant. You can help users with basic arithmetic operations.
    """,
    tools=[
        calculate_add,
    ],
)

calculation_agent_tool = AgentTool(agent=calculation_agent)
enable_neo4j_logging()


def mk_agent(opensage_session_id: str):
    opensage_session = get_opensage_session(opensage_session_id)
    ensemble_manager = opensage_session.ensemble
    ensemble_manager.add_thread_safe_tool("calculate_add")
    config = opensage_session.config
    config.agent_ensemble.available_models_for_ensemble = [
        "openai/o4-mini",
        "openai/gpt-5",
    ]
    opensage_session.config = config
    root_agent = OpenSageAgent(
        model=LiteLlm(model="openai/gpt-5"),
        name="simple_math_agent",
        instruction="""
        You are a helpful math assistant. You can help users with basic arithmetic operations.
        When a user asks you to add two numbers, use the calculate_add tool to perform the calculation.
        Always use the tool to get accurate results instead of calculating manually.
        Provide clear and friendly responses to the user.
        Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
        """,
        description="A simple math agent that can perform addition operations.",
        tools=[
            calculation_agent_tool,
            agent_ensemble,
            get_available_agents_for_ensemble,
            get_available_models,
        ],
    )
    return root_agent
