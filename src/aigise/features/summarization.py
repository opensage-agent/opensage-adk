import asyncio
import logging
import os
from datetime import datetime

from google.adk.agents.base_agent import BaseAgent
from google.adk.events.event import Event
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from aigise.framework.agent_history_tracker import is_neo4j_logging_enabled
from aigise.utils.agent_utils import (
    discover_all_agents,
    get_aigise_session_id_from_context,
    register_callback_to_all_agents,
)

logger = logging.getLogger(__name__)


async def _get_summary_async(model, llm_request):
    """Get summary from model asynchronously"""
    summary_parts = []
    async for llm_response in model.generate_content_async(llm_request):
        if llm_response.content and llm_response.content.parts:
            for part in llm_response.content.parts:
                if part.text:
                    summary_parts.append(part.text)
    return "".join(summary_parts).strip()


async def tool_response_summarizer_callback(tool, args, tool_context, tool_response):
    """
    Summarize the tool response.
    If neo4j logging is enabled, create a new node in the neo4j database containing the summary of the original tool response, pointing to the original tool response node.
    """
    # Import here to avoid circular import
    from aigise.session import get_aigise_session

    aigise_session_id = get_aigise_session_id_from_context(tool_context)
    aigise_session = get_aigise_session(aigise_session_id)

    MAX_TOOL_RESPONSE_LENGTH = aigise_session.config.history.max_tool_response_length
    if len(str(tool_response)) < MAX_TOOL_RESPONSE_LENGTH:
        return None

    model_name = aigise_session.config.llm.summarize_model
    if not model_name:
        logger.warning(
            "summarize model not configured in LLM settings, trying to use agent model"
        )
        agent = tool_context._invocation_context.agent
        if not hasattr(agent, "canonical_model"):
            logger.warning("Agent has no model, skipping summarization")
            return None
        model = agent.canonical_model
    else:
        model = LiteLlm(model=model_name)

    tool_response = str(tool_response)

    async def run_summary():
        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig()

        summary_prompt = f"""Please summarize the following tool execution concisely:

        Tool: {tool.name}
        Arguments: {args}
        Response: {str(tool_response)[:50000]}{"..." if len(str(tool_response)) > 50000 else ""}

        Instructions:
        1. First, provide a brief 3–5 sentence summary of the key points.
        2. Then, attach the most critical key information from the Response **verbatim** (do not rephrase), so that the evidence for your summary is preserved.
        For example, if the Response is a very long build failure log, only include the error messages or essential lines that clearly reveal the nature of the failure.

        Output format:
        Summary:
        - [your 3–5 sentence summary here]

        Key Information (verbatim):
        - [verbatim key information from the Response here]

        """

        llm_request.contents = [
            types.Content(
                role="user", parts=[types.Part.from_text(text=summary_prompt)]
            )
        ]
        summary = await _get_summary_async(model, llm_request)

        return summary

    try:
        summary = await run_summary()
    except Exception as e:
        logger.error(f"Error summarizing tool response: {e}")
        tool_response = str(tool_response)
        summary = (
            tool_response[:1000] + "..." if len(tool_response) > 1000 else tool_response
        )

    summary = "<Summary by aigise>" + summary + "</Summary by aigise>"

    if is_neo4j_logging_enabled():
        from aigise.utils.neo4j_history_management import (
            create_raw_tool_response_node,
        )

        await create_raw_tool_response_node(
            tool, args, tool_context, tool_response, summary
        )

    return summary


async def history_summarizer_callback(tool, args, tool_context, tool_response):
    """
    Summarize the history.
    If neo4j logging is enabled, create a new node in the neo4j database containing the summary of the original events,
    pointing to the original events,
    detach the original events from the agent run node.

    Args:
        tool: The tool that was executed
        args: Arguments passed to the tool
        tool_context: Context for the tool execution
        tool_response: Response from the tool
    """
    session = tool_context._invocation_context.session
    agent = tool_context._invocation_context.agent

    if not hasattr(agent, "canonical_model"):
        logger.warning("Agent has no model, skipping history summarization")
        return None

    # Get all events from session history
    events = session.events
    if len(events) <= 2:  # Skip if too few events
        return None

    # Calculate total text length
    total_text_length = 0
    all_contents = ""
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    all_contents += part.text
                    total_text_length += len(part.text)

    aigise_session_id = get_aigise_session_id_from_context(tool_context)
    # Import here to avoid circular import
    from aigise.session import get_aigise_session

    aigise_session = get_aigise_session(aigise_session_id)

    MAX_HISTORY_SUMMARY_LENGTH = (
        aigise_session.config.history.max_history_summary_length
    )
    MAX_TOOL_RESPONSE_LENGTH = aigise_session.config.history.max_tool_response_length

    # Check if summarization is needed
    if total_text_length <= int(MAX_HISTORY_SUMMARY_LENGTH) - int(
        MAX_TOOL_RESPONSE_LENGTH
    ):
        return None

    logger.info(
        f"History text length {total_text_length} exceeds threshold {MAX_HISTORY_SUMMARY_LENGTH - MAX_TOOL_RESPONSE_LENGTH}, triggering summarization..."
    )

    # Find incomplete tool calls (no corresponding tool response)
    def get_function_call_id(func_call):
        return func_call.id

    def get_function_response_id(func_response):
        return func_response.id

    # Collect all tool call IDs
    tool_call_ids = set()
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    tool_call_ids.add(get_function_call_id(part.function_call))

    # Find tool responses
    responded_tool_calls = set()
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "function_response") and part.function_response:
                    responded_tool_calls.add(
                        get_function_response_id(part.function_response)
                    )

    # Identify events to keep (incomplete tool calls and recent events)
    events_to_keep = []
    events_to_summarize = []

    for event in events:
        should_keep = False
        if event.content and event.content.parts:
            for part in event.content.parts:
                # Keep tool calls that haven't been responded to
                if hasattr(part, "function_call") and part.function_call:
                    call_id = get_function_call_id(part.function_call)
                    # Check if this function call has been responded to
                    # Note: We use function name matching since responses don't include args
                    if call_id not in responded_tool_calls:
                        should_keep = True
                        break

        if should_keep:
            events_to_keep.append(event)
        else:
            events_to_summarize.append(event)

    if not events_to_summarize:
        return None

    model_name = aigise_session.config.llm.summarize_model
    if not model_name:
        logger.warning(
            "summarize model not configured in LLM settings, trying to use agent model"
        )
        model = agent.canonical_model
    else:
        model = LiteLlm(model=model_name)

    # Generate summary using LLM directly with await
    try:
        summary_text = await _summarize_events_async(model, events_to_summarize)
    except Exception as e:
        logger.error(f"Error summarizing history: {e}")
        return None

    # Create new summary event
    latest_timestamp = max(
        [event.timestamp for event in events_to_summarize],
        default=datetime.now().timestamp(),
    )
    summary_timestamp = latest_timestamp

    # Create summary event content
    summary_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=f"[History Summary] {summary_text}")],
    )

    # Create new event with proper fields
    summary_event = Event(
        invocation_id=tool_context._invocation_context.invocation_id,
        author="user",  # History summaries are treated as user messages
        timestamp=summary_timestamp,
        content=summary_content,
    )

    # Update session events: new summary + kept events
    session.events = [summary_event] + events_to_keep

    logger.info(
        f"History summarized: {len(events_to_summarize)} events → 1 summary event"
    )

    # Handle Neo4j operations if enabled
    if is_neo4j_logging_enabled():
        from aigise.utils.neo4j_history_management import (
            create_history_summary_node,
        )

        await create_history_summary_node(
            tool_context, summary_event, events_to_summarize
        )

    return None


async def _summarize_events_async(model, events_to_summarize):
    """Summarize a list of events using the LLM"""
    # Prepare events text
    events_text = []
    for i, event in enumerate(events_to_summarize):
        event_text = f"Event {i + 1} ({event.timestamp}):\n"
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    event_text += f"  Text: {part.text}\n"
                elif hasattr(part, "function_call") and part.function_call:
                    event_text += f"  Tool Call: {part.function_call.name}({part.function_call.args})\n"
                elif hasattr(part, "function_response") and part.function_response:
                    event_text += f"  Tool Response: {str(part.function_response.response)[:500]}...\n"
        events_text.append(event_text)

    combined_events = "\n".join(events_text)

    summary_prompt = f"""Please create a concise summary of the following conversation history:

{combined_events}

Instructions:
1. Summarize the key topics, decisions, and outcomes
2. Preserve important context and facts
3. Keep the summary under 1000 words
4. Focus on actionable information and key insights

The history events might contain several sub-goals, you should summarize each sub-goal separately, but omit or only mention in high level the detailed steps within each sub-goal.
The overall task may be not completed, you should summarize the progress of the overall task.

Summary:"""
    llm_request = LlmRequest()
    llm_request.config = types.GenerateContentConfig()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=summary_prompt)])
    ]

    return await _get_summary_async(model, llm_request)


def setup_summarization_callbacks(root_agent: BaseAgent):
    """Example of how to add tool name printer callback to all agents."""
    agents = discover_all_agents(root_agent)
    results = register_callback_to_all_agents(
        agents, [history_summarizer_callback, tool_response_summarizer_callback]
    )
    agent_names = [agent.name for agent in agents]
    logger.info(
        f"✅ Registered summarization callbacks to {sum(results.values())} agents: {agent_names}"
    )
