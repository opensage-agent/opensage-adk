import logging

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from aigise.session import get_aigise_session
from aigise.toolbox.decorators import safe_tool_execution
from aigise.utils.agent_utils import get_aigise_session_id_from_context

logger = logging.getLogger(__name__)


@safe_tool_execution
async def get_idea_from_other_models(tool_context: ToolContext):
    """
    Call this to query another model as a consultant to help you solve the task, you should call this frequently to get an idea of how to solve the task.

    Returns:
        dict with 'idea' containing the other model's suggestion
    """
    try:
        aigise_session_id = get_aigise_session_id_from_context(tool_context)
        session = get_aigise_session(aigise_session_id)
        FLAG_UNJUSTIFIED_CLAIMS_MODEL = session.config.llm.flag_claims_model
        if not FLAG_UNJUSTIFIED_CLAIMS_MODEL:
            print("FLAG_UNJUSTIFIED_CLAIMS_MODEL not configured in LLM settings")
            return []
        model_name = FLAG_UNJUSTIFIED_CLAIMS_MODEL
        # Get session and current conversation history
        session = tool_context._invocation_context.session

        # Get current agent's task/instruction for context
        agent = tool_context._invocation_context.agent
        agent_instruction = getattr(agent, "instruction", "")

        # Build conversation history summary for context
        history_text = []
        for event in session.events:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        history_text.append(f"[{event.author}]: {part.text}")

        context_summary = (
            "\n".join(history_text) if history_text else "No recent history"
        )

        # Construct prompt for the other model
        prompt = f"""You are being consulted by another AI agent who is stuck on a task.

**Original Task**: {agent_instruction}

**Recent conversation history**:
{context_summary}

**The agent needs help with**: Understanding what to do next, what might be missing, or alternative approaches.

Please provide:
1. A brief analysis of what the agent has tried so far
2. Suggestions on what the agent should try next
3. Any potential issues or missing steps you notice

You need to be critical and objective, do not sugarcoat the truth, do not be afraid to tell the agent what they are doing wrong.
You should also find all unjustified claims and flag them, you should find all the unjustified assumptions and flag them. There are probably something missing or wrong in the task, you need to find it and tell the agent.

Keep your response concise and actionable."""

        # Create LLM request
        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig()
        llm_request.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ]

        # Get or create model
        model = LiteLlm(model=model_name)

        # Call model
        idea_parts = []
        async for llm_response in model.generate_content_async(llm_request):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        idea_parts.append(part.text)

        idea = "".join(idea_parts).strip()

        return {
            "success": True,
            "idea": idea,
            "model_used": model_name,
        }

    except Exception as e:
        logger.error(f"Failed to get idea from other models: {e}")
        return {
            "success": False,
            "error": f"Failed to get idea from other models: {str(e)}",
        }


@safe_tool_execution
async def flag_unjustified_claims(tool_context: ToolContext):
    """
    Flag the unjustified claims in the history, this is done by another model

    Returns:
        A natural language analysis of unjustified claims found in the conversation
    """
    # Get model name from environment variable
    aigise_session_id = get_aigise_session_id_from_context(tool_context)
    session = get_aigise_session(aigise_session_id)
    FLAG_UNJUSTIFIED_CLAIMS_MODEL = session.config.llm.flag_claims_model
    if not FLAG_UNJUSTIFIED_CLAIMS_MODEL:
        print("FLAG_UNJUSTIFIED_CLAIMS_MODEL not configured in LLM settings")
        return []
    model_name = FLAG_UNJUSTIFIED_CLAIMS_MODEL

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
                event_info += f"\n  Content: {'\n'.join(content_parts)}"

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

{"\n".join(events_text)}

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


@safe_tool_execution
async def get_available_agents_and_models_for_ensemble(tool_context: ToolContext):
    """
    Get the available agents for the ensemble.
    Uses AgentEnsembleManager to discover static subagents, agent tools, and dynamic agents.
    Only agents whose tools are all covered by THREAD_SAFE_TOOLS are considered safe for ensemble.
    """
    try:
        # Get session ID from tool context or use default
        session_id = get_aigise_session_id_from_context(tool_context)

        # Use session-specific AigiseEnsembleManager
        aigise_session = get_aigise_session(session_id)
        ensemble_manager = aigise_session.ensemble
        current_agent = tool_context._invocation_context.agent

        # Get all ensemble-ready agents (static + dynamic) in current session
        ensemble_result = ensemble_manager.get_ensemble_ready_agents(
            current_agent=current_agent, include_dynamic=True
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

        available_models = aigise_session.ensemble.get_available_models_for_ensemble()

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


def _build_full_instruction(
    instruction: str, include_history: bool, tool_context: ToolContext
) -> str:
    """Build complete instruction with optional conversation history.

    Args:
        instruction: The base instruction
        include_history: Whether to include conversation history
        tool_context: Tool context containing session events

    Returns:
        Complete instruction string with optional history context
    """
    task_parts = [f"=== INSTRUCTION ===\n{instruction}\n"]

    if include_history:
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
                            task_parts.append(f"Event {i}: {event.author}: {part.text}")
            task_parts.append("=== END HISTORY ===")

    return "\n".join(task_parts)


@safe_tool_execution
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
        # Build complete instruction with optional history
        full_instruction = _build_full_instruction(
            instruction, history_passed_in, tool_context
        )

        # Get session and validate agent
        session_id = get_aigise_session_id_from_context(tool_context)
        aigise_session = get_aigise_session(session_id)
        current_agent = tool_context._invocation_context.agent

        # Validate the agent is in the safe agents list and get agent info
        ensemble_result = aigise_session.ensemble.get_ensemble_ready_agents(
            current_agent=current_agent, include_dynamic=True
        )

        # Check if the requested agent is in the safe agents list
        safe_agent_names = [agent.name for agent in ensemble_result["safe_agents"]]
        if agent_name not in safe_agent_names:
            return {
                "success": False,
                "error": f"Agent '{agent_name}' is not in the safe agents list. Available agents: {safe_agent_names}",
                "safe_agents": safe_agent_names,
            }

        # Find the target agent info
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

        # Delegate to ensemble manager with validated agent info
        return await aigise_session.ensemble.execute_agent_ensemble(
            full_instruction=full_instruction,
            target_agent_info=target_agent_info,
            model_name_to_count=model_name_to_count,
            current_agent=current_agent,
            tool_context=tool_context,
        )

    except Exception as e:
        return {
            "success": False,
            "error": f"Agent ensemble failed: {str(e)}",
            "instruction": instruction,
            "agent_name": agent_name,
        }
