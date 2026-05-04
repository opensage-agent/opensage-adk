# Basic Agent with a Function Tool

**Source:** [`examples/agents_101/sample_agent`](https://github.com/opensage-agent/opensage-adk/tree/main/examples/agents_101/sample_agent)

The simplest possible OpenSage agent: a single `OpenSageAgent` with one Python function tool. The function's signature and docstring become the tool schema automatically, so no extra registration code is needed.

## Agent Source Code

```python title="examples/agents_101/sample_agent/agent.py"
from google.adk.models.lite_llm import LiteLlm
from opensage.agents.opensage_agent import OpenSageAgent


def calculate_add(a: float, b: float) -> float:
    """Calculate the sum of two numbers.

    Args:
        a: The first number to add.
        b: The second number to add.

    Returns:
        The sum of a and b.
    """
    return a + b


root_agent = OpenSageAgent(
    model=LiteLlm(model="anthropic/claude-opus-4-7"),
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
```

## Run It

```bash
uv run opensage web \
  --agent examples/agents_101/sample_agent \
  --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and ask the agent to add two numbers; it will call `calculate_add` under the hood.
