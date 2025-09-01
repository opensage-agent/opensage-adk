from io import StringIO

import pytest
from google.adk.agents import LlmAgent

from aigise.utils.mock_model_cli import MockModelCLI
from aigise.utils.third_party import testing_utils


def add(x: int, y: int) -> int:
    return x + y


def multiply(x: int, y: int) -> int:
    return x * y


@pytest.mark.asyncio
async def test_mock_model_cli(monkeypatch):
    user_inputs = [
        "t",
        "add",
        '{"x": 1, "y": 2}',
        "m",
        "bye bye",
    ]
    monkeypatch.setattr("sys.stdin", StringIO("\n".join(user_inputs) + "\n"))
    agent = LlmAgent(name="cli_agent", model=MockModelCLI(), tools=[add, multiply])
    runner = testing_utils.InMemoryRunner(root_agent=agent)
    events = await runner.run_async("What is 1 + 2 and 3 * 4?")
    assert len(events) == 3
    assert events[1].content.parts[0].function_response.response["result"] == 3
