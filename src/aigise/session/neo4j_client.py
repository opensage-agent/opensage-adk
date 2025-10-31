import asyncio
import logging
from contextlib import contextmanager

from neo4j import AsyncGraphDatabase, GraphDatabase

logger = logging.getLogger(__name__)


class AsyncNeo4jClient:
    def __init__(self, uri, user, password, database=None):
        """Initialize async Neo4j client with optional wait for readiness.

        Args:
            uri: Neo4j connection URI
            user: Username
            password: Password
            database: Optional database name
        """
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
            True if ready, False if timeout
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
            database_name: Name of database to check/create
        """
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
            database_name: Name of database to wait for
            timeout: Maximum wait time in seconds
        """
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
            return await result.data()

    @contextmanager
    async def session(self, database=None):
        async with self.driver.session(database=database) as session:
            yield session
