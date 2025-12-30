## How to use

```python
from examples.agents.code_understanding_agent import create_code_understanding_agent_tool
from google.adk.models import BaseLlm, Gemini
from google.adk.agents import LlmAgent

code_understanding_agent_tool = create_code_understanding_agent_tool(
    model = Gemini(model = "gemini-2.5-flash"),  # should be a BaseLlm instance
    name = "code_understanding_agent",
)
agent = LlmAgent(
    name = "example_agent",
    model = Gemini(model = "gemini-2.5-flash"),
    tools = [code_understanding_agent_tool],
)
```
