"""
Neo4j History Operations

Provides functions for recording agent execution history, events, and tool responses in Neo4j.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.tools.tool_context import ToolContext

from opensage.utils.agent_utils import (
    get_neo4j_client_from_context,
    get_opensage_session_id_from_context,
)

logger = logging.getLogger(__name__)


async def record_agent_start(agent: BaseAgent, context: InvocationContext) -> str:
    """Record the start of an agent run in Neo4j."""
    # Use history client for agent history operations (database creation handled automatically)
    client = await get_neo4j_client_from_context(context, "history")
    opensage_session_id = get_opensage_session_id_from_context(context)

    session_id = context.session.id
    try:
        if context.user_content and context.user_content.parts:
            input_content = context.user_content.parts[-1].text
        else:
            input_content = ""
    except Exception as e:
        logger.warning(f"Failed to extract input content: {e}")
        input_content = ""

    query = """
    MERGE (a:AgentRun {session_id: $session_id})
    ON CREATE SET a.agent_name = $agent_name,
                  a.opensage_session_id = $opensage_session_id,
                  a.start_time = $start_time,
                  a.agent_model = $agent_model,
                  a.input_contents = CASE WHEN $input_content = '' THEN [] ELSE [$input_content] END
    ON MATCH SET a.input_contents = COALESCE(a.input_contents, []) +
                    CASE WHEN $input_content = '' THEN [] ELSE [$input_content] END
    RETURN a.session_id as session_id
    """

    try:
        await client.run_query(
            query,
            {
                "session_id": session_id,
                "agent_name": agent.name,
                "opensage_session_id": opensage_session_id,
                "start_time": datetime.now().isoformat(),
                "agent_model": agent.model
                if hasattr(agent, "model") and isinstance(agent.model, str)
                else agent.model.model
                if hasattr(agent, "model")
                else "No model",
                "input_content": input_content,
            },
        )

        # Store the latest event (user input) when agent starts
        if context.session.events:
            latest_event = context.session.events[-1]
            await log_single_event_neo4j(latest_event, session_id, context)

    except Exception as e:
        logger.error(f"Failed to record agent start: {e}")

    return session_id


async def record_agent_end(
    context: InvocationContext,
    output_content: str = "",
    status: str = "completed",
):
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")
    session_id = context.session.id

    query = """
    MATCH (a:AgentRun {session_id: $session_id})
    SET a.end_time = $end_time,
        a.output_contents = COALESCE(a.output_contents, []) +
                           CASE WHEN $output_content = '' THEN [] ELSE [$output_content] END,
        a.status = $status
    """

    try:
        await client.run_query(
            query,
            {
                "session_id": session_id,
                "end_time": datetime.now().isoformat(),
                "output_content": output_content,
                "status": status,
            },
        )
    except Exception as e:
        logger.error(f"Failed to record agent end: {e}")


async def create_agent_call_relation(
    caller_agent_name: str,
    callee_agent_name: str,
    caller_session_id: str,
    callee_session_id: str,
    input_content: str,
    output_content: str,
    caller_agent_model: str,
    callee_agent_model: str,
    context: ToolContext,
):
    """Create a call relationship between caller and callee agents in Neo4j.

    Args:
        caller_agent_name (str): Name of the calling agent
        callee_agent_name (str): Name of the called agent
        caller_session_id (str): Session ID of the caller
        callee_session_id (str): Session ID of the callee
        caller_agent_model (str): Model of the calling agent
        callee_agent_model (str): Model of the called agent
        input_content (str): Input context/parameters for the call (stored as list in Neo4j)
        output_content (str): Output context/result (stored as list in Neo4j)
        context (ToolContext): Session context for database name resolution"""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")

    # First ensure both nodes exist or create them
    create_nodes_query = """
    MERGE (caller:AgentRun {session_id: $caller_session_id})
    ON CREATE SET caller.agent_name = $caller_agent_name,
                    caller.created_at = $timestamp,
                    caller.agent_model = $caller_agent_model
    MERGE (callee:AgentRun {session_id: $callee_session_id})
    ON CREATE SET callee.agent_name = $callee_agent_name,
                    callee.created_at = $timestamp,
                    callee.agent_model = $callee_agent_model
    """

    # Create the call relationship
    create_relation_query = """
    MATCH (caller:AgentRun {session_id: $caller_session_id})
    MATCH (callee:AgentRun {session_id: $callee_session_id})
        CREATE (caller)-[:AGENT_CALLS {
            caller_agent_name: $caller_agent_name,
            callee_agent_name: $callee_agent_name,
            input_contents: [$input_content],
            output_contents: [$output_content],
            agent_call_time: $timestamp,
            caller_agent_session_id: $caller_session_id,
            callee_agent_session_id: $callee_session_id
        }]->(callee)
    """

    timestamp = datetime.now().isoformat()

    try:
        # Create or ensure nodes exist
        await client.run_query(
            create_nodes_query,
            {
                "caller_session_id": caller_session_id,
                "callee_session_id": callee_session_id,
                "caller_agent_name": caller_agent_name,
                "callee_agent_name": callee_agent_name,
                "caller_agent_model": caller_agent_model,
                "callee_agent_model": callee_agent_model,
                "timestamp": timestamp,
            },
        )

        # Create the relationship
        await client.run_query(
            create_relation_query,
            {
                "caller_session_id": caller_session_id,
                "callee_session_id": callee_session_id,
                "caller_agent_name": caller_agent_name,
                "callee_agent_name": callee_agent_name,
                "caller_agent_model": caller_agent_model,
                "callee_agent_model": callee_agent_model,
                "input_content": input_content,
                "output_content": output_content,
                "timestamp": timestamp,
            },
        )

        print(
            f"Created agent call relation: {caller_agent_name} -> {callee_agent_name}"
        )

    except Exception as e:
        logger.error(f"Failed to create agent call relation: {e}")


async def store_session_state(
    session_id: str, state_dict: Dict[str, Any], context: InvocationContext
):
    """Store the session state dictionary to Neo4j."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")

    query = """
    MATCH (a:AgentRun {session_id: $session_id})
    SET a.session_state = $state_dict,
        a.state_updated_at = $timestamp
    """

    try:
        await client.run_query(
            query,
            {
                "session_id": session_id,
                "state_dict": json.dumps(state_dict),  # Serialize to JSON string
                "timestamp": datetime.now().isoformat(),
            },
        )
        logger.info(f"Stored session state for session: {session_id}")
    except Exception as e:
        logger.error(f"Failed to store session state: {e}")


def _determine_event_type(event: Event) -> str:
    """Determine the type of event based on its content."""
    if not event.content or not event.content.parts:
        return "unknown"

    # Check each part for function calls/responses
    for part in event.content.parts:
        if hasattr(part, "function_call") and part.function_call:
            return "function_call"
        if hasattr(part, "function_response") and part.function_response:
            return "function_response"

    # If no function calls/responses and it's a user role
    if event.content.role == "user":
        return "user_prompt"

    # Default for model responses without function calls
    return "model_response"


def _extract_event_content(event: Event) -> str:
    """Extract content from event.content.parts as JSON string."""
    if not event.content or not event.content.parts:
        return "[]"

    content_list = []
    for part in event.content.parts:
        part_dict = {}

        # Extract text content
        if hasattr(part, "text") and part.text:
            part_dict["text"] = part.text

        # Extract function call
        if hasattr(part, "function_call") and part.function_call:
            part_dict["function_call"] = {
                "name": part.function_call.name,
                "args": dict(part.function_call.args)
                if part.function_call.args
                else {},
            }

        # Extract function response
        if hasattr(part, "function_response") and part.function_response:
            part_dict["function_response"] = {
                "name": part.function_response.name,
                "response": part.function_response.response,
            }

        content_list.append(part_dict)

    return json.dumps(content_list)


async def _event_exists(
    event_id: str, session_id: str, context: InvocationContext
) -> bool:
    """Check if event node already exists in Neo4j."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")

    query = """
    MATCH (e:Event {event_id: $event_id, session_id: $session_id})
    RETURN e.event_id as found_event_id
    """

    try:
        result = await client.run_query(
            query, {"event_id": event_id, "session_id": session_id}
        )
        return len(result) > 0
    except Exception as e:
        logger.error(f"Failed to check event existence: {e}")
        return False


async def _create_event_node(event: Event, session_id: str, context: InvocationContext):
    """Create an event node in Neo4j with the required attributes."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")

    # Prepare event data
    event_type = _determine_event_type(event)
    content_parts = _extract_event_content(event)

    # Serialize event to JSON (excluding some heavy fields if needed)
    try:
        raw_content = event.model_dump_json(exclude_none=True)
    except Exception as e:
        logger.error(f"Failed to serialize event: {e}")
        raw_content = json.dumps(
            {"error": "serialization_failed", "event_id": event.id}
        )

    # Create the event node and link it to agent_run
    query = """
    MATCH (a:AgentRun {session_id: $session_id})
    CREATE (e:Event {
        event_id: $event_id,
        session_id: $session_id,
        invocation_id: $invocation_id,
        author: $author,
        type: $event_type,
        raw_content: $raw_content,
        content: $content_parts,
        timestamp: $event_timestamp,
        created_at: $created_at
    })
    CREATE (a)-[:HAS_EVENT]->(e)
    RETURN e.event_id as created_event_id
    """
    try:
        result = await client.run_query(
            query,
            {
                "session_id": session_id,
                "event_id": event.id,
                "invocation_id": event.invocation_id,
                "author": event.author,
                "event_type": event_type,
                "raw_content": raw_content,
                "content_parts": content_parts,
                "event_timestamp": event.timestamp,
                "created_at": datetime.now().isoformat(),
            },
        )

        if result:
            print(
                f"Created event node: {event.id} of type {event_type} at timestamp {event.timestamp}"
            )

    except Exception as e:
        logger.error(f"Failed to create event node for {event.id}: {e}")


async def create_raw_tool_response_node(
    tool, args, tool_context: ToolContext, tool_response, summary
):
    """Create a raw tool response node in Neo4j."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(tool_context, "history")
    session_id = tool_context._invocation_context.session.id

    # Normalize summary: persist exactly the tagged summary block if present
    try:
        import re as _re

        _pattern = r"<Summary by opensage>(.*?)</Summary by opensage>"
        if isinstance(summary, str):
            _m = _re.search(_pattern, summary, _re.DOTALL)
            summary_to_store = _m.group(0) if _m else summary
        else:
            _s = str(summary)
            _m = _re.search(_pattern, _s, _re.DOTALL)
            summary_to_store = _m.group(0) if _m else _s
    except Exception:
        summary_to_store = str(summary)

    query = """
    MATCH (a:AgentRun {session_id: $session_id})
    CREATE (r:RawToolResponse {
        node_id: $node_id,
        session_id: $session_id,
        tool_name: $tool_name,
        tool_args: $tool_args,
        raw_content: $raw_content,
        summary: $summary,
        created_at: $created_at
    })
    CREATE (a)-[:AGENT_RUN_HAS_RAW_TOOL_RESPONSE]->(r)
    RETURN r.node_id as created_node_id
    """

    try:
        result = await client.run_query(
            query,
            {
                "session_id": session_id,
                "node_id": str(uuid.uuid4()),
                "tool_name": tool.name,
                "tool_args": str(args),
                "raw_content": str(tool_response),
                "summary": summary_to_store,
                "created_at": datetime.now().isoformat(),
            },
        )

        if result:
            logger.info(f"Created raw_tool_response node for tool {tool.name}")
            return True

    except Exception as e:
        logger.error(f"Failed to create raw_tool_response node: {e}")
        return False


async def _create_summarize_relation(
    event: Event, session_id: str, context: InvocationContext, summary_content: str
):
    """Create a summarize relation between event and matching RawToolResponse node."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")

    query = """
    MATCH (e:Event {event_id: $event_id})
    MATCH (r:RawToolResponse {session_id: $session_id})
    WHERE r.summary = $summary_content
    CREATE (e)-[:SUMMARIZES_TOOL_RESPONSE]->(r)
    SET e.type = "tool_response_summary"
    RETURN r.node_id as matched_node_id, r.tool_name as tool_name
    """

    try:
        result = await client.run_query(
            query,
            {
                "event_id": event.id,
                "session_id": session_id,
                "summary_content": summary_content,
            },
        )

        if result:
            tool_name = result[0]["tool_name"]
            print(
                f"Created SUMMARIZES relation: RawToolResponse({tool_name}) -> Event({event.id})"
            )
            return True
        else:
            logger.error(f"No matching RawToolResponse found for summary content")
            return False

    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.error(f"Failed to create summarize relation: {e}")
        return False


async def _maybe_create_summarize_relation(
    event: Event, session_id: str, context: InvocationContext
):
    """Check if event contains summary tags and create relation if found."""
    # Use history client for agent history operations (for tool response summarization)
    client = await get_neo4j_client_from_context(context, "history")
    if not (event.content and event.content.parts):
        return False

    pattern = r"<Summary by opensage>(.*?)</Summary by opensage>"

    for part in event.content.parts:
        # Check part.text
        if hasattr(part, "text") and part.text:
            if (
                "<Summary by opensage>" in part.text
                and "</Summary by opensage>" in part.text
            ):
                # Extract the complete content including tags
                match = re.search(pattern, part.text, re.DOTALL)
                if match:
                    # Keep the full content with tags
                    summary_content = match.group(
                        0
                    )  # group(0) includes the entire match with tags
                    return await _create_summarize_relation(
                        event, session_id, context, summary_content
                    )

        # Check part.function_response.response - convert all values to string
        if (
            hasattr(part, "function_response")
            and part.function_response
            and hasattr(part.function_response, "response")
            and part.function_response.response
        ):
            # Convert all response values to string and check for summary tags
            response_dict = part.function_response.response
            for key, value in response_dict.items():
                # Convert value to string
                value_str = str(value) if value is not None else ""
                if (
                    "<Summary by opensage>" in value_str
                    and "</Summary by opensage>" in value_str
                ):
                    match = re.search(pattern, value_str, re.DOTALL)
                    if match:
                        # Keep the full content with tags
                        summary_content = match.group(
                            0
                        )  # group(0) includes the entire match with tags
                        return await _create_summarize_relation(
                            event, session_id, context, summary_content
                        )

    return False


async def log_single_event_neo4j(
    event: Event, session_id: str, context: InvocationContext
):
    """Process a single event, create event node if it doesn't exist."""
    # Use history client for agent history operations
    try:
        # Check if event already exists, event nodes are created after the agent loop yields one
        if not await _event_exists(event.id, session_id, context):
            # Create event node
            await _create_event_node(event, session_id, context)
            # Check if event contains summary tags and create relation if found (for tool response summarization)
            await _maybe_create_summarize_relation(event, session_id, context)
        else:
            logger.info(f"Event {event.id} already exists, skipping")

    except Exception as e:
        logger.error(
            f"Failed to process event {event.id} at timestamp {event.timestamp}: {e}"
        )


async def find_agent_run_by_session_id(
    session_id: str, context: InvocationContext
) -> Optional[str]:
    """Find agent_run node with the given session_id."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(context, "history")

    query = """
    MATCH (a:AgentRun {session_id: $session_id})
    RETURN a.session_id as found_session_id, a.agent_name as agent_name
    """
    try:
        result = await client.run_query(query, {"session_id": session_id})
        if result:
            return result[0]["found_session_id"]
        return None
    except Exception as e:
        logger.error(f"Failed to find agent_run by session_id: {e}")
        return None


async def create_history_summary_node(
    tool_context: ToolContext, summary_event: Event, events_to_summarize: list
):
    """Create history summary node and manage relationships in Neo4j."""
    # Use history client for agent history operations
    client = await get_neo4j_client_from_context(tool_context, "history")
    session_id = tool_context._invocation_context.session.id

    # First create the summary event node
    try:
        create_summary_query = """

        MATCH (a:AgentRun {session_id: $session_id})
        CREATE (s:Event {
            event_id: $event_id,
            session_id: $session_id,
            role: $role,
            content: $content,
            timestamp: $timestamp,
            type: "history_summary",
            created_at: $created_at
        })
        CREATE (a)-[:HAS_EVENT]->(s)
        RETURN s.event_id as event_id
        """

        # Extract summary content
        summary_content = ""
        comp = getattr(getattr(summary_event, "actions", None), "compaction", None)
        compacted = getattr(comp, "compacted_content", None) if comp else None
        if compacted and getattr(compacted, "parts", None):
            for part in compacted.parts:
                if getattr(part, "text", None):
                    summary_content += part.text
        params = {
            "event_id": summary_event.id,
            "session_id": session_id,
            "role": summary_event.content.role if summary_event.content else "user",
            "content": summary_content,
            "timestamp": datetime.fromtimestamp(summary_event.timestamp).isoformat(),
            "created_at": datetime.now().isoformat(),
        }

        result = await client.run_query(create_summary_query, params)

        logger.info(f"Created history summary node: {summary_event.id}")

        # Now handle the summarized events
        for event in events_to_summarize:
            # Remove HAS_EVENT relationship from AgentRun
            remove_relation_query = """

            MATCH (a:AgentRun {session_id: $session_id})-[r:HAS_EVENT]->(e:Event {event_id: $event_id})
            DELETE r
            """

            await client.run_query(
                remove_relation_query,
                {"session_id": session_id, "event_id": event.id},
            )

            # Create SUMMARIZES_EVENTS relationship
            create_summarize_relation_query = """

            MATCH (s:Event {event_id: $summary_event_id})
            MATCH (e:Event {event_id: $event_id})
            CREATE (s)-[:SUMMARIZES_EVENTS]->(e)
            """

            await client.run_query(
                create_summarize_relation_query,
                {"summary_event_id": summary_event.id, "event_id": event.id},
            )

        print(
            f"History summary processed: {len(events_to_summarize)} events summarized into {summary_event.id}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to create history summary node: {e}")
        return False
