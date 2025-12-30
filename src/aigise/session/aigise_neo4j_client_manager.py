"""
AigiseNeo4jClientManager: Session-specific Neo4j client management

This module provides session-bound Neo4j client management with support for
different database types (history, analysis, etc.).
"""

from __future__ import annotations

import logging
from typing import Dict, Literal, Optional

from aigise.session.aigise_sandbox_manager import AigiseSandboxManager
from aigise.session.neo4j_client import AsyncNeo4jClient

logger = logging.getLogger(__name__)


class AigiseNeo4jClientManager:
    """Session-specific Neo4j client manager.

    Manages different types of async Neo4j clients for different purposes:
    - history: For agent execution history
    - analysis: For static analysis data

    All clients are async-only for better performance and non-blocking operations.
    """

    def __init__(self, session):
        """Initialize AigiseNeo4jClientManager.

        Args:
            session: AigiseSession instance (stores reference, not copied)
        """
        self._session = session
        self.aigise_session_id = session.aigise_session_id

        self._clients: Dict[str, AsyncNeo4jClient] = {}

        logger.info(
            f"Created AigiseNeo4jClientManager (async-only) for session: {session.aigise_session_id}"
        )

    @property
    def config(self):
        """Get latest config from session dynamically."""
        return self._session.config

    @property
    def sandbox_manager(self):
        """Get sandbox manager from session dynamically."""
        return self._session.sandboxes

    def _get_database_name_for_type(self, client_type: str) -> str:
        """Get database name for a specific client type.

        Args:
            client_type: Type of client

        Returns:
            Database name for the type
        """
        database_mapping = {
            "history": f"agent-history",
            "analysis": f"analysis",
            "memory": f"memory",
            "default": "neo4j",
        }
        return database_mapping.get(client_type, client_type)

    def get_async_client_without_connection(
        self,
        client_type: str = "history",
        database_name: Optional[str] = None,
    ):
        """Get async Neo4j client for a specific type without connection verification.

        Args:
            client_type: Type of client ("history", "analysis", etc.)
            database_name: Optional specific database name (defaults based on type)
        """
        if client_type not in self._clients:
            # Determine database name based on type
            if database_name is None:
                database_name = self._get_database_name_for_type(client_type)

            config = self.config
            self._clients[client_type] = AsyncNeo4jClient(
                config.neo4j.uri,
                config.neo4j.user,
                config.neo4j.password,
                database=database_name,
            )

        return self._clients[client_type]

    async def get_async_client(
        self,
        client_type: Literal["history", "analysis", "memory", "default"] = "history",
        database_name: Optional[str] = None,
    ):
        """Get async Neo4j client for a specific type.

        Args:
            client_type: Type of client ("history", "analysis", etc.)
            database_name: Optional specific database name (defaults based on type)

        Returns:
            AsyncNeo4jClient instance ready for use
        """
        if client_type not in self._clients:
            # Determine database name based on type
            if database_name is None:
                database_name = self._get_database_name_for_type(client_type)

            config = self.config
            self._clients[client_type] = AsyncNeo4jClient(
                config.neo4j.uri,
                config.neo4j.user,
                config.neo4j.password,
                database=database_name,
            )
            if await self._clients[client_type].verify_connection():
                logger.info(
                    f"Connected to existing Neo4j for type: {client_type} (database: {database_name})"
                )
            else:
                raise Exception(
                    f"Failed to connect to Neo4j for type: {client_type} (database: {database_name})"
                )

        return self._clients[client_type]

    def list_clients(self) -> Dict[str, str]:
        """List all active Neo4j clients for this session.

        Returns:
            Dictionary mapping client types to their database names
        """
        result = {}
        for client_type, client in self._clients.items():
            result[client_type] = client.database or "neo4j"
        return result
