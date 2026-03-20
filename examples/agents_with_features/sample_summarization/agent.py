# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
from typing import Dict

from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext

from opensage.agents.opensage_agent import OpenSageAgent
from opensage.features import enable_neo4j_logging
from opensage.toolbox.general.dynamic_subagent import (
    call_subagent_as_tool,
    create_subagent,
    list_active_agents,
)
from opensage.utils.agent_utils import (
    discover_all_agents,
    register_callback_to_all_agents,
)


def add_numbers(a: float, b: float) -> float:
    """Add two numbers together.

    Args:
      a: First number to add
      b: Second number to add

    Returns:
      The sum of a and b
    """
    sum = a + b
    return "here is the sum: " * 100 + str(sum)


def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers together.

    Args:
      a: First number to multiply
      b: Second number to multiply

    Returns:
      The product of a and b
    """
    return "here is the product: " * 100 + str(a * b)


def subtract_numbers(a: float, b: float) -> float:
    """Subtract two numbers.

    Args:
      a: First number to subtract
      b: Second number to subtract
    """
    return a - b


def calculate_area_and_perimeter(
    length: float, width: float, tool_context: ToolContext
) -> Dict[str, float]:
    """Calculate area and perimeter of a rectangle.

    Args:
      length: Length of the rectangle
      width: Width of the rectangle

    Returns:
      Dictionary with 'area' and 'perimeter' keys
    """
    area = length * width
    perimeter = 2 * (length + width)
    return {
        "area": area,
        "perimeter": perimeter,
        "length": "length: " * 100 + str(length),
        "width": "width: " * 100 + str(width),
    }


def mk_agent(opensage_session_id: str):
    enable_neo4j_logging()
    os.environ["MAX_HISTORY_SUMMARY_LENGTH"] = "300"
    os.environ["MAX_TOOL_RESPONSE_LENGTH"] = "100"

    # Create agents inside mk_agent to avoid reusing instances across multiple calls
    geometry_calculator = OpenSageAgent(
        name="geometry_calculator",
        description="Calculates geometric properties like area and perimeter of shapes",
        model=LiteLlm(model="openai/gpt-5"),
        instruction="""You are a geometry calculator agent. You specialize in calculating geometric properties.
Use the provided tools to calculate areas, perimeters, and other geometric measurements.
Always explain the geometric concepts involved and show the calculation steps.
Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
""",
        tools=[calculate_area_and_perimeter],
    )

    # Create AgentTools from sub-agents
    # Note: AgentTool automatically uses the agent's name and description
    geometry_tool = AgentTool(agent=geometry_calculator)

    math_calculator = OpenSageAgent(
        name="math_calculator",
        description="Calculates multiplication",
        model=LiteLlm(model="openai/gpt-5"),
        instruction="""You are a math calculator agent. You specialize in calculating mathematical properties.
Use the provided tools to calculate addition and multiplication.
Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
""",
        tools=[multiply_numbers],
    )

    root_agent = OpenSageAgent(
        name="calculation_orchestrator",
        description="Main agent that coordinates mathematical and geometric calculations with Neo4j history logging",
        model=LiteLlm(model="openai/gpt-5"),
        instruction="""You are a calculation orchestrator. You help users with various mathematical and geometric calculations.
      Formulate the final answer as a single number inside <final_answer> ...</final_answer> tags.
      """,
        # Agent tools - these are tools that wrap agents
        tools=[
            geometry_tool,
            create_subagent,
            list_active_agents,
            call_subagent_as_tool,
            add_numbers,
            subtract_numbers,
        ],
        sub_agents=[math_calculator],
    )

    return root_agent
