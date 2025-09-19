from contextlib import contextmanager

from neo4j import AsyncGraphDatabase, GraphDatabase


class Neo4jClient:
    def __init__(self, uri, user, password, database=None):
        self.driver = GraphDatabase.driver(
            uri, auth=(user, password), database=database
        )

    def close(self):
        self.driver.close()

    def run_query(self, query, parameters=None, database=None, **kwargs):
        with self.driver.session(database=database) as session:
            result = session.run(query, parameters, **kwargs)
            return result.data()

    @contextmanager
    def session(self, database=None):
        with self.driver.session(database=database) as session:
            yield session


class AsyncNeo4jClient:
    def __init__(self, uri, user, password, database=None):
        self.driver = AsyncGraphDatabase.driver(
            uri, auth=(user, password), database=database
        )

    async def close(self):
        await self.driver.close()

    async def run_query(self, query, parameters=None, database=None, **kwargs):
        async with self.driver.session(database=database) as session:
            result = await session.run(query, parameters, **kwargs)
            return await result.data()

    @contextmanager
    async def session(self, database=None):
        async with self.driver.session(database=database) as session:
            yield session
