import json
import os
import traceback
from typing import Any, Dict, List, Optional

from google.adk.events.event import Event
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from neomodel import db

from aigise.extended_features.neo4j_history_manager import get_neo4j_history_manager

DROP_OR_SUMMARIZE_EVENTS_MODEL = (
    os.getenv("DROP_OR_SUMMARIZE_EVENTS_MODEL") or "anthropic/claude-sonnet-4-20250514"
)


def get_all_invocations_for_agent(agent_name: str, tool_context: ToolContext):
    """
    Get all invocations for an agent

    Args:
        agent_name: The name of the agent

    Returns:
        A list of invocations
    """
    history_manager = get_neo4j_history_manager()
    shared_session_id = history_manager.get_shared_session_id(tool_context)
    db_name = f"agent-history-{shared_session_id}".replace("-", "")
    query = f"""
    USE {db_name}
    MATCH (a:AgentRun {{agent_name: $agent_name}})
    RETURN a.input_content as input_content, a.session_id as session_id, a.agent_name as agent_name
    """
    return db.cypher_query(query, {"agent_name": agent_name})


def get_all_invocations_from_session_id(session_id: str, tool_context: ToolContext):
    """
    Get all invocations from an agent with the given session_id, this returns all agent_tools that were called by the agent with the given session_id

    Args:
        session_id: The id of the session
    """
    pass


def get_full_tool_res_and_grep(
    tool_invocation_id: str, grep_pattern: str, tool_context: ToolContext
):
    """
    Get the full tool result and grep the result for the given tool invocation id

    Args:
        tool_invocation_id: The id of the tool invocation
        grep_pattern: The pattern to grep the result

    Returns:
        The grepped result
    """
    pass


def list_all_events_for_session(session_id: str, tool_context: ToolContext):
    """
    List all events for the given session id, for tool responses, only show the ids, no contents will be shown

    Args:
        session_id: The id of the session

    Returns:
        A list of events
    """
    pass


def get_tool_res(tool_invocation_id: str, tool_context: ToolContext):
    """
    Get the result of the tool with the given tool invocation id

    Args:
        tool_invocation_id: The id of the tool invocation

    Returns:
        The result of the tool
    """
    pass


def get_all_events_for_summarization(summarization_id: str, tool_context: ToolContext):
    """
    Get all events for the given summarization id

    Args:
        summarization_id: The id of the summarization

    Returns:
        A list of events
    """
    pass


async def drop_or_summarize_events(tool_context: ToolContext):
    """
    Drop or summarize some of the historical messages that may not be useful in the future, this is done by another model
    """
    # Get model name from environment variable
    model_name = os.getenv("DROP_OR_SUMMARIZE_EVENTS_MODEL")
    if not model_name:
        print("DROP_OR_SUMMARIZE_EVENTS_MODEL environment variable not set")
        return

    # Get events from tool context
    events = tool_context._invocation_context.session.events
    if not events or len(events) <= 1:
        print("Not enough events to process")
        return

    # Create LiteLLM model instance
    model = LiteLlm(model=model_name)

    # Define tool functions for the model to choose from
    def _no_modification() -> Dict[str, str]:
        """No modification needed - keep all events as they are"""
        pass

    def _summarize_events(
        start_index: int, end_index: int, summarization: str
    ) -> Dict[str, Any]:
        """Summarize a range of events into a single summary

        Args:
            start_index: Starting index of events to summarize (inclusive)
            end_index: Ending index of events to summarize (inclusive)
            summarization: The summary text that will replace the events
        """
        pass

    def _drop_events(indices: List[int]) -> Dict[str, Any]:
        """Drop specific events that are not useful

        Args:
            indices: List of event indices to drop
        """
        pass

    # Prepare events with indices for the model
    events_text = []
    for i, event in enumerate(events):
        event_info = f"Index {i}: Author={event.author}, Timestamp={event.timestamp}"
        if event.content and event.content.parts:
            content_parts = []
            for part in event.content.parts:
                if part.text:
                    content_parts.append(
                        f"Text: {part.text[:200]}..."
                        if len(part.text) > 200
                        else f"Text: {part.text}"
                    )
                elif hasattr(part, "function_call") and part.function_call:
                    content_parts.append(
                        f"Function Call: {part.function_call.name}({part.function_call.args})"
                    )
                elif hasattr(part, "function_response") and part.function_response:
                    content_parts.append(
                        f"Function Response: {part.function_response.name} -> {str(part.function_response.response)[:100]}..."
                    )

            if content_parts:
                event_info += f", Content: {'; '.join(content_parts)}"

        events_text.append(event_info)

    # Create the prompt
    prompt = f"""You are analyzing conversation history to decide whether to drop redundant events or summarize related events.

Here are the current {len(events)} events with their indices:

{chr(10).join(events_text)}

Please analyze these events and decide if any optimization is needed:
- Use _no_modification() if all events are useful and should be kept
- Use _summarize_events(start_index, end_index, summarization) to replace a range of related events with a summary
- Use _drop_events([indices]) to remove redundant or unhelpful events

Consider:
1. Redundant events (similar questions, repeated information)
2. Long sequences of related events that could be summarized
3. Events that don't contribute to the conversation context

You must call exactly one of the three functions."""

    # Create LLM request
    llm_request = LlmRequest()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(prompt)])
    ]

    # Add tools to the request
    llm_request.append_tools([_no_modification, _summarize_events, _drop_events])

    try:
        # Call the model
        response = None
        async for llm_response in model.generate_content_async(llm_request):
            response = llm_response
            break

        if not response or not response.content:
            print("No response from model")
            return

        # Process the response to extract function calls
        function_calls = []
        for part in response.content.parts:
            if hasattr(part, "function_call") and part.function_call:
                function_calls.append(part.function_call)

        if not function_calls:
            print("No function calls found in model response")
            return

        # Execute the chosen function call
        function_call = function_calls[0]  # Take the first function call
        function_name = function_call.name
        function_args = function_call.args or {}

        print(f"Model chose: {function_name} with args: {function_args}")

        # Execute the appropriate action
        if function_name == "_no_modification":
            return "No modifications needed"

        elif function_name == "_summarize_events":
            result = _summarize_events(
                start_index=function_args.get("start_index"),
                end_index=function_args.get("end_index"),
                summarization=function_args.get("summarization", ""),
            )
            return "Successfully summarized events"

        elif function_name == "_drop_events":
            result = _drop_events(indices=function_args.get("indices", []))
            return "Successfully dropped events"

        else:
            print(f"Unknown function: {function_name}")
            return (
                "Error in drop_or_summarize_events: Unknown function: " + function_name
            )

    except Exception as e:
        print(f"Error in drop_or_summarize_events: {str(e)}")
        import traceback

        traceback.print_exc()
        return "Error in drop_or_summarize_events: " + str(e)
