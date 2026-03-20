from __future__ import annotations

import json
import logging
import os
import re
import traceback
import uuid
from typing import Any, Dict, List, Optional

from google.adk.events.event import Event
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.session import get_opensage_session
from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import (
    get_neo4j_client_from_context,
    get_opensage_session_id_from_context,
)

logger = logging.getLogger(__name__)


@requires_sandbox("neo4j")
async def get_all_invocations_for_agent(agent_name: str, tool_context: ToolContext):
    """
    Get all invocations for an agent

    Args:
        agent_name (str): The name of the agent
    Returns:
        A list of invocations
    """
    query = """
    MATCH (a:AgentRun {agent_name: $agent_name})
    RETURN a.input_content as input_content, a.session_id as session_id, a.agent_name as agent_name
    """
    # Use history client for agent history queries
    client = await get_neo4j_client_from_context(tool_context, "history")
    result = await client.run_query(query, {"agent_name": agent_name})
    return result, None


@requires_sandbox("neo4j")
async def get_all_agent_runs(tool_context: ToolContext):
    """
    Get all agent runs in the current shared session

    Returns:
        A list of all agent runs with their basic information
    """
    # Use history client for agent history queries
    client = await get_neo4j_client_from_context(tool_context, "history")

    query = """
    MATCH (a:AgentRun)
    RETURN a.session_id as session_id,
           a.agent_name as agent_name,
           a.opensage_session_id as opensage_session_id,
           a.start_time as start_time,
           a.end_time as end_time,
           a.status as status,
           a.input_contents as input_contents,
           a.output_contents as output_contents,
           a.agent_model as agent_model
    ORDER BY a.start_time DESC
    """

    try:
        result = await client.run_query(query)

        # Format the results
        agent_runs = []
        for row in result:
            # Neo4j results with aliases return dictionaries, not indexed tuples
            agent_run_info = {
                "session_id": row["session_id"],
                "agent_name": row["agent_name"],
                "input_contents": row["input_contents"],  # This is a list
                "output_contents": row["output_contents"],  # This is a list
                "agent_model": row["agent_model"],
            }
            agent_runs.append(agent_run_info)

        return agent_runs

    except Exception as e:
        logger.error(f"Failed to get all agent runs: {e}")
        return []


@requires_sandbox("neo4j")
async def get_full_tool_res_and_grep(
    event_id: str, grep_pattern: str, tool_context: ToolContext
):
    """
    Get the RawToolResponse that this event summarizes and grep its raw_content

    Args:
        event_id (str): The id of the event that contains the summary
        grep_pattern (str): The pattern to grep the result
    Returns:
        The grepped result from the original tool response
    """
    # Use history client for agent history queries
    client = await get_neo4j_client_from_context(tool_context, "history")

    # Find RawToolResponse via SUMMARIZES_TOOL_RESPONSE relationship
    query = """
    MATCH (e:Event {event_id: $event_id})-[:SUMMARIZES_TOOL_RESPONSE]->(r:RawToolResponse)
    RETURN r.raw_content as content, r.tool_name as tool_name
    """

    try:
        result = await client.run_query(query, {"event_id": event_id})
        if not result:
            return f"No RawToolResponse found for event_id: {event_id}. This event may not summarize any tool response."

        row = result[0]
        content = row["content"]
        tool_name = row["tool_name"]
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


@requires_sandbox("neo4j")
async def list_all_events_for_session(session_id: str, tool_context: ToolContext):
    """
    List all events for the given session id, for tool responses, only show the ids, no contents will be shown

    Args:
        session_id (str): The id of the session
    Returns:
        A list of events with basic information
    """
    # Use history client for agent history queries
    client = await get_neo4j_client_from_context(tool_context, "history")

    query = """
    MATCH (a:AgentRun {session_id: $session_id})-[:HAS_EVENT]->(e:Event)
    RETURN e.event_id as event_id,
           e.type as event_type,
           e.author as author,
           e.timestamp as timestamp,
           e.invocation_id as invocation_id,
           e.content as content
    ORDER BY e.timestamp ASC
    """

    try:
        result = await client.run_query(query, {"session_id": session_id})

        # Format output and determine category in Python
        formatted_events = []
        for row in result:
            event_type = row["event_type"]
            if event_type == "function_response":
                content = "Content hidden"
            else:
                content = row["content"]

            event_info = {
                "event_id": row["event_id"],
                "type": event_type,
                "author": row["author"],
                "timestamp": row["timestamp"],
                "invocation_id": row["invocation_id"],
                "content": content,
            }
            formatted_events.append(event_info)

        return formatted_events

    except Exception as e:
        logger.error(f"Failed to list events for session {session_id}: {e}")
        return []


@requires_sandbox("neo4j")
async def get_full_tool_res(event_id: str, tool_context: ToolContext):
    """
    Get the RawToolResponse that this event summarizes via SUMMARIZES_TOOL_RESPONSE relationship

    Args:
        event_id (str): The id of the event that contains the summary
    Returns:
        The original tool response that was summarized by this event
    """
    # Use history client for agent history queries
    client = await get_neo4j_client_from_context(tool_context, "history")

    # Find RawToolResponse via SUMMARIZES_TOOL_RESPONSE relationship
    query = """
    MATCH (e:Event {event_id: $event_id})-[:SUMMARIZES_TOOL_RESPONSE]->(r:RawToolResponse)
    RETURN r.node_id as node_id,
           r.tool_name as tool_name,
           r.tool_args as tool_args,
           r.raw_content as raw_content,
           r.summary as summary,
           r.created_at as created_at,
           r.session_id as session_id
    """

    try:
        result = await client.run_query(query, {"event_id": event_id})
        if result:
            row = result[0]
            return {
                "node_id": row["node_id"],
                "tool_name": row["tool_name"],
                "tool_args": row["tool_args"],
                "raw_content": row["raw_content"],
            }

        return {
            "error": f"No RawToolResponse found for event_id: {event_id}. This event may not summarize any tool response."
        }

    except Exception as e:
        return {"error": f"Failed to get tool result: {e}"}


@requires_sandbox("neo4j")
async def get_all_events_for_summarization(
    summarization_id: str, tool_context: ToolContext
):
    """
    Get all events for the given summarization id, for tool responses, only show the ids, no contents will be shown

    Args:
        summarization_id (str): The id of the summarization (event_id of the summary event)
    Returns:
        A list of events that were summarized
    """
    # Use history client for agent history queries
    client = await get_neo4j_client_from_context(tool_context, "history")

    # Find all events that are summarized by the given summarization event
    query = """
    MATCH (summary:Event {event_id: $summarization_id})-[:SUMMARIZES_EVENTS]->(original:Event)
    RETURN original.event_id as event_id,
           original.type as event_type,
           original.author as author,
           original.timestamp as timestamp,
           original.invocation_id as invocation_id,
           original.content as content
    ORDER BY original.timestamp ASC
    """

    try:
        result = await client.run_query(query, {"summarization_id": summarization_id})

        # Format output and determine category in Python
        summarized_events = []
        for row in result:
            event_type = row["event_type"]

            # Categorize event types
            if event_type == "function_response":
                category = "TOOL_RESPONSE"
            elif event_type == "function_call":
                category = "TOOL_CALL"
            elif event_type == "tool_response_summary":
                category = "TOOL_SUMMARY"
            else:
                category = "OTHER"

            # Handle content display based on category
            if category in ["TOOL_RESPONSE", "TOOL_CALL", "TOOL_SUMMARY"]:
                content = "Content hidden"
            else:
                content = row["content"]

            event_info = {
                "event_id": row["event_id"],
                "type": event_type,
                "author": row["author"],
                "timestamp": row["timestamp"],
                "invocation_id": row["invocation_id"],
                "content": content,
            }
            summarized_events.append(event_info)

        # Also get info about the summary event itself
        summary_query = """
        MATCH (summary:Event {event_id: $summarization_id})
        RETURN summary.event_id as event_id,
               summary.type as event_type,
               summary.content as summary_content,
               summary.timestamp as timestamp
        """

        summary_result = await client.run_query(
            summary_query, {"summarization_id": summarization_id}
        )
        summary_info = None
        if summary_result:
            summary_row = summary_result[0]
            summary_info = {
                "summary_event_id": summary_row["event_id"],
                "summary_type": summary_row["event_type"],
                "summary_content": summary_row["summary_content"],
                "summary_timestamp": summary_row["timestamp"],
            }

        return {
            "summarization_id": summarization_id,
            "summary_info": summary_info,
            "summarized_events": summarized_events,
            "total_summarized_events": len(summarized_events),
        }

    except Exception as e:
        logger.error(f"Failed to get events for summarization {summarization_id}: {e}")
        return {
            "error": f"Failed to get summarized events: {e}",
            "summarization_id": summarization_id,
            "summarized_events": [],
        }


@requires_sandbox("neo4j")
async def drop_or_summarize_events(tool_context: ToolContext):
    """
    Drop or summarize some of the historical messages that may not be useful in the future, this is done by another model
    """
    # Get model name from configuration
    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    opensage_session = get_opensage_session(opensage_session_id)

    # Check if LLM configuration exists
    if not opensage_session.config or not opensage_session.config.llm:
        logger.warning("LLM configuration not available")
        return

    model_name = opensage_session.config.llm.summarize_model
    if not model_name:
        logger.warning(
            "summarize model not configured in LLM settings, trying to use agent model"
        )
        agent = tool_context._invocation_context.agent
        if not hasattr(agent, "canonical_model"):
            logger.warning("Agent has no model, skipping drop or summarize events")
            return None
        model = agent.canonical_model
    else:
        model = LiteLlm(model=model_name)

    # Get events from tool context
    events = tool_context._invocation_context.session.events
    if not events or len(events) <= 1:
        logger.info("Not enough events to process")
        return

    def _find_unmatched_tool_calls_and_responses(
        events_slice: List[Event],
    ) -> Dict[str, Any]:
        """Find unmatched tool calls and responses in a slice of events to preserve pairing"""
        tool_calls = {}  # call_id -> event_index
        tool_responses = {}  # call_id -> event_index
        unmatched_calls = []
        unmatched_responses = []

        for i, event in enumerate(events_slice):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        call_id = part.function_call.id
                        tool_calls[call_id] = i
                    elif hasattr(part, "function_response") and part.function_response:
                        response_id = part.function_response.id
                        tool_responses[response_id] = i

        # Find unmatched calls and responses
        for call_id, event_idx in tool_calls.items():
            if call_id not in tool_responses:
                unmatched_calls.append(event_idx)

        for response_id, event_idx in tool_responses.items():
            if response_id not in tool_calls:
                unmatched_responses.append(event_idx)

        return {
            "unmatched_calls": unmatched_calls,
            "unmatched_responses": unmatched_responses,
            "has_unmatched": len(unmatched_calls) > 0 or len(unmatched_responses) > 0,
        }

    def _create_fake_tool_call_response_pair(
        summary: str, start_idx: int, end_idx: int
    ) -> tuple[Event, Event]:
        """Create a fake tool call and response pair for summarization"""
        # Create fake tool call part
        call_id = str(uuid.uuid4())
        call_part = types.Part.from_function_call(
            name="event_summarization", args={"action": "summarize_events"}
        )
        call_part.function_call.id = call_id

        call_content = types.Content(role="model", parts=[call_part])

        call_event = Event(
            invocation_id=tool_context._invocation_context.invocation_id,
            author=tool_context._invocation_context.agent.name,  # Function calls are also authored by the agent
            timestamp=events[
                start_idx
            ].timestamp,  # Use timestamp of first summarized event
            content=call_content,
        )

        # Create fake tool response part
        response_part = types.Part.from_function_response(
            name="event_summarization", response={"summary": summary}
        )
        response_part.function_response.id = call_id

        response_content = types.Content(
            role="user",  # Based on ADK examples, function responses use "user" role
            parts=[response_part],
        )

        response_event = Event(
            invocation_id=tool_context._invocation_context.invocation_id,
            author=tool_context._invocation_context.agent.name,  # Function responses are authored by the agent
            timestamp=events[
                end_idx
            ].timestamp,  # Use timestamp of last summarized event
            content=response_content,
        )

        return call_event, response_event

    # Define tool functions for the model to choose from
    def _no_modification() -> Dict[str, str]:
        """No modification needed - keep all events as they are"""
        return {"status": "success", "message": "No modifications needed"}

    def _summarize_events(
        start_index: int, end_index: int, summarization: str
    ) -> Dict[str, Any]:
        """Summarize a range of events into a single summary

        Args:
            start_index (int): Starting index of events to summarize (inclusive)
            end_index (int): Ending index of events to summarize (inclusive)
            summarization (str): The summary text that will replace the events"""
        try:
            # Validate indices
            if start_index < 0 or end_index >= len(events) or start_index > end_index:
                return {
                    "status": "error",
                    "message": f"Invalid indices: start={start_index}, end={end_index}, total_events={len(events)}",
                }

            # Check for unmatched tool calls/responses and adjust range if needed
            original_range = (start_index, end_index)
            events_to_summarize = events[start_index : end_index + 1]
            pairing_info = _find_unmatched_tool_calls_and_responses(events_to_summarize)

            if pairing_info["has_unmatched"]:
                # Find safe ranges by excluding unmatched events
                unmatched_absolute_indices = set()
                for rel_idx in (
                    pairing_info["unmatched_calls"]
                    + pairing_info["unmatched_responses"]
                ):
                    unmatched_absolute_indices.add(start_index + rel_idx)

                # Find the largest continuous range that doesn't include unmatched events
                safe_ranges = []
                current_start = start_index

                for i in range(start_index, end_index + 1):
                    if i in unmatched_absolute_indices:
                        # End current range before this unmatched event
                        if current_start < i:
                            safe_ranges.append((current_start, i - 1))
                        current_start = i + 1

                # Always check if there's a final range to add after the loop
                if current_start <= end_index:
                    safe_ranges.append((current_start, end_index))

                if not safe_ranges:
                    return {
                        "status": "error",
                        "message": f"Cannot summarize events {start_index}-{end_index}: all events have unmatched tool calls or responses",
                    }

                # Use the largest safe range
                largest_range = max(safe_ranges, key=lambda x: x[1] - x[0])
                start_index, end_index = largest_range
                events_to_summarize = events[start_index : end_index + 1]

                logger.info(
                    f"Adjusted summarization range from {original_range} to ({start_index}, {end_index}) to avoid unmatched tool calls/responses"
                )

            # Create fake tool call and response pair
            fake_call_event, fake_response_event = _create_fake_tool_call_response_pair(
                summarization, start_index, end_index
            )

            # Replace the events: before + fake pair + after
            new_events = (
                events[:start_index]
                + [fake_call_event, fake_response_event]
                + events[end_index + 1 :]
            )

            # Update the session events
            tool_context._invocation_context.session.events = new_events

            logger.info(
                f"Events summarized: {len(events_to_summarize)} events (indices {start_index}-{end_index}) → fake tool call/response pair"
            )

            # Prepare success message
            success_message = f"Successfully summarized {len(events_to_summarize)} events (range {start_index}-{end_index})"
            if original_range != (start_index, end_index):
                success_message += f" (adjusted from original range {original_range[0]}-{original_range[1]} to preserve pairing)"

            return {
                "status": "success",
                "message": success_message,
                "events_summarized": len(events_to_summarize),
                "original_range": original_range,
                "final_range": (start_index, end_index),
            }

        except Exception as e:
            logger.error(f"Error in _summarize_events: {str(e)}")
            import traceback

            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to summarize events: {str(e)}",
            }

    def _drop_events(indices: List[int]) -> Dict[str, Any]:
        """Drop specific events that are not useful

        Args:
            indices (List[int]): List of event indices to drop"""
        try:
            # Validate indices
            invalid_indices = [i for i in indices if i < 0 or i >= len(events)]
            if invalid_indices:
                return {
                    "status": "error",
                    "message": f"Invalid indices: {invalid_indices}, total_events={len(events)}",
                }

            if not indices:
                return {"status": "success", "message": "No events to drop"}

            # Smart pairing check: filter out indices that would break pairing
            original_indices = sorted(set(indices))
            safe_indices = []
            skipped_indices = []

            for idx in original_indices:
                event = events[idx]
                can_drop = True
                skip_reason = None

                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            call_id = part.function_call.id
                            # Look for corresponding response in remaining events
                            found_response = False
                            for other_idx, other_event in enumerate(events):
                                if (
                                    other_idx in original_indices
                                ):  # Skip other events being dropped
                                    continue
                                if other_event.content and other_event.content.parts:
                                    for other_part in other_event.content.parts:
                                        if (
                                            hasattr(other_part, "function_response")
                                            and other_part.function_response
                                            and other_part.function_response.id
                                            == call_id
                                        ):
                                            found_response = True
                                            break
                                if found_response:
                                    break
                            if not found_response:
                                can_drop = False
                                skip_reason = f"tool call (id: {call_id}) has no corresponding response"

                        elif (
                            hasattr(part, "function_response")
                            and part.function_response
                        ):
                            response_id = part.function_response.id
                            # Look for corresponding call in remaining events
                            found_call = False
                            for other_idx, other_event in enumerate(events):
                                if (
                                    other_idx in original_indices
                                ):  # Skip other events being dropped
                                    continue
                                if other_event.content and other_event.content.parts:
                                    for other_part in other_event.content.parts:
                                        if (
                                            hasattr(other_part, "function_call")
                                            and other_part.function_call
                                            and other_part.function_call.id
                                            == response_id
                                        ):
                                            found_call = True
                                            break
                                if found_call:
                                    break
                            if not found_call:
                                can_drop = False
                                skip_reason = f"tool response (id: {response_id}) has no corresponding call"

                if can_drop:
                    safe_indices.append(idx)
                else:
                    skipped_indices.append((idx, skip_reason))

            if not safe_indices:
                return {
                    "status": "error",
                    "message": f"Cannot drop any events: all requested events would break tool call/response pairing",
                }

            if skipped_indices:
                skipped_info = [
                    f"index {idx} ({reason})" for idx, reason in skipped_indices
                ]
                logger.info(
                    f"Adjusted drop operation: skipped {len(skipped_indices)} events to preserve pairing: {'; '.join(skipped_info)}"
                )

            # Use only the safe indices
            indices = safe_indices

            # Safe to drop - create new events list without the dropped indices
            new_events = [event for i, event in enumerate(events) if i not in indices]

            # Update the session events
            tool_context._invocation_context.session.events = new_events

            logger.info(
                f"Events dropped: {len(indices)} events at indices {sorted(indices)}"
            )

            # Prepare success message
            success_message = f"Successfully dropped {len(indices)} events"
            if len(skipped_indices) > 0:
                success_message += (
                    f" (skipped {len(skipped_indices)} events to preserve pairing)"
                )

            return {
                "status": "success",
                "message": success_message,
                "events_dropped": len(indices),
                "dropped_indices": sorted(indices),
                "skipped_indices": [idx for idx, _ in skipped_indices]
                if skipped_indices
                else [],
            }

        except Exception as e:
            logger.error(f"Error in _drop_events: {str(e)}")
            import traceback

            traceback.print_exc()
            return {"status": "error", "message": f"Failed to drop events: {str(e)}"}

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

{"\n".join(events_text)}

Please analyze these events and decide if any optimization is needed:
- Use _no_modification() if all events are useful and should be kept
- Use _summarize_events(start_index, end_index, summarization) to replace a range of related events with a summary
- Use _drop_events([indices]) to remove redundant or unhelpful events

CRITICAL REQUIREMENT: You MUST preserve tool call/response pairing relationships!
- Each Function Call must have its corresponding Function Response
- Never drop or summarize only one part of a tool call/response pair
- The system will AUTOMATICALLY adjust your operations to preserve pairing:
  * For summarization: if unmatched calls/responses are found, the range will be adjusted to exclude them
  * For dropping: events that would break pairing will be automatically skipped
- You can safely specify ranges or indices - the system will handle pairing protection

Consider:
1. Redundant events (similar questions, repeated information)
2. Long sequences of related events that could be summarized
3. Events that don't contribute to the conversation context
4. Tool call/response pairs must be kept together or removed together

You must call exactly one of the three functions."""

    # Create LLM request
    llm_request = LlmRequest()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    ]

    # Add tools to the request - wrap functions with FunctionTool
    llm_request.append_tools(
        [
            FunctionTool(_no_modification),
            FunctionTool(_summarize_events),
            FunctionTool(_drop_events),
        ]
    )

    try:
        # Call the model
        response = None
        async for llm_response in model.generate_content_async(llm_request):
            response = llm_response
            break

        if not response or not response.content:
            logger.warning("No response from model")
            return

        # Process the response to extract function calls
        function_calls = []
        for part in response.content.parts:
            if hasattr(part, "function_call") and part.function_call:
                function_calls.append(part.function_call)

        if not function_calls:
            logger.warning("No function calls found in model response")
            return

        # Execute the chosen function call
        function_call = function_calls[0]  # Take the first function call
        function_name = function_call.name
        function_args = function_call.args or {}

        logger.info(f"Model chose: {function_name} with args: {function_args}")

        # Execute the appropriate action
        if function_name == "_no_modification":
            return "No modifications needed"

        elif function_name == "_summarize_events":
            result = _summarize_events(
                start_index=function_args.get("start_index"),
                end_index=function_args.get("end_index"),
                summarization=function_args.get("summarization", ""),
            )
            if result.get("status") == "success":
                return f"Successfully summarized events: {result.get('message')}"
            else:
                return f"Failed to summarize events: {result.get('message')}"

        elif function_name == "_drop_events":
            result = _drop_events(indices=function_args.get("indices", []))
            if result.get("status") == "success":
                return f"Successfully dropped events: {result.get('message')}"
            else:
                return f"Failed to drop events: {result.get('message')}"

        else:
            logger.error(f"Unknown function: {function_name}")
            return (
                "Error in drop_or_summarize_events: Unknown function: " + function_name
            )

    except Exception as e:
        logger.error(f"Error in drop_or_summarize_events: {str(e)}")
        import traceback

        traceback.print_exc()
        return "Error in drop_or_summarize_events: " + str(e)
