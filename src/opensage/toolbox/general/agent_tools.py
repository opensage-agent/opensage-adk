import asyncio
import logging

from google.adk.models.llm_request import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.session import get_opensage_session
from opensage.utils.agent_utils import (
    create_litellm_model,
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
        configured_model = session.config.llm.flag_claims_model
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

        # Resolve model: use configured ``flag_claims_model`` if set, else fall
        # back to the calling agent's own model object directly. No "inherit"
        # sentinel — the fallback is the explicit semantics for an empty config.
        if configured_model:
            model = create_litellm_model(configured_model)
            model_used = configured_model
        else:
            model = get_model_from_agent(agent)
            if model is None:
                return {
                    "success": False,
                    "error": (
                        "flag_claims_model is not configured and the calling "
                        "agent has no usable model to fall back to"
                    ),
                }
            model_used = getattr(model, "model", "<caller-model>")

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
            "model_used": model_used,
        }

    except Exception as e:
        logger.exception(f"Failed to get idea from other models: {e}")
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
    # Get model name from config; fall back to caller agent's model directly.
    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    session = get_opensage_session(opensage_session_id)
    configured_model = session.config.llm.flag_claims_model

    # Get events from tool context
    events = tool_context._invocation_context.session.events
    if not events:
        print("No events to analyze")
        return []

    if configured_model:
        model = create_litellm_model(configured_model)
    else:
        current_agent = tool_context._invocation_context.agent
        model = get_model_from_agent(current_agent)
        if model is None:
            return {
                "success": False,
                "error": (
                    "flag_claims_model is not configured and the calling "
                    "agent has no usable model to fall back to"
                ),
            }

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
