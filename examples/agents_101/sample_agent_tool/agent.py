from google.adk import Agent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers.

    Args:
        a: The first number to add.
        b: The second number to add.

    Returns:
        The sum of a and b.
    """
    return a + b


def calculate_subtract(a: float, b: float) -> float:
    """Calculate the difference of two numbers.

    Args:
        a: The first number to subtract.
        b: The second number to subtract.

    Returns:
        The difference of a and b.
    """
    return a - b


calculation_agent = LlmAgent(
    model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
    name="calculation_agent",
    instruction="""
    You are a helpful math assistant. You can help users with basic arithmetic operations.
    """,
    tools=[
        calculate_add,
    ],
)

calculation_tool = AgentTool(agent=calculation_agent)

root_agent = Agent(
    model=LiteLlm(model="openai/o4-mini"),
    name="simple_math_agent",
    instruction="""
    You are a helpful math assistant. You can help users with basic arithmetic operations.
    When a user asks you to add two numbers, use the calculate_add tool to perform the calculation.
    Always use the tool to get accurate results instead of calculating manually.
    Provide clear and friendly responses to the user.
    """,
    description="A simple math agent that can perform addition operations.",
    tools=[
        calculation_tool,
    ],
)


def mk_agent(opensage_session_id: str = None):
    """Create and return the root agent.

    Args:
        opensage_session_id: Optional session ID (not used in this simple agent)

    Returns:
        The configured root agent
    """
    return root_agent
