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

import json
from typing import Any, Dict

from google.adk.agents.llm_agent import LlmAgent
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


def add_numbers(a: float, b: float) -> float:
    """Add two numbers together.

    Args:
      a: First number to add
      b: Second number to add

    Returns:
      The sum of a and b
    """
    return a + b


def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers together.

    Args:
      a: First number to multiply
      b: Second number to multiply

    Returns:
      The product of a and b
    """
    return a * b


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
        "length": "length: " * 200 + str(length),
        "width": "width: " * 100 + str(width),
    }


geometry_calculator = OpenSageAgent(
    name="geometry_calculator",
    description="Calculates geometric properties like area and perimeter of shapes",
    model=LiteLlm(model="openai/o4-mini"),
    instruction="""You are a geometry calculator agent. You specialize in calculating geometric properties.
Use the provided tools to calculate areas, perimeters, and other geometric measurements.
Always explain the geometric concepts involved and show the calculation steps.""",
    tools=[calculate_area_and_perimeter],
)

# Create AgentTools from sub-agents
# Note: AgentTool automatically uses the agent's name and description
geometry_tool = AgentTool(agent=geometry_calculator)


def mk_agent(opensage_session_id: str):
    enable_neo4j_logging()

    root_agent = OpenSageAgent(
        name="calculation_orchestrator",
        description="Main agent that coordinates mathematical and geometric calculations with Neo4j history logging",
        model=LiteLlm(model="openai/o4-mini"),
        instruction="""
      You are a calculation orchestrator. You help users with various mathematical and geometric calculations.
      Formulate the final answer as a single number inside <final_answer>...</final_answer> tags.
      """,
        # Agent tools - these are tools that wrap agents
        tools=[
            geometry_tool,
            call_subagent_as_tool,
            create_subagent,
            list_active_agents,
            add_numbers,
            multiply_numbers,
        ],
    )
    return root_agent
