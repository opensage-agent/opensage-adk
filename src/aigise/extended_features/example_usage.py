import asyncio
from typing import Any, Dict

from google.adk import Agent
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from .agent_creation_tools import CreateAgentTool, GetAgentInfoTool, ListAgentsTool
from .agent_registry import get_agent_registry, register_agent_template
from .dynamic_agent_manager import AgentStatus, get_dynamic_agent_manager


async def demo_dynamic_agent_creation():
    """Demonstrate dynamic agent creation and management."""

    # Get the manager and registry
    manager = get_dynamic_agent_manager()
    registry = get_agent_registry()

    print("=== SecAgentFramework Dynamic Agent Demo ===")

    # 1. Register a custom template
    math_template = {
        "builder": "llm_agent",
        "model": "anthropic/claude-sonnet-4-20250514",
        "instruction": "You are a specialized math agent. Use your tools to perform calculations accurately.",
        "description": "Math specialist agent with calculation tools",
    }
    registry.register_template("math_specialist", math_template)
    print("✓ Registered math_specialist template")

    # 2. Create an agent from template
    config = {
        "template": "math_specialist",
        "name": "calculator_bot",
        "description": "Calculator bot for basic math operations",
    }

    agent_id, agent = await manager.create_agent(
        config=config, creator="demo_user", persist=True
    )
    print(f"✓ Created agent: {agent.name} (ID: {agent_id})")

    # 3. Activate the agent
    await manager.update_agent_status(agent_id, AgentStatus.ACTIVE)
    print(f"✓ Activated agent: {agent_id}")

    # 4. List all agents
    agents = manager.list_agents()
    print(f"✓ Total agents managed: {len(agents)}")
    for agent_meta in agents:
        print(f"  - {agent_meta.name} ({agent_meta.status.value})")

    # 5. Clone the agent with modifications
    clone_config = {
        "name": "advanced_calculator",
        "description": "Advanced calculator with extended capabilities",
    }

    clone_id, clone_agent = await manager.clone_agent(
        source_id=agent_id, updates=clone_config, creator="demo_user"
    )
    print(f"✓ Cloned agent: {clone_agent.name} (ID: {clone_id})")

    # 6. Get detailed agent info
    metadata = manager.get_agent_metadata(agent_id)
    if metadata:
        print(f"✓ Agent metadata:")
        print(f"  Name: {metadata.name}")
        print(f"  Type: {metadata.type}")
        print(f"  Status: {metadata.status.value}")
        print(f"  Creator: {metadata.creator}")
        print(f"  Created: {metadata.created_at}")

    # 7. Demonstrate agent execution (if tools are available)
    try:
        # Create a simple content for the agent
        content = types.Content(
            role="user", parts=[types.Part.from_text(text="Hello, introduce yourself!")]
        )

        # Create Runner
        runner = Runner(
            app_name=agent.name,
            agent=agent,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

        # Create session
        session = await runner.session_service.create_session(
            app_name=agent.name, user_id="demo_user", state={}
        )

        print(f"✓ Created session for agent execution")

        # Execute agent (just one iteration for demo)
        last_event = None
        count = 0
        async for event in runner.run_async(
            user_id=session.user_id, session_id=session.id, new_message=content
        ):
            last_event = event
            count += 1
            if count >= 1:  # Only get first response
                break

        if last_event and last_event.content and last_event.content.parts:
            response = "\n".join(p.text for p in last_event.content.parts if p.text)
            print(f"✓ Agent response: {response[:100]}...")

    except Exception as e:
        print(f"⚠ Agent execution demo failed: {e}")

    # 8. Clean up - remove agents
    removed = await manager.remove_agent(clone_id)
    print(f"✓ Removed clone agent: {removed}")

    removed = await manager.remove_agent(agent_id)
    print(f"✓ Removed original agent: {removed}")

    print("=== Demo completed ===")


async def demo_tool_based_creation():
    """Demonstrate agent creation using tools."""

    print("\n=== Tool-Based Agent Creation Demo ===")

    # Create tools
    create_tool = CreateAgentTool()
    list_tool = ListAgentsTool()
    info_tool = GetAgentInfoTool()

    # Mock tool context
    class MockToolContext:
        def __init__(self):
            self.user_id = "tool_demo_user"

    tool_context = MockToolContext()

    # 1. Create agent using tool
    create_args = {
        "agent_type": "llm_agent",
        "name": "tool_created_agent",
        "description": "Agent created via tool interface",
        "instruction": "You are a helpful assistant created through the tool interface.",
        "model": "anthropic/claude-sonnet-4-20250514",
    }

    result = await create_tool.run_async(args=create_args, tool_context=tool_context)
    if result["success"]:
        agent_id = result["agent_id"]
        print(f"✓ Tool created agent: {result['agent_name']} (ID: {agent_id})")
    else:
        print(f"✗ Tool creation failed: {result['error']}")
        return

    # 2. List agents using tool
    list_result = await list_tool.call(tool_context, include_templates=True)
    if list_result["success"]:
        print(f"✓ Listed {list_result['total_count']} agents")
        if "available_templates" in list_result:
            print(f"  Available templates: {list_result['available_templates']}")

    # 3. Get agent info using tool
    info_result = await info_tool.call(tool_context, agent_id=agent_id)
    if info_result["success"]:
        agent_info = info_result["agent"]
        print(f"✓ Agent info retrieved:")
        print(f"  Name: {agent_info['name']}")
        print(f"  Status: {agent_info['status']}")
        print(f"  Active: {agent_info['is_active']}")

    # 4. Clean up
    manager = get_dynamic_agent_manager()
    await manager.remove_agent(agent_id)
    print(f"✓ Cleaned up agent: {agent_id}")

    print("=== Tool demo completed ===")


def create_root_agent_with_dynamic_tools():
    """Create a root agent with dynamic agent management tools."""

    # Create tools for dynamic agent management
    tools = [CreateAgentTool(), ListAgentsTool(), GetAgentInfoTool()]

    # Create root agent
    root_agent = Agent(
        model=LiteLlm(model="anthropic/claude-sonnet-4-20250514"),
        name="dynamic_agent_manager_root",
        instruction="""
        You are a root agent capable of creating and managing specialized sub-agents dynamically.

        Your capabilities include:
        1. Creating new agents with custom configurations using create_agent
        2. Listing all managed agents using list_agents
        3. Getting detailed information about specific agents using get_agent_info

        When users request specialized functionality, you can:
        - Create domain-specific agents with appropriate tools and instructions
        - Manage multiple agents for complex workflows
        - Coordinate between different specialized agents

        Use your tools to provide comprehensive agent management services.
        """,
        description="Root agent for dynamic agent creation and management in SecAgentFramework",
        tools=tools,
    )

    return root_agent


if __name__ == "__main__":

    async def main():
        """Run all demos."""
        await demo_dynamic_agent_creation()
        await demo_tool_based_creation()

        # Show how to create root agent
        root_agent = create_root_agent_with_dynamic_tools()
        print(f"\n✓ Created root agent: {root_agent.name}")
        print(f"  Available tools: {[tool.name for tool in root_agent.tools]}")

    asyncio.run(main())
