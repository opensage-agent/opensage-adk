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


def get_all_agent_runs(tool_context: ToolContext):
    """
    Get all agent runs in the current shared session

    Returns:
        A list of all agent runs with their basic information
    """
    history_manager = get_neo4j_history_manager()
    shared_session_id = history_manager.get_shared_session_id(tool_context)
    db_name = f"agent-history-{shared_session_id}".replace("-", "")

    query = f"""
    USE {db_name}
    MATCH (a:AgentRun)
    RETURN a.session_id as session_id,
           a.agent_name as agent_name,
           a.shared_session_id as shared_session_id,
           a.start_time as start_time,
           a.end_time as end_time,
           a.status as status,
           a.input_contents as input_contents,
           a.output_contents as output_contents,
           a.agent_model as agent_model
    ORDER BY a.start_time DESC
    """

    try:
        result, _ = db.cypher_query(query)

        # Format the results
        agent_runs = []
        for row in result:
            agent_run_info = {
                "session_id": row[0],
                "agent_name": row[1],
                "input_contents": row[6],  # This is a list
                "output_contents": row[7],  # This is a list
                "agent_model": row[8],
            }
            agent_runs.append(agent_run_info)

        return agent_runs

    except Exception as e:
        print(f"Failed to get all agent runs: {e}")
        return []


def get_full_tool_res_and_grep(
    event_id: str, grep_pattern: str, tool_context: ToolContext
):
    """
    Get the RawToolResponse that this event summarizes and grep its raw_content

    Args:
        event_id: The id of the event that contains the summary
        grep_pattern: The pattern to grep the result

    Returns:
        The grepped result from the original tool response
    """
    import re

    history_manager = get_neo4j_history_manager()
    shared_session_id = history_manager.get_shared_session_id(tool_context)
    db_name = f"agent-history-{shared_session_id}".replace("-", "")

    # Find RawToolResponse via SUMMARIZES_TOOL_RESPONSE relationship
    query = f"""
    USE {db_name}
    MATCH (e:Event {{event_id: $event_id}})-[:SUMMARIZES_TOOL_RESPONSE]->(r:RawToolResponse)
    RETURN r.raw_content as content, r.tool_name as tool_name
    """

    try:
        result, _ = db.cypher_query(query, {"event_id": event_id})
        if not result:
            return f"No RawToolResponse found for event_id: {event_id}. This event may not summarize any tool response."

        content = result[0][0]
        tool_name = result[0][1]
        source_type = f"RawToolResponse({tool_name})"

        if not content:
            return f"No content found in RawToolResponse for event_id: {event_id}"

        # Perform grep on content
        content_str = str(content)
        matching_lines = []

        for line_num, line in enumerate(content_str.split("\n"), 1):
            if re.search(grep_pattern, line, re.IGNORECASE):
                matching_lines.append(f"{line_num}: {line}")

        if matching_lines:
            return (
                f"Found {len(matching_lines)} matching lines in {source_type}:\n"
                + "\n".join(matching_lines)
            )
        else:
            return f"No matches found for pattern '{grep_pattern}' in {source_type}"

    except Exception as e:
        return f"Error searching tool result: {e}"


def list_all_events_for_session(session_id: str, tool_context: ToolContext):
    """
    List all events for the given session id, for tool responses, only show the ids, no contents will be shown

    Args:
        session_id: The id of the session

    Returns:
        A list of events with basic information
    """
    history_manager = get_neo4j_history_manager()
    shared_session_id = history_manager.get_shared_session_id(tool_context)
    db_name = f"agent-history-{shared_session_id}".replace("-", "")

    query = f"""
    USE {db_name}
    MATCH (a:AgentRun {{session_id: $session_id}})-[:HAS_EVENT]->(e:Event)
    RETURN e.event_id as event_id,
           e.type as event_type,
           e.author as author,
           e.timestamp as timestamp,
           e.invocation_id as invocation_id,
           CASE
             WHEN e.type = 'function_response' THEN 'TOOL_RESPONSE'
             WHEN e.type = 'function_call' THEN 'TOOL_CALL'
             WHEN e.type = 'tool_response_summary' THEN 'TOOL_SUMMARY'
             ELSE 'OTHER'
           END as category
    ORDER BY e.timestamp ASC
    """

    try:
        result, _ = db.cypher_query(query, {"session_id": session_id})

        # Format output to hide content for tool responses
        formatted_events = []
        for row in result:
            event_info = {
                "event_id": row[0],
                "type": row[1],
                "author": row[2],
                "timestamp": row[3],
                "invocation_id": row[4],
                "category": row[5],
                "content": "Content hidden"
                if row[5] in ["TOOL_RESPONSE", "TOOL_CALL", "TOOL_SUMMARY"]
                else "Available",
            }
            formatted_events.append(event_info)

        return formatted_events

    except Exception as e:
        print(f"Failed to list events for session {session_id}: {e}")
        return []


def get_full_tool_res(event_id: str, tool_context: ToolContext):
    """
    Get the RawToolResponse that this event summarizes via SUMMARIZES_TOOL_RESPONSE relationship

    Args:
        event_id: The id of the event that contains the summary

    Returns:
        The original tool response that was summarized by this event
    """
    history_manager = get_neo4j_history_manager()
    shared_session_id = history_manager.get_shared_session_id(tool_context)
    db_name = f"agent-history-{shared_session_id}".replace("-", "")

    # Find RawToolResponse via SUMMARIZES_TOOL_RESPONSE relationship
    query = f"""
    USE {db_name}
    MATCH (e:Event {{event_id: $event_id}})-[:SUMMARIZES_TOOL_RESPONSE]->(r:RawToolResponse)
    RETURN r.node_id as node_id,
           r.tool_name as tool_name,
           r.tool_args as tool_args,
           r.raw_content as raw_content,
           r.summary as summary,
           r.created_at as created_at,
           r.session_id as session_id
    """

    try:
        result, _ = db.cypher_query(query, {"event_id": event_id})
        if result:
            row = result[0]
            return {
                "node_id": row[0],
                "tool_name": row[1],
                "tool_args": row[2],
                "raw_content": row[3],
            }

        return {
            "error": f"No RawToolResponse found for event_id: {event_id}. This event may not summarize any tool response."
        }

    except Exception as e:
        return {"error": f"Failed to get tool result: {e}"}


def get_all_events_for_summarization(summarization_id: str, tool_context: ToolContext):
    """
    Get all events for the given summarization id, for tool responses, only show the ids, no contents will be shown

    Args:
        summarization_id: The id of the summarization (event_id of the summary event)

    Returns:
        A list of events that were summarized
    """
    history_manager = get_neo4j_history_manager()
    shared_session_id = history_manager.get_shared_session_id(tool_context)
    db_name = f"agent-history-{shared_session_id}".replace("-", "")

    # Find all events that are summarized by the given summarization event
    query = f"""
    USE {db_name}
    MATCH (summary:Event {{event_id: $summarization_id}})-[:SUMMARIZES_EVENTS]->(original:Event)
    RETURN original.event_id as event_id,
           original.type as event_type,
           original.author as author,
           original.timestamp as timestamp,
           original.invocation_id as invocation_id,
           CASE
             WHEN original.type = 'function_response' THEN 'TOOL_RESPONSE'
             WHEN original.type = 'function_call' THEN 'TOOL_CALL'
             WHEN original.type = 'tool_response_summary' THEN 'TOOL_SUMMARY'
             ELSE 'OTHER'
           END as category
    ORDER BY original.timestamp ASC
    """

    try:
        result, _ = db.cypher_query(query, {"summarization_id": summarization_id})

        # Format output to hide content for tool responses
        summarized_events = []
        for row in result:
            event_info = {
                "event_id": row[0],
                "type": row[1],
                "author": row[2],
                "timestamp": row[3],
                "invocation_id": row[4],
                "category": row[5],
                "content": "Content hidden"
                if row[5] in ["TOOL_RESPONSE", "TOOL_CALL", "TOOL_SUMMARY"]
                else "IDs only",
            }
            summarized_events.append(event_info)

        # Also get info about the summary event itself
        summary_query = f"""
        USE {db_name}
        MATCH (summary:Event {{event_id: $summarization_id}})
        RETURN summary.event_id as event_id,
               summary.type as event_type,
               summary.content as summary_content,
               summary.timestamp as timestamp
        """

        summary_result, _ = db.cypher_query(
            summary_query, {"summarization_id": summarization_id}
        )
        summary_info = None
        if summary_result:
            summary_row = summary_result[0]
            summary_info = {
                "summary_event_id": summary_row[0],
                "summary_type": summary_row[1],
                "summary_content": summary_row[2],
                "summary_timestamp": summary_row[3],
            }

        return {
            "summarization_id": summarization_id,
            "summary_info": summary_info,
            "summarized_events": summarized_events,
            "total_summarized_events": len(summarized_events),
        }

    except Exception as e:
        print(f"Failed to get events for summarization {summarization_id}: {e}")
        return {
            "error": f"Failed to get summarized events: {e}",
            "summarization_id": summarization_id,
            "summarized_events": [],
        }


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
