import asyncio
import logging

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.session import get_opensage_session
from opensage.utils.agent_utils import (
    INHERIT_MODEL,
    get_model_from_agent,
    get_opensage_session_id_from_context,
)

logger = logging.getLogger(__name__)


async def complain(complaint: str, tool_context: ToolContext):
    """
    If you have a complaint, you should call this tool to complain about it. E.g., if a tool is hard to use, if a file or folder is supposed to be there but is not, etc. We will take your complaint into consideration and improve the tooling.
    If there is a description that contradicts with the reality, you should call this tool to complain about it.
    Note that the task description is always correct, and there is definitely a way to complete it,you should not complain about it.

    Returns:
        "Complained, we will take your complaint into consideration and improve the tooling."
    """
    return "Complained, we will take your complaint into consideration and improve the tooling."


async def note_suspicious_things(suspicious_things: str, tool_context: ToolContext):
    """
    If you have multiple intereting points or suspicious things to explore, you can call this tool to note them down so that you don't forget them.

    Returns:
        "Noted"
    """
    return "Noted"


async def think(thinking: str, tool_context: ToolContext):
    """
    If you have want to do some reasoning, do not output the reasoning in plain text, call this tool to do the reasoning.

    Returns:
        "Thinking done"
    """
    return "Thinking done"


async def plan(plan: str, tool_context: ToolContext):
    """
    If you have want to do some planning, do not output the plan in plain text, call this tool to do the planning.

    Returns:
        "Planning done"
    """
    return "Planning done"


async def critique(tool_context: ToolContext):
    """
    Call this to query another model as a consultant to help you solve the task, you should call this frequently to get an idea of how to solve the task.

    Returns:
        dict with 'idea' containing the other model's suggestion
    """
    try:
        opensage_session_id = get_opensage_session_id_from_context(tool_context)
        session = get_opensage_session(opensage_session_id)
        FLAG_UNJUSTIFIED_CLAIMS_MODEL = session.config.llm.flag_claims_model
        if not FLAG_UNJUSTIFIED_CLAIMS_MODEL:
            print("FLAG_UNJUSTIFIED_CLAIMS_MODEL not configured in LLM settings")
            return []
        model_name = FLAG_UNJUSTIFIED_CLAIMS_MODEL
        # Get session and current conversation history
        invocation_context = tool_context._invocation_context
        session = invocation_context.session
        current_branch = getattr(invocation_context, "branch", None)

        # Get current agent's task/instruction for context
        agent = invocation_context.agent
        agent_instruction = getattr(agent, "instruction", "")

        def _format_event_to_text(event) -> str:
            """Format event to text, including all information (text, function_call, function_response)."""

            compaction = getattr(getattr(event, "actions", None), "compaction", None)
            if compaction:
                compacted_content = getattr(compaction, "compacted_content", None)
                if compacted_content and getattr(compacted_content, "parts", None):
                    summary_parts = [
                        part.text
                        for part in compacted_content.parts
                        if getattr(part, "text", None)
                    ]
                    if summary_parts:
                        author = getattr(event, "author", "model")
                        return f"[{author}][Summary]: {' | '.join(summary_parts)}"

            parts_text = []

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        parts_text.append(part.text)
                    elif part.function_call:
                        parts_text.append(
                            f"[TOOL_CALL] {part.function_call.name}({part.function_call.args})"
                        )
                    elif part.function_response:
                        parts_text.append(
                            f"[TOOL_RESULT] {part.function_response.name}: {part.function_response.response}"
                        )

            if parts_text:
                return f"[{event.author}]: {' | '.join(parts_text)}"
            return ""

        def _is_branch_match(event) -> bool:
            if not current_branch:
                return True
            event_branch = getattr(event, "branch", None)
            return event_branch is None or event_branch == current_branch

        # Build conversation history summary for context
        events = session.events or []
        branch_events = [event for event in events if _is_branch_match(event)]

        processed_events = branch_events
        if branch_events:
            try:
                from google.adk.flows.llm_flows import contents as adk_contents

                processed = adk_contents._process_compaction_events(branch_events)
                if processed:
                    processed_events = processed
            except Exception as exc:
                logger.warning(
                    "Failed to apply compaction summarization for history: %s", exc
                )

        history_text = []
        for event in processed_events:
            formatted = _format_event_to_text(event)
            if formatted:
                history_text.append(formatted)

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
2. Suggestions on what the agent should see next
3. Any potential issues or missing steps you notice

You need to be critical and objective, do not sugarcoat the truth, do not be afraid to tell the agent what they are doing wrong.
You should also find all unjustified claims and assumptions and flag them.
There are probably something missing or wrong in the task, you need to find it and tell the agent.
There are probably some context missing, the agent might not have all the information it needs to solve the task, indicate what needs to be added to the context.
Does the agent verify the result of the task carefully, considering all possible cases and edge cases?
Keep your response concise and actionable."""

        # Create LLM request
        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig()
        llm_request.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ]

        # Get or create model
        if model_name == INHERIT_MODEL:
            model = get_model_from_agent(agent)
            if model is None:
                return {
                    "success": False,
                    "error": "flag_claims_model='inherit' but current agent has no model",
                }
        else:
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


async def flag_unjustified_claims(tool_context: ToolContext):
    """
    Flag the unjustified claims in the history, this is done by another model

    Returns:
        A natural language analysis of unjustified claims found in the conversation
    """
    # Get model name from environment variable
    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    session = get_opensage_session(opensage_session_id)
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
    if model_name == INHERIT_MODEL:
        current_agent = tool_context._invocation_context.agent
        model = get_model_from_agent(current_agent)
        if model is None:
            return {
                "success": False,
                "error": "flag_claims_model='inherit' but current agent has no model",
            }
    else:
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


async def get_available_agents_for_ensemble(tool_context: ToolContext):
    """
    Get the available agents for the ensemble.
    Uses AgentEnsembleManager to discover static subagents, agent tools, and dynamic agents.
    Only agents whose tools are all covered by THREAD_SAFE_TOOLS are considered safe for ensemble.

    Note that maybe there are no agents that are suitable for the current task, you should create a dynamic subagent that is suitable for the current task and then call it by agent_ensemble tool.
    Pick up thread-safe tools for dynamic agents if you want to create one for the current task.

    Returns:
        Dictionary with safe_agents list, summary, and agent counts
    """
    try:
        # Get session ID from tool context or use default
        session_id = get_opensage_session_id_from_context(tool_context)

        # Use session-specific OpenSageEnsembleManager
        opensage_session = get_opensage_session(session_id)
        ensemble_manager = opensage_session.ensemble
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

        return {
            "success": True,
            "safe_agents": safe_agent_names,
            "safe_agents_details": safe_agents,
            "unsafe_agents_details": unsafe_agents,
            "summary": ensemble_result["summary"],
            "thread_safe_tools": ensemble_result["thread_safe_tools"],
            "static_agents_count": len(ensemble_result["static_agents"]),
            "dynamic_agents_count": len(ensemble_result["dynamic_agents"]),
            "message": f"Found {len(safe_agents)} thread-safe agents out of {ensemble_result['summary']['total_static_agents'] + ensemble_result['summary']['total_dynamic_agents']} total agents. If there are no suitable agents for the current task, you should create a dynamic subagent that is suitable for the current task by calling the create_subagent tool and then call it by agent_ensemble tool.",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get available agents for ensemble: {str(e)}",
            "safe_agents": [],
        }


async def get_available_models(tool_context: ToolContext):
    """
    Get the available models configured for ensemble use.

    Notes:
        - The special model name "inherit" means: reuse the current agent's model
          object from context (i.e., the root/current agent model).

    Returns:
        Dictionary with available_models list and count
    """
    try:
        # Get session ID from tool context or use default
        session_id = get_opensage_session_id_from_context(tool_context)

        # Use session-specific OpenSageEnsembleManager
        opensage_session = get_opensage_session(session_id)
        ensemble_manager = opensage_session.ensemble

        # Get available models for ensemble
        available_models = ensemble_manager.get_available_models()

        return {
            "success": True,
            "available_models": available_models,
            "models_count": len(available_models),
            "message": f"Found {len(available_models)} available models for ensemble",
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get available models for ensemble: {str(e)}",
            "available_models": [],
        }


def _build_full_instruction(
    instruction: str, include_history: bool, tool_context: ToolContext
) -> str:
    """Build complete instruction with optional conversation history.

    Args:
        instruction (str): The base instruction
        include_history (bool): Whether to include conversation history
    Returns:
        str: Complete instruction string with optional history context
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

    Before calling this tool, you must call get_available_agents_for_ensemble and get_available_models FIRST to get the allowed agents and models, as the allowed agents and models may change over time.

    IMPORTANT:
        - "inherit" is a special model name meaning: reuse the current/root
          agent's model object from context.
        - If get_available_models returns only ["inherit"], then you MUST pass
          model_name_to_count={"inherit": N}. This will run N ensemble agents
          using the same model object as the current/root agent.

        Args:
            instruction (str): The specific instruction/task you want all agents to execute
            agent_name (str): The name of the agent to launch (must be in safe agents list)
            model_name_to_count (dict[str, int]): A dictionary of model names and the number of agents to launch with that model, where the key is the model name and the value is the number of agents to launch with that model, the total number of agents to launch is the sum of the values in the dictionary, it should be at least 2.
            history_passed_in (bool): Whether to pass conversation history to agents for additional context
        Returns:
            The aggregated final result from all agents
    """
    if not isinstance(model_name_to_count, dict) or len(model_name_to_count) == 0:
        return {
            "success": False,
            "error": "model_name_to_count must be a non-empty dictionary",
        }
    if sum(model_name_to_count.values()) < 2:
        return {
            "success": False,
            "error": "the total number of agents to launch is less than 2",
        }
    try:
        # Build complete instruction with optional history
        full_instruction = _build_full_instruction(
            instruction, history_passed_in, tool_context
        )

        # Get session and validate agent
        session_id = get_opensage_session_id_from_context(tool_context)
        opensage_session = get_opensage_session(session_id)
        current_agent = tool_context._invocation_context.agent

        # Validate the agent is in the safe agents list and get agent info
        ensemble_result = opensage_session.ensemble.get_ensemble_ready_agents(
            current_agent=current_agent, include_dynamic=True
        )

        # Check if the requested agent is in the safe agents list
        safe_agent_names = [agent.name for agent in ensemble_result["safe_agents"]]
        if agent_name not in safe_agent_names:
            return {
                "success": False,
                "error": f"Agent '{agent_name}' is not in the safe agents list. Available agents: {safe_agent_names}, if no agents are suitable for the current task, you should create a dynamic subagent that is suitable for the current task by calling the create_subagent tool and then call it by agent_ensemble tool. Pick up thread-safe tools for dynamic agents if you want to create one for the current task.",
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
        return await opensage_session.ensemble.execute_agent_ensemble(
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


async def agent_ensemble_pairwise(
    instructions: list[str],
    agent_name: str,
    model_names: list[str],
    history_passed_in: bool,
    tool_context: ToolContext,
):
    """
    Launch multiple agents in parallel, each with its own instruction and model.
    call this tool when you have multiple tasks to complete, for example, you have different approaches to solve the task, you can use this tool to try different approaches in parallel.
    - instructions: list of per-agent instructions
    - model_names: list of per-agent model names (same length as instructions)
    - agent_name: target agent to launch (must be in safe agents list)
    - history_passed_in: whether to include folded history in each instruction

    Examples:
      1) Two tasks on the same model
         instructions = [
           "Summarize repo READMEs",
           "Extract CVEs from logs",
         ]
         model_names = [
           "openai/gpt-5",
           "openai/gpt-5",
         ]

      2) Three tasks with mixed models
         instructions = [
           "Generate remediation plan",
           "List risky endpoints from code",
           "Draft incident report",
         ]
         model_names = [
           "anthropic/claude-sonnet-4-20250514",
           "openai/gpt-5",
           "openai/gpt-5",
         ]
    real instructions should be more specific and detailed, not just a general task description.
    """
    try:
        # Validate inputs
        if not isinstance(instructions, list) or not isinstance(model_names, list):
            return {
                "success": False,
                "error": "instructions and model_names must be lists",
            }
        if len(instructions) == 0 or len(model_names) == 0:
            return {"success": False, "error": "lists must be non-empty"}
        if len(instructions) != len(model_names):
            return {
                "success": False,
                "error": f"lists must have equal length: got {len(instructions)} vs {len(model_names)}",
            }
        for i, (ins, mdl) in enumerate(zip(instructions, model_names)):
            if not isinstance(ins, str) or not isinstance(mdl, str):
                return {
                    "success": False,
                    "error": f"instructions[{i}] and model_names[{i}] must be strings",
                }

        # Get session and validate agent
        session_id = get_opensage_session_id_from_context(tool_context)
        opensage_session = get_opensage_session(session_id)
        current_agent = tool_context._invocation_context.agent

        ensemble_result = opensage_session.ensemble.get_ensemble_ready_agents(
            current_agent=current_agent, include_dynamic=True
        )
        safe_agent_names = [agent.name for agent in ensemble_result["safe_agents"]]
        if agent_name not in safe_agent_names:
            return {
                "success": False,
                "error": f"Agent '{agent_name}' is not in the safe agents list. Available agents: {safe_agent_names}, if no agents are suitable for the current task, you should create a dynamic subagent that is suitable for the current task by calling the create_subagent tool and then call it by agent_ensemble tool. Pick up thread-safe tools for dynamic agents if you want to create one for the current task.",
                "safe_agents": safe_agent_names,
            }

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

        # Build and run tasks in parallel (one model per instruction)
        async def _run_one(instr: str, model_name: str):
            full_instruction = _build_full_instruction(
                instr, history_passed_in, tool_context
            )
            return await opensage_session.ensemble.execute_agent_ensemble(
                full_instruction=full_instruction,
                target_agent_info=target_agent_info,
                model_name_to_count={model_name: 1},
                current_agent=current_agent,
                tool_context=tool_context,
            )

        tasks = [_run_one(instr, mdl) for instr, mdl in zip(instructions, model_names)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        aggregated = []
        for idx, (instr, mdl, res) in enumerate(
            zip(instructions, model_names, results)
        ):
            if isinstance(res, Exception):
                aggregated.append(
                    {
                        "index": idx,
                        "instruction": instr,
                        "model_name": mdl,
                        "success": False,
                        "error": str(res),
                    }
                )
            else:
                aggregated.append(
                    {
                        "index": idx,
                        "instruction": instr,
                        "model_name": mdl,
                        "success": True
                        if isinstance(res, dict) and res.get("success", True)
                        else True,
                        "result": res,
                    }
                )

        return {"success": True, "results": aggregated}

    except Exception as e:
        return {
            "success": False,
            "error": f"Agent ensemble pairwise failed: {str(e)}",
        }
