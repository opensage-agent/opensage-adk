from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime
from threading import BrokenBarrierError
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

    def create_raw_tool_response_node(
        self, tool, args, tool_context, tool_response, summary
    ):
        """Create a raw tool response node in Neo4j."""
        shared_session_id = self.get_shared_session_id(tool_context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")
        session_id = tool_context._invocation_context.session.id

        query = f"""
        USE {db_name}
        MATCH (a:AgentRun {{session_id: $session_id}})
        CREATE (r:RawToolResponse {{
            node_id: $node_id,
            session_id: $session_id,
            tool_name: $tool_name,
            tool_args: $tool_args,
            raw_content: $raw_content,
            summary: $summary,
            created_at: $created_at
        }})
        CREATE (a)-[:AGENT_RUN_HAS_RAW_TOOL_RESPONSE]->(r)
        RETURN r.node_id as created_node_id
        """

        try:
            result, _ = db.cypher_query(
                query,
                {
                    "session_id": session_id,
                    "node_id": str(uuid.uuid4()),
                    "tool_name": tool.name,
                    "tool_args": str(args),
                    "raw_content": str(tool_response),
                    "summary": summary,
                    "created_at": datetime.now().isoformat(),
                },
            )

            if result:
                print(f"Created raw_tool_response node for tool {tool.name}")
                return True

        except Exception as e:
            print(f"Failed to create raw_tool_response node: {e}")
            return False

    def _create_summarize_relation(self, event, session_id, context, summary_content):
        """Create a summarize relation between event and matching RawToolResponse node."""
        shared_session_id = self.get_shared_session_id(context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")

        query = f"""
        USE {db_name}
        MATCH (e:Event {{event_id: $event_id}})
        MATCH (r:RawToolResponse {{session_id: $session_id}})
        WHERE r.summary = $summary_content
        CREATE (e)-[:SUMMARIZES_TOOL_RESPONSE]->(r)
        SET e.type = "tool_response_summary"
        RETURN r.node_id as matched_node_id, r.tool_name as tool_name
        """

        try:
            result, _ = db.cypher_query(
                query,
                {
                    "event_id": event.id,
                    "session_id": session_id,
                    "summary_content": summary_content,
                },
            )

            if result:
                matched_node_id = result[0][0]
                tool_name = result[0][1]
                print(
                    f"Created SUMMARIZES relation: RawToolResponse({tool_name}) -> Event({event.id})"
                )
                return True
            else:
                print(f"No matching RawToolResponse found for summary content")
                return False

        except Exception as e:
            print(f"Failed to create summarize relation: {e}")
            return False

    def _maybe_create_summarize_relation(self, event, session_id, context):
        """Check if event contains summary tags and create relation if found."""
        if not (event.content and event.content.parts):
            return False

        pattern = r"<Summary by aigise>(.*?)</Summary by aigise>"

        for part in event.content.parts:
            # Check part.text
            if hasattr(part, "text") and part.text:
                if (
                    "<Summary by aigise>" in part.text
                    and "</Summary by aigise>" in part.text
                ):
                    # Extract the complete content including tags
                    match = re.search(pattern, part.text, re.DOTALL)
                    if match:
                        # Keep the full content with tags
                        summary_content = match.group(
                            0
                        )  # group(0) includes the entire match with tags
                        return self._create_summarize_relation(
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
                        "<Summary by aigise>" in value_str
                        and "</Summary by aigise>" in value_str
                    ):
                        match = re.search(pattern, value_str, re.DOTALL)
                        if match:
                            # Keep the full content with tags
                            summary_content = match.group(
                                0
                            )  # group(0) includes the entire match with tags
                            return self._create_summarize_relation(
                                event, session_id, context, summary_content
                            )

        return False

    def process_single_event(self, event, session_id: str, context):
        """Process a single event, create event node if it doesn't exist."""
        try:
            # Check if event already exists
            if not self.event_exists(event.id, session_id, context):
                # Create event node
                self.create_event_node(event, session_id, context)
            else:
                print(f"Event {event.id} already exists, skipping")

            # Check if event contains summary tags and create relation if found
            self._maybe_create_summarize_relation(event, session_id, context)
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

    def create_history_summary_node(
        self, tool_context, summary_event, events_to_summarize
    ):
        """Create history summary node and manage relationships in Neo4j."""

        shared_session_id = self.get_shared_session_id(tool_context)
        db_name = f"agent-history-{shared_session_id}".replace("-", "")
        session_id = tool_context._invocation_context.session.id

        # First create the summary event node
        try:
            create_summary_query = f"""
            USE {db_name}
            MATCH (a:AgentRun {{session_id: $session_id}})
            CREATE (s:Event {{
                event_id: $event_id,
                session_id: $session_id,
                role: $role,
                content: $content,
                timestamp: $timestamp,
                type: "history_summary",
                created_at: $created_at
            }})
            CREATE (a)-[:HAS_EVENT]->(s)
            RETURN s.event_id as event_id
            """

            # Extract summary content
            summary_content = ""
            if summary_event.content and summary_event.content.parts:
                for part in summary_event.content.parts:
                    if hasattr(part, "text") and part.text:
                        summary_content += part.text

            params = {
                "event_id": summary_event.id,
                "session_id": session_id,
                "role": summary_event.content.role if summary_event.content else "user",
                "content": summary_content,
                "timestamp": datetime.fromtimestamp(
                    summary_event.timestamp
                ).isoformat(),
                "created_at": datetime.now().isoformat(),
            }

            result, _ = db.cypher_query(create_summary_query, params)

            print(f"Created history summary node: {summary_event.id}")

            # Now handle the summarized events
            for event in events_to_summarize:
                # Remove HAS_EVENT relationship from AgentRun
                remove_relation_query = f"""
                USE {db_name}
                MATCH (a:AgentRun {{session_id: $session_id}})-[r:HAS_EVENT]->(e:Event {{event_id: $event_id}})
                DELETE r
                """

                db.cypher_query(
                    remove_relation_query,
                    {"session_id": session_id, "event_id": event.id},
                )

                # Create SUMMARIZES_EVENTS relationship
                create_summarize_relation_query = f"""
                USE {db_name}
                MATCH (s:Event {{event_id: $summary_event_id}})
                MATCH (e:Event {{event_id: $event_id}})
                CREATE (s)-[:SUMMARIZES_EVENTS]->(e)
                """

                db.cypher_query(
                    create_summarize_relation_query,
                    {"summary_event_id": summary_event.id, "event_id": event.id},
                )

            print(
                f"History summary processed: {len(events_to_summarize)} events summarized into {summary_event.id}"
            )
            return True

        except Exception as e:
            print(f"Failed to create history summary node: {e}")
            return False


_neo4j_manager = None


def get_neo4j_history_manager() -> Neo4jHistoryManager:
    global _neo4j_manager
    if _neo4j_manager is None:
        _neo4j_manager = Neo4jHistoryManager()
    return _neo4j_manager
