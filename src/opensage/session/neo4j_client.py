import asyncio
import logging
from contextlib import contextmanager
from typing import Any

from neo4j import AsyncGraphDatabase, GraphDatabase

logger = logging.getLogger(__name__)


def _safe_str_fallback(value: Any, type_name: str = "") -> str:
    """Safely convert a value to string with fallback.

    Args:
        value (Any): The value to convert.
        type_name (str): Optional type name for error messages.
    Returns:
        str: String representation of the value, or a safe placeholder if conversion fails.
    """
    try:
        return str(value)
    except Exception as e:
        logger.warning(
            f"Failed to convert {type_name or type(value).__name__} to string: {e}"
        )
        return f"<non-serializable: {type(value).__name__}>"


def _convert_neo4j_types_to_native(value: Any) -> Any:
    """Recursively convert Neo4j special types to Python native types.

    This function handles all Neo4j types that are not directly JSON serializable.
    It uses a generic approach with multiple fallback layers:
    1. Try specific conversion methods (to_native(), dict conversion, etc.)
    2. Fallback to str() representation
    3. Final fallback to safe placeholder string

    Args:
        value (Any): The value to convert (can be dict, list, or any Neo4j type).
    Returns:
        Any: Converted value with Neo4j types replaced by Python native types.
        Always returns a JSON-serializable type.
    """
    # Handle dict recursively
    if isinstance(value, dict):
        try:
            return {k: _convert_neo4j_types_to_native(v) for k, v in value.items()}
        except Exception as e:
            logger.warning(f"Failed to convert dict recursively: {e}")
            return _safe_str_fallback(value, "dict")

    # Handle list recursively
    if isinstance(value, list):
        try:
            return [_convert_neo4j_types_to_native(item) for item in value]
        except Exception as e:
            logger.warning(f"Failed to convert list recursively: {e}")
            return _safe_str_fallback(value, "list")

    # Handle Neo4j special types by checking module name
    # This is more generic than checking specific type names
    try:
        module_name = type(value).__module__
    except Exception:
        # If we can't even get the module name, return safe placeholder
        return _safe_str_fallback(value)

    # Handle all neo4j.time types (DateTime, Date, Time, LocalDateTime, etc.)
    if module_name == "neo4j.time":
        # Most temporal types have to_native() method
        if hasattr(value, "to_native"):
            try:
                return value.to_native()
            except Exception as e:
                logger.debug(f"to_native() failed for {type(value).__name__}: {e}")
                # Fallback to string if to_native() fails
                return _safe_str_fallback(value, type(value).__name__)
        else:
            # Duration and other types without to_native()
            return _safe_str_fallback(value, type(value).__name__)

    # Handle neo4j.spatial types (Point, CartesianPoint, WGS84Point, etc.)
    if module_name.startswith("neo4j.spatial") or module_name.startswith(
        "neo4j._spatial"
    ):
        # Convert Point-like objects to dict with coordinates
        try:
            if hasattr(value, "x") and hasattr(value, "y"):
                result = {"x": value.x, "y": value.y}
                if hasattr(value, "z") and value.z is not None:
                    result["z"] = value.z
                if hasattr(value, "srid"):
                    result["srid"] = value.srid
                return result
        except Exception as e:
            logger.debug(f"Failed to convert spatial type to dict: {e}")
        # Fallback to string representation
        return _safe_str_fallback(value, type(value).__name__)

    # Handle neo4j.graph types (Node, Relationship, Path)
    if module_name.startswith("neo4j.graph") or module_name.startswith("neo4j._graph"):
        # Convert graph objects to dict representation
        try:
            if hasattr(value, "id"):
                result = {"id": value.id}
                if hasattr(value, "labels"):
                    try:
                        result["labels"] = list(value.labels) if value.labels else []
                    except Exception:
                        result["labels"] = []
                if hasattr(value, "properties"):
                    try:
                        result["properties"] = _convert_neo4j_types_to_native(
                            dict(value.properties)
                        )
                    except Exception:
                        result["properties"] = {}
                if hasattr(value, "type"):
                    result["type"] = value.type
                if hasattr(value, "start_node"):
                    try:
                        result["start_node"] = _convert_neo4j_types_to_native(
                            value.start_node
                        )
                    except Exception:
                        result["start_node"] = _safe_str_fallback(value.start_node)
                if hasattr(value, "end_node"):
                    try:
                        result["end_node"] = _convert_neo4j_types_to_native(
                            value.end_node
                        )
                    except Exception:
                        result["end_node"] = _safe_str_fallback(value.end_node)
                if hasattr(value, "nodes"):
                    try:
                        result["nodes"] = [
                            _convert_neo4j_types_to_native(n) for n in value.nodes
                        ]
                    except Exception:
                        result["nodes"] = []
                if hasattr(value, "relationships"):
                    try:
                        result["relationships"] = [
                            _convert_neo4j_types_to_native(r)
                            for r in value.relationships
                        ]
                    except Exception:
                        result["relationships"] = []
                return result
        except Exception as e:
            logger.debug(f"Failed to convert graph type to dict: {e}")
        # Fallback to string representation
        return _safe_str_fallback(value, type(value).__name__)

    # Handle any other neo4j.* types that might exist
    if module_name.startswith("neo4j."):
        # Try to_native() first for any neo4j type
        if hasattr(value, "to_native"):
            try:
                return value.to_native()
            except Exception as e:
                logger.debug(f"to_native() failed for {type(value).__name__}: {e}")
        # Fallback to string representation
        return _safe_str_fallback(value, type(value).__name__)

    # Return unchanged if not a Neo4j special type
    # Python native types (int, str, float, bool, None) are already JSON serializable
    # If there are other non-serializable types, they will be caught during actual
    # JSON serialization and can be handled at that level
    return value


class AsyncNeo4jClient:
    def __init__(self, uri, user, password, database=None):
        """Initialize async Neo4j client with optional wait for readiness.

        Args:
            uri: Neo4j connection URI
            user: Username
            password: Password
            database: Optional database name"""
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database

        self.driver = AsyncGraphDatabase.driver(
            uri, auth=(user, password), database=database
        )

    async def verify_connection(self) -> bool:
        """Wait for Neo4j readiness (used in __init__).

        Returns:
            bool: True if ready, False if timeout
        """
        logger.info(f"Waiting for Neo4j at {self.uri} to be ready (async client)...")

        try:
            # Use async driver for testing connection
            async with self.driver.session(database="neo4j") as session:
                result = await session.run("RETURN 1 as test")
                data = await result.data()
                if data and data[0]["test"] == 1:
                    logger.info(f"Neo4j default database ready")

                    # If we need a specific database, check if it exists and create if needed
                    if self.database and self.database != "neo4j":
                        await self._ensure_database_exists(self.driver, self.database)

                    return True
        except Exception as e:
            logger.debug(f"Neo4j default database not ready yet: {e}")
        return False

    async def _ensure_database_exists(self, driver, database_name: str):
        """Ensure the target database exists, create if it doesn't.

        Args:
            driver: Async Neo4j driver connected to default database
            database_name (str): Name of database to check/create"""
        try:
            async with driver.session(database="neo4j") as session:
                # Check if database exists
                result = await session.run("SHOW DATABASES")
                data = await result.data()
                existing_databases = [record["name"] for record in data]

                if database_name not in existing_databases:
                    logger.info(f"Database {database_name} does not exist, creating...")
                    await session.run(f"CREATE DATABASE `{database_name}`")
                    logger.info(f"Created database: {database_name}")

                # Start the database after creation
                logger.info(f"Starting database: {database_name}")
                await session.run(f"START DATABASE `{database_name}`")
                logger.info(f"Started database: {database_name}")

                # Wait for database to become online after creation
                await self._wait_for_database_online(driver, database_name)
        except Exception as e:
            logger.warning(f"Failed to check/create database {database_name}: {e}")
            # Continue anyway, maybe the database exists but we can't check it

    async def _wait_for_database_online(
        self, driver, database_name: str, timeout: int = 60
    ):
        """Wait for database to become online after creation (async version).

        Args:
            driver: Async Neo4j driver connected to default database
            database_name (str): Name of database to wait for
            timeout (int): Maximum wait time in seconds"""
        logger.info(f"Waiting for database {database_name} to come online...")

        for attempt in range(timeout):
            try:
                async with driver.session(database="neo4j") as session:
                    result = await session.run("SHOW DATABASES")
                    data = await result.data()

                    for record in data:
                        if (
                            record["name"] == database_name
                            and record.get("currentStatus", "").lower() == "online"
                        ):
                            # Database shows as online, now test if it's actually usable
                            try:
                                async with driver.session(
                                    database=database_name
                                ) as test_session:
                                    test_result = await test_session.run(
                                        "RETURN 1 as test"
                                    )
                                    test_data = await test_result.data()
                                    if test_data and test_data[0]["test"] == 1:
                                        logger.info(
                                            f"Database {database_name} is now online and functional after {attempt + 1} seconds"
                                        )
                                        return True
                            except Exception as e:
                                logger.debug(
                                    f"Database {database_name} online but not functional yet: {e}"
                                )
                                break  # Exit inner loop, continue waiting
            except Exception as e:
                logger.debug(
                    f"Error checking database status (attempt {attempt + 1}): {e}"
                )

            await asyncio.sleep(1)
        logger.warning(
            f"Database {database_name} did not come online within {timeout} seconds"
        )
        return False

    async def close(self):
        await self.driver.close()

    async def run_query(self, query, parameters=None, **kwargs):
        async with self.driver.session(database=self.database) as session:
            result = await session.run(query, parameters, **kwargs)
            data = await result.data()
            # Convert Neo4j special types to Python native types for JSON serialization
            return _convert_neo4j_types_to_native(data)

    @contextmanager
    async def session(self, database=None):
        async with self.driver.session(database=database) as session:
            yield session
