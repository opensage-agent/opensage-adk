import asyncio
import json
import os

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from aigise.extended_features.agent_ensemble_manager import get_agent_ensemble_manager
from aigise.extended_features.neo4j_history_manager import get_neo4j_history_manager
from aigise.toolbox.general.dynamic_subagent import call_subagent_as_tool
from aigise.utils.agent_utils import _copy_agent_with_updated_model

FLAG_UNJUSTIFIED_CLAIMS_MODEL = (
    os.getenv("FLAG_UNJUSTIFIED_CLAIMS_MODEL") or "anthropic/claude-sonnet-4-20250514"
)


async def flag_unjustified_claims(tool_context: ToolContext):
    """
    Flag the unjustified claims in the history, this is done by another model

    Returns:
        A natural language analysis of unjustified claims found in the conversation
    """
    # Get model name from environment variable
    model_name = FLAG_UNJUSTIFIED_CLAIMS_MODEL
    if not model_name:
        print("FLAG_UNJUSTIFIED_CLAIMS_MODEL not configured")
        return []

    # Get events from tool context
    events = tool_context._invocation_context.session.events
    if not events:
        print("No events to analyze")
        return []

    # Create LiteLLM model instance
    model = LiteLlm(model=model_name)

    # Prepare events text for analysis
    events_text = []
    for i, event in enumerate(events):
        event_info = f"Event {i} (Author: {event.author}, Time: {event.timestamp}):"
        if event.content and event.content.parts:
            content_parts = []
            for part in event.content.parts:
                if part.text:
                    # Include full text for claim analysis
                    content_parts.append(f"Text: {part.text}")
                elif hasattr(part, "function_call") and part.function_call:
                    content_parts.append(f"Function Call: {part.function_call.name}")
                elif hasattr(part, "function_response") and part.function_response:
                    # Include function responses as they might contain claims
                    response_text = str(part.function_response.response)
                    if len(response_text) > 500:
                        response_text = response_text[:500] + "..."
                    content_parts.append(
                        f"Function Response: {part.function_response.name} -> {response_text}"
                    )

            if content_parts:
                event_info += f"\n  Content: {chr(10).join(content_parts)}"

        events_text.append(event_info)

    # Create the prompt for claim analysis
    prompt = f"""You are analyzing a conversation history to identify unjustified claims. An unjustified claim is a statement that:

1. Makes factual assertions without providing evidence or sources
2. States opinions as if they were facts
3. Makes definitive statements about uncertain or complex topics
4. Claims expertise or authority without backing
5. Makes predictions or guarantees without basis
6. States absolute generalizations without qualification

Here is the conversation history with {len(events)} events:

{chr(10).join(events_text)}

Please analyze each event and identify any unjustified claims. For each claim you identify, please include:
- Which event (by index) contains the claim
- The exact text of the unjustified claim
- Why this claim is unjustified (lack of evidence, stated as fact vs opinion, etc.)
- Who made the claim

Guidelines:
- Focus on factual claims that lack support, not opinions clearly stated as opinions
- Consider the context - some claims might be justified by earlier evidence in the conversation
- Look for hedge words like "might", "could", "seems" - their absence in uncertain topics is a red flag
- Technical claims without citations or evidence are particularly suspect
- Personal experiences and preferences are generally not unjustified claims

If no unjustified claims are found, simply state that no problematic claims were identified."""

    # Create LLM request
    llm_request = LlmRequest()
    llm_request.config = types.GenerateContentConfig()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    ]

    try:
        # Call the model
        response = None
        async for llm_response in model.generate_content_async(llm_request):
            response = llm_response
            break

        if not response or not response.content:
            print("No response from model")
            return "No response from model"

        # Extract text response
        response_text = ""
        for part in response.content.parts:
            if part.text:
                response_text += part.text

        if not response_text.strip():
            print("Empty response from model")
            return "Empty response from model"

        # Return the model's natural language response directly
        print("Model analysis of unjustified claims:")
        print(response_text)

        return response_text.strip()

    except Exception as e:
        error_msg = f"Error in flag_unjustified_claims: {str(e)}"
        print(error_msg)
        import traceback

        traceback.print_exc()
        return error_msg


async def get_available_agents_and_models_for_ensemble(tool_context: ToolContext):
    """
    Get the available agents for the ensemble.
    Uses AgentEnsembleManager to discover static subagents, agent tools, and dynamic agents.
    Only agents whose tools are all covered by THREAD_SAFE_TOOLS are considered safe for ensemble.
    """
    try:
        # Use AgentEnsembleManager to get all ensemble-ready agents
        ensemble_manager = get_agent_ensemble_manager()
        root_agent = tool_context._invocation_context.agent

        # Get all ensemble-ready agents (static + dynamic) in current session
        ensemble_result = ensemble_manager.get_ensemble_ready_agents(
            root_agent=root_agent, include_dynamic=True, context=tool_context
        )

        # Convert EnsembleAgentInfo objects to dictionaries for API response
        safe_agents = []
        for agent_info in ensemble_result["safe_agents"]:
            safe_agents.append(
                {
                    "name": agent_info.name,
                    "description": agent_info.description,
                    "tools": agent_info.tools,
                    "model": agent_info.model,
                    "agent_type": agent_info.agent_type,
                    "source_path": agent_info.source_path,
                }
            )

        unsafe_agents = []
        for agent_info in ensemble_result["unsafe_agents"]:
            unsafe_tools = getattr(agent_info, "unsafe_tools", [])
            unsafe_agents.append(
                {
                    "name": agent_info.name,
                    "description": agent_info.description,
                    "tools": agent_info.tools,
                    "model": agent_info.model,
                    "agent_type": agent_info.agent_type,
                    "source_path": agent_info.source_path,
                    "unsafe_tools": unsafe_tools,
                }
            )

        safe_agent_names = [agent["name"] for agent in safe_agents]

        # Get available models from environment variable
        available_models_str = os.getenv("AVAILABLE_MODELS", "")
        available_models = []
        if available_models_str:
            available_models = [
                model.strip()
                for model in available_models_str.split(",")
                if model.strip()
            ]

        return {
            "success": True,
            "safe_agents": safe_agent_names,
            "summary": ensemble_result["summary"],
            "thread_safe_tools": ensemble_result["thread_safe_tools"],
            "available_models": available_models,
            "static_agents_count": len(ensemble_result["static_agents"]),
            "dynamic_agents_count": len(ensemble_result["dynamic_agents"]),
            "message": f"Found {len(safe_agents)} thread-safe agents out of {ensemble_result['summary']['total_static_agents'] + ensemble_result['summary']['total_dynamic_agents']} total agents",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get available agents for ensemble: {str(e)}",
            "safe_agents": [],
        }


async def agent_ensemble(
    instruction: str,
    agent_name: str,
    model_name_to_count: dict[str, int],
    history_passed_in: bool,
    tool_context: ToolContext,
):
    """
    Agent ensemble is a tool that allows launching multiple agents, each with a different model, to perform a task.
    The agent will then aggregate the results from the agents and return the final result.

    Before calling this tool, you must call get_available_agents_and_models_for_ensemble FIRST to get the allowed agents and models, as the allowed agents and models may change over time.

        Args:
            instruction: The specific instruction/task you want all agents to execute
            agent_name: The name of the agent to launch (must be in safe agents list)
            model_name_to_count: A dictionary of model names and the number of agents to launch with that model
            history_passed_in: Whether to pass conversation history to agents for additional context
            tool_context: The tool context

        Returns:
            The aggregated final result from all agents
    """
    try:
        # Step 1: Validate the agent is in the safe agents list
        ensemble_manager = get_agent_ensemble_manager()
        root_agent = tool_context._invocation_context.agent

        ensemble_result = ensemble_manager.get_ensemble_ready_agents(
            root_agent=root_agent, include_dynamic=True, context=tool_context
        )

        # Check if the requested agent is in the safe agents list
        safe_agent_names = [agent.name for agent in ensemble_result["safe_agents"]]
        if agent_name not in safe_agent_names:
            return {
                "success": False,
                "error": f"Agent '{agent_name}' is not in the safe agents list. Available agents: {safe_agent_names}",
                "safe_agents": safe_agent_names,
            }

        # Step 2: Get the target agent info
        target_agent_info = None
        for agent in ensemble_result["safe_agents"]:
            if agent.name == agent_name:
                target_agent_info = agent
                break

        if not target_agent_info:
            return {
                "success": False,
                "error": f"Failed to find agent info for '{agent_name}'",
            }

        # Step 3: Prepare the task message with instruction and optional history
        task_parts = [f"=== INSTRUCTION ===\n{instruction}\n"]

        if history_passed_in:
            # Include conversation history for context
            session_events = tool_context._invocation_context.session.events
            if session_events:
                task_parts.append("=== CONVERSATION HISTORY ===")
                for i, event in enumerate(
                    session_events[-10:]
                ):  # Last 10 events to avoid too much context
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                task_parts.append(
                                    f"Event {i}: {event.author}: {part.text}"
                                )
                task_parts.append("=== END HISTORY ===")

        task_message = "\n".join(task_parts)

        # Step 4: Create multiple agent execution tasks
        agent_tasks = []
        task_descriptions = []

        for model_name, count in model_name_to_count.items():
            for i in range(count):
                task_id = f"{model_name}_{i + 1}"
                task_descriptions.append(f"Agent {task_id} using model {model_name}")

                # Create individual task with model-specific instruction
                enhanced_task_message = f"""
You are running as part of an agent ensemble using model: {model_name}
Task ID: {task_id}

{task_message}

Please provide your unique perspective and analysis. Consider that other agents using different models will also analyze this, so focus on your strengths and provide diverse insights.
"""

                # Create new agent instance with the specified model
                try:
                    agent_with_model = _copy_agent_with_updated_model(
                        target_agent_info, model_name
                    )

                    # Wrap the agent in AgentTool and call it directly
                    agent_tool = AgentTool(agent=agent_with_model)

                    # Fix closure issue by capturing current values as default parameters
                    async def agent_tool_call(
                        captured_agent_tool=agent_tool,
                        captured_task_message=enhanced_task_message,
                        captured_agent_name=agent_with_model.name,
                        captured_model_name=model_name,
                    ):
                        try:
                            # Call the AgentTool directly
                            result = await captured_agent_tool.run_async(
                                args={"request": captured_task_message},
                                tool_context=tool_context,
                            )

                            return {
                                "success": True,
                                "response": str(result),
                                "agent_name": captured_agent_name,
                                "model": captured_model_name,
                            }
                        except Exception as e:
                            return {
                                "success": False,
                                "error": f"AgentTool call failed: {str(e)}",
                                "agent_name": captured_agent_name,
                                "model": captured_model_name,
                            }

                    agent_task = agent_tool_call()

                except Exception as e:
                    # Fallback to original method if agent creation fails
                    print(
                        f"Warning: Failed to create agent with model {model_name}, using original agent: {e}"
                    )
                    agent_task = call_subagent_as_tool(
                        agent_name=agent_name,
                        task_message=enhanced_task_message,
                        tool_context=tool_context,
                    )

                agent_tasks.append((task_id, model_name, agent_task))

        # Step 5: Execute all agents in parallel
        print(f"Launching {len(agent_tasks)} agent instances...")

        # Create all tasks (start parallel execution immediately)
        tasks = []
        for task_id, model_name, task_coroutine in agent_tasks:
            # Use default parameters to capture current loop values (avoid closure issue)
            async def execute_agent_task(coro, tid=task_id, mn=model_name):
                try:
                    result = await coro
                    return {
                        "task_id": tid,
                        "model_name": mn,
                        "success": result.get("success", False),
                        "response": result.get("response", ""),
                        "error": result.get("error", None),
                    }
                except Exception as e:
                    return {
                        "task_id": tid,
                        "model_name": mn,
                        "success": False,
                        "response": "",
                        "error": str(e),
                    }

            task = asyncio.create_task(execute_agent_task(task_coroutine))
            tasks.append(task)

        # Collect all results (tasks are already running in parallel)
        task_results = []
        for task in tasks:
            result = await task
            task_results.append(result)

        # Collect successful results
        successful_results = [r for r in task_results if r["success"]]
        failed_results = [r for r in task_results if not r["success"]]

        if not successful_results:
            return {
                "success": False,
                "error": "All agent executions failed",
                "failed_results": failed_results,
                "total_attempted": len(agent_tasks),
            }

        # Step 6: Aggregate results using LLM
        aggregation_prompt = f"""
You are aggregating responses from {len(successful_results)} different AI agents that analyzed the same instruction using different models.

Original instruction given to all agents:
{instruction}

{"History was provided to agents for context." if history_passed_in else "No conversation history was provided to agents."}

Agent Responses:
"""

        for i, result in enumerate(successful_results):
            aggregation_prompt += f"""

=== Agent {result["task_id"]} (Model: {result["model_name"]}) ===
{result["response"]}
"""

        aggregation_prompt += f"""

=== AGGREGATION INSTRUCTIONS ===
Please analyze all the above responses and create a comprehensive, well-reasoned final answer that:

1. Synthesizes the best insights from all agents
2. Identifies areas of consensus and disagreement
3. Provides a balanced, nuanced perspective
4. Highlights unique insights that only emerged from the ensemble approach
5. Gives a clear, actionable conclusion

If there are significant disagreements between agents, explain the different perspectives and provide your reasoned judgment on which approach is most sound.

Final aggregated response:
"""

        # Use LiteLLM to aggregate results
        aggregation_model = LiteLlm(
            model="anthropic/claude-sonnet-4-20250514"
        )  # Use a capable model for aggregation

        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig()
        llm_request.contents = [
            types.Content(
                role="user", parts=[types.Part.from_text(text=aggregation_prompt)]
            )
        ]

        aggregated_response = ""
        async for llm_response in aggregation_model.generate_content_async(llm_request):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        aggregated_response += part.text

        return {
            "success": True,
            # "instruction": instruction,
            # "agent_name": agent_name,
            # "models_used": list(model_name_to_count.keys()),
            # "history_passed_in": history_passed_in,
            # "total_agents": len(agent_tasks),
            # "successful_agents": len(successful_results),
            # "failed_agents": len(failed_results),
            # "individual_results": task_results,
            "aggregated_response": aggregated_response.strip(),
            "message": f"Successfully ran ensemble with {len(successful_results)}/{len(agent_tasks)} agents",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Agent ensemble failed: {str(e)}",
            "instruction": instruction,
            "agent_name": agent_name,
        }
