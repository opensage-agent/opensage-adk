from contextlib import contextmanager

from neo4j import GraphDatabase


class Neo4JClient:
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
