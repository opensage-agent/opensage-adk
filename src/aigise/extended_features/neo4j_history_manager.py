from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.tools.tool_context import ToolContext
from neomodel import db


class Neo4jHistoryManager:
    def __init__(self):
        self._setup_connection()

    def _setup_connection(self):
        db.set_connection(
            f"bolt://{os.getenv('NEO4J_USER')}:{os.getenv('NEO4J_PASSWORD')}@{os.getenv('NEO4J_URI_SUFFIX')}"
        )

    def get_shared_session_id(self, context) -> str:
        if hasattr(context, "_invocation_context"):
            session = context._invocation_context.session
        elif hasattr(context, "session"):
            session = context.session

        if "shared_session_id" not in session.state:
            session.state["shared_session_id"] = session.id

        return session.state["shared_session_id"]

    def create_session_database(self, shared_session_id: str):
        db_name = f"agent-history-{shared_session_id}".replace("-", "")
        try:
            # 1. Create the database if it does not exist
            db.cypher_query(f"CREATE DATABASE {db_name} IF NOT EXISTS")

            # 2. Wait until the database is online
            if not self._wait_for_db_online(db_name, timeout=30):
                print(f"Database {db_name} did not come online in time.")
                return

            # 3. Create constraints and indexes
            constraints = [
                f"USE {db_name} CREATE CONSTRAINT agent_run_key IF NOT EXISTS "
                f"FOR (a:AgentRun) REQUIRE (a.session_id) IS UNIQUE",
            ]

            for constraint in constraints:
                try:
                    db.cypher_query(constraint)
                except Exception as e:
                    print(f"Failed to create constraint {constraint}: {e}")
                    pass

        except Exception as e:
            print(f"Failed to create database {db_name}: {e}")

    def _wait_for_db_online(self, db_name: str, timeout: int = 30) -> bool:
        """Poll SHOW DATABASES until the target database is online or timeout."""
        for _ in range(timeout):
            try:
                result, _ = db.cypher_query("SHOW DATABASES")
                # result is a list of rows, row[0] is database name, row[8] is currentStatus
                for row in result:
                    if row[0] == db_name and row[8].lower() == "online":
                        return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def record_agent_start(self, agent: BaseAgent, context: InvocationContext) -> str:
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")
        self.create_session_database(shared_session_id)

        session_id = context.session.id
        try:
            input_content = context.user_content.parts[-1].text
        except:
            input_content = ""

        query = f"""
        USE {db_name}
        MERGE (a:AgentRun {{session_id: $session_id}})
        ON CREATE SET a.agent_name = $agent_name,
                      a.shared_session_id = $shared_session_id,
                      a.start_time = $start_time,
                      a.input_contents = [$input_content]
        ON MATCH SET a.input_contents = COALESCE(a.input_contents, []) + [$input_content]
        RETURN a.session_id as session_id
        """

        try:
            db.cypher_query(
                query,
                {
                    "session_id": session_id,
                    "agent_name": agent.name,
                    "shared_session_id": shared_session_id,
                    "start_time": datetime.now().isoformat(),
                    "input_content": input_content,
                },
            )

            # Store the latest event (user input) when agent starts
            if context.session.events:
                latest_event = context.session.events[-1]
                self.process_single_event(latest_event, session_id, context)

        except Exception as e:
            print(f"Failed to record agent start: {e}")

        return session_id

    def record_agent_end(
        self,
        agent: BaseAgent,
        context,
        output_content: str = "",
        status: str = "completed",
    ):
        session_id = context.session.id

        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        query = f"""
        USE {db_name}
        MATCH (a:AgentRun {{session_id: $session_id}})
        SET a.end_time = $end_time,
            a.output_contents = COALESCE(a.output_contents, []) + [$output_content],
            a.status = $status
        """

        try:
            db.cypher_query(
                query,
                {
                    "session_id": session_id,
                    "end_time": datetime.now().isoformat(),
                    "output_content": output_content,
                    "status": status,
                },
            )
        except Exception as e:
            print(f"Failed to record agent end: {e}")

    def create_agent_call_relation(
        self,
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
            caller_agent_name: Name of the calling agent
            callee_agent_name: Name of the called agent
            caller_session_id: Session ID of the caller
            callee_session_id: Session ID of the callee
            caller_agent_model: Model of the calling agent
            callee_agent_model: Model of the called agent
            input_content: Input context/parameters for the call (stored as list in Neo4j)
            output_content: Output context/result (stored as list in Neo4j)
            context: Session context for database name resolution
        """
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        # First ensure both nodes exist or create them
        create_nodes_query = f"""
        USE {db_name}
        MERGE (caller:AgentRun {{session_id: $caller_session_id}})
        ON CREATE SET caller.agent_name = $caller_agent_name,
                     caller.created_at = $timestamp,
                     caller.agent_model = $caller_agent_model
        MERGE (callee:AgentRun {{session_id: $callee_session_id}})
        ON CREATE SET callee.agent_name = $callee_agent_name,
                     callee.created_at = $timestamp,
                     callee.agent_model = $callee_agent_model
        """

        # Create the call relationship
        create_relation_query = f"""
        USE {db_name}
        MATCH (caller:AgentRun {{session_id: $caller_session_id}})
        MATCH (callee:AgentRun {{session_id: $callee_session_id}})
         CREATE (caller)-[:AGENT_CALLS {{
             caller_agent_name: $caller_agent_name,
             callee_agent_name: $callee_agent_name,
             input_contents: [$input_content],
             output_contents: [$output_content],
             agent_call_time: $timestamp,
             caller_agent_session_id: $caller_session_id,
             callee_agent_session_id: $callee_session_id
         }}]->(callee)
        """

        timestamp = datetime.now().isoformat()

        try:
            # Create or ensure nodes exist
            db.cypher_query(
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
            db.cypher_query(
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
            print(f"Failed to create agent call relation: {e}")

    def store_session_state(self, session_id: str, state_dict: Dict[str, Any], context):
        """Store the session state dictionary to Neo4j."""
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        query = f"""
        USE {db_name}
        MATCH (a:AgentRun {{session_id: $session_id}})
        SET a.session_state = $state_dict,
            a.state_updated_at = $timestamp
        """

        try:
            db.cypher_query(
                query,
                {
                    "session_id": session_id,
                    "state_dict": json.dumps(state_dict),  # Serialize to JSON string
                    "timestamp": datetime.now().isoformat(),
                },
            )
            print(f"Stored session state for session: {session_id}")
        except Exception as e:
            print(f"Failed to store session state: {e}")

    def _determine_event_type(self, event) -> str:
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

    def _extract_event_content(self, event) -> str:
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

    def event_exists(self, event_id: str, session_id: str, context) -> bool:
        """Check if event node already exists in Neo4j."""
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        query = f"""
        USE {db_name}
        MATCH (e:Event {{event_id: $event_id, session_id: $session_id}})
        RETURN e.event_id as found_event_id
        """

        try:
            result, _ = db.cypher_query(
                query, {"event_id": event_id, "session_id": session_id}
            )
            return len(result) > 0
        except Exception as e:
            print(f"Failed to check event existence: {e}")
            return False

    def create_event_node(self, event, session_id: str, context):
        """Create an event node in Neo4j with the required attributes."""
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        # Prepare event data
        event_type = self._determine_event_type(event)
        content_parts = self._extract_event_content(event)

        # Serialize event to JSON (excluding some heavy fields if needed)
        try:
            raw_content = event.model_dump_json(exclude_none=True)
        except Exception as e:
            print(f"Failed to serialize event: {e}")
            raw_content = json.dumps(
                {"error": "serialization_failed", "event_id": event.id}
            )

        # Create the event node and link it to agent_run
        query = f"""
        USE {db_name}
        MATCH (a:AgentRun {{session_id: $session_id}})
        CREATE (e:Event {{
            event_id: $event_id,
            session_id: $session_id,
            invocation_id: $invocation_id,
            author: $author,
            type: $event_type,
            raw_content: $raw_content,
            content: $content_parts,
            timestamp: $event_timestamp,
            created_at: $created_at
        }})
        CREATE (a)-[:HAS_EVENT]->(e)
        RETURN e.event_id as created_event_id
        """
        try:
            result, _ = db.cypher_query(
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
            print(f"Failed to create event node for {event.id}: {e}")

    def process_single_event(self, event, session_id: str, context):
        """Process a single event, create event node if it doesn't exist."""
        try:
            # Check if event already exists
            if not self.event_exists(event.id, session_id, context):
                # Create event node
                self.create_event_node(event, session_id, context)
            else:
                print(f"Event {event.id} already exists, skipping")
        except Exception as e:
            print(
                f"Failed to process event {event.id} at timestamp {event.timestamp}: {e}"
            )

    def process_session_events(self, session_id: str, events: list, context):
        """Process all events in the session, create missing event nodes."""
        if not events:
            return

        print(f"Processing {len(events)} events for session {session_id}")

        for event in events:
            self.process_single_event(event, session_id, context)

    def find_agent_run_by_session_id(self, session_id: str, context) -> Optional[str]:
        """Find agent_run node with the given session_id."""
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        query = f"""
        USE {db_name}
        MATCH (a:AgentRun {{session_id: $session_id}})
        RETURN a.session_id as found_session_id, a.agent_name as agent_name
        """
        try:
            result, _ = db.cypher_query(query, {"session_id": session_id})
            if result:
                return result[0][0]  # Return the session_id
            return None
        except Exception as e:
            print(f"Failed to find agent_run by session_id: {e}")
            return None


_neo4j_manager = None


def get_neo4j_history_manager() -> Neo4jHistoryManager:
    global _neo4j_manager
    if _neo4j_manager is None:
        _neo4j_manager = Neo4jHistoryManager()
    return _neo4j_manager
