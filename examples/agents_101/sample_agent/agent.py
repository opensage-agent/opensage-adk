from google.adk import Agent
from google.adk.models.lite_llm import LiteLlm


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers.

    Args:
        a: The first number to add.
        b: The second number to add.

    Returns:
        The sum of a and b.
    """
    return a + b


root_agent = Agent(
    model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
    name="simple_math_agent",
    instruction="""
    You are a helpful math assistant. You can help users with basic arithmetic operations.
    When a user asks you to add two numbers, use the calculate_add tool to perform the calculation.
    Always use the tool to get accurate results instead of calculating manually.
    Provide clear and friendly responses to the user.
    """,
    description="A simple math agent that can perform addition operations.",
    tools=[
        calculate_add,
    ],
)
